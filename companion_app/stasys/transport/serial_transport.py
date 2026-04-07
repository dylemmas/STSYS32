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
MAX_CONSECUTIVE_ERRORS = 5
BAUD_RATE = 115200
# Maximum time to wait for initial data before giving up (seconds).
# If no data is received within this window, the ESP32 is likely not connected.
INITIAL_DATA_TIMEOUT_SEC = 8.0


XON = 0x11
XOFF = 0x13


class SerialTransport:
    """Manages serial connection to STASYS device.

    Supports auto-discovery of the COM port and a background read thread
    that feeds received bytes into a thread-safe queue. The read thread
    survives all serial errors and automatically attempts reconnection on
    unexpected disconnects (after the initial connection is established).
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

        # Error recovery state
        self._consecutive_errors: int = 0
        self._last_error_time: float = 0.0

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

    def connect(self, port: Optional[str] = None) -> tuple[bool, Optional[str]]:
        """Open the serial connection and start the read thread.

        On Windows, Bluetooth SPP creates a pair of virtual COM ports (Outgoing
        and Incoming). If the primary port fails to open (e.g. it is already
        open by another process), this method automatically attempts the alternate
        STASYS SPP port before giving up.

        Args:
            port: COM port override. Falls back to the port set at
                  construction time if omitted.

        Returns:
            True if connection succeeded (ESP32 responded with data), False otherwise.
        """
        target = port or self._port
        if not target:
            logger.error("No port specified")
            return (False, "No COM port specified.")

        with self._lock:
            if self.is_connected:
                logger.warning("Already connected to %s", target)
                return (True, None)

        self._status_callback(f"connecting:{target}")

        ser, primary_reason = self._open_port(target)
        if ser is None:
            # Primary port failed — try to find and connect to an alternate
            # STASYS SPP port. On Windows BT SPP, Outgoing and Incoming ports
            # are created as a pair. If one is blocked, the other may work.
            alternate = self._find_alternate_spp_port(target)
            if alternate:
                logger.info("Trying alternate STASYS port: %s", alternate)
                self._status_callback(f"connecting:{alternate}")
                ser, alternate_reason = self._open_port(alternate)
                if ser is not None:
                    target = alternate
                    primary_reason = None  # alternate succeeded — no error to report
                else:
                    primary_reason = (
                        f"{target} failed. {alternate} also failed: {alternate_reason}"
                    )
            if ser is None:
                self._status_callback("connection_failed")
                return (False, primary_reason or f"Cannot open {target}.")

        # From here on, ser is a valid serial object. Keep a local reference for
        # cleanup so the read thread doesn't see a partially-set-up connection.
        serial_dev: serial.Serial = ser  # type: ignore[assignment]

        with self._lock:
            self._serial = serial_dev
            self._port = target
            self._running = True
            self._consecutive_errors = 0

        self._read_thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name="SerialReadThread",
        )
        self._read_thread.start()

        logger.info("Connected to %s", target)
        self._status_callback("connected")
        return (True, None)

    def _wait_for_data(self, timeout_sec: float) -> bool:
        """Wait for data to arrive on the queue, or timeout.

        Returns True if data was received within timeout, False otherwise.
        This is used to detect whether the ESP32 is actually connected —
        a BT SPP port can be open locally while the remote device is offline.
        """
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            with self._lock:
                if not self._running:
                    # Disconnect was requested — give up immediately
                    return False
            try:
                self._receive_queue.get(timeout=0.5)
                # Got data — connection is live
                return True
            except queue.Empty:
                pass
        return False

    def _open_port(self, port: str) -> tuple[Optional[serial.Serial], Optional[str]]:
        """Open a serial port with retry on permission error.

        Returns:
            A (Serial, None) tuple on success, or (None, reason) on failure.
            The reason string is a user-facing diagnostic message.
        """
        for attempt in range(1 + 1):  # up to 2 attempts
            try:
                ser = serial.Serial(
                    port=port,
                    baudrate=BAUD_RATE,
                    timeout=0.01,          # 10ms — return immediately if no data
                    write_timeout=5.0,
                )
                # Increase OS buffer sizes to reduce Windows BT SPP batching latency.
                # bthmodem.sys defaults to small buffers, causing ~50-100ms of
                # hardware-level batching on the virtual COM port.
                ser.set_buffer_size(rx_size=8192, tx_size=8192)
                return (ser, None)
            except serial.SerialException as e:
                err_str = str(e)
                # Extract OS-level error code for diagnostics.
                # pyserial wraps platform-specific errors:
                #   - Windows: e.winerror (int)
                #   - Linux/macOS: e.errno (int) via OSError
                os_code: Optional[int] = getattr(e, "winerror", None)
                if os_code is None:
                    os_code = getattr(e, "errno", None)
                os_msg = f" (OS error {os_code})" if os_code is not None else ""

                if os_code == 5 and attempt == 0:
                    # ERROR_ACCESS_DENIED (5) on Windows — another process has the port.
                    logger.warning(
                        "Port %s in use by another process%s. Retrying in 1s... "
                        "If this persists, close other apps using this port.", port, os_msg
                    )
                    time.sleep(1.0)
                    continue

                # Build user-facing diagnostic message.
                if os_code == 2:
                    reason = f"{port} not found (OS error {os_code}) — check that the device is paired and powered on."
                elif os_code == 32:
                    reason = f"{port} is already open by another process — close the other app, then try again."
                elif os_code == 31:
                    reason = f"{port} exists but the device is offline — make sure STASYS is powered on and in range."
                else:
                    reason = f"Could not open {port}: {err_str}{os_msg}."

                if os_code == 2:
                    logger.error("Port %s not found%s", port, os_msg)
                elif os_code == 32 or os_code == 267:
                    logger.error("Port %s is already open by another process%s", port, os_msg)
                elif os_code == 31:
                    logger.error("Port %s exists but device is offline%s", port, os_msg)
                else:
                    logger.error("Failed to open %s: %s%s", port, err_str, os_msg)
                self._status_callback("connection_failed")
                return (None, reason)
        return (None, "Unexpected: port open returned None")

    def _find_alternate_spp_port(self, failed_port: str) -> Optional[str]:
        """Find an alternate Bluetooth SPP port for the STASYS device.

        On Windows, when the user's port (e.g. COM3 — Outgoing) fails to open,
        this method looks for the paired Incoming port (e.g. COM4) that was
        created for the same BT SPP service. This handles the case where one
        direction of the SPP virtual COM pair is blocked by another process.

        Returns:
            The alternate port device name, or None if none found.
        """
        # Extract numeric suffix from the failed port (e.g. "COM3" → 3).
        import re
        m = re.search(r"(\d+)$", failed_port.upper())
        if not m:
            return None
        failed_num = int(m.group(1))

        # Try ±1 offsets — common pattern: COM3 (Outgoing) ↔ COM4 (Incoming).
        candidates = []
        for offset in (1, -1):
            alt_num = failed_num + offset
            if alt_num <= 0:
                continue
            alt_name = re.sub(r"\d+$", str(alt_num), failed_port, flags=re.IGNORECASE)
            candidates.append(alt_name)

        for port_info in serial.tools.list_ports.comports():
            if port_info.device in candidates:
                hwid = (port_info.hwid or "").upper()
                # Only accept paired BT SPP ports — not random hardware.
                if "LOCALMFG&0002" in hwid:
                    desc = port_info.description or ""
                    logger.debug(
                        "Found alternate BT SPP port: %s (desc=%r, hwid=%s)",
                        port_info.device, desc, port_info.hwid,
                    )
                    return port_info.device
                else:
                    # Port exists but isn't a paired BT SPP device. Skip it —
                    # we don't want to accidentally connect to a different device.
                    logger.debug(
                        "Port %s exists but is not a paired BT SPP device (hwid=%s); skipping",
                        port_info.device, port_info.hwid,
                    )

        return None

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

        # Drain the receive queue on disconnect so stale data doesn't poison the parser
        self._drain_queue()

        logger.info("Disconnected")
        self._status_callback("disconnected")

    def _drain_queue(self) -> None:
        """Drain all pending bytes from the receive queue."""
        drained = 0
        try:
            while True:
                self._receive_queue.get_nowait()
                drained += 1
        except queue.Empty:
            pass
        if drained > 0:
            logger.debug("Drained %d stale byte chunks from queue on disconnect", drained)

    # -------------------------------------------------------------------------
    # Internal read loop — designed to survive all serial errors
    # -------------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Background thread: read bytes and push to queue.

        XON (0x11) and XOFF (0x13) bytes are intercepted here and forwarded to
        the flow control handler instead of the parser.

        Recovery behavior:
        - SerialException / OSError: retry up to MAX_CONSECUTIVE_ERRORS times with
          100ms sleep, then attempt immediate port reopen. If reopen fails, enter
          the blocking reconnect loop in _handle_disconnect().
        - The thread exits only when _running becomes False (explicit disconnect).
        """
        logger.debug("Read loop started")

        while True:
            serial_dev: Optional[serial.Serial] = None
            with self._lock:
                if not self._running:
                    logger.debug("Read loop: _running=False, exiting")
                    break
                serial_dev = self._serial

            if serial_dev is None or not serial_dev.is_open:
                # Port is closed — either reconnect was triggered by an error,
                # or disconnect() was called. Check _running to know which.
                with self._lock:
                    if not self._running:
                        break
                self._handle_disconnect()
                # _handle_disconnect returns only on: reconnect success
                # (sets _serial and returns) or _running=False (exits loop).
                with self._lock:
                    if not self._running:
                        break
                continue

            # ── Read from serial port ─────────────────────────────────────────
            try:
                n = serial_dev.in_waiting
                if n > 0:
                    data = serial_dev.read(n)
                else:
                    data = serial_dev.read(1)  # block briefly so we don't spin the CPU

                if not data:
                    self._consecutive_errors = 0
                    continue

                # Successful read — reset error counter
                self._consecutive_errors = 0
                self._dispatch_read(data)

            except serial.SerialException as e:
                self._consecutive_errors += 1
                now = time.time()
                logger.warning(
                    "Read error #%d (last %.1fs): %s",
                    self._consecutive_errors,
                    now - self._last_error_time,
                    e,
                )
                self._last_error_time = now

                if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error("Too many read errors — attempting reconnect")
                    self._consecutive_errors = 0
                    self._close_port_and_reconnect()
                    with self._lock:
                        if not self._running:
                            break
                else:
                    time.sleep(0.1)
                continue

            except OSError as e:
                self._consecutive_errors += 1
                logger.warning("OS error during read: %s", e)
                self._close_port_and_reconnect()
                with self._lock:
                    if not self._running:
                        break
                continue

            except Exception as e:
                # Defensive: unexpected exceptions must NOT kill the thread.
                logger.exception("Unexpected error in read loop: %s", e)
                self._consecutive_errors += 1
                if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error("Too many unexpected errors — attempting reconnect")
                    self._consecutive_errors = 0
                    self._close_port_and_reconnect()
                    with self._lock:
                        if not self._running:
                            break

        logger.debug("Read loop exited")

    def _close_port_and_reconnect(self) -> None:
        """Close the port and attempt to reopen it once. Non-blocking."""
        logger.info("Closing port for reconnect...")
        with self._lock:
            if self._serial:
                try:
                    self._serial.close()
                except serial.SerialException:
                    pass
                self._serial = None

        if self._port:
            ser, _ = self._open_port(self._port)
            if ser is not None:
                with self._lock:
                    self._serial = ser
                    self._consecutive_errors = 0
                logger.info("Reconnected to %s", self._port)
                self._status_callback("connected")
                self._drain_queue()
                return

        # Port couldn't be reopened — enter blocking reconnect loop
        self._status_callback("reconnecting")
        self._handle_disconnect()

    def _dispatch_read(self, data: bytes) -> None:
        """Split incoming bytes: XON/XOFF to flow control, rest to parser queue."""
        fc = self._flow_control
        if fc is None:
            self._receive_queue.put(data)
            return

        chunks: list[bytes] = []
        for b in data:
            if b == XON:
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
        """Blocking reconnect loop — only called after a connection was established.

        On entry, the port is closed and _running should be True (we're retrying
        a previously-good connection). Exits only when:
        - Reconnection succeeds: _serial is set, _running stays True, returns
        - Disconnect is requested: _running becomes False, exits immediately
        """
        logger.warning("Attempting reconnect in %.1fs...", RECONNECT_DELAY_SEC)

        reconnect_delay = RECONNECT_DELAY_SEC
        attempt = 0

        while True:
            with self._lock:
                if not self._running:
                    logger.info("Disconnect requested — giving up reconnect")
                    return

            time.sleep(reconnect_delay)

            with self._lock:
                if not self._running:
                    logger.info("Disconnect requested — giving up reconnect")
                    return

            if not self._port:
                logger.warning("No port for reconnect — waiting...")
                attempt += 1
                reconnect_delay = min(reconnect_delay * 1.5, 30.0)
                continue

            ser, _ = self._open_port(self._port)
            if ser is not None:
                with self._lock:
                    self._serial = ser
                    self._consecutive_errors = 0
                logger.info("Reconnected to %s after %d attempts", self._port, attempt + 1)
                self._status_callback("connected")
                self._drain_queue()
                return

            attempt += 1
            logger.warning("Reconnect attempt %d failed — retrying in %.1fs...", attempt, reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 30.0)

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