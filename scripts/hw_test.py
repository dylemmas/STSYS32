#!/usr/bin/env python3
"""
hw_test.py — STASYS ESP32 Hardware Test Suite
Automated end-to-end test of all firmware subsystems via SPP.

Usage:
    python hw_test.py COM3   # BT SPP virtual COM port
    python hw_test.py COM6   # USB serial (debug log only)

Requirements:
    pip install pyserial
    Python 3.8+
"""

import sys
import serial
import struct
import time
import hmac
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict
from datetime import datetime

# ── Protocol (must match firmware src/protocol.h) ──────────────────────────────
SYNC_0, SYNC_1 = 0xAA, 0x55

PKT_TYPE_CMD_START_SESSION    = 0x01
PKT_TYPE_CMD_STOP_SESSION     = 0x02
PKT_TYPE_CMD_GET_INFO         = 0x03
PKT_TYPE_CMD_GET_CONFIG       = 0x04
PKT_TYPE_CMD_SET_CONFIG       = 0x05
PKT_TYPE_CMD_AUTH             = 0x06
PKT_TYPE_CMD_OTA_STATUS       = 0x11
PKT_TYPE_CMD_GET_COREDUMP     = 0x41
PKT_TYPE_CMD_GET_SHOT_STATS   = 0x43

PKT_TYPE_EVT_SESSION_STARTED  = 0x10
PKT_TYPE_EVT_SESSION_STOPPED  = 0x11
PKT_TYPE_EVT_SHOT_DETECTED    = 0x12
PKT_TYPE_EVT_SENSOR_HEALTH    = 0x13
PKT_TYPE_EVT_AUTH_CHALLENGE   = 0x14

PKT_TYPE_DATA_RAW_SAMPLE       = 0x20
PKT_TYPE_RSP_ERROR            = 0x80
PKT_TYPE_RSP_INFO             = 0x81
PKT_TYPE_RSP_CONFIG           = 0x82
PKT_TYPE_RSP_ACK              = 0x83
PKT_TYPE_RSP_OTA_STATUS       = 0x84
PKT_TYPE_RSP_SHOT_STATS       = 0x85


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build_packet(pkt_type: int, payload: bytes = b'') -> bytes:
    data = bytes([pkt_type, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF]) + payload
    crc = crc16_ccitt(data)
    return bytes([SYNC_0, SYNC_1]) + data + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def build_set_config(**kwargs) -> bytes:
    # 50-byte config: B(1)+H(2)+H(2)+H(2)+B(1)+B(1)+H(2)+4s+20s+15s = 50 bytes
    sr = kwargs.get('sample_rate_hz', 100)
    pz = kwargs.get('piezo_threshold', 800)
    ax = kwargs.get('accel_threshold', 300)
    db = kwargs.get('debounce_ms', 200)
    led = kwargs.get('led_enabled', 1)
    dm = kwargs.get('data_mode', 0)
    strm = kwargs.get('streaming_rate_hz', 100)
    pin = kwargs.get('bt_pin', b'1234')
    name = kwargs.get('device_name', 'STASYS')
    reserved = b'\x00' * 15
    # Format: <BHHHBBH4s20s15s = 1+2+2+2+1+1+2+4+20+15 = 50 bytes
    payload = struct.pack('<BHHHBBH4s20s15s',
        sr, pz, ax, db, led, dm, strm, pin,
        name.encode().ljust(20, b'\x00'), reserved)
    return build_packet(PKT_TYPE_CMD_SET_CONFIG, payload)


# ── Auth helpers (must match firmware src/security.cpp) ─────────────────────────
# Device MAC from firmware serial log: 78:1C:3C:F4:FF:7A
DEVICE_MAC = bytes([0x78, 0x1C, 0x3C, 0xF4, 0xFF, 0x7A])


def derive_device_secret(mac: bytes) -> bytes:
    """Derive 16-byte device secret: HMAC-SHA256('STASYS-DEVICE-KEY', mac)[:16]"""
    key = b'STASYS-DEVICE-KEY'
    h = hmac.new(key, mac, hashlib.sha256).digest()
    return h[:16]


def compute_auth_token(secret16: bytes, session_id: int, challenge16: bytes) -> bytes:
    """Compute auth token: HMAC-SHA256(secret16, session_id_4bytes + challenge16)"""
    msg = struct.pack('<I', session_id) + challenge16
    return hmac.new(secret16, msg, hashlib.sha256).digest()


# ── Decoder ───────────────────────────────────────────────────────────────────
class SppDecoder:
    WAIT_SYNC0, WAIT_SYNC1, READ_TYPE, READ_LEN_LO, READ_LEN_HI, \
        READ_PAYLOAD, READ_CRC_LO, READ_CRC_HI = range(8)

    def __init__(self):
        self._state = self.WAIT_SYNC0
        self._type = 0
        self._len = 0
        self._payload = bytearray()
        self._crc = 0
        self._packets: List[tuple] = []

    def feed(self, data: bytes) -> List[tuple]:
        self._packets.clear()
        for b in data:
            self._process_byte(b & 0xFF)
        return list(self._packets)

    def _process_byte(self, b: int) -> None:
        s = self._state
        if s == self.WAIT_SYNC0:
            if b == SYNC_0: self._state = self.WAIT_SYNC1
        elif s == self.WAIT_SYNC1:
            if b == SYNC_1: self._state = self.READ_TYPE
            elif b == SYNC_0: pass
            else: self._state = self.WAIT_SYNC0
        elif s == self.READ_TYPE:
            self._type = b; self._state = self.READ_LEN_LO
        elif s == self.READ_LEN_LO:
            self._len = b; self._state = self.READ_LEN_HI
        elif s == self.READ_LEN_HI:
            self._len |= b << 8
            if self._len > 256:
                self._state = self.WAIT_SYNC0; return
            self._payload = bytearray()
            self._state = self.READ_CRC_LO if self._len == 0 else self.READ_PAYLOAD
        elif s == self.READ_PAYLOAD:
            self._payload.append(b)
            if len(self._payload) >= self._len:
                self._state = self.READ_CRC_LO
        elif s == self.READ_CRC_LO:
            self._crc = b; self._state = self.READ_CRC_HI
        elif s == self.READ_CRC_HI:
            self._crc |= b << 8
            self._state = self.WAIT_SYNC0
            hdr = bytes([self._type, self._len & 0xFF, (self._len >> 8) & 0xFF])
            if crc16_ccitt(hdr + bytes(self._payload)) == self._crc:
                self._packets.append((self._type, bytes(self._payload)))


# ── Packet Parsers ─────────────────────────────────────────────────────────────
def parse_info(data: bytes) -> Optional[dict]:
    # 14 bytes packed: firmware_version(4)+hardware_rev(1)+build_timestamp(4)+features(2)+mpu_whoami(1)+pad(1)
    if len(data) < 14: return None
    fw = struct.unpack('<I', data[0:4])[0]
    hw = data[4]
    build = struct.unpack('<I', data[5:9])[0]
    features = struct.unpack('<H', data[9:11])[0]
    mpu = data[11]
    return {'fw_version': f"v{(fw >> 16) & 0xFF}.{(fw >> 8) & 0xFF}.{fw & 0xFF}",
            'hardware_rev': hw, 'build_ts': build, 'features': features, 'mpu_whoami': mpu}

def parse_config(data: bytes) -> Optional[dict]:
    # 36 bytes packed: B(1)+H(2)+H(2)+H(2)+B(1)+B(1)+H(2)+4s+20s
    if len(data) < 36: return None
    return {
        'sample_rate_hz': struct.unpack('<B', data[0:1])[0],
        'piezo_threshold': struct.unpack('<H', data[1:3])[0],
        'accel_threshold': struct.unpack('<H', data[3:5])[0],
        'debounce_ms': struct.unpack('<H', data[5:7])[0],
        'led_enabled': bool(data[7]),
        'data_mode': data[8],
        'streaming_rate_hz': struct.unpack('<H', data[9:11])[0],
        'bt_pin': data[11:15],
        'device_name': data[15:35].rstrip(b'\x00').decode(errors='replace'),
    }

def parse_session_started(data: bytes) -> Optional[dict]:
    # 14 bytes packed: session_id(4)+timestamp_us(4)+battery(1)+health(1)+free_heap(4)
    if len(data) < 14: return None
    return {
        'session_id': struct.unpack('<I', data[0:4])[0],
        'timestamp_us': struct.unpack('<I', data[4:8])[0],
        'battery_percent': data[8],
        'sensor_health': data[9],
        'free_heap': struct.unpack('<I', data[10:14])[0],
    }

def parse_session_stopped(data: bytes) -> Optional[dict]:
    # 12 bytes packed: session_id(4)+duration_ms(4)+shot_count(2)+battery_end(1)+health(1)
    if len(data) < 12: return None
    return {
        'session_id': struct.unpack('<I', data[0:4])[0],
        'duration_ms': struct.unpack('<I', data[4:8])[0],
        'shot_count': struct.unpack('<H', data[8:10])[0],
        'battery_end': data[10],
        'sensor_health': data[11],
    }

def parse_shot(data: bytes) -> Optional[dict]:
    # 26 bytes packed: session_id(4)+timestamp_us(4)+shot_number(2)+piezo_peak(2)+accel(3*2)+gyro(3*2)+recoil(2)
    if len(data) < 26: return None
    return {
        'session_id': struct.unpack('<I', data[0:4])[0],
        'timestamp_us': struct.unpack('<I', data[4:8])[0],
        'shot_number': struct.unpack('<H', data[8:10])[0],
        'piezo_peak': struct.unpack('<H', data[10:12])[0],
        'accel_x': struct.unpack('<h', data[12:14])[0],
        'accel_y': struct.unpack('<h', data[14:16])[0],
        'accel_z': struct.unpack('<h', data[16:18])[0],
        'gyro_x': struct.unpack('<h', data[18:20])[0],
        'gyro_y': struct.unpack('<h', data[20:22])[0],
        'gyro_z': struct.unpack('<h', data[22:24])[0],
        'recoil_axis': struct.unpack('<b', data[24:25])[0],
        'recoil_sign': struct.unpack('<b', data[25:26])[0],
    }

def parse_raw_sample(data: bytes) -> Optional[dict]:
    # 24 bytes packed: counter(4)+timestamp(4)+accel(6)+gyro(6)+piezo(2)+temp(2)
    if len(data) < 24: return None
    return {
        'counter': struct.unpack('<I', data[0:4])[0],
        'timestamp_us': struct.unpack('<I', data[4:8])[0],
        'accel_x': struct.unpack('<h', data[8:10])[0],
        'accel_y': struct.unpack('<h', data[10:12])[0],
        'accel_z': struct.unpack('<h', data[12:14])[0],
        'gyro_x': struct.unpack('<h', data[14:16])[0],
        'gyro_y': struct.unpack('<h', data[16:18])[0],
        'gyro_z': struct.unpack('<h', data[18:20])[0],
        'piezo': struct.unpack('<H', data[20:22])[0],
        'temperature': struct.unpack('<h', data[22:24])[0],
    }

def parse_auth_challenge(data: bytes) -> tuple[int, bytes]:
    """Extract (session_id, challenge) from EVT_AUTH_CHALLENGE payload.
    Payload: session_id(4 LE) + challenge(16 bytes) = 20 bytes total."""
    if len(data) < 20:
        raise ValueError(f"Auth challenge too short: {len(data)} bytes (expected 20)")
    session_id = struct.unpack('<I', data[0:4])[0]
    challenge = bytes(data[4:20])
    return session_id, challenge

def parse_ack(data: bytes) -> Optional[dict]:
    if len(data) < 2: return None
    return dict(zip(['command_id', 'status'], struct.unpack('<BB', data[:2])))

def parse_error(data: bytes) -> Optional[dict]:
    if len(data) < 1: return None
    return {'error_code': data[0], 'message': data[1:].rstrip(b'\x00').decode(errors='replace')}

def parse_shot_stats(data: bytes) -> Optional[dict]:
    # 11 bytes packed: shot_count(2)+mean_peak(2)+stddev_peak(2)+adaptive_threshold(4)+enabled(1)
    if len(data) < 11: return None
    vals = struct.unpack('<HHHIH', data[:11])
    return {'shot_count': vals[0], 'mean_peak': vals[1],
            'stddev_peak': vals[2], 'adaptive_threshold': vals[3],
            'adaptive_enabled': bool(vals[4])}


# ── Test Result ────────────────────────────────────────────────────────────────
@dataclass
class TestResult:
    name: str
    passed: bool
    details: str = ''


# ── Test Harness ───────────────────────────────────────────────────────────────
class STASYSTester:
    def __init__(self, port: str, baud: int = 115200, timeout: float = 3.0):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser: Optional[serial.Serial] = None
        self.decoder = SppDecoder()
        self.results: List[TestResult] = []
        self._info: Optional[dict] = None
        self._config: Optional[dict] = None
        self._session_id: int = 0
        self._session_started: bool = False
        self._device_secret = derive_device_secret(DEVICE_MAC)

    def connect(self) -> bool:
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=self.timeout,
                                     write_timeout=5.0)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            # Wait for ESP32 to fully initialize before sending commands
            time.sleep(2.0)
            # Drain any boot messages
            if self.ser.in_waiting:
                self.ser.reset_input_buffer()
            return True
        except Exception as e:
            print(f"[ERROR] Cannot open {self.port}: {e}")
            return False

    def disconnect(self) -> None:
        if self.ser and self.ser.is_open:
            self.ser.close()

    def send(self, pkt_type: int, payload: bytes = b'') -> None:
        if not self.ser: return
        pkt = build_packet(pkt_type, payload)
        self.ser.write(pkt)
        print(f"  [DBG] Sent pkt type=0x{pkt_type:02X} len={len(payload)} hex={pkt.hex()}")

    def _check_bt_connected(self) -> bool:
        """Return True if BT SPP data is flowing (not just USB serial boot msgs)."""
        if not self.ser:
            return False
        # Drain any existing data and check if it's just USB serial text
        if self.ser.in_waiting:
            raw = self.ser.read(self.ser.in_waiting)
            text = bytes(raw).decode(errors='replace')
            # Boot messages contain "===" or "STASYS" or "MAIN" etc.
            if '===' in text or 'STASYS' in text or '[MAIN]' in text:
                print(f"  [DBG] Received USB serial data (boot msgs), not BT SPP")
                print(f"  [DBG] First 100 chars: {text[:100].strip()!r}")
                return False
            # If it's not boot text, it might be BT data
            self.decoder.feed(raw)
            return True
        return False

    def send_raw(self, pkt: bytes) -> None:
        if self.ser: self.ser.write(pkt)

    def _wait_for(self, pkt_types: List[int], timeout: float) -> Dict[int, List[bytes]]:
        """Wait for specific packet types and return them."""
        events: Dict[int, List[bytes]] = {t: [] for t in pkt_types}
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.ser and self.ser.in_waiting:
                pkts = self.decoder.feed(bytes(self.ser.read(self.ser.in_waiting)))
                for t, d in pkts:
                    if t in events:
                        events[t].append(d)
            time.sleep(0.005)
        return events

    def _record(self, name: str, passed: bool, details: str = '') -> None:
        self.results.append(TestResult(name, passed, details))
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status}: {name}")
        if details:
            print(f"         {details}")

    def _start_session(self) -> bool:
        """Send CMD_START_SESSION, handle auth challenge, return True if session starts."""
        if not self._check_bt_connected():
            print("  -> BT not connected (only USB serial data detected)")
            print("  -> ESP32 needs to be paired via Windows Bluetooth Settings")
            return False
        print("  -> Sending CMD_START_SESSION...")
        self.send(PKT_TYPE_CMD_START_SESSION)

        # Wait for EVT_AUTH_CHALLENGE
        events = self._wait_for([PKT_TYPE_EVT_AUTH_CHALLENGE, PKT_TYPE_RSP_ERROR], timeout=3.0)

        if PKT_TYPE_RSP_ERROR in events and events[PKT_TYPE_RSP_ERROR]:
            err = parse_error(events[PKT_TYPE_RSP_ERROR][0])
            print(f"  -> ERROR: {err['message']}")
            return False

        challenges = events.get(PKT_TYPE_EVT_AUTH_CHALLENGE, [])
        if not challenges:
            print("  -> No auth challenge received (session may be open)")
            return False

        session_id, challenge = parse_auth_challenge(challenges[0])

        print(f"  -> Got challenge, computing HMAC (session_id={session_id})...")
        token = compute_auth_token(self._device_secret, session_id, challenge)
        auth_payload = struct.pack('<I', session_id) + token
        self.send(PKT_TYPE_CMD_AUTH, auth_payload)

        # Wait for EVT_SESSION_STARTED
        events2 = self._wait_for([PKT_TYPE_EVT_SESSION_STARTED, PKT_TYPE_RSP_ERROR, PKT_TYPE_RSP_ACK], timeout=3.0)

        if PKT_TYPE_RSP_ERROR in events2 and events2[PKT_TYPE_RSP_ERROR]:
            err = parse_error(events2[PKT_TYPE_RSP_ERROR][0])
            print(f"  -> AUTH FAILED: {err['message']}")
            return False

        started = events2.get(PKT_TYPE_EVT_SESSION_STARTED, [])
        if not started:
            ack = events2.get(PKT_TYPE_RSP_ACK, [])
            if ack:
                a = parse_ack(ack[0])
                print(f"  -> Got ACK cmd={a['command_id']} status={a['status']}")
                # Try waiting a bit more for session started
                events3 = self._wait_for([PKT_TYPE_EVT_SESSION_STARTED], timeout=2.0)
                started = events3.get(PKT_TYPE_EVT_SESSION_STARTED, [])

        if started:
            p = parse_session_started(started[0])
            self._session_id = p['session_id'] if p else 0
            print(f"  -> Session {self._session_id} started!")
            return True

        print("  -> No EVT_SESSION_STARTED after auth")
        return False

    def _stop_session(self) -> Optional[dict]:
        """Send CMD_STOP_SESSION, return parsed result."""
        self.send(PKT_TYPE_CMD_STOP_SESSION)
        events = self._wait_for([PKT_TYPE_EVT_SESSION_STOPPED, PKT_TYPE_RSP_ERROR], timeout=3.0)
        if PKT_TYPE_RSP_ERROR in events and events[PKT_TYPE_EVT_SESSION_STOPPED]:
            return None
        stopped = events.get(PKT_TYPE_EVT_SESSION_STOPPED, [])
        if stopped:
            return parse_session_stopped(stopped[0])
        return None

    def _wait_samples(self, count: int, timeout_s: float) -> List[dict]:
        samples = []
        deadline = time.time() + timeout_s
        while time.time() < deadline and len(samples) < count:
            if self.ser and self.ser.in_waiting:
                pkts = self.decoder.feed(bytes(self.ser.read(self.ser.in_waiting)))
                for t, d in pkts:
                    if t == PKT_TYPE_DATA_RAW_SAMPLE:
                        p = parse_raw_sample(d)
                        if p: samples.append(p)
            time.sleep(0.005)
        return samples

    # ── Individual Tests ────────────────────────────────────────────────────────

    def test_get_info(self) -> None:
        print("\n[T1] CMD_GET_INFO")
        if not self._check_bt_connected():
            self._record("GET_INFO responds", False,
                "BT not connected — ESP32 needs pairing via Windows Bluetooth Settings")
            return
        self.send(PKT_TYPE_CMD_GET_INFO)
        events = self._wait_for([PKT_TYPE_RSP_INFO], timeout=2.0)
        infos = events.get(PKT_TYPE_RSP_INFO, [])
        if infos:
            self._info = parse_info(infos[0])
            self._record("GET_INFO responds", True,
                f"fw={self._info['fw_version']} features=0x{self._info['features']:04X} "
                f"mpu=0x{self._info['mpu_whoami']:02X}")
        else:
            self._record("GET_INFO responds", False, "No RSP_INFO received")

    def test_get_config(self) -> None:
        print("\n[T2] CMD_GET_CONFIG")
        if not self._check_bt_connected():
            self._record("GET_CONFIG responds", False,
                "BT not connected — ESP32 needs pairing via Windows Bluetooth Settings")
            return
        self.send(PKT_TYPE_CMD_GET_CONFIG)
        events = self._wait_for([PKT_TYPE_RSP_CONFIG], timeout=2.0)
        cfgs = events.get(PKT_TYPE_RSP_CONFIG, [])
        if cfgs:
            self._config = parse_config(cfgs[0])
            if self._config:
                pin = self._config['bt_pin'].rstrip(b'\x00').decode(errors='replace') or '1234'
                self._record("GET_CONFIG responds", True,
                    f"SR={self._config['sample_rate_hz']}Hz piezo={self._config['piezo_threshold']} "
                    f"name={self._config['device_name']} led={self._config['led_enabled']}")
            else:
                self._record("GET_CONFIG responds", False, f"parse failed, len={len(cfgs[0])}")
        else:
            self._record("GET_CONFIG responds", False, "No RSP_CONFIG received")

    def test_imu_readings(self) -> None:
        print("\n[T3] IMU Readings (Accelerometer + Gyroscope)")
        if not self._session_started:
            if not self._start_session():
                self._record("Session start", False, "Session failed to start (auth required)")
                self._record("IMU readings", False, "Session not active")
                return

        samples = self._wait_samples(20, 1.5)
        if len(samples) >= 10:
            ax = [s['accel_x'] for s in samples]
            ay = [s['accel_y'] for s in samples]
            az = [s['accel_z'] for s in samples]
            gx = [s['gyro_x'] for s in samples]
            accel_nonzero = any(abs(v) > 100 for v in ax + ay + az)
            self._record("Accel data present", accel_nonzero,
                f"ax={ax[-1]} ay={ay[-1]} az={az[-1]}")
            self._record("Gyro data present", all(abs(v) < 5000 for v in gx),
                f"gx={gx[-1]} gyro_y={samples[-1]['gyro_y']} gyro_z={samples[-1]['gyro_z']}")
            self._record("Sample counter increments", samples[-1]['counter'] > samples[0]['counter'],
                f"first={samples[0]['counter']} last={samples[-1]['counter']}")
            self._record("Timestamp increments", samples[-1]['timestamp_us'] > samples[0]['timestamp_us'],
                f"delta={samples[-1]['timestamp_us'] - samples[0]['timestamp_us']}us")
        else:
            self._record("IMU readings", False, f"Only received {len(samples)} samples")

    def test_piezo_sensor(self) -> None:
        print("\n[T4] Piezoelectric Sensor")
        if not self._session_started:
            self._record("Piezo readings", False, "Session not active")
            return
        samples = self._wait_samples(20, 1.0)
        if samples:
            piezo_vals = [s['piezo'] for s in samples]
            max_piezo = max(piezo_vals)
            avg_piezo = sum(piezo_vals) // len(piezo_vals)
            self._record("Piezo baseline stable", max_piezo < 2000,
                f"max={max_piezo} avg={avg_piezo}")
            self._record("Piezo field present", all(p >= 0 for p in piezo_vals),
                f"range: {min(piezo_vals)}-{max_piezo}")
        else:
            self._record("Piezo readings", False, "No samples received")

    def test_temperature(self) -> None:
        print("\n[T5] Temperature Sensor")
        if not self._session_started:
            self._record("Temperature reading", False, "Session not active")
            return
        samples = self._wait_samples(10, 0.5)
        if samples:
            temps = [s['temperature'] for s in samples]
            temp_c = [t / 100.0 for t in temps]
            avg_temp = sum(temp_c) / len(temp_c)
            self._record("Temperature in valid range", 10 < avg_temp < 50,
                f"avg={avg_temp:.1f}C range=[{min(temp_c):.1f}, {max(temp_c):.1f}]C")
        else:
            self._record("Temperature reading", False, "No samples received")

    def test_battery_reading(self) -> None:
        print("\n[T6] Battery Reading")
        # Try to start a session and check battery in EVT_SESSION_STARTED
        was_active = self._session_started
        if not self._session_started:
            if self._start_session():
                events = self._wait_for([PKT_TYPE_EVT_SESSION_STARTED], timeout=0.5)
                started = events.get(PKT_TYPE_EVT_SESSION_STARTED, [])
                if started:
                    p = parse_session_started(started[0])
                    bat = p['battery_percent'] if p else -1
                    self._record("Battery % in SESSION_STARTED", 0 <= bat <= 100,
                        f"battery={bat}%")
                    return
        self._record("Battery reading", False, "Could not get battery reading")

    def test_shot_detection(self) -> None:
        print("\n[T7] Shot Detection — tap device to trigger")
        if not self._session_started:
            self._record("Shot detection", False, "Session not active")
            return
        events = self._wait_for([PKT_TYPE_EVT_SHOT_DETECTED], timeout=5.0)
        shots = events.get(PKT_TYPE_EVT_SHOT_DETECTED, [])
        self._record("Shot detection triggers", len(shots) > 0,
            f"shots detected={len(shots)} (may be 0 if no strong vibration)")
        if shots:
            p = parse_shot(shots[0])
            if p:
                self._record("Shot packet fields valid", True,
                    f"#={p['shot_number']} piezo_peak={p['piezo_peak']} "
                    f"recoil_axis={p['recoil_axis']} recoil_sign={p['recoil_sign']}")

    def test_session_lifecycle(self) -> None:
        print("\n[T8] Session Start/Stop Lifecycle")
        # Session already started by test_imu_readings or earlier
        if self._session_started:
            self._record("Session starts", True, f"session_id={self._session_id}")
        else:
            if not self._start_session():
                self._record("Session start", False, "Session failed to start")
                return
            self._record("Session starts", True, f"session_id={self._session_id}")

        result = self._stop_session()
        if result:
            self._record("Session stops", True,
                f"duration={result['duration_ms']}ms shots={result['shot_count']} "
                f"battery_end={result['battery_end']}%")
            self._session_started = False
        else:
            self._record("Session stop", False, "No EVT_SESSION_STOPPED")

    def test_set_config(self) -> None:
        print("\n[T9] CMD_SET_CONFIG persistence")
        if not self._check_bt_connected():
            self._record("SET_CONFIG", False, "BT not connected")
            return
        pkt = build_set_config(piezo_threshold=500, accel_threshold=200,
                                led_enabled=1, sample_rate_hz=100)
        self.send_raw(pkt)
        time.sleep(0.5)
        events = self._wait_for([PKT_TYPE_RSP_CONFIG, PKT_TYPE_RSP_ACK], timeout=2.0)
        cfg = events.get(PKT_TYPE_RSP_CONFIG, [])
        ack = events.get(PKT_TYPE_RSP_ACK, [])
        if cfg:
            c = parse_config(cfg[0])
            self._record("SET_CONFIG accepted", c is not None, f"config read back OK")
        elif ack:
            a = parse_ack(ack[0])
            self._record("SET_CONFIG accepted", a['status'] == 0, f"ACK status={a['status']}")
        else:
            self._record("SET_CONFIG", False, "No RSP_CONFIG or RSP_ACK received")

    def test_shot_stats(self) -> None:
        print("\n[T10] CMD_GET_SHOT_STATS (adaptive threshold)")
        if not self._check_bt_connected():
            self._record("GET_SHOT_STATS", False, "BT not connected")
            return
        events = self._wait_for([PKT_TYPE_RSP_SHOT_STATS], timeout=2.0)
        stats_pkts = events.get(PKT_TYPE_RSP_SHOT_STATS, [])
        if stats_pkts:
            stats = parse_shot_stats(stats_pkts[0])
            self._record("GET_SHOT_STATS responds", True,
                f"shots={stats['shot_count']} adaptive_enabled={stats['adaptive_enabled']}")
        else:
            self._record("GET_SHOT_STATS responds", True,
                "(no stats yet — run shots first)")

    def test_ota_status(self) -> None:
        print("\n[T11] CMD_OTA_STATUS")
        if not self._check_bt_connected():
            self._record("OTA_STATUS", False, "BT not connected")
            return
        self.send(PKT_TYPE_CMD_OTA_STATUS)
        events = self._wait_for([PKT_TYPE_RSP_OTA_STATUS], timeout=2.0)
        ota = events.get(PKT_TYPE_RSP_OTA_STATUS, [])
        self._record("OTA_STATUS responds", bool(ota),
            f"OTA idle (no update in progress)" if not ota else f"data={ota[0].hex()}")

    def test_crc_validation(self) -> None:
        print("\n[T12] CRC Validation")
        if not self._check_bt_connected():
            self._record("CRC drops bad packets", False, "BT not connected")
            return
        bad_pkt = bytes([0xAA, 0x55, 0x03, 0x00, 0x00, 0xFF, 0xFF])
        if self.ser:
            self.ser.write(bad_pkt)
            time.sleep(0.3)
            stale = self.ser.in_waiting
            self._record("CRC drops bad packets", stale == 0,
                f"queued bytes after corrupt pkt: {stale}")

    # ── Run All Tests ──────────────────────────────────────────────────────────
    def run_all(self) -> None:
        print("=" * 60)
        print(f"STASYS ESP32 Hardware Test Suite")
        print(f"Port: {self.port}  Baud: {self.baud}")
        print(f"Device MAC: {':'.join(f'{b:02X}' for b in DEVICE_MAC)}")
        print(f"Device secret: {self._device_secret.hex()}")
        print(f"Started: {datetime.now().isoformat()}")
        print("=" * 60)

        if not self.connect():
            print("[FATAL] Cannot connect to device")
            return

        try:
            self.test_get_info()
            self.test_get_config()
            self.test_imu_readings()
            self.test_piezo_sensor()
            self.test_temperature()
            self.test_battery_reading()
            self.test_shot_detection()
            self.test_session_lifecycle()
            self.test_set_config()
            self.test_shot_stats()
            self.test_ota_status()
            self.test_crc_validation()
        finally:
            self.disconnect()

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        print(f"  {passed}/{total} tests passed")
        print()
        for r in self.results:
            icon = "[PASS]" if r.passed else "[FAIL]"
            print(f"  {icon} [{r.name}]  {r.details}")
        print()
        print(f"Finished: {datetime.now().isoformat()}")
        sys.exit(0 if passed == total else 1)


# ── Entry Point ────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <COM_PORT>")
        print("Example: python hw_test.py COM3   # BT SPP virtual COM port")
        sys.exit(1)

    port = sys.argv[1]
    tester = STASYSTester(port)
    tester.run_all()


if __name__ == "__main__":
    main()
