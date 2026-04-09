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
# Retries for Bluetooth handshake timeout (winerror 121 on Windows).
BT_HANDSHAKE_RETRIES = 3
BT_HANDSHAKE_RETRY_DELAY_SEC = 2.0
# Settling delay after the serial port opens, before sending commands.
# The ESP32 BluetoothTask needs ~500ms after RFCOMM connects to be ready.
POST_OPEN_SETTLE_SEC = 0.5


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
    def find_stasys_ports() -> tuple[Optional[str], Optional[str]]:
        """Scan all COM ports and return the STASYS BT SPP port pair.

        Windows assigns a paired SPP device as a two-port set:
          - Outgoing: description contains "ESP32SPP" — initiates RFCOMM connections
          - Incoming: plain "STASYS" in description — only accepts inbound

        Returns:
            (outgoing_port, incoming_port). Either may be None if not found.
        """
        outgoing: Optional[str] = None
        incoming: Optional[str] = None

        for port_info in serial.tools.list_ports.comports():
            hwid = (port_info.hwid or "").upper()
            if "LOCALMFG&0002" not in hwid:
                continue  # skip unpaired / non-BT ports

            name = (port_info.name or "").upper()
            desc = (port_info.description or "").upper()
            # Windows SPP pair: Outgoing has "ESP32SPP" or "OUTGOING" in name/desc;
            # Incoming has "INCOMING" in name/desc. When neither keyword exists but
            # LOCALMFG&0002 is present, a single paired port is typically the Outgoing
            # one (Windows may only create Outgoing for some devices). Treat it as
            # connectable regardless.
            if "ESP32SPP" in name or "ESP32SPP" in desc:
                outgoing = port_info.device
                logger.info("Found STASYS Outgoing port: %s (%s)", port_info.device, port_info.description)
            elif "OUTGOING" in name or "OUTGOING" in desc:
                outgoing = port_info.device
                logger.info("Found STASYS Outgoing port: %s (%s)", port_info.device, port_info.description)
            elif "INCOMING" in name or "INCOMING" in desc:
                incoming = port_info.device
                logger.info("Found STASYS Incoming port: %s (%s)", port_info.device, port_info.description)
            else:
                # Paired BT SPP with no keyword — this is the single (Outgoing) port.
                if outgoing is None:
                    outgoing = port_info.device
                logger.info("Found STASYS single-port (treat as Outgoing): %s (%s)", port_info.device, port_info.description)

        return (outgoing, incoming)

    @staticmethod
    def find_stasys_port() -> Optional[str]:
        """Legacy single-port scan — prefers Outgoing, falls back to any candidate.

        Returns:
            COM port device name, or None if not found.
        """
        outgoing, incoming = SerialTransport.find_stasys_ports()
        return outgoing or incoming

    # -------------------------------------------------------------------------
    # Connection management
    # -------------------------------------------------------------------------

    def connect(self, port: Optional[str] = None) -> tuple[bool, Optional[str]]:
        """Open the serial connection and start the read thread.

        If no port is specified, auto-discovers STASYS ports and tries them in order:
        Outgoing (ESP32SPP) first, then Incoming as fallback.

        On Windows, Bluetooth SPP creates a pair of virtual COM ports (Outgoing
        and Incoming). If the primary port fails to open (e.g. it is already
        open by another process), this method automatically attempts the alternate
        STASYS SPP port before giving up.

        Args:
            port: COM port override. Falls back to auto-discovery if omitted.

        Returns:
            True if connection succeeded (ESP32 responded with data), False otherwise.
        """
        target = port or self._port
        if not target:
            # No port given — run auto-discovery
            outgoing, incoming = SerialTransport.find_stasys_ports()
            if outgoing:
                target = outgoing
                logger.info("Auto-discovered STASYS Outgoing port: %s", target)
            elif incoming:
                target = incoming
                logger.info("Auto-discovered STASYS Incoming port (Outgoing not found): %s", target)
            else:
                # No STASYS ports found at all
                available = [p.device for p in serial.tools.list_ports.comports()]
                available_str = ", ".join(available) if available else "none"
                logger.error("No STASYS ports found. Available: %s", available_str)
                return (False, f"No STASYS device found. Available ports: {available_str}")

        with self._lock:
            if self.is_connected:
                logger.warning("Already connected to %s", target)
                return (True, None)

        # Validate the target port actually exists before attempting to open.
        # This catches stale saved-port values (e.g. COM5 from a prior session).
        target_exists = any(
            p.device == target for p in serial.tools.list_ports.comports()
        )
        if not target_exists:
            available = [p.device for p in serial.tools.list_ports.comports()]
            available_str = ", ".join(available) if available else "none"
            logger.error("Target port %s does not exist. Available: %s", target, available)
            return (False, f"Port {target} not found. Available: {available_str}")

        self._status_callback(f"connecting:{target}")

        ser, primary_reason = self._open_port(target)
        if ser is None:
            # Primary port failed — try the other STASYS SPP port.
            outgoing, incoming = SerialTransport.find_stasys_ports()
            # Determine the alternate: whichever port is NOT the target.
            candidates = [p for p in (outgoing, incoming) if p and p != target]
            alternate = candidates[0] if candidates else None
            if alternate:
                logger.info("Primary port %s failed. Trying alternate STASYS port: %s", target, alternate)
                self._status_callback(f"connecting:{alternate}")
                ser, alternate_reason = self._open_port(alternate)
                if ser is not None:
                    target = alternate
                    primary_reason = None
                else:
                    primary_reason = f"{target} failed. {alternate} also failed: {alternate_reason}"
            if ser is None:
                self._status_callback("connection_failed")
                return (False, primary_reason or f"Cannot open {target}.")

        # From here on, ser is a valid serial object. Keep a local reference for
        # cleanup so the read thread doesn't see a partially-set-up connection.
        serial_dev: serial.Serial = ser  # type: ignore[assignment]

        # The read thread starts INSIDE connect() — but the GUI sets _parser
        # AFTER connect() returns (to avoid a race where both threads consume
        # from the queue before _parser is set). The _parser flag signals the
        # read loop whether it's safe to dispatch packets. Until the GUI sets
        # _parser, the read thread buffers data in _receive_queue and the GUI's
        # _packet_reader drains it.
        with self._lock:
            self._serial = serial_dev
            self._port = target
            self._running = True
            self._consecutive_errors = 0
            self._parser: Optional[object] = None  # type: ignore[assignment]

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
        """Open a serial port with retry on Bluetooth handshake timeout.

        On Windows, pyserial's SerialException with winerror 121
        (ERROR_SEM_TIMEOUT) means the RFCOMM channel-establishment handshake
        timed out — the ESP32 BT stack was busy or not yet ready. We retry
        up to BT_HANDSHAKE_RETRIES times with a short delay, which usually
        succeeds on the second or third attempt.

        Returns:
            A (Serial, None) tuple on success, or (None, reason) on failure.
            The reason string is a user-facing diagnostic message.
        """
        # First pass: try once, with retry on ERROR_ACCESS_DENIED (5).
        for attempt in range(2):  # up to 2 attempts
            try:
                ser = serial.Serial(
                    port=port,
                    baudrate=BAUD_RATE,
                    timeout=5.0,          # 5s read timeout — BT SPP needs breathing room
                    write_timeout=5.0,
                )
                # Increase OS buffer sizes to reduce Windows BT SPP batching latency.
                # bthmodem.sys defaults to small buffers, causing ~50-100ms of
                # hardware-level batching on the virtual COM port.
                ser.set_buffer_size(rx_size=8192, tx_size=8192)
                # Settling delay: let the ESP32 BluetoothTask finish its RFCOMM
                # setup before we start sending commands.
                time.sleep(POST_OPEN_SETTLE_SEC)
                return (ser, None)
            except serial.SerialException as e:
                err_str = str(e)
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
                    reason = f"{port} not found — the port may have been removed. Re-pair STASYS if needed."
                elif os_code == 5:
                    reason = f"{port} is in use by another application — close the other app and try again."
                elif os_code == 32:
                    reason = f"{port} is already open by another process — close the other app, then try again."
                elif os_code == 121:
                    # ERROR_SEM_TIMEOUT (121) — RFCOMM handshake timed out. Retry up to
                    # BT_HANDSHAKE_RETRIES times. This is a Windows BT stack timing issue.
                    for bt_attempt in range(1, BT_HANDSHAKE_RETRIES + 1):
                        logger.warning(
                            "BT handshake timeout on %s (winerror 121) — "
                            "the ESP32 may still be initializing. Retrying (%d/%d) in %ds...",
                            port, bt_attempt, BT_HANDSHAKE_RETRIES,
                            BT_HANDSHAKE_RETRY_DELAY_SEC,
                        )
                        time.sleep(BT_HANDSHAKE_RETRY_DELAY_SEC)
                        try:
                            ser = serial.Serial(
                                port=port,
                                baudrate=BAUD_RATE,
                                timeout=5.0,
                                write_timeout=5.0,
                            )
                            ser.set_buffer_size(rx_size=8192, tx_size=8192)
                            time.sleep(POST_OPEN_SETTLE_SEC)
                            return (ser, None)
                        except serial.SerialException as retry_e:
                            retry_os_code = getattr(retry_e, "winerror", None) or getattr(retry_e, "errno", None)
                            if retry_os_code == 121:
                                continue  # try again
                            # Different error on retry — propagate it to outer handler
                            raise retry_e
                    reason = (
                        "Bluetooth connection timed out — ensure STASYS is powered on "
                        "and not connected to another device. Try power-cycling STASYS."
                    )
                elif os_code == 31:
                    # ERROR_GEN_FAILURE (31) — BT radio glitch. Retry up to 3 times.
                    for bt_attempt in range(1, BT_HANDSHAKE_RETRIES + 1):
                        logger.warning(
                            "BT radio glitch on %s (winerror 31) — retrying (%d/%d) in %ds...",
                            port, bt_attempt, BT_HANDSHAKE_RETRIES,
                            BT_HANDSHAKE_RETRY_DELAY_SEC,
                        )
                        time.sleep(BT_HANDSHAKE_RETRY_DELAY_SEC)
                        try:
                            ser = serial.Serial(
                                port=port,
                                baudrate=BAUD_RATE,
                                timeout=5.0,
                                write_timeout=5.0,
                            )
                            ser.set_buffer_size(rx_size=8192, tx_size=8192)
                            time.sleep(POST_OPEN_SETTLE_SEC)
                            return (ser, None)
                        except serial.SerialException as retry_e:
                            retry_os_code = getattr(retry_e, "winerror", None) or getattr(retry_e, "errno", None)
                            if retry_os_code == 31:
                                continue  # try again
                            raise retry_e
                    reason = (
                        f"{port} exists but the device is offline — make sure STASYS "
                        "is powered on and in Bluetooth range."
                    )
                else:
                    reason = f"Could not open {port}: {err_str}{os_msg}."

                if os_code == 2:
                    logger.error("Port %s not found%s", port, os_msg)
                elif os_code == 32 or os_code == 267:
                    logger.error("Port %s is already open by another process%s", port, os_msg)
                elif os_code == 31 or os_code == 121:
                    logger.error("BT handshake failed on %s after %d attempts%s", port, BT_HANDSHAKE_RETRIES, os_msg)
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
                os_code: Optional[int] = getattr(e, "winerror", None) or getattr(e, "errno", None)
                self._consecutive_errors += 1
                logger.warning("OS error %d during read: %s", os_code, e)
                if os_code in (121, 31):
                    # BT handshake / radio glitch — brief delay then reconnect
                    time.sleep(1.0)
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
        """Split incoming bytes: XON/XOFF to flow control, rest to queue.

        When _parser is None (GUI hasn't set it up yet), data is still placed in
        the receive queue. The GUI's _packet_reader drains it and feeds the parser
        once _parser is set.
        """
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