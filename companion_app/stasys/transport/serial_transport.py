"""Serial transport layer for STASYS ESP32 SPP connection."""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

import serial
import serial.tools.list_ports

logger = logging.getLogger(__name__)

RECONNECT_DELAY_SEC = 3.0
BAUD_RATE = 115200
READ_CHUNK = 1024


XON = 0x11
XOFF = 0x13


class SerialTransport:
    """Manages serial connection to STASYS device.

    Supports auto-discovery of the COM port and a background read thread
    that feeds received bytes into a thread-safe queue.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        flow_control: Optional[object] = None,
    ) -> None:
        """Initialize transport, optionally with a specific port.

        Args:
            port: Explicit COM port name (e.g. "COM5"). If None, port
                  must be set before connect() via find_stasys_port().
            status_callback: Optional callback invoked on connection status changes.
                             Receives a string describing the new state.
            flow_control: Optional object with handle_xon() / handle_xoff() methods.
                          If provided, incoming XON (0x11) and XOFF (0x13) bytes
                          are intercepted here instead of being passed to the parser.
        """
        self._port: Optional[str] = port
        self._serial: Optional[serial.Serial] = None
        self._read_thread: Optional[threading.Thread] = None
        self._receive_queue: queue.Queue[bytes] = queue.Queue()
        self._running: bool = False
        self._lock = threading.RLock()  # RLock so is_connected can be called inside _lock scope
        self._status_callback = status_callback or (lambda s: None)
        self._flow_control = flow_control

    # -------------------------------------------------------------------------
    # Public properties
    # -------------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._serial is not None and self._serial.is_open

    @property
    def read_queue(self) -> queue.Queue[bytes]:
        """Queue of raw byte chunks received from the device."""
        return self._receive_queue

    # -------------------------------------------------------------------------
    # Port discovery
    # -------------------------------------------------------------------------

    @staticmethod
    def find_stasys_port() -> Optional[str]:
        """Scan all COM ports and return the STASYS BT SPP port.

        On Windows, pyserial does not expose the friendly Bluetooth device name
        in the port description — it only shows "Standard Serial over Bluetooth link".
        The reliable identifier for a paired Bluetooth SPP device is the HWID
        containing ``LOCALMFG&0002`` (vs ``LOCALMFG&0000`` for generic/unpaired ports).

        Returns:
            COM port device name, or None if not found.
        """
        candidates: list[str] = []

        for port_info in serial.tools.list_ports.comports():
            hwid = (port_info.hwid or "").upper()
            # Match paired Bluetooth SPP devices: LOCALMFG&0002 in HWID.
            # Pattern: BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002
            if "LOCALMFG&0002" not in hwid:
                continue

            # Skip Incoming — only Outgoing can initiate connections.
            name = (port_info.name or "").upper()
            desc = (port_info.description or "").upper()
            if "INCOMING" in name or "INCOMING" in desc:
                logger.debug("Skipping Incoming port: %s (%s)", port_info.device, port_info.description)
                continue

            candidates.append(port_info.device)
            logger.info("Found paired BT SPP port: %s (hwid=%s)", port_info.device, port_info.hwid)

        if not candidates:
            return None

        if len(candidates) > 1:
            logger.warning(
                "Multiple paired BT SPP devices found (%s); using the first one: %s",
                candidates, candidates[0],
            )

        return candidates[0]

    # -------------------------------------------------------------------------
    # Connection management
    # -------------------------------------------------------------------------

    def connect(self, port: Optional[str] = None) -> bool:
        """Open the serial connection and start the read thread.

        Args:
            port: COM port override. Falls back to the port set at
                  construction time if omitted.

        Returns:
            True if connection succeeded, False otherwise.
        """
        target = port or self._port
        if not target:
            logger.error("No port specified")
            return False

        with self._lock:
            if self.is_connected:
                logger.warning("Already connected to %s", target)
                return True

        self._status_callback(f"connecting:{target}")

        try:
            ser = serial.Serial(
                port=target,
                baudrate=BAUD_RATE,
                timeout=0.01,          # 10ms — return immediately if no data
                write_timeout=5.0,
            )
            # Increase OS buffer sizes to reduce Windows BT SPP batching latency.
            # bthmodem.sys defaults to small buffers, causing ~50-100ms of
            # hardware-level batching on the virtual COM port.
            ser.set_buffer_size(rx_size=8192, tx_size=8192)
        except serial.SerialException as e:
            logger.error("Failed to open %s: %s", target, e)
            self._status_callback("connection_failed")
            return False

        with self._lock:
            self._serial = ser
            self._port = target
            self._running = True

        self._read_thread = threading.Thread(target=self._read_loop, daemon=True, name="SerialReadThread")
        self._read_thread.start()

        logger.info("Connected to %s", target)
        self._status_callback("connected")
        return True

    def disconnect(self) -> None:
        """Close the connection and stop the read thread."""
        with self._lock:
            if not self.is_connected:
                return
            self._running = False

        if self._serial:
            try:
                self._serial.close()
            except serial.SerialException as e:
                logger.warning("Error closing serial port: %s", e)

        with self._lock:
            self._serial = None

        if self._read_thread:
            self._read_thread.join(timeout=2.0)
            self._read_thread = None

        logger.info("Disconnected")
        self._status_callback("disconnected")

    # -------------------------------------------------------------------------
    # Internal read loop
    # -------------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Background thread: non-blocking drain of serial OS buffer, push bytes into queue.

        XON (0x11) and XOFF (0x13) bytes are intercepted here and forwarded to
        the flow control handler instead of the parser, preventing them from
        corrupting the protocol frame scanner.
        """
        logger.debug("Read loop started")
        while True:
            serial_dev: Optional[serial.Serial] = None
            with self._lock:
                if not self._running:
                    break
                serial_dev = self._serial

            if serial_dev is None or not serial_dev.is_open:
                self._handle_disconnect()
                break

            try:
                # Drain the full OS buffer in one call — no per-byte overhead,
                # no blocking waiting for a fixed chunk size.  With timeout=0.01
                # the underlying read() returns immediately if the buffer is empty.
                n = serial_dev.in_waiting
                if n > 0:
                    data = serial_dev.read(n)
                else:
                    data = serial_dev.read(1)  # block briefly so we don't spin the CPU
            except serial.SerialException as e:
                logger.warning("Read error: %s", e)
                self._handle_disconnect()
                break

            if data:
                self._dispatch_read(data)

        logger.debug("Read loop exited")

    def _dispatch_read(self, data: bytes) -> None:
        """Split incoming bytes: XON/XOFF to flow control, rest to parser queue."""
        fc = self._flow_control
        if fc is None:
            self._receive_queue.put(data)
            return

        # Scan for XON/XOFF bytes and split the chunk.
        # XON (0x11) and XOFF (0x13) are sent between complete protocol frames,
        # never inside them, so splitting is safe.
        chunks: list[bytes] = []
        for b in data:
            if b == XON:
                # Flush any pending non-flow-control chunk first
                if chunks:
                    self._receive_queue.put(b"".join(chunks))
                    chunks.clear()
                try:
                    fc.handle_xon()
                except Exception:
                    logger.exception("Error in flow control handle_xon")
            elif b == XOFF:
                if chunks:
                    self._receive_queue.put(b"".join(chunks))
                    chunks.clear()
                try:
                    fc.handle_xoff()
                except Exception:
                    logger.exception("Error in flow control handle_xoff")
            else:
                chunks.append(bytes([b]))

        if chunks:
            self._receive_queue.put(b"".join(chunks))

    def _handle_disconnect(self) -> None:
        """Called when the read loop detects a connection drop. Triggers reconnect."""
        logger.warning("Connection lost, attempting reconnect in %.1fs...", RECONNECT_DELAY_SEC)
        self._status_callback("reconnecting")

        with self._lock:
            self._running = False
            if self._serial:
                try:
                    self._serial.close()
                except serial.SerialException:
                    pass
                self._serial = None

        while self._running is False:
            time.sleep(RECONNECT_DELAY_SEC)
            if self._port and self.connect(self._port):
                break
            logger.warning("Reconnect failed, retrying in %.1fs...", RECONNECT_DELAY_SEC)

    # -------------------------------------------------------------------------
    # Write
    # -------------------------------------------------------------------------

    def write(self, data: bytes) -> int:
        """Write raw bytes to the serial port.

        Returns:
            Number of bytes written, or -1 on error.
        """
        with self._lock:
            if not self.is_connected:
                return -1
            try:
                return self._serial.write(data)  # type: ignore[union-attr]
            except serial.SerialException as e:
                logger.error("Write error: %s", e)
                return -1
