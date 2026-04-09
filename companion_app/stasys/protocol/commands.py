"""Command encoder for outgoing Python -> ESP32 packets.

Encodes commands with proper sync bytes + CRC framing matching the firmware protocol.
"""

from __future__ import annotations

import struct

from stasys.protocol.crc import crc16
from stasys.protocol.packets import PacketType, RspConfig


# Sync bytes
SYNC = b"\xAA\x55"


def _encode(packet_type: int, payload: bytes = b"") -> bytes:
    """Encode a packet with sync bytes and CRC.

    Frame: [0xAA][0x55][TYPE][LEN_LO][LEN_HI][PAYLOAD...][CRC_LO][CRC_HI]
    CRC covers TYPE + LEN + PAYLOAD.
    """
    length = len(payload)
    header = bytes([packet_type]) + struct.pack("<H", length)
    crc = crc16(header + payload)
    return SYNC + header + payload + struct.pack("<H", crc)


# =============================================================================
# Command Builders
# =============================================================================

def cmd_start_session() -> bytes:
    """Encode CMD_START_SESSION (empty payload)."""
    return _encode(int(PacketType.CMD_START_SESSION))


def cmd_stop_session() -> bytes:
    """Encode CMD_STOP_SESSION (empty payload)."""
    return _encode(int(PacketType.CMD_STOP_SESSION))


def cmd_get_info() -> bytes:
    """Encode CMD_GET_INFO (empty payload)."""
    return _encode(int(PacketType.CMD_GET_INFO))


def cmd_get_config() -> bytes:
    """Encode CMD_GET_CONFIG (empty payload)."""
    return _encode(int(PacketType.CMD_GET_CONFIG))


def cmd_set_config(config: RspConfig) -> bytes:
    """Encode CMD_SET_CONFIG with configuration payload.

    Layout (50 bytes, matches firmware PktConfig):
      0: sample_rate_hz (1)
      1-2: piezo_threshold (2, LE)
      3-4: accel_threshold (2, LE)
      5-6: debounce_ms (2, LE)
      7: led_enabled (1)
      8: data_mode (1)
      9-10: streaming_rate_hz (2, LE)
      11-30: device_name (20)
      31-49: reserved (19)
    """
    payload = bytearray(50)
    payload[0] = config.sample_rate_hz
    payload[1:3] = struct.pack("<H", config.piezo_threshold)
    payload[3:5] = struct.pack("<H", config.accel_threshold)
    payload[5:7] = struct.pack("<H", config.debounce_ms)
    payload[7] = 1 if config.led_enabled else 0
    payload[8] = config.data_mode
    payload[9:11] = struct.pack("<H", config.streaming_rate_hz)
    name_bytes = config.device_name.encode("ascii")[:20]
    payload[11:11 + len(name_bytes)] = name_bytes
    # reserved bytes remain 0
    return _encode(int(PacketType.CMD_SET_CONFIG), bytes(payload))


def cmd_set_mount_mode(mode: int) -> bytes:
    """Encode CMD_SET_MOUNT_MODE with mount orientation mode (0-6).

    Mount modes:
      0: Standard (device upright, Z up)
      1: Rotated 90° clockwise (around Z)
      2: Inverted 180° (around Z)
      3: Rotated 270° (around Z)
      4: Barrel-under (device on side, Z along barrel, Y up)
      5: Barrel-under inverted (device on side, Z along barrel, upside-down)
      6: Side mount (device on side, X along barrel)
    """
    return _encode(int(PacketType.CMD_SET_MOUNT_MODE), bytes([mode & 0x07]))
