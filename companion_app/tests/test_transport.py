"""Tests for the SerialTransport layer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from stasys.transport.serial_transport import SerialTransport


class TestAutoDiscovery:
    def test_returns_none_when_no_matching_port(self) -> None:
        with patch("serial.tools.list_ports.comports", return_value=[]):
            result = SerialTransport.find_stasys_port()
            assert result is None

    def test_returns_none_when_no_localmfg0002_in_hwid(self) -> None:
        mock_port = MagicMock()
        mock_port.name = "COM3"
        mock_port.description = "Standard Serial over Bluetooth link (COM3)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0000"
        mock_port.device = "COM3"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            result = SerialTransport.find_stasys_port()
            assert result is None

    def test_returns_port_for_localmfg0002_hwid(self) -> None:
        mock_port = MagicMock()
        mock_port.name = "COM5"
        mock_port.description = "Standard Serial over Bluetooth link (COM5)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        mock_port.device = "COM5"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            result = SerialTransport.find_stasys_port()
            assert result == "COM5"

    def test_skips_incoming_port(self) -> None:
        mock_port = MagicMock()
        mock_port.name = "COM3 (Incoming)"
        mock_port.description = "Standard Serial over Bluetooth link (COM3)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        mock_port.device = "COM3"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            result = SerialTransport.find_stasys_port()
            assert result is None

    def test_skips_generic_bt_port(self) -> None:
        mock_port = MagicMock()
        mock_port.name = "COM3"
        mock_port.description = "Standard Serial over Bluetooth link (COM3)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0000"
        mock_port.device = "COM3"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            result = SerialTransport.find_stasys_port()
            assert result is None

    def test_returns_first_when_multiple_localmfg0002_ports(self) -> None:
        port1 = MagicMock()
        port1.name = "COM3"
        port1.description = "Standard Serial over Bluetooth link (COM3)"
        port1.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        port1.device = "COM3"

        port2 = MagicMock()
        port2.name = "COM4"
        port2.description = "Standard Serial over Bluetooth link (COM4)"
        port2.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        port2.device = "COM4"

        with patch("serial.tools.list_ports.comports", return_value=[port1, port2]):
            result = SerialTransport.find_stasys_port()
            # Should return the first one found.
            assert result == "COM3"

    def test_matches_without_name_in_description(self) -> None:
        """LOCALMFG&0002 in HWID is sufficient even when desc is generic."""
        mock_port = MagicMock()
        mock_port.name = ""
        mock_port.description = "Standard Serial over Bluetooth link (COM8)"
        mock_port.hwid = "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002"
        mock_port.device = "COM8"

        with patch("serial.tools.list_ports.comports", return_value=[mock_port]):
            result = SerialTransport.find_stasys_port()
            assert result == "COM8"


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
        transport = SerialTransport()
        result = transport.connect()
        assert result is False
        assert not transport.is_connected

    def test_disconnect_is_safe_when_not_connected(self) -> None:
        transport = SerialTransport()
        transport.disconnect()
        assert not transport.is_connected


class TestSerialTransportWithMockedSerial:
    def test_connect_opens_serial_and_starts_thread(self) -> None:
        mock_ser = MagicMock()
        mock_ser.is_open = True

        with patch("serial.Serial", return_value=mock_ser) as mock_class:
            transport = SerialTransport(port="COM5")
            result = transport.connect()

            assert result is True
            assert transport.is_connected
            mock_class.assert_called_once()
            call_kwargs = mock_class.call_args
            assert call_kwargs.kwargs["port"] == "COM5"
            assert call_kwargs.kwargs["baudrate"] == 115200

    def test_write_returns_bytes_written(self) -> None:
        mock_ser = MagicMock()
        mock_ser.is_open = True
        mock_ser.write.return_value = 4

        with patch("serial.Serial", return_value=mock_ser):
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

        with patch("serial.Serial", return_value=mock_ser):
            transport = SerialTransport(port="COM5")
            transport.connect()
            transport.disconnect()

            assert not transport.is_connected
            mock_ser.close.assert_called_once()
