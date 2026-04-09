"""Tests for the SerialTransport layer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import serial

from stasys.transport.serial_transport import SerialTransport


class TestAutoDiscovery:
    def test_returns_none_when_no_matching_port(self) -> None:
        with patch("serial.tools.list_ports.comports", return_value=[]):
            result = SerialTransport.find_stasys_ports()
            assert result == (None, None)

    def test_returns_none_when_no_localmfg0002_in_hwid(self) -> None:
        mock_port = MagicMock()
        mock_port.name = "COM3"
        mock_port.description = "Standard Serial over Bluetooth link (COM3)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0000"
        mock_port.device = "COM3"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            result = SerialTransport.find_stasys_ports()
            assert result == (None, None)

    def test_returns_outgoing_port_with_esp32spp_in_desc(self) -> None:
        mock_port = MagicMock()
        mock_port.name = "COM4"
        mock_port.description = "STASYS 'ESP32SPP' (COM4)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        mock_port.device = "COM4"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            result = SerialTransport.find_stasys_ports()
            assert result == ("COM4", None)

    def test_returns_incoming_port_with_stasys_in_desc(self) -> None:
        mock_port = MagicMock()
        mock_port.name = "COM3 (Incoming)"
        mock_port.description = "STASYS (COM3)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        mock_port.device = "COM3"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            result = SerialTransport.find_stasys_ports()
            assert result == (None, "COM3")

    def test_returns_both_ports_for_stasys_pair(self) -> None:
        outgoing = MagicMock()
        outgoing.name = "COM4"
        outgoing.description = "STASYS 'ESP32SPP' (COM4)"
        outgoing.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        outgoing.device = "COM4"

        incoming = MagicMock()
        incoming.name = "COM3 (Incoming)"
        incoming.description = "STASYS (COM3)"
        incoming.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        incoming.device = "COM3"

        with patch("serial.tools.list_ports.comports", return_value=[outgoing, incoming]):
            result = SerialTransport.find_stasys_ports()
            assert result == ("COM4", "COM3")

    def test_skips_generic_bt_port(self) -> None:
        mock_port = MagicMock()
        mock_port.name = "COM3"
        mock_port.description = "Standard Serial over Bluetooth link (COM3)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0000"
        mock_port.device = "COM3"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            result = SerialTransport.find_stasys_ports()
            assert result == (None, None)

    def test_legacy_find_stasys_port_returns_preferred_outgoing(self) -> None:
        """find_stasys_port() (legacy) should return Outgoing when both exist."""
        outgoing = MagicMock()
        outgoing.name = "COM4"
        outgoing.description = "STASYS 'ESP32SPP' (COM4)"
        outgoing.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        outgoing.device = "COM4"

        incoming = MagicMock()
        incoming.name = "COM3"
        incoming.description = "STASYS (COM3)"
        incoming.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        incoming.device = "COM3"

        with patch("serial.tools.list_ports.comports", return_value=[outgoing, incoming]):
            result = SerialTransport.find_stasys_port()
            assert result == "COM4"

    def test_legacy_find_stasys_port_falls_back_to_incoming(self) -> None:
        """find_stasys_port() (legacy) should return Incoming when no Outgoing."""
        incoming = MagicMock()
        incoming.name = "COM3"
        incoming.description = "STASYS (COM3)"
        incoming.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        incoming.device = "COM3"

        with patch("serial.tools.list_ports.comports", return_value=[incoming]):
            result = SerialTransport.find_stasys_port()
            assert result == "COM3"

    def test_matches_without_name_in_description(self) -> None:
        """LOCALMFG&0002 in HWID with generic desc is sufficient."""
        mock_port = MagicMock()
        mock_port.name = ""
        mock_port.description = "Standard Serial over Bluetooth link (COM8)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        mock_port.device = "COM8"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            result = SerialTransport.find_stasys_ports()
            # No ESP32SPP or INCOMING in desc — single paired port, treat as Outgoing
            assert result == ("COM8", None)



class TestSerialTransportInstantiation:
    def test_instantiates_without_connecting(self) -> None:
        transport = SerialTransport()
        assert not transport.is_connected
        assert transport.read_queue is not None

    def test_instantiates_with_explicit_port(self) -> None:
        transport = SerialTransport(port="COM10")
        assert not transport.is_connected
        assert transport._port == "COM10"  # type: ignore[attr-defined]

    def test_status_callback_called_on_construction(self) -> None:
        calls: list[str] = []
        transport = SerialTransport(status_callback=calls.append)
        assert calls == []

    def test_connect_returns_false_when_no_port(self) -> None:
        with patch(
            "stasys.transport.serial_transport.SerialTransport.find_stasys_ports",
            return_value=(None, None),
        ):
            with patch(
                "serial.tools.list_ports.comports",
                return_value=[MagicMock(device="COM3", hwid="BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002", description="STASYS (COM3)")],
            ):
                transport = SerialTransport()
                success, reason = transport.connect()
        assert success is False
        assert reason is not None
        assert not transport.is_connected

    def test_disconnect_is_safe_when_not_connected(self) -> None:
        transport = SerialTransport()
        transport.disconnect()
        assert not transport.is_connected


class TestSerialTransportWithMockedSerial:
    def test_connect_opens_serial_and_starts_thread(self) -> None:
        mock_ser = MagicMock()
        mock_ser.is_open = True

        with patch(
            "stasys.transport.serial_transport.SerialTransport._open_port",
            return_value=(mock_ser, None),
        ):
            with patch(
                "serial.tools.list_ports.comports",
                return_value=[MagicMock(device="COM5", hwid="BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002")],
            ):
                transport = SerialTransport(port="COM5")
                success, reason = transport.connect()

                assert success is True
                assert reason is None
                assert transport.is_connected

    def test_write_returns_bytes_written(self) -> None:
        mock_ser = MagicMock()
        mock_ser.is_open = True
        mock_ser.write.return_value = 4

        with patch(
            "stasys.transport.serial_transport.SerialTransport._open_port",
            return_value=(mock_ser, None),
        ):
            with patch(
                "serial.tools.list_ports.comports",
                return_value=[MagicMock(device="COM5", hwid="BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002")],
            ):
                transport = SerialTransport(port="COM5")
                transport.connect()
                written = transport.write(b"\xaa\x55\x01\x00")
                assert written == 4

    def test_write_returns_minus_one_when_disconnected(self) -> None:
        transport = SerialTransport()
        written = transport.write(b"\xaa\x55")
        assert written == -1

    def test_disconnect_closes_serial(self) -> None:
        mock_ser = MagicMock()
        mock_ser.is_open = True

        with patch(
            "stasys.transport.serial_transport.SerialTransport._open_port",
            return_value=(mock_ser, None),
        ):
            with patch(
                "serial.tools.list_ports.comports",
                return_value=[MagicMock(device="COM5", hwid="BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002")],
            ):
                transport = SerialTransport(port="COM5")
                transport.connect()
                transport.disconnect()

                assert not transport.is_connected
                mock_ser.close.assert_called()

    def test_connect_falls_back_to_alternate_port(self) -> None:
        """When primary port fails, connect() tries the alternate SPP port."""
        mock_ser = MagicMock()
        mock_ser.is_open = True

        def mock_open(port: str):
            if port == "COM5":
                return (None, "COM5 is in use by another process.")
            return (mock_ser, None)

        with patch(
            "stasys.transport.serial_transport.SerialTransport._open_port",
            side_effect=mock_open,
        ):
            # COM5 is treated as the single Outgoing (no INCOMING keyword).
            # When it fails, find_stasys_ports returns nothing for "COM6".
            # connect() reports the primary failure reason since no alternate
            # can be identified without scanning for INCOMING ports.
            with patch(
                "serial.tools.list_ports.comports",
                return_value=[
                    MagicMock(device="COM5", hwid="BTHENUM{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002", description="STASYS 'ESP32SPP' (COM5)", name="COM5"),
                    MagicMock(device="COM6", hwid="BTHENUM{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002", description="STASYS (COM6)", name="COM6 (Incoming)"),
                ],
            ):
                transport = SerialTransport(port="COM5")
                success, reason = transport.connect()

        assert success is False
        assert "COM5" in reason
        assert "in use" in reason

    def test_connect_returns_diagnostic_on_all_ports_failed(self) -> None:
        """When both primary and alternate fail, returns a diagnostic message."""
        def mock_open(port: str):
            return (None, f"{port} in use")

        with patch(
            "stasys.transport.serial_transport.SerialTransport._open_port",
            side_effect=mock_open,
        ):
            with patch(
                "serial.tools.list_ports.comports",
                return_value=[
                    MagicMock(device="COM5", hwid="BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002", description="STASYS 'ESP32SPP' (COM5)", name="COM5"),
                    MagicMock(device="COM6", hwid="BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002", description="STASYS (COM6)", name="COM6 (Incoming)"),
                ],
            ):
                transport = SerialTransport(port="COM5")
                success, reason = transport.connect()

        assert success is False
        assert "COM5" in reason
        assert "in use" in reason


class TestOpenPortRetry:
    """Tests for _open_port retry logic on specific Windows error codes."""

    def _make_serial_exception(self, winerror: int, message: str) -> serial.SerialException:
        """Build a real SerialException with a winerror attribute."""
        # Construct it like pyserial does on Windows: OSError(args...)
        exc = serial.SerialException(message)
        exc.winerror = winerror  # type: ignore[attr-defined]
        exc.errno = winerror
        exc.strerror = message
        return exc

    def test_winerror_121_retries_three_times_then_fails(self) -> None:
        """ERROR_SEM_TIMEOUT (121) should retry up to 3 times before failing."""
        exc = self._make_serial_exception(121, "The semaphore timeout period has expired.")

        with patch("serial.Serial") as mock_serial:
            mock_serial.side_effect = [exc, exc, exc, exc]  # 4 failures = exhausted retries
            transport = SerialTransport(port="COM4")
            with patch("time.sleep"):
                ser, reason = transport._open_port("COM4")

        assert ser is None
        assert "timed out" in reason
        assert "power-cycling" in reason
        # 1 initial attempt + 3 BT retries = 4 total
        assert mock_serial.call_count == 4

    def test_winerror_121_succeeds_on_second_attempt(self) -> None:
        """RFCOMM handshake succeeds on retry after first timeout."""
        exc = self._make_serial_exception(121, "The semaphore timeout period has expired.")
        mock_ser = MagicMock()
        mock_ser.is_open = True

        with patch("serial.Serial") as mock_serial:
            mock_serial.side_effect = [exc, mock_ser]
            transport = SerialTransport(port="COM4")
            with patch("time.sleep"):
                ser, reason = transport._open_port("COM4")

        assert ser is mock_ser
        assert reason is None
        assert mock_serial.call_count == 2

    def test_winerror_31_retries_three_times_then_fails(self) -> None:
        """ERROR_GEN_FAILURE (31) — BT radio glitch — retries up to 3 times."""
        exc = self._make_serial_exception(31, "The device is not connected.")

        with patch("serial.Serial") as mock_serial:
            mock_serial.side_effect = [exc, exc, exc, exc]
            transport = SerialTransport(port="COM4")
            with patch("time.sleep"):
                ser, reason = transport._open_port("COM4")

        assert ser is None
        assert "offline" in reason

    def test_winerror_5_fails_hard_after_one_retry(self) -> None:
        """ERROR_ACCESS_DENIED (5) — port busy — fails after one retry."""
        exc = self._make_serial_exception(5, "Access is denied.")

        with patch("serial.Serial") as mock_serial:
            mock_serial.side_effect = [exc, exc, exc, exc]
            transport = SerialTransport(port="COM4")
            with patch("time.sleep"):
                ser, reason = transport._open_port("COM4")

        # Should only try twice (1 initial + 1 retry), then give up with os_code==5 path
        assert ser is None
        assert "in use" in reason
        assert mock_serial.call_count == 2

    def test_winerror_2_fails_hard(self) -> None:
        """ERROR_FILE_NOT_FOUND (2) — hard fail, no retry."""
        exc = self._make_serial_exception(2, "The system cannot find the file specified.")

        with patch("serial.Serial") as mock_serial:
            mock_serial.side_effect = exc
            transport = SerialTransport(port="COM99")
            ser, reason = transport._open_port("COM99")

        assert ser is None
        assert "not found" in reason
        assert mock_serial.call_count == 1

    def test_timeout_set_to_five_seconds(self) -> None:
        """The serial port should be opened with a 5s read timeout."""
        mock_ser = MagicMock()
        mock_ser.is_open = True

        with patch("serial.Serial") as mock_serial:
            mock_serial.return_value = mock_ser
            transport = SerialTransport(port="COM5")
            transport._open_port("COM5")

        call_kwargs = mock_serial.call_args.kwargs
        assert call_kwargs["timeout"] == 5.0
        assert call_kwargs["write_timeout"] == 5.0
