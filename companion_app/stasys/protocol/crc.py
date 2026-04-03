"""CRC-16/CCITT implementation matching the STASYS ESP32 firmware."""

from __future__ import annotations

# CRC-16/CCITT parameters
_POLY = 0x1021
_SEED = 0xFFFF


def crc16(data: bytes) -> int:
    """Compute CRC-16/CCITT over a byte buffer.

    Parameters match the ESP32 firmware:
    - Polynomial: 0x1021
    - Initial seed: 0xFFFF
    - No input or output reflection

    Args:
        data: Bytes to checksum.

    Returns:
        16-bit CRC value (0-65535).
    """
    crc = _SEED
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ _POLY
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return crc
