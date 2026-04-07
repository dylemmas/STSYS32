"""STASYS binary protocol parser.

Takes raw bytes from the transport layer and produces typed packet dataclasses.
Frame layout::

    [0xAA][0x55][TYPE][LEN_LO][LEN_HI][PAYLOAD...][CRC_LO][CRC_HI]

CRC-16/CCITT (seed=0xFFFF) covers TYPE + LEN + PAYLOAD.

Debug logging: set PARSER_DEBUG = True (or call set_debug(True)) to log every
parsed packet's type byte, payload length, and CRC validation result.

Error recovery: after MAX_CONSECUTIVE_DISCARDS consecutive garbage bytes,
the parser forces a full state reset to prevent getting stuck in a corrupt
state from a BT SPP batch delivery error or physical disconnects.
"""

from __future__ import annotations

import logging
import struct
import queue
from typing import Callable, Optional, Union

from stasys.protocol.crc import crc16
from stasys.protocol.packets import (
    DataRawSample,
    EvtSensorHealth,
    EvtSessionStarted,
    EvtSessionStopped,
    EvtShotDetected,
    PacketType,
    RawPacket,
    RspAck,
    RspConfig,
    RspError,
    RspInfo,
)

logger = logging.getLogger(__name__)

# Set to True (or call set_debug(True)) to enable per-packet debug logging.
# Logs: type byte (hex), payload length, CRC pass/fail for every parsed packet.
PARSER_DEBUG = True

# After this many consecutive garbage bytes (bytes that don't advance parser
# state), force a full state reset. Prevents the parser getting permanently
# stuck when BT SPP delivers mangled frames or the device restarts mid-stream.
MAX_CONSECUTIVE_DISCARDS = 1024


def set_debug(enabled: bool) -> None:
    """Enable or disable parser debug logging."""
    global PARSER_DEBUG
    PARSER_DEBUG = enabled


_SYNC = b"\xAA\x55"
_HEADER_LEN = 2 + 1 + 2  # sync + type + len
_CRC_LEN = 2
_MAX_PAYLOAD = 256  # sanity cap


# Pre-compiled struct for DATA_RAW_SAMPLE (24 bytes, matches firmware PktRawSample):
#   counter(4)+ts(4)+accel_x(2)+accel_y(2)+accel_z(2)+gyro_x(2)+gyro_y(2)+gyro_z(2)+temp(2)
#   Struct '<IIhhhhhhhH': 2xI(8) + 8xh(16) + 1xH(2) = 24 bytes, 10 values
#   Firmware field order: counter, ts, accel_x/y/z, gyro_x/y/z, gyro_z, temperature
#   vals[7]=gyro_z, vals[9]=temperature (vals[8]=gz padding in this layout)
_STRUCT_RAW_SAMPLE = struct.Struct("<IIhhhhhhhH")

# Pre-compiled struct for RSP_ACK (2 bytes)
_STRUCT_ACK = struct.Struct("<BB")


def _unpack_config(data: bytes) -> RspConfig:
    """Unpack 46-byte configuration payload."""
    if len(data) < 11:
        raise ValueError(f"Config payload too short: {len(data)} bytes")
    d = data[:46]
    return RspConfig(
        sample_rate_hz=d[0],
        piezo_threshold=int.from_bytes(d[1:3], "little"),
        accel_threshold=int.from_bytes(d[3:5], "little"),
        debounce_ms=int.from_bytes(d[5:7], "little"),
        led_enabled=bool(d[7]),
        data_mode=d[8],
        streaming_rate_hz=int.from_bytes(d[9:11], "little"),
        device_name=d[11:31].split(b"\x00")[0].decode("ascii", errors="replace"),
    )


class ProtocolParser:
    """Streaming binary protocol parser for the STASYS ESP32 protocol.

    Scans an internal byte buffer for 0xAA 0x55 sync markers, validates
    CRC-16/CCITT checksums, and emits parsed packets via callback or the
    output queue.

    Error recovery: if MAX_CONSECUTIVE_DISCARDS bytes are consumed without
    advancing the parser state (e.g. BT drops or buffer overflow), the
    parser forces a full reset and clears the buffer to resync.
    """

    def __init__(
        self,
        packet_callback: Optional[Callable[[object], None]] = None,
    ) -> None:
        self._buf: bytearray = bytearray()
        self._callback = packet_callback
        self._packet_queue: queue.Queue[object] = queue.Queue()

        # Error recovery state
        self._consecutive_discards: int = 0

    @property
    def packet_queue(self) -> queue.Queue[object]:
        """Queue of parsed packet objects."""
        return self._packet_queue

    def feed(self, data: bytes) -> None:
        """Feed raw bytes into the parser. Incomplete frames are buffered."""
        self._buf.extend(data)
        self._dispatch()

    def _force_reset(self, reason: str) -> None:
        """Force a full parser state reset. Called after severe corruption."""
        logger.warning("Parser forced reset: %s (buf cleared, %d bytes discarded)", reason, len(self._buf))
        self._buf.clear()
        self._consecutive_discards = 0

    def _dispatch(self) -> None:
        """Main parsing loop — runs until buffer is exhausted or incomplete."""
        while True:
            consumed = self._try_parse_frame()
            if consumed == 0:
                break
            elif consumed < 0:
                pass  # garbage discarded, try again

    def _try_parse_frame(self) -> int:
        """Parse one complete frame from the front of the buffer.

        Returns:
            Positive int: bytes consumed (frame complete).
            0: not enough bytes (wait for more).
            -1: garbage discarded, retry scan.
        """
        # Phase 1: scan for sync
        sync_pos = bytes(self._buf).find(_SYNC)
        if sync_pos < 0:
            # Sync not found. Track consecutive discards for error recovery.
            discarded = len(self._buf)
            self._consecutive_discards += discarded

            if self._consecutive_discards >= MAX_CONSECUTIVE_DISCARDS:
                self._force_reset(f"too many discards ({self._consecutive_discards} bytes, no sync found)")
            elif discarded > 0:
                self._log_hex(f"No sync found, discarding {discarded} bytes", bytes(self._buf[:min(discarded, 16)]))
            self._buf.clear()
            return 0

        if sync_pos > 0:
            self._consecutive_discards += sync_pos
            if self._consecutive_discards >= MAX_CONSECUTIVE_DISCARDS:
                self._force_reset(f"too many discards ({self._consecutive_discards} bytes before sync)")
                return 0
            self._log_hex(f"Discarding {sync_pos} garbage byte(s) before sync", bytes(self._buf[:min(sync_pos, 16)]))
            del self._buf[:sync_pos]
            return -1

        # Sync found — reset discard counter on each confirmed sync
        self._consecutive_discards = 0

        # Phase 2: check minimum header length
        if len(self._buf) < _HEADER_LEN:
            return 0

        # Phase 3: read type and length
        packet_type_byte = self._buf[2]
        payload_len = int.from_bytes(self._buf[3:5], byteorder="little")

        # Phase 4: sanity-check declared length
        if payload_len > _MAX_PAYLOAD:
            self._log_hex(
                f"Length {payload_len} exceeds max {_MAX_PAYLOAD}; discarding header byte",
                bytes(self._buf[:min(1, len(self._buf))]),
            )
            self._consecutive_discards += 1
            if self._consecutive_discards >= MAX_CONSECUTIVE_DISCARDS:
                self._force_reset(f"payload length overflow ({payload_len} > {_MAX_PAYLOAD})")
                return 0
            del self._buf[:1]
            return -1

        # Phase 5: check total frame completeness
        total_len = _HEADER_LEN + payload_len + _CRC_LEN
        if len(self._buf) < total_len:
            return 0

        # Phase 6: extract frame components
        frame_end = _HEADER_LEN + payload_len
        payload = bytes(self._buf[_HEADER_LEN:frame_end])
        received_crc = int.from_bytes(self._buf[frame_end:frame_end + _CRC_LEN], byteorder="little")

        # Phase 7: CRC validation over TYPE + LEN + PAYLOAD
        computed_crc = crc16(bytes(self._buf[2:frame_end]))
        if computed_crc != received_crc:
            self._consecutive_discards += 1
            if self._consecutive_discards >= MAX_CONSECUTIVE_DISCARDS:
                self._force_reset(f"CRC mismatch after {self._consecutive_discards} discards")
                return 0
            self._log_hex(
                f"CRC mismatch: got 0x{received_crc:04X}, computed 0x{computed_crc:04X}; discarding header",
                bytes(self._buf[:min(total_len, 32)]),
            )
            del self._buf[:_HEADER_LEN]
            return -1

        # CRC valid — reset discard counter
        self._consecutive_discards = 0

        # Phase 8: parse typed packet
        try:
            packet_type = PacketType(packet_type_byte)
        except ValueError:
            logger.warning("Unknown packet type 0x%02X, returning raw packet", packet_type_byte)
            packet_type = PacketType(packet_type_byte)  # type: ignore[assignment]

        packet = self._parse_payload(packet_type, payload)
        self._emit(packet)

        # Phase 9: remove frame from buffer
        del self._buf[:total_len]
        return total_len

    def _parse_payload(
        self,
        packet_type: PacketType,
        payload: bytes,
    ) -> Union[
        DataRawSample, EvtSensorHealth, EvtSessionStarted,
        EvtSessionStopped, EvtShotDetected, RspInfo,
        RspConfig, RspAck, RspError, RawPacket,
    ]:
        """Parse the payload bytes into a typed dataclass."""
        try:
            if packet_type == PacketType.EVT_SESSION_STARTED:
                # 14 bytes: session_id(4) + timestamp_us(4) + battery(1) + health(1) + free_heap(4)
                if len(payload) < 14:
                    raise ValueError(f"EVT_SESSION_STARTED: expected 14 bytes, got {len(payload)}")
                d = payload[:14]
                return EvtSessionStarted(
                    session_id=int.from_bytes(d[0:4], "little"),
                    timestamp_us=int.from_bytes(d[4:8], "little"),
                    battery_percent=d[8],
                    sensor_health=d[9],
                    free_heap=int.from_bytes(d[10:14], "little"),
                )

            elif packet_type == PacketType.EVT_SESSION_STOPPED:
                # 12 bytes: session_id(4) + duration_ms(4) + shot_count(2) + battery_end(1) + sensor_health(1)
                # Struct '<IIHBB': I(4) I(4) H(2) B(1) B(1) = 12 bytes, 5 values
                if len(payload) < 12:
                    raise ValueError(f"EVT_SESSION_STOPPED: expected 12 bytes, got {len(payload)}")
                vals = struct.unpack_from("<IIHBB", payload[:12])
                return EvtSessionStopped(
                    session_id=vals[0],
                    duration_ms=vals[1],
                    shot_count=vals[2],
                    battery_end=vals[3],
                    sensor_health=vals[4],
                )

            elif packet_type == PacketType.EVT_SHOT_DETECTED:
                # 29 bytes: session_id(4) + timestamp_us(4) + shot_number(2) + piezo_peak(2)
                #           + accel_peak_xyz(2*3) + gyro_peak_xyz(2*3) + recoil_axis(1) + recoil_sign(1)
                #           + [reserved 5 bytes]
                if len(payload) < 29:
                    raise ValueError(f"EVT_SHOT_DETECTED: expected 29 bytes, got {len(payload)}")
                d = payload[:29]
                return EvtShotDetected(
                    session_id=int.from_bytes(d[0:4], "little"),
                    timestamp_us=int.from_bytes(d[4:8], "little"),
                    shot_number=int.from_bytes(d[8:10], "little", signed=False),
                    piezo_peak=int.from_bytes(d[10:12], "little", signed=False),
                    accel_x_peak=int.from_bytes(d[12:14], "little", signed=True),
                    accel_y_peak=int.from_bytes(d[14:16], "little", signed=True),
                    accel_z_peak=int.from_bytes(d[16:18], "little", signed=True),
                    gyro_x_peak=int.from_bytes(d[18:20], "little", signed=True),
                    gyro_y_peak=int.from_bytes(d[20:22], "little", signed=True),
                    gyro_z_peak=int.from_bytes(d[22:24], "little", signed=True),
                    recoil_axis=d[26] if d[26] < 128 else d[26] - 256,
                    recoil_sign=d[27] if d[27] < 128 else d[27] - 256,
                )

            elif packet_type == PacketType.EVT_SENSOR_HEALTH:
                # 11 bytes: mpu_present(1) + i2c_errors(1) + samples_total(2) + samples_invalid(2)
                #           + i2c_recovery_count(1) + reserved(4)
                # reserved[0] is used as degraded-mode signal (1=degraded, 2=recovery in progress)
                if len(payload) < 11:
                    raise ValueError(f"EVT_SENSOR_HEALTH: expected 8+ bytes, got {len(payload)}")
                d = payload[:11]
                return EvtSensorHealth(
                    mpu_present=d[0],
                    i2c_errors=d[1],
                    samples_total=int.from_bytes(d[2:4], "little"),
                    samples_invalid=int.from_bytes(d[4:6], "little"),
                    i2c_recovery_count=d[6],
                    degraded_flag=d[7] if len(d) > 7 else 0,
                    reserved1=d[8] if len(d) > 8 else 0,
                    reserved2=d[9] if len(d) > 9 else 0,
                    reserved3=d[10] if len(d) > 10 else 0,
                )

            elif packet_type == PacketType.DATA_RAW_SAMPLE:
                # 24 bytes: counter(4)+ts(4)+accel_xyz(6)+gyro_xyz(6)+temp(2)+piezo(2)
                if len(payload) < 24:
                    raise ValueError(f"DATA_RAW_SAMPLE: expected 24 bytes, got {len(payload)}")
                vals = _STRUCT_RAW_SAMPLE.unpack(payload[:24])
                return DataRawSample(
                    sample_counter=vals[0],
                    timestamp_us=vals[1],
                    accel_x=vals[2],
                    accel_y=vals[3],
                    accel_z=vals[4],
                    gyro_x=vals[5],
                    gyro_y=vals[6],
                    gyro_z=vals[7],
                    temperature=vals[9],
                    piezo=0,
                )

            elif packet_type == PacketType.RSP_INFO:
                # 14 bytes packed: firmware_version(4) + hardware_rev(1) +
                #                 build_timestamp(4) + supported_features(2) +
                #                 mpu_whoami(1) + reserved(2)
                # Offsets: 0-3=I, 4=B, 5-8=I, 9-10=H, 11=B, 12-13=B
                if len(payload) < 14:
                    raise ValueError(f"RSP_INFO: expected 14 bytes, got {len(payload)}")
                d = payload[:14]
                return RspInfo(
                    firmware_version=int.from_bytes(d[0:4], "little"),
                    hardware_rev=d[4],
                    build_timestamp=int.from_bytes(d[5:9], "little"),
                    supported_features=int.from_bytes(d[9:11], "little"),
                    mpu_whoami=d[11],
                )

            elif packet_type in (PacketType.RSP_CONFIG, PacketType.CMD_SET_CONFIG):
                return _unpack_config(payload)

            elif packet_type == PacketType.RSP_ACK:
                if len(payload) < 2:
                    raise ValueError(f"RSP_ACK: expected 2 bytes, got {len(payload)}")
                vals = _STRUCT_ACK.unpack(payload[:2])
                return RspAck(command_id=vals[0], status=vals[1])

            elif packet_type == PacketType.RSP_ERROR:
                if len(payload) < 1:
                    raise ValueError(f"RSP_ERROR: empty payload")
                error_code = payload[0]
                msg_bytes = payload[1:33]
                message = msg_bytes.split(b"\x00")[0].decode("ascii", errors="replace")
                return RspError(error_code=error_code, message=message)

            else:
                return RawPacket(packet_type=packet_type, payload=payload)

        except struct.error as e:
            logger.warning("Struct error for type 0x%02X: %s", packet_type, e)
            return RawPacket(packet_type=packet_type, payload=payload)
        except ValueError as e:
            logger.warning("Parse error for type 0x%02X: %s", packet_type, e)
            return RawPacket(packet_type=packet_type, payload=payload)

    def _emit(self, packet: object) -> None:
        """Dispatch a parsed packet via callback or queue."""
        if PARSER_DEBUG:
            pkt_type = getattr(packet, "packet_type", None)
            if pkt_type is None:
                pkt_type = type(packet).__name__
            logger.debug(
                "PARSED  type=0x%02X / %-25s  CRC=PASS",
                int(pkt_type) if isinstance(pkt_type, PacketType) else -1,
                str(pkt_type),
            )
        if self._callback is not None:
            self._callback(packet)
        else:
            self._packet_queue.put(packet)

    def _log_hex(self, msg: str, data: bytes) -> None:
        """Log a hex dump."""
        hex_str = " ".join(f"{b:02X}" for b in data)
        logger.warning("%s: [%s]", msg, hex_str)

    def reset(self) -> None:
        """Manually reset parser state and clear buffer.

        Use this when the Python app knows the ESP32 has restarted or
        the BT link was reset, to ensure the parser starts from a clean state.
        """
        self._buf.clear()
        self._consecutive_discards = 0
        logger.info("Parser state reset by application")