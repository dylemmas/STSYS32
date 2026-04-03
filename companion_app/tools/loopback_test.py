"""Loopback test: simulates an ESP32 sending DATA_RAW_SAMPLE and EVT_SHOT_DETECTED
packets over a mock serial stream, then verifies the Python parser decodes both
correctly with valid CRC.

This tests the full end-to-end packet flow:
    ESP32 encodePacket() → serial stream → Python ProtocolParser.feed() → decoded packet

Run:
    python tools/loopback_test.py
"""

from __future__ import annotations

import struct
import sys
import os
from io import BytesIO

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stasys.protocol.crc import crc16
from stasys.protocol.parser import ProtocolParser
from stasys.protocol.packets import (
    DataRawSample,
    EvtShotDetected,
    PacketType,
)


def make_frame(packet_type: int, payload: bytes) -> bytes:
    """Build a protocol frame exactly like the ESP32 firmware does.

    Frame layout:
        [0xAA][0x55][TYPE][LEN_LO][LEN_HI][PAYLOAD...][CRC_LO][CRC_HI]

    CRC-16/CCITT (seed=0xFFFF) covers TYPE + LEN + PAYLOAD.
    Matches encodePacket() in src/protocol.cpp.
    """
    header = bytes([packet_type]) + struct.pack("<H", len(payload))
    crc = crc16(header + payload)
    return b"\xAA\x55" + header + payload + struct.pack("<H", crc)


def make_data_raw_sample(
    sample_counter: int,
    timestamp_us: int,
    accel_x: int,
    accel_y: int,
    accel_z: int,
    gyro_x: int,
    gyro_y: int,
    gyro_z: int,
    temperature: int,
    piezo: int,
) -> bytes:
    """Build a DATA_RAW_SAMPLE packet matching the firmware PktRawSample struct.

    Firmware struct (24 bytes, #pragma pack(1)):
        counter(4) + ts(4) + ax(2) + ay(2) + az(2) + gx(2) + gy(2) + gz(2)
        + temp(2) + piezo(2)

    Layout: counter, timestamp, ax, ay, az, gx, gy, gz, temp, piezo
    Format: '<IIhhhhhhHh' (10 values, 24 bytes)
    """
    payload = struct.pack(
        "<IIhhhhhhHh",
        sample_counter,
        timestamp_us,
        accel_x,
        accel_y,
        accel_z,
        gyro_x,
        gyro_y,
        gyro_z,
        temperature,
        piezo,
    )
    return make_frame(int(PacketType.DATA_RAW_SAMPLE), payload)


def make_evt_shot_detected(
    session_id: int,
    timestamp_us: int,
    shot_number: int,
    piezo_peak: int,
    accel_x_peak: int,
    accel_y_peak: int,
    accel_z_peak: int,
    gyro_x_peak: int,
    gyro_y_peak: int,
    gyro_z_peak: int,
    recoil_axis: int,
    recoil_sign: int,
) -> bytes:
    """Build an EVT_SHOT_DETECTED packet matching the firmware PktShotDetected struct.

    29 bytes: session_id(4) + ts(4) + shot_number(2) + piezo_peak(2)
              + accel_peak_xyz(6) + gyro_peak_xyz(6) + recoil_axis(1)
              + recoil_sign(1) + reserved(3)
    """
    payload = bytearray(29)
    struct.pack_into("<I", payload, 0, session_id)
    struct.pack_into("<I", payload, 4, timestamp_us)
    struct.pack_into("<H", payload, 8, shot_number)
    struct.pack_into("<H", payload, 10, piezo_peak)
    struct.pack_into("<h", payload, 12, accel_x_peak)
    struct.pack_into("<h", payload, 14, accel_y_peak)
    struct.pack_into("<h", payload, 16, accel_z_peak)
    struct.pack_into("<h", payload, 18, gyro_x_peak)
    struct.pack_into("<h", payload, 20, gyro_y_peak)
    struct.pack_into("<h", payload, 22, gyro_z_peak)
    payload[26] = recoil_axis
    payload[27] = recoil_sign & 0xFF  # int8 → byte
    return make_frame(int(PacketType.EVT_SHOT_DETECTED), bytes(payload))


class MockSerialStream:
    """Simulates a Bluetooth SPP serial port that delivers bytes to the parser.

    Bytes arrive in chunks (simulating partial reads) and are fed to the parser
    one byte at a time, exactly as the SerialTransport read thread would.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self._parser = ProtocolParser()

    def read_chunks(self, chunk_size: int = 1) -> list[bytes]:
        """Simulate serial read returning chunks of up to chunk_size bytes."""
        chunks = []
        while self._pos < len(self._data):
            chunk = bytes(self._data[self._pos : self._pos + chunk_size])
            self._parser.feed(chunk)
            chunks.append(chunk)
            self._pos += chunk_size
        return chunks

    @property
    def parsed_packets(self) -> list[object]:
        """Return all packets that were successfully parsed."""
        packets = []
        while not self._parser.packet_queue.empty():
            packets.append(self._parser.packet_queue.get_nowait())
        return packets


def test_data_raw_sample_loopback() -> bool:
    """Test: encode DATA_RAW_SAMPLE on ESP32, decode in Python."""
    print("\n=== Test: DATA_RAW_SAMPLE loopback ===")

    # Simulate ESP32 sending a sample with known values
    frame = make_data_raw_sample(
        sample_counter=12345,
        timestamp_us=1_000_000,
        accel_x=8192,   # ~1G
        accel_y=-4096,  # ~-0.5G
        accel_z=0,
        gyro_x=655,     # ~10 deg/s
        gyro_y=0,
        gyro_z=-655,    # ~-10 deg/s
        temperature=340,  # ~37.53 C
        piezo=2048,
    )

    print(f"  Frame length: {len(frame)} bytes (hex: {frame.hex()})")

    # Simulate serial read in small chunks
    stream = MockSerialStream(frame)
    chunks = stream.read_chunks(chunk_size=1)  # 1 byte at a time = worst case
    print(f"  Serial chunks: {len(chunks)} (1-byte reads)")

    packets = stream.parsed_packets
    print(f"  Parsed packets: {len(packets)}")

    if len(packets) != 1:
        print(f"  FAIL: expected 1 packet, got {len(packets)}")
        return False

    pkt = packets[0]
    if not isinstance(pkt, DataRawSample):
        print(f"  FAIL: expected DataRawSample, got {type(pkt).__name__}")
        return False

    # Verify all fields
    errors = []
    checks = [
        ("sample_counter", pkt.sample_counter, 12345),
        ("timestamp_us", pkt.timestamp_us, 1_000_000),
        ("accel_x", pkt.accel_x, 8192),
        ("accel_y", pkt.accel_y, -4096),
        ("accel_z", pkt.accel_z, 0),
        ("gyro_x", pkt.gyro_x, 655),
        ("gyro_y", pkt.gyro_y, 0),
        ("gyro_z", pkt.gyro_z, -655),
        ("temperature", pkt.temperature, 340),
        ("piezo", pkt.piezo, 2048),
    ]
    for name, actual, expected in checks:
        if actual != expected:
            errors.append(f"    {name}: expected {expected}, got {actual}")

    # Check converted values
    import math

    ax_ms2 = pkt.accel_x_ms2
    expected_ax_ms2 = 8192 / 8192.0 * 9.81
    if not math.isclose(ax_ms2, expected_ax_ms2, rel_tol=1e-3):
        errors.append(f"    accel_x_ms2: expected {expected_ax_ms2:.4f}, got {ax_ms2:.4f}")

    temp_c = pkt.temperature_c
    expected_temp_c = 340 / 340.0 + 36.53
    if not math.isclose(temp_c, expected_temp_c, rel_tol=1e-2):
        errors.append(f"    temperature_c: expected {expected_temp_c:.2f}, got {temp_c:.2f}")

    if errors:
        print("  FAIL: field mismatches:")
        for e in errors:
            print(e)
        return False

    print(f"  PASS: sample_counter={pkt.sample_counter}, accel_x={pkt.accel_x}, "
          f"gyro_z={pkt.gyro_z}, temp={pkt.temperature}, piezo={pkt.piezo}")
    return True


def test_evt_shot_detected_loopback() -> bool:
    """Test: encode EVT_SHOT_DETECTED on ESP32, decode in Python."""
    print("\n=== Test: EVT_SHOT_DETECTED loopback ===")

    frame = make_evt_shot_detected(
        session_id=42,
        timestamp_us=5_000_000,
        shot_number=7,
        piezo_peak=3500,
        accel_x_peak=6000,
        accel_y_peak=-2000,
        accel_z_peak=1000,
        gyro_x_peak=300,
        gyro_y_peak=-150,
        gyro_z_peak=50,
        recoil_axis=0,   # X axis
        recoil_sign=1,   # positive direction
    )

    print(f"  Frame length: {len(frame)} bytes (hex: {frame.hex()})")

    # Simulate serial read in 8-byte chunks (typical MTU for BT SPP)
    stream = MockSerialStream(frame)
    chunks = stream.read_chunks(chunk_size=8)
    print(f"  Serial chunks: {len(chunks)} (8-byte reads)")

    packets = stream.parsed_packets
    print(f"  Parsed packets: {len(packets)}")

    if len(packets) != 1:
        print(f"  FAIL: expected 1 packet, got {len(packets)}")
        return False

    pkt = packets[0]
    if not isinstance(pkt, EvtShotDetected):
        print(f"  FAIL: expected EvtShotDetected, got {type(pkt).__name__}")
        return False

    errors = []
    checks = [
        ("session_id", pkt.session_id, 42),
        ("timestamp_us", pkt.timestamp_us, 5_000_000),
        ("shot_number", pkt.shot_number, 7),
        ("piezo_peak", pkt.piezo_peak, 3500),
        ("accel_x_peak", pkt.accel_x_peak, 6000),
        ("accel_y_peak", pkt.accel_y_peak, -2000),
        ("accel_z_peak", pkt.accel_z_peak, 1000),
        ("gyro_x_peak", pkt.gyro_x_peak, 300),
        ("gyro_y_peak", pkt.gyro_y_peak, -150),
        ("gyro_z_peak", pkt.gyro_z_peak, 50),
        ("recoil_axis", pkt.recoil_axis, 0),
        ("recoil_sign", pkt.recoil_sign, 1),
    ]
    for name, actual, expected in checks:
        if actual != expected:
            errors.append(f"    {name}: expected {expected}, got {actual}")

    if pkt.recoil_axis_name != "X":
        errors.append(f"    recoil_axis_name: expected 'X', got '{pkt.recoil_axis_name}'")

    if errors:
        print("  FAIL: field mismatches:")
        for e in errors:
            print(e)
        return False

    print(f"  PASS: session_id={pkt.session_id}, shot={pkt.shot_number}, "
          f"piezo={pkt.piezo_peak}, recoil={pkt.recoil_axis_name}{'+' if pkt.recoil_sign > 0 else '-'} ({pkt.recoil_sign})")
    return True


def test_crc_validation() -> bool:
    """Test: frames with corrupted CRC are silently dropped by the parser."""
    print("\n=== Test: CRC validation ===")

    # Build a valid frame
    frame = make_data_raw_sample(
        sample_counter=1, timestamp_us=1,
        accel_x=0, accel_y=0, accel_z=0,
        gyro_x=0, gyro_y=0, gyro_z=0,
        temperature=0, piezo=0,
    )

    # Corrupt the CRC (flip last 2 bytes)
    corrupted = bytearray(frame)
    corrupted[-2] ^= 0xFF
    corrupted[-1] ^= 0xFF

    stream = MockSerialStream(bytes(corrupted))
    stream.read_chunks(chunk_size=1)
    packets = stream.parsed_packets

    if len(packets) != 0:
        print(f"  FAIL: corrupted CRC should be dropped, got {len(packets)} packets")
        return False

    print("  PASS: corrupted CRC correctly rejected (0 packets parsed)")
    return True


def test_split_stream() -> bool:
    """Test: two packets arriving in a single serial read are both parsed."""
    print("\n=== Test: split stream (two packets one read) ===")

    frame1 = make_data_raw_sample(
        sample_counter=1, timestamp_us=10_000,
        accel_x=1000, accel_y=0, accel_z=0,
        gyro_x=0, gyro_y=0, gyro_z=0,
        temperature=0, piezo=0,
    )
    frame2 = make_data_raw_sample(
        sample_counter=2, timestamp_us=20_000,
        accel_x=2000, accel_y=0, accel_z=0,
        gyro_x=0, gyro_y=0, gyro_z=0,
        temperature=0, piezo=100,
    )

    # Feed both in a single read (as if BT delivers them together)
    combined = frame1 + frame2
    stream = MockSerialStream(combined)
    stream.read_chunks(chunk_size=len(combined))  # Single read = entire stream
    packets = stream.parsed_packets

    if len(packets) != 2:
        print(f"  FAIL: expected 2 packets, got {len(packets)}")
        return False

    if not isinstance(packets[0], DataRawSample) or not isinstance(packets[1], DataRawSample):
        print(f"  FAIL: both should be DataRawSample")
        return False

    if packets[0].sample_counter != 1 or packets[1].sample_counter != 2:
        print(f"  FAIL: wrong sample counters")
        return False

    if packets[1].piezo != 100:
        print(f"  FAIL: second packet piezo should be 100, got {packets[1].piezo}")
        return False

    print(f"  PASS: both packets parsed correctly, piezo of 2nd packet = {packets[1].piezo}")
    return True


def test_mixed_packet_types() -> bool:
    """Test: DATA_RAW_SAMPLE followed by EVT_SHOT_DETECTED in same stream."""
    print("\n=== Test: mixed packet types ===")

    frame1 = make_data_raw_sample(
        sample_counter=10, timestamp_us=100_000,
        accel_x=8192, accel_y=0, accel_z=0,
        gyro_x=0, gyro_y=0, gyro_z=0,
        temperature=340, piezo=500,
    )
    frame2 = make_evt_shot_detected(
        session_id=99,
        timestamp_us=200_000,
        shot_number=1,
        piezo_peak=2500,
        accel_x_peak=8192, accel_y_peak=0, accel_z_peak=0,
        gyro_x_peak=0, gyro_y_peak=0, gyro_z_peak=0,
        recoil_axis=2,  # Z
        recoil_sign=-1,
    )

    combined = frame1 + frame2
    stream = MockSerialStream(combined)
    stream.read_chunks(chunk_size=16)  # 16-byte reads
    packets = stream.parsed_packets

    if len(packets) != 2:
        print(f"  FAIL: expected 2 packets, got {len(packets)}")
        return False

    if not isinstance(packets[0], DataRawSample):
        print(f"  FAIL: first packet should be DataRawSample")
        return False

    if not isinstance(packets[1], EvtShotDetected):
        print(f"  FAIL: second packet should be EvtShotDetected")
        return False

    # Verify fields
    errors = []
    if packets[0].sample_counter != 10:
        errors.append(f"  sample_counter[0]: 10 != {packets[0].sample_counter}")
    if packets[0].temperature != 340:
        errors.append(f"  temperature[0]: 340 != {packets[0].temperature}")
    if packets[0].piezo != 500:
        errors.append(f"  piezo[0]: 500 != {packets[0].piezo}")
    if packets[1].session_id != 99:
        errors.append(f"  session_id[1]: 99 != {packets[1].session_id}")
    if packets[1].recoil_axis != 2:
        errors.append(f"  recoil_axis[1]: 2 != {packets[1].recoil_axis}")
    if packets[1].recoil_sign != -1:
        errors.append(f"  recoil_sign[1]: -1 != {packets[1].recoil_sign}")
    if packets[1].recoil_axis_name != "Z":
        errors.append(f"  recoil_axis_name[1]: 'Z' != '{packets[1].recoil_axis_name}'")

    if errors:
        print("  FAIL:")
        for e in errors:
            print(e)
        return False

    print(f"  PASS: DATA_RAW_SAMPLE (counter={packets[0].sample_counter}, "
          f"temp={packets[0].temperature}, piezo={packets[0].piezo}) + "
          f"EVT_SHOT_DETECTED (session={packets[1].session_id}, "
          f"recoil={packets[1].recoil_axis_name}{packets[1].recoil_sign})")
    return True


def main() -> int:
    print("=" * 60)
    print("STASYS Loopback Test")
    print("Simulates ESP32 sending packets over mock serial stream")
    print("=" * 60)

    results = [
        ("DATA_RAW_SAMPLE loopback", test_data_raw_sample_loopback),
        ("EVT_SHOT_DETECTED loopback", test_evt_shot_detected_loopback),
        ("CRC validation", test_crc_validation),
        ("Split stream (two packets)", test_split_stream),
        ("Mixed packet types", test_mixed_packet_types),
    ]

    passed = 0
    failed = 0
    for name, test_fn in results:
        try:
            if test_fn():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"\n  EXCEPTION in {name}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(results)} tests")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
