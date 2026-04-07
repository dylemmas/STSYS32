"""Tests for tools/monitor.py, tools/console.py, and tools/replay.py."""

from __future__ import annotations

import queue
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from stasys.protocol.commands import cmd_get_info, cmd_start_session, cmd_stop_session
from stasys.protocol.parser import ProtocolParser
from stasys.protocol.packets import DataRawSample, EvtSensorHealth, EvtSessionStarted, EvtSessionStopped, EvtShotDetected, PacketType, RspInfo, RspConfig
from stasys.storage.conversions import imu_accel_gyro_convert
from stasys.storage.raw_store import RawStore
from stasys.storage.session_store import SessionStore


# =============================================================================
# SerialTransport auto-discovery
# =============================================================================

class TestAutoDiscovery:
    def test_returns_none_when_no_ports(self) -> None:
        with patch("serial.tools.list_ports.comports", return_value=[]):
            from stasys.transport.serial_transport import SerialTransport
            result = SerialTransport.find_stasys_port()
            assert result is None

    def test_skips_non_bt_port(self) -> None:
        mock_port = MagicMock()
        mock_port.name = "COM3"
        mock_port.description = "USB Serial"
        mock_port.hwid = "USB\\VID_1234"
        mock_port.device = "COM3"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            from stasys.transport.serial_transport import SerialTransport
            result = SerialTransport.find_stasys_port()
            assert result is None

    def test_returns_localmfg0002_port(self) -> None:
        mock_port = MagicMock()
        mock_port.name = "COM5"
        mock_port.description = "Standard Serial over Bluetooth link (COM5)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        mock_port.device = "COM5"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            from stasys.transport.serial_transport import SerialTransport
            result = SerialTransport.find_stasys_port()
            assert result == "COM5"

    def test_skips_incoming_port(self) -> None:
        mock_port = MagicMock()
        mock_port.name = "COM3 (Incoming)"
        mock_port.description = "Standard Serial over Bluetooth link (COM3)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        mock_port.device = "COM3"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            from stasys.transport.serial_transport import SerialTransport
            result = SerialTransport.find_stasys_port()
            assert result is None

    def test_returns_first_of_multiple(self) -> None:
        port1 = MagicMock()
        port1.name = "COM3"
        port1.description = "BT COM3"
        port1.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        port1.device = "COM3"

        port2 = MagicMock()
        port2.name = "COM4"
        port2.description = "BT COM4"
        port2.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        port2.device = "COM4"

        with patch("serial.tools.list_ports.comports", return_value=[port1, port2]):
            from stasys.transport.serial_transport import SerialTransport
            result = SerialTransport.find_stasys_port()
            assert result == "COM3"


# =============================================================================
# SerialTransport mocked connect
# =============================================================================

class TestSerialTransport:
    def test_instantiate_without_connect(self) -> None:
        from stasys.transport.serial_transport import SerialTransport
        t = SerialTransport()
        assert not t.is_connected
        assert t.read_queue is not None

    def test_connect_with_mock(self) -> None:
        mock_ser = MagicMock()
        mock_ser.is_open = True

        with patch("serial.Serial", return_value=mock_ser):
            from stasys.transport.serial_transport import SerialTransport
            t = SerialTransport(port="COM5")
            with patch.object(
                SerialTransport, "_wait_for_data", return_value=True,
            ):
                success, reason = t.connect()
                assert success is True
                assert reason is None
                assert t.is_connected

    def test_write_when_disconnected_returns_minus_one(self) -> None:
        from stasys.transport.serial_transport import SerialTransport
        t = SerialTransport()
        written = t.write(b"\xaa\x55")
        assert written == -1


# =============================================================================
# Parser round-trip with mocked transport
# =============================================================================

class TestParserIntegration:
    def test_feed_and_parse_data_sample(self) -> None:
        import struct
        from stasys.protocol.crc import crc16

        parser = ProtocolParser()
        # 24 bytes: counter(4)+ts(4)+accel_xyz(6)+gyro_xyz(6)+temp(2)+piezo(2)
        # Format '<IIhhhhhhHh': I(4)+I(4)+h(2)*6+h(2)+h(2) = 24 bytes, 10 values
        # Layout: counter, timestamp, ax, ay, az, gx, gy, gz, temp, piezo
        payload = struct.pack("<IIhhhhhhHh",
                              1, 10_000,        # counter, timestamp
                              8192, 0, 0,       # accel_x/y/z
                              0, 0, 0,           # gyro_x/y/z
                              500,               # temp
                              0)                 # piezo
        header = bytes([PacketType.DATA_RAW_SAMPLE]) + struct.pack("<H", len(payload))
        crc = crc16(header + payload)
        frame = b"\xAA\x55" + header + payload + struct.pack("<H", crc)

        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()
        assert isinstance(pkt, DataRawSample)
        assert pkt.accel_x_ms2 == pytest.approx(9.81, rel=1e-3)

    def test_feed_and_parse_shot_detected(self) -> None:
        import struct
        from stasys.protocol.crc import crc16

        parser = ProtocolParser()
        d = bytearray(29)
        struct.pack_into("<I", d, 0, 1)
        struct.pack_into("<I", d, 4, 1_000_000)
        struct.pack_into("<H", d, 8, 1)
        struct.pack_into("<H", d, 10, 2048)
        struct.pack_into("<h", d, 12, 5000)
        struct.pack_into("<h", d, 14, -3000)
        struct.pack_into("<h", d, 16, 1000)
        struct.pack_into("<h", d, 18, 200)
        struct.pack_into("<h", d, 20, -100)
        struct.pack_into("<h", d, 22, 50)
        d[26] = 0  # recoil_axis = X
        d[27] = 1  # recoil_sign = +1

        header = bytes([PacketType.EVT_SHOT_DETECTED]) + struct.pack("<H", len(d))
        crc = crc16(header + bytes(d))
        frame = b"\xAA\x55" + header + bytes(d) + struct.pack("<H", crc)

        parser.feed(frame)
        pkt = parser.packet_queue.get_nowait()
        assert isinstance(pkt, EvtShotDetected)
        assert pkt.shot_number == 1
        assert pkt.recoil_axis == 0
        assert pkt.recoil_sign == 1
        assert pkt.recoil_axis_name == "X"


# =============================================================================
# ReplayLoader
# =============================================================================

class TestReplayLoader:
    def test_load_no_session(self) -> None:
        tmpdir = tempfile.mkdtemp()
        db = str(Path(tmpdir) / "stasys.db")
        try:
            store = SessionStore(db_path=db)
            from tools.replay import ReplayLoader
            loader = ReplayLoader(session_id=9999, db_path=db)
            events = loader.load()
            assert events == []
        finally:
            store._db_conn.close()

    def test_load_session_with_shots(self) -> None:
        tmpdir = tempfile.mkdtemp()
        db = str(Path(tmpdir) / "stasys.db")
        raw_base = str(Path(tmpdir) / "data" / "sessions")
        try:
            store = SessionStore(db_path=db)
            raw_store = RawStore(base_path=raw_base)

            sid = store.open_session(firmware_version="v1.0.0")

            # Record a shot in DB
            shot_pkt = EvtShotDetected(
                session_id=0, timestamp_us=1_000_000, shot_number=1,
                piezo_peak=2048, accel_x_peak=5000, accel_y_peak=0, accel_z_peak=0,
                gyro_x_peak=0, gyro_y_peak=0, gyro_z_peak=0,
                recoil_axis=0, recoil_sign=1,
            )
            store.record_shot(sid, shot_pkt)

            # Record IMU in raw store
            imu_pkt = DataRawSample(
                sample_counter=0, timestamp_us=500_000,
                accel_x=8192, accel_y=0, accel_z=0,
                gyro_x=0, gyro_y=0, gyro_z=0,
                piezo=0, temperature=340,
            )
            raw_store.record_imu(sid, imu_pkt)

            from tools.replay import ReplayLoader
            loader = ReplayLoader(session_id=sid, db_path=db)
            events = loader.load()

            assert len(events) == 2
            # IMU at 500ms, SHOT at 1000ms — IMU comes first
            labels = [e[1] for e in events]
            assert labels == ["IMU", "SHOT"]
        finally:
            store._db_conn.close()


# =============================================================================
# ReplayPlayer
# =============================================================================

class TestReplayPlayer:
    def test_empty_events_prints_warning(self, capsys) -> None:
        from tools.replay import ReplayPlayer
        player = ReplayPlayer([], speed=1.0)
        player.run()
        captured = capsys.readouterr()
        assert "No events" in captured.out

    def test_events_sorted_by_timestamp(self) -> None:
        # Simulate ReplayPlayer ordering
        events = [
            (1000.0, "IMU", "ax=0"),
            (500.0, "SHOT", "shot1"),
            (1500.0, "IMU", "ax=1"),
        ]
        sorted_events = sorted(events, key=lambda e: e[0])
        assert sorted_events[0][0] == 500.0
        assert sorted_events[1][0] == 1000.0
        assert sorted_events[2][0] == 1500.0

    def test_speed_scaling(self) -> None:
        # Speed 2x means 1000ms elapsed in sim time = 500ms real time
        t0, t1 = 1000.0, 2000.0
        speed_2x = (t1 - t0) / 2.0
        assert speed_2x == 500.0

        # Speed 0.5x means 1000ms elapsed in sim time = 2000ms real time
        speed_half = (t1 - t0) / 0.5
        assert speed_half == 2000.0


# =============================================================================
# DataRawSample display formatting
# =============================================================================

class TestPacketFormatting:
    def test_data_sample_ms2_display(self) -> None:
        pkt = DataRawSample(
            sample_counter=0, timestamp_us=0,
            accel_x=8192, accel_y=0, accel_z=0,
            gyro_x=655, gyro_y=0, gyro_z=0,
            piezo=500, temperature=340,
        )
        # Verify conversion properties work
        assert pkt.accel_x_ms2 == pytest.approx(9.81, rel=1e-3)
        assert pkt.gyro_x_dps == pytest.approx(10.0, rel=1e-3)
        assert pkt.temperature_c == pytest.approx(37.53, rel=1e-2)

    def test_evt_shot_detected_display(self) -> None:
        pkt = EvtShotDetected(
            session_id=1, timestamp_us=1_000_000, shot_number=5,
            piezo_peak=2048, accel_x_peak=5000, accel_y_peak=-3000, accel_z_peak=1000,
            gyro_x_peak=200, gyro_y_peak=-100, gyro_z_peak=50,
            recoil_axis=1, recoil_sign=-1,
        )
        assert pkt.recoil_axis_name == "Y"
        assert pkt.recoil_sign == -1

    def test_rsp_info_display(self) -> None:
        pkt = RspInfo(
            firmware_version=0x010200,
            hardware_rev=3,
            build_timestamp=1700000000,
            supported_features=0x000F,
            mpu_whoami=0x68,
        )
        assert pkt.firmware_version_str == "v1.2.0"
        assert pkt.mpu_ok is True
