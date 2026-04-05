"""Tests for the STASYS protocol layer — packets, parser, commands, and flow control."""

from __future__ import annotations

import queue
import struct

import numpy as np
import pytest

from stasys.protocol.commands import cmd_get_config, cmd_get_info, cmd_set_config, cmd_start_session, cmd_stop_session
from stasys.protocol.crc import crc16
from stasys.protocol.flow_control import FlowControl
from stasys.protocol.parser import ProtocolParser
from stasys.protocol.packets import (
    ACCEL_LSB_PER_G,
    GYRO_LSB_PER_DEG,
    DataRawSample,
    EvtSensorHealth,
    EvtSessionStarted,
    EvtSessionStopped,
    EvtShotDetected,
    PacketType,
    RspAck,
    RspConfig,
    RspError,
    RspInfo,
    raw_to_accel_ms2,
    raw_to_gyro_dps,
    raw_to_temp_c,
)


# =============================================================================
# CRC
# =============================================================================

class TestCRC16:
    def test_empty_returns_seed_complement(self) -> None:
        assert crc16(b"") == 0xFFFF

    def test_known_vector(self) -> None:
        # CRC of b"123456789" is the canonical reference for CRC-16/CCITT with seed=0xFFFF
        assert crc16(b"123456789") == 0x29B1

    def test_deterministic(self) -> None:
        data = b"\x03\x04\x00"
        assert crc16(data) == crc16(data)

    def test_different_data_different_crc(self) -> None:
        assert crc16(b"AAAA") != crc16(b"BBBB")


# =============================================================================
# Unit Conversions
# =============================================================================

class TestConversions:
    def test_accel_raw_to_ms2(self) -> None:
        # At 4G range, 8192 LSB = 1G = 9.81 m/s²
        assert raw_to_accel_ms2(8192) == pytest.approx(9.81, rel=1e-3)
        assert raw_to_accel_ms2(0) == 0.0
        assert raw_to_accel_ms2(-8192) == pytest.approx(-9.81, rel=1e-3)

    def test_gyro_raw_to_dps(self) -> None:
        # At 500 dps range, 65.5 LSB = 1 deg/s
        assert raw_to_gyro_dps(655) == pytest.approx(10.0, rel=1e-3)
        assert raw_to_gyro_dps(0) == 0.0
        assert raw_to_gyro_dps(-655) == pytest.approx(-10.0, rel=1e-3)

    def test_temp_raw_to_c(self) -> None:
        # MPU6050: raw / 340.0 + 36.53
        assert raw_to_temp_c(0) == pytest.approx(36.53, rel=1e-2)
        assert raw_to_temp_c(340) == pytest.approx(37.53, rel=1e-2)


# =============================================================================
# Packet Properties
# =============================================================================

class TestPacketProperties:
    def test_data_raw_sample_converts(self) -> None:
        pkt = DataRawSample(
            sample_counter=1, timestamp_us=1000,
            accel_x=8192, accel_y=0, accel_z=0,
            gyro_x=655, gyro_y=0, gyro_z=0,
            piezo=500, temperature=340,
        )
        assert pkt.accel_x_ms2 == pytest.approx(9.81, rel=1e-3)
        assert pkt.gyro_x_dps == pytest.approx(10.0, rel=1e-3)
        assert pkt.temperature_c == pytest.approx(37.53, rel=1e-2)

    def test_data_raw_sample_accel_magnitude(self) -> None:
        # 1G on each axis = sqrt(3) * 9.81
        pkt = DataRawSample(
            sample_counter=0, timestamp_us=0,
            accel_x=8192, accel_y=8192, accel_z=8192,
            gyro_x=0, gyro_y=0, gyro_z=0, piezo=0, temperature=0,
        )
        mag = pkt.accel_magnitude_ms2
        assert mag == pytest.approx(16.992, rel=1e-3)

    def test_rsp_info_version_str(self) -> None:
        pkt = RspInfo(firmware_version=0x010203, hardware_rev=1, build_timestamp=0, supported_features=0, mpu_whoami=0x68)
        assert pkt.firmware_version_str == "v1.2.3"

    def test_rsp_info_mpu_ok(self) -> None:
        assert RspInfo(0, 0, 0, 0, 0x68).mpu_ok is True   # MPU6050
        assert RspInfo(0, 0, 0, 0, 0x70).mpu_ok is True   # MPU6500
        assert RspInfo(0, 0, 0, 0, 0x00).mpu_ok is False

    def test_rsp_ack_success(self) -> None:
        assert RspAck(command_id=0x01, status=0).is_success is True
        assert RspAck(command_id=0x01, status=1).is_success is False

    def test_evt_shot_detected_axis_name(self) -> None:
        pkt = EvtShotDetected(
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1,  # recoil_axis=1 (Y), recoil_sign=1
        )
        assert pkt.recoil_axis_name == "Y"


# =============================================================================
# Protocol Parser — Frame Building
# =============================================================================

def _make_frame(packet_type: int, payload: bytes) -> bytes:
    """Build a valid protocol frame."""
    header = bytes([packet_type]) + struct.pack("<H", len(payload))
    crc = crc16(header + payload)
    return b"\xAA\x55" + header + payload + struct.pack("<H", crc)


# =============================================================================
# Parser: DATA_RAW_SAMPLE
# =============================================================================

class TestParseDataRawSample:
    def test_parse_sample(self) -> None:
        parser = ProtocolParser()
        # 24 bytes: counter(4)+ts(4)+accel_xyz(6)+gyro_xyz(6)+gyro_z(2)+temp(2)
        # Format '<IIhhhhhhhh': 2xI(8) + 8xh(16) = 24 bytes, 10 values
        # vals[7]=gyro_z, vals[9]=temperature (piezo not in DATA_RAW_SAMPLE)
        payload = struct.pack("<IIhhhhhhhh",
                              42, 1_000_000,  # counter, timestamp
                              8192, 0, 0,     # accel_x/y/z
                              0, 0, -655,      # gyro_x/y/z
                              567,             # vals[8]: gyro_z reads vals[7]=-655 (correct)
                              1234)            # vals[9]: temperature
        frame = _make_frame(PacketType.DATA_RAW_SAMPLE, payload)

        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()

        assert isinstance(pkt, DataRawSample)
        assert pkt.sample_counter == 42
        assert pkt.timestamp_us == 1_000_000
        assert pkt.accel_x == 8192
        assert pkt.accel_x_ms2 == pytest.approx(9.81, rel=1e-3)
        assert pkt.gyro_z == -655
        assert pkt.temperature == 1234
        assert pkt.temperature_c == pytest.approx(1234 / 340.0 + 36.53, rel=1e-2)
        # piezo is hardcoded to 0 in DATA_RAW_SAMPLE (not in firmware payload)
        assert pkt.piezo == 0


# =============================================================================
# Parser: EVT_SESSION_STARTED
# =============================================================================

class TestParseEvtSessionStarted:
    def test_parse_session_started(self) -> None:
        parser = ProtocolParser()
        # 15 bytes: session_id(4) + ts_us(4) + battery(1) + health(1) + free_heap(4) + [pad](1)
        payload = bytearray(15)
        struct.pack_into("<II", payload, 0, 12345, 5_000_000)
        payload[8] = 85         # battery
        payload[9] = 0x03        # health flags
        struct.pack_into("<I", payload, 10, 150000)  # free_heap at offset 10

        frame = _make_frame(PacketType.EVT_SESSION_STARTED, bytes(payload))
        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()

        assert isinstance(pkt, EvtSessionStarted)
        assert pkt.session_id == 12345
        assert pkt.timestamp_us == 5_000_000
        assert pkt.battery_percent == 85
        assert pkt.sensor_health == 0x03
        assert pkt.free_heap == 150000


# =============================================================================
# Parser: EVT_SESSION_STOPPED
# =============================================================================

class TestParseEvtSessionStopped:
    def test_parse_session_stopped(self) -> None:
        parser = ProtocolParser()
        # 14 bytes: session_id(4) + duration(4) + shot_count(2) + battery_end(1) + health(1) + [pad 2]
        payload = struct.pack("<IIHBBxx", 999, 60_000, 10, 82, 0x01)
        frame = _make_frame(PacketType.EVT_SESSION_STOPPED, payload)

        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()

        assert isinstance(pkt, EvtSessionStopped)
        assert pkt.session_id == 999
        assert pkt.duration_ms == 60_000
        assert pkt.shot_count == 10
        assert pkt.battery_end == 82
        assert pkt.sensor_health == 0x01


# =============================================================================
# Parser: EVT_SHOT_DETECTED
# =============================================================================

class TestParseEvtShotDetected:
    def test_parse_shot_detected(self) -> None:
        parser = ProtocolParser()
        # 29 bytes: session_id(4) + timestamp_us(4) + shot_number(2) + piezo_peak(2)
        #           + accel_peak_xyz(6) + gyro_peak_xyz(6) + recoil_axis(1) + recoil_sign(1)
        #           + [reserved 5]
        d = bytearray(29)
        struct.pack_into("<I", d, 0, 1)          # session_id
        struct.pack_into("<I", d, 4, 2_000_000)   # timestamp_us
        struct.pack_into("<H", d, 8, 3)           # shot_number
        struct.pack_into("<H", d, 10, 2048)       # piezo_peak
        struct.pack_into("<h", d, 12, 5000)       # accel_x_peak
        struct.pack_into("<h", d, 14, -3000)      # accel_y_peak
        struct.pack_into("<h", d, 16, 1000)       # accel_z_peak
        struct.pack_into("<h", d, 18, 200)        # gyro_x_peak
        struct.pack_into("<h", d, 20, -100)       # gyro_y_peak
        struct.pack_into("<h", d, 22, 50)         # gyro_z_peak
        d[26] = 0                                  # recoil_axis (X)
        d[27] = 1                                  # recoil_sign (+1)

        frame = _make_frame(PacketType.EVT_SHOT_DETECTED, bytes(d))
        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()

        assert isinstance(pkt, EvtShotDetected)
        assert pkt.session_id == 1
        assert pkt.timestamp_us == 2_000_000
        assert pkt.shot_number == 3
        assert pkt.piezo_peak == 2048
        assert pkt.accel_x_peak == 5000
        assert pkt.recoil_axis == 0
        assert pkt.recoil_sign == 1
        assert pkt.recoil_axis_name == "X"


# =============================================================================
# Parser: EVT_SENSOR_HEALTH
# =============================================================================

class TestParseEvtSensorHealth:
    def test_parse_sensor_health(self) -> None:
        parser = ProtocolParser()
        # 11 bytes: mpu_present(1) + i2c_errors(1) + samples_total(2) + samples_invalid(2)
        #           + i2c_recovery_count(1) + reserved(4)
        d = bytearray(11)
        d[0] = 1                      # mpu_present
        d[1] = 3                      # i2c_errors
        struct.pack_into("<HH", d, 2, 1000, 5)  # samples_total=1000, samples_invalid=5
        d[6] = 2                      # i2c_recovery_count
        # d[7:11] = 0                 # reserved

        frame = _make_frame(PacketType.EVT_SENSOR_HEALTH, bytes(d))
        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()

        assert isinstance(pkt, EvtSensorHealth)
        assert pkt.mpu_present == 1
        assert pkt.i2c_errors == 3
        assert pkt.samples_total == 1000
        assert pkt.samples_invalid == 5
        assert pkt.i2c_recovery_count == 2


# =============================================================================
# Parser: RSP_INFO
# =============================================================================

class TestParseRspInfo:
    def test_parse_rsp_info(self) -> None:
        parser = ProtocolParser()
        # 14 bytes packed: firmware_version(4) + hardware_rev(1) +
        #                 build_timestamp(4) + supported_features(2) +
        #                 mpu_whoami(1) + reserved(2)
        # Offsets: 0-3=I, 4=B, 5-8=I, 9-10=H, 11=B, 12-13=B
        d = bytearray(14)
        struct.pack_into("<I", d, 0, 0x010000)
        d[4] = 2
        struct.pack_into("<I", d, 5, 0x65534200)
        struct.pack_into("<H", d, 9, 0x000F)
        d[11] = 0x68
        payload = bytes(d)
        frame = _make_frame(PacketType.RSP_INFO, payload)

        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()

        assert isinstance(pkt, RspInfo)
        assert pkt.firmware_version == 0x010000
        assert pkt.firmware_version_str == "v1.0.0"
        assert pkt.hardware_rev == 2
        assert pkt.build_timestamp == 0x65534200
        assert pkt.supported_features == 0x000F
        assert pkt.mpu_whoami == 0x68
        assert pkt.mpu_ok is True


# =============================================================================
# Parser: RSP_ACK
# =============================================================================

class TestParseRspAck:
    def test_parse_ack_success(self) -> None:
        parser = ProtocolParser()
        payload = struct.pack("<BB", 0x01, 0)  # CMD_START_SESSION, success
        frame = _make_frame(PacketType.RSP_ACK, payload)

        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()

        assert isinstance(pkt, RspAck)
        assert pkt.command_id == 0x01
        assert pkt.status == 0
        assert pkt.is_success is True

    def test_parse_ack_error(self) -> None:
        parser = ProtocolParser()
        payload = struct.pack("<BB", 0x05, 1)  # CMD_SET_CONFIG, error
        frame = _make_frame(PacketType.RSP_ACK, payload)

        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()

        assert isinstance(pkt, RspAck)
        assert pkt.is_success is False


# =============================================================================
# Parser: RSP_ERROR
# =============================================================================

class TestParseRspError:
    def test_parse_rsp_error(self) -> None:
        parser = ProtocolParser()
        payload = bytearray(33)
        payload[0] = 0x04
        msg = b"SENSOR_NOT_READY\x00"
        payload[1:1 + len(msg)] = msg

        frame = _make_frame(PacketType.RSP_ERROR, bytes(payload))
        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()

        assert isinstance(pkt, RspError)
        assert pkt.error_code == 0x04
        assert pkt.message == "SENSOR_NOT_READY"


# =============================================================================
# Parser: RSP_CONFIG
# =============================================================================

class TestParseRspConfig:
    def test_parse_default_config(self) -> None:
        parser = ProtocolParser()
        cfg = RspConfig.default()
        # Encode config payload manually — 46 bytes matching CMD_SET_CONFIG layout
        # 0: sample_rate_hz (1), 1-2: piezo_threshold (2), 3-4: accel_threshold (2),
        # 5-6: debounce_ms (2), 7: led_enabled (1), 8: data_mode (1),
        # 9-10: streaming_rate_hz (2), 11-30: device_name (20), 31-45: reserved (15)
        payload = bytearray(46)
        payload[0] = cfg.sample_rate_hz
        payload[1:3] = struct.pack("<H", cfg.piezo_threshold)
        payload[3:5] = struct.pack("<H", cfg.accel_threshold)
        payload[5:7] = struct.pack("<H", cfg.debounce_ms)
        payload[7] = 1 if cfg.led_enabled else 0
        payload[8] = cfg.data_mode
        payload[9:11] = struct.pack("<H", cfg.streaming_rate_hz)
        name_bytes = cfg.device_name.encode("ascii")
        payload[11:11 + len(name_bytes)] = name_bytes

        frame = _make_frame(PacketType.RSP_CONFIG, bytes(payload))
        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()

        assert isinstance(pkt, RspConfig)
        assert pkt.sample_rate_hz == 100
        assert pkt.piezo_threshold == 800
        assert pkt.accel_threshold == 300
        assert pkt.led_enabled is True
        assert pkt.device_name == "STASYS"


# =============================================================================
# Parser: CRC / Resync
# =============================================================================

class TestParserResync:
    def test_discard_bad_crc(self) -> None:
        parser = ProtocolParser()
        payload = b"\x00" * 26
        frame = bytearray(_make_frame(PacketType.DATA_RAW_SAMPLE, payload))
        frame[-3] ^= 0xFF  # corrupt CRC

        parser.feed(bytes(frame))
        assert parser.packet_queue.empty()

    def test_discard_leading_garbage(self) -> None:
        parser = ProtocolParser()
        # 26-byte payload: counter(4)+ts(4)+accel_xyz(6)+gyro_xyz(6)+temp(2)+piezo(2)
        # Format '<IIhhhhhhhHh': 11 values
        payload = struct.pack("<IIhhhhhhhHh", 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)  # 11 values: 2I+7h+H+h
        frame = _make_frame(PacketType.DATA_RAW_SAMPLE, payload)
        garbage = b"\x00\x01\x02\xFF\xEE"
        combined = garbage + frame

        parser.feed(combined)
        pkt = parser.packet_queue.get_nowait()
        assert isinstance(pkt, DataRawSample)
        assert pkt.sample_counter == 1

    def test_split_packet(self) -> None:
        parser = ProtocolParser()
        payload = struct.pack("<IIhhhhhhhHh", 7, 0, 0, 0, 0, 0, 0, 0, 100, 0, 500)  # ax=0,ay=0,az=0,gx=0,gy=0,gz=0,temp=100,piezo=500
        frame = _make_frame(PacketType.DATA_RAW_SAMPLE, payload)
        split = len(frame) // 2

        parser.feed(frame[:split])
        assert parser.packet_queue.empty()

        parser.feed(frame[split:])
        pkt = parser.packet_queue.get_nowait()
        assert isinstance(pkt, DataRawSample)
        assert pkt.sample_counter == 7

    def test_callback_mode(self) -> None:
        received = []
        parser = ProtocolParser(packet_callback=received.append)
        payload = struct.pack("<IIhhhhhhhHh", 99, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)  # 11 values: 2I+7h+H+h
        frame = _make_frame(PacketType.DATA_RAW_SAMPLE, payload)

        parser.feed(frame)
        assert len(received) == 1
        assert isinstance(received[0], DataRawSample)
        assert parser.packet_queue.empty()

    def test_empty_feed_is_safe(self) -> None:
        parser = ProtocolParser()
        parser.feed(b"")
        assert parser.packet_queue.empty()


# =============================================================================
# Commands
# =============================================================================

class TestCommands:
    def test_cmd_start_session(self) -> None:
        data = cmd_start_session()
        assert data[:2] == b"\xAA\x55"
        assert data[2] == 0x01
        length = struct.unpack_from("<H", data, 3)[0]
        assert length == 0

    def test_cmd_stop_session(self) -> None:
        data = cmd_stop_session()
        assert data[2] == 0x02
        length = struct.unpack_from("<H", data, 3)[0]
        assert length == 0

    def test_cmd_get_info(self) -> None:
        data = cmd_get_info()
        assert data[2] == 0x03
        length = struct.unpack_from("<H", data, 3)[0]
        assert length == 0

    def test_cmd_get_config(self) -> None:
        data = cmd_get_config()
        assert data[2] == 0x04
        length = struct.unpack_from("<H", data, 3)[0]
        assert length == 0

    def test_cmd_set_config(self) -> None:
        cfg = RspConfig.default()
        data = cmd_set_config(cfg)
        assert data[2] == 0x05
        length = struct.unpack_from("<H", data, 3)[0]
        assert length == 46

    def test_command_crc_validates(self) -> None:
        # Verify the encoded frame can be re-parsed
        data = cmd_start_session()
        parser = ProtocolParser()
        parser.feed(data)
        pkt = parser.packet_queue.get_nowait()
        # CMD_START_SESSION has no response type, parser returns RawPacket
        assert pkt is not None


# =============================================================================
# Flow Control
# =============================================================================

class TestFlowControl:
    def test_initially_not_paused(self) -> None:
        writes = []
        fc = FlowControl(write_callback=writes.append)
        assert fc.is_paused is False

    def test_xoff_sets_paused(self) -> None:
        writes = []
        fc = FlowControl(write_callback=lambda d: writes.append(d) or len(d))
        fc.handle_xoff()
        assert fc.is_paused is True

    def test_xon_clears_paused(self) -> None:
        writes = []
        fc = FlowControl(write_callback=lambda d: writes.append(d) or len(d))
        fc.handle_xoff()
        fc.handle_xon()
        assert fc.is_paused is False

    def test_write_while_not_paused(self) -> None:
        writes = []
        fc = FlowControl(write_callback=lambda d: writes.append(d) or len(d))
        fc.write(b"\xAA\x55\x01\x00\x00\xAC\xFB")
        assert len(writes) == 1
        assert writes[0] == b"\xAA\x55\x01\x00\x00\xAC\xFB"

    def test_write_buffered_while_paused(self) -> None:
        writes = []
        fc = FlowControl(write_callback=lambda d: writes.append(d) or len(d))
        fc.handle_xoff()

        fc.write(b"\xAA\x55\x02\x00\x00\xAC\xFB")  # CMD_STOP
        assert len(writes) == 0  # buffered, not written

        fc.handle_xon()
        assert len(writes) == 1  # now flushed
        assert writes[0] == b"\xAA\x55\x02\x00\x00\xAC\xFB"

    def test_multiple_writes_buffered(self) -> None:
        writes = []
        fc = FlowControl(write_callback=lambda d: writes.append(d) or len(d))
        fc.handle_xoff()

        fc.write(b"packet1")
        fc.write(b"packet2")
        assert len(writes) == 0

        fc.handle_xon()
        assert len(writes) == 2
        assert writes[0] == b"packet1"
        assert writes[1] == b"packet2"
