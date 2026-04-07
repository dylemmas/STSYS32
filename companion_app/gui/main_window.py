"""STASYS main window — top bar, tab widget, and data routing."""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

import pyqtgraph as pg
from PyQt6.QtCore import QMetaObject, Qt, QTimer, QUrl, Q_ARG, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.theme import (
    ACCENT,
    BG3,
    DARK_QSS,
    FONT_MONO,
    FONT_SIZE_LOGO,
    FONT_SIZE_MD,
    FONT_SIZE_SM,
    FG,
    FG_DIM,
)
from gui.widgets.status_bar import StatusBar
from stasys.core.imu_calibrator import IMUCalibrator
from stasys.protocol.commands import cmd_get_info, cmd_start_session, cmd_stop_session
from stasys.protocol.flow_control import FlowControl
from PyQt6.QtWidgets import QCheckBox
from stasys.protocol.parser import ProtocolParser
from stasys.protocol.packets import (
    DataRawSample,
    EvtSensorHealth,
    EvtSessionStarted,
    EvtSessionStopped,
    EvtShotDetected,
    PacketType,
    RspInfo,
)
from stasys.transport.serial_transport import SerialTransport

# Configure pyqtgraph globally
pg.setConfigOptions(antialias=True, background="#0d0d0d", foreground="#e0e0e0")


# ─────────────────────────────────────────────────────────────────────────────
# Signal router — thread-safe bridge between serial thread and Qt GUI
# ─────────────────────────────────────────────────────────────────────────────

class DataRouter(QObject):
    """Thread-safe packet router using Qt signals."""

    sample_received = pyqtSignal(object)   # DataRawSample
    shot_received = pyqtSignal(object)     # EvtShotDetected
    health_received = pyqtSignal(object)  # EvtSensorHealth
    session_started = pyqtSignal(object)   # EvtSessionStarted
    session_stopped = pyqtSignal(object)  # EvtSessionStopped
    info_received = pyqtSignal(object)    # RspInfo
    connection_changed = pyqtSignal(bool)  # connected bool
    calibrating = pyqtSignal(bool)          # True = calibrating, False = done
    calibration_progress = pyqtSignal(float)  # 0.0-1.0 progress
    session_start_failed = pyqtSignal()    # timeout — no EVT_SESSION_STARTED received


# ─────────────────────────────────────────────────────────────────────────────
# Top bar widget
# ─────────────────────────────────────────────────────────────────────────────

class TopBar(QWidget):
    """Persistent top bar with logo, status, and controls."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setStyleSheet(f"background: {BG3}; border-bottom: 1px solid #222;")
        self._connected = False
        self._battery = 0
        self._shot_count = 0
        self._avg_score: float | None = None
        self._firmware_version: str = ""
        self._port: str = ""
        self._port_input: QLineEdit | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(16)

        # Logo
        self._logo = QLabel("STASYS")
        self._logo.setObjectName("logo")
        self._logo.setStyleSheet(
            f"color: {ACCENT}; font-size: {FONT_SIZE_LOGO}px; font-weight: bold; "
            f"font-family: {FONT_MONO}; background: transparent;"
        )
        layout.addWidget(self._logo)

        # Version label
        self._ver_label = QLabel("v2.0")
        self._ver_label.setStyleSheet(
            f"color: {FG_DIM}; font-family: {FONT_MONO}; font-size: {FONT_SIZE_SM}px; "
            f"background: transparent; padding-top: 4px;"
        )
        layout.addWidget(self._ver_label)

        layout.addSpacing(20)

        # Connection badge
        self._badge = QLabel("DISCONNECTED")
        self._badge.setObjectName("badge_disconnected")
        self._badge.setStyleSheet(
            "background: #2a2a2a; color: #888888; padding: 4px 12px; "
            "border-radius: 3px; font-size: 10px; letter-spacing: 1px; "
            f"font-family: {FONT_MONO}; text-transform: uppercase;"
        )
        layout.addWidget(self._badge)

        layout.addSpacing(12)

        # Battery indicator
        self._battery_label = QLabel("🔋 —")
        self._battery_label.setStyleSheet(
            f"font-size: {FONT_SIZE_MD}px; color: {FG_DIM}; "
            f"font-family: {FONT_MONO}; background: transparent;"
        )
        layout.addWidget(self._battery_label)

        layout.addSpacing(12)

        # Shots / Avg
        self._shots_label = QLabel("Shots: 0  Avg: —")
        self._shots_label.setStyleSheet(
            f"color: {FG}; font-family: {FONT_MONO}; font-size: {FONT_SIZE_SM}px; "
            f"background: transparent;"
        )
        layout.addWidget(self._shots_label)

        layout.addStretch()

        # COM port input
        self._port_input = QLineEdit()
        self._port_input.setPlaceholderText("COM5")
        self._port_input.setMaximumWidth(100)
        self._port_input.setStyleSheet(
            f"background: #1a1a1a; color: {FG}; border: 1px solid #444; "
            f"border-radius: 3px; padding: 5px 8px; font-family: {FONT_MONO}; "
            f"font-size: {FONT_SIZE_SM}px;"
        )
        layout.addWidget(self._port_input)

        # Connect button
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setObjectName("connect")
        self._connect_btn.setStyleSheet(
            f"background: {BG3}; color: {ACCENT}; border: 1px solid {ACCENT}; "
            f"border-radius: 3px; padding: 5px 14px; font-family: {FONT_MONO}; "
            f"font-size: {FONT_SIZE_SM}px; letter-spacing: 1px; text-transform: uppercase;"
        )
        layout.addWidget(self._connect_btn)

        # Disconnect button
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.setStyleSheet(
            f"background: {BG3}; color: {FG_DIM}; border: 1px solid #444; "
            f"border-radius: 3px; padding: 5px 14px; font-family: {FONT_MONO}; "
            f"font-size: {FONT_SIZE_SM}px; letter-spacing: 1px; text-transform: uppercase;"
        )
        layout.addWidget(self._disconnect_btn)

        # Re-zero button
        self._rezero_btn = QPushButton("Re-zero")
        self._rezero_btn.setEnabled(False)
        self._rezero_btn.setStyleSheet(
            f"background: {BG3}; color: {FG_DIM}; border: 1px solid #444; "
            f"border-radius: 3px; padding: 5px 14px; font-family: {FONT_MONO}; "
            f"font-size: {FONT_SIZE_SM}px; letter-spacing: 1px; text-transform: uppercase;"
        )
        layout.addWidget(self._rezero_btn)

        # New Session button
        self._session_btn = QPushButton("New Session")
        self._session_btn.setEnabled(False)
        self._session_btn.setStyleSheet(
            f"background: {ACCENT}; color: #0d0d0d; border: 1px solid {ACCENT}; "
            f"border-radius: 3px; padding: 5px 14px; font-family: {FONT_MONO}; "
            f"font-size: {FONT_SIZE_SM}px; font-weight: bold; letter-spacing: 1px; "
            f"text-transform: uppercase;"
        )
        layout.addWidget(self._session_btn)

        # Auto-start session on connect
        self._auto_start_cb = QCheckBox("Auto-start")
        self._auto_start_cb.setToolTip("Automatically start a recording session when connected")
        self._auto_start_cb.setStyleSheet(
            f"color: {FG_DIM}; font-family: {FONT_MONO}; font-size: {FONT_SIZE_SM}px; "
            f"background: transparent;"
        )
        layout.addWidget(self._auto_start_cb)

    def set_connected(self, port: str, version: str) -> None:
        self._connected = True
        self._port = port
        self._firmware_version = version
        self._badge.setText("CONNECTED")
        self._badge.setStyleSheet(
            f"background: #1a3a2a; color: {ACCENT}; padding: 4px 12px; "
            "border-radius: 3px; font-size: 10px; letter-spacing: 1px; "
            f"font-family: {FONT_MONO}; text-transform: uppercase;"
        )
        self._connect_btn.setEnabled(False)
        if self._port_input:
            self._port_input.setEnabled(False)
        self._disconnect_btn.setEnabled(True)
        self._rezero_btn.setEnabled(True)
        self._session_btn.setEnabled(True)

    def set_disconnected(self) -> None:
        self._connected = False
        self._badge.setText("DISCONNECTED")
        self._badge.setStyleSheet(
            "background: #2a2a2a; color: #888888; padding: 4px 12px; "
            "border-radius: 3px; font-size: 10px; letter-spacing: 1px; "
            f"font-family: {FONT_MONO}; text-transform: uppercase;"
        )
        self._connect_btn.setEnabled(True)
        if self._port_input:
            self._port_input.setEnabled(True)
            self._port_input.clear()
        self._disconnect_btn.setEnabled(False)
        self._rezero_btn.setEnabled(False)
        self._session_btn.setEnabled(False)
        self._battery_label.setText("🔋 —")
        self._shots_label.setText("Shots: 0  Avg: —")
        self._session_btn.setText("New Session")
        self._session_btn.setStyleSheet(
            f"background: {ACCENT}; color: #0d0d0d; border: 1px solid {ACCENT}; "
            f"border-radius: 3px; padding: 5px 14px; font-family: {FONT_MONO}; "
            f"font-size: {FONT_SIZE_SM}px; font-weight: bold; letter-spacing: 1px; "
            f"text-transform: uppercase;"
        )

    def update_battery(self, percent: int) -> None:
        self._battery = percent
        icons = ["🔋", "🪫"]
        icon = icons[0]
        self._battery_label.setText(f"{icon} {percent}%")
        color = "#00ff88" if percent > 20 else "#ff6600" if percent > 10 else "#ff3333"
        self._battery_label.setStyleSheet(
            f"font-size: {FONT_SIZE_MD}px; color: {color}; "
            f"font-family: {FONT_MONO}; background: transparent;"
        )

    def update_shots(self, count: int, avg_score: float | None) -> None:
        self._shot_count = count
        self._avg_score = avg_score
        if avg_score is not None:
            self._shots_label.setText(f"Shots: {count}  Avg: {avg_score:.0f}")
        else:
            self._shots_label.setText(f"Shots: {count}  Avg: —")

    def set_session_active(self, active: bool) -> None:
        self._session_btn.setEnabled(True)
        if active:
            self._session_btn.setText("Stop Session")
            self._session_btn.setStyleSheet(
                f"background: #ff3333; color: #fff; border: 1px solid #ff3333; "
                f"border-radius: 3px; padding: 5px 14px; font-family: {FONT_MONO}; "
                f"font-size: {FONT_SIZE_SM}px; font-weight: bold; letter-spacing: 1px; "
                f"text-transform: uppercase;"
            )
        else:
            self._session_btn.setText("New Session")
            self._session_btn.setStyleSheet(
                f"background: {ACCENT}; color: #0d0d0d; border: 1px solid {ACCENT}; "
                f"border-radius: 3px; padding: 5px 14px; font-family: {FONT_MONO}; "
                f"font-size: {FONT_SIZE_SM}px; font-weight: bold; letter-spacing: 1px; "
                f"text-transform: uppercase;"
            )

    def set_session_starting(self, starting: bool) -> None:
        """Show pending 'Starting...' state while waiting for EVT_SESSION_STARTED."""
        if starting:
            self._session_btn.setText("Starting...")
            self._session_btn.setEnabled(False)
            self._session_btn.setStyleSheet(
                f"background: #3a3a1a; color: #aaaaaa; border: 1px solid #666; "
                f"border-radius: 3px; padding: 5px 14px; font-family: {FONT_MONO}; "
                f"font-size: {FONT_SIZE_SM}px; font-weight: bold; letter-spacing: 1px; "
                f"text-transform: uppercase;"
            )
        else:
            # Revert to idle "New Session" button
            self._session_btn.setText("New Session")
            self._session_btn.setEnabled(self._connected)
            self._session_btn.setStyleSheet(
                f"background: {ACCENT}; color: #0d0d0d; border: 1px solid {ACCENT}; "
                f"border-radius: 3px; padding: 5px 14px; font-family: {FONT_MONO}; "
                f"font-size: {FONT_SIZE_SM}px; font-weight: bold; letter-spacing: 1px; "
                f"text-transform: uppercase;"
            )

    @property
    def port_input(self) -> QLineEdit:
        return self._port_input

    @property
    def connect_button(self) -> QPushButton:
        return self._connect_btn

    @property
    def disconnect_button(self) -> QPushButton:
        return self._disconnect_btn

    @property
    def rezero_button(self) -> QPushButton:
        return self._rezero_btn

    @property
    def session_button(self) -> QPushButton:
        return self._session_btn

    @property
    def auto_start_checkbox(self) -> QCheckBox:
        return self._auto_start_cb


# ─────────────────────────────────────────────────────────────────────────────
# Main Window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """STASYS main window — hosts the top bar, tab widget, and data routing."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("STASYS - Shooting Athlete Training System")
        self.setMinimumSize(1280, 800)
        self.resize(1400, 900)
        self._logger = logging.getLogger(__name__)

        self._transport: SerialTransport | None = None
        self._flow: FlowControl | None = None
        self._parser: ProtocolParser | None = None
        self._router = DataRouter()
        self._packet_thread: threading.Thread | None = None
        self._running = False
        self._session_active = False
        self._session_pending = False        # True while waiting for EVT_SESSION_STARTED
        self._auto_start_session = False   # set True to auto-start session after connect
        self._current_firmware_session_id: int = 0
        self._shot_scores: list[float] = []
        self._current_session_db_id: int | None = None
        self._session_timeout_timer = QTimer()
        self._session_timeout_timer.setSingleShot(True)
        self._session_timeout_timer.timeout.connect(self._on_session_start_failed)

        # IMU software calibration — triggered on New Session click
        self._calibrator = IMUCalibrator()
        self._calibration_mode = False  # True = collecting calibration samples

        # App settings (local-only, no firmware sync for MVP)
        self._settings = {
            "fire_mode": "Live Fire",
            "weapon_type": "Pistol",
            "jerk_threshold": 5.0,
            "usb_direction": "Forward",
            "calibrated": False,
            "calibrating": False,
        }
        # Re-zero reference (gyro integration baseline)
        self._zero_angle_x: float = 0.0
        self._zero_angle_y: float = 0.0
        self._zero_set: bool = False

        self._build_ui()
        self._connect_signals()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        vlayout = QVBoxLayout(central)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(0)

        # Top bar
        self._top_bar = TopBar()
        vlayout.addWidget(self._top_bar)

        # Tab widget
        self._tab_widget = QTabWidget()
        self._tab_widget.setStyleSheet(DARK_QSS)

        # Lazy import tabs to avoid circular imports
        from gui.tab_live import LiveTab
        from gui.tab_shot_detail import ShotDetailTab
        from gui.tab_analysis import AnalysisTab
        from gui.tab_history import HistoryTab
        from gui.tab_settings import SettingsTab

        self._tab_live = LiveTab(self._router, self)
        self._tab_shot_detail = ShotDetailTab(self._router, self)
        self._tab_analysis = AnalysisTab(self._router, self)
        self._tab_history = HistoryTab(self._router, self)
        self._tab_settings = SettingsTab(self._router, self)

        self._tab_widget.addTab(self._tab_live, "LIVE")
        self._tab_widget.addTab(self._tab_shot_detail, "SHOT DETAIL")
        self._tab_widget.addTab(self._tab_analysis, "ANALYSIS")
        self._tab_widget.addTab(self._tab_history, "HISTORY")
        self._tab_widget.addTab(self._tab_settings, "SETTINGS")

        vlayout.addWidget(self._tab_widget, 1)

        # Status bar
        self._status_bar = StatusBar()
        vlayout.addWidget(self._status_bar)

        self.setCentralWidget(central)

    def _connect_signals(self) -> None:
        tb = self._top_bar
        tb.connect_button.clicked.connect(lambda _: self._on_connect())
        tb.port_input.returnPressed.connect(lambda: self._on_connect())
        tb.disconnect_button.clicked.connect(self._on_disconnect)
        tb.rezero_button.clicked.connect(self._on_rezero)
        tb.session_button.clicked.connect(self._on_session_toggle)

        # Router → top bar updates
        self._router.health_received.connect(self._on_health)
        self._router.session_started.connect(self._on_session_started)
        self._router.session_stopped.connect(self._on_session_stopped)
        self._router.session_start_failed.connect(self._on_session_start_failed)
        self._router.calibrating.connect(self._on_calibrating_done)
        self._router.info_received.connect(self._on_info)
        self._router.connection_changed.connect(self._on_connection_changed)

    # ── Connection ────────────────────────────────────────────────────────────

    def _on_connect(self) -> None:
        """Connect to the manually entered COM port."""
        port = self._top_bar.port_input.text().strip()
        if not port:
            self._status_bar.set_status("Please enter a COM port (e.g., COM5)", "warning")
            return
        self._connect_to_port(port)

    def _connect_to_port(self, port: str) -> None:
        self._status_bar.set_status(f"Connecting to {port}...")
        self._top_bar._connect_btn.setEnabled(False)
        self._top_bar.port_input.setEnabled(False)
        try:
            self._transport = SerialTransport(
                port=port,
                status_callback=self._on_transport_status,
                flow_control=None,  # FlowControl set up below after transport exists
            )
            success, reason = self._transport.connect(port)
            if not success:
                # reason carries a diagnostic message from the transport layer.
                msg = reason or f"Cannot open {port} — is the STASYS device powered on and paired?"
                self._status_bar.set_status(msg, "error")
                self._top_bar._connect_btn.setEnabled(True)
                self._top_bar.port_input.setEnabled(True)
                return
            # FlowControl: write_callback → transport.write; read-path XON/XOFF
            # interception is done inside SerialTransport._dispatch_read.
            self._flow = FlowControl(write_callback=self._transport.write)
            self._transport._flow_control = self._flow
        except Exception as e:
            self._status_bar.set_status(
                f"Connection error: {e}", "error",
            )
            self._top_bar._connect_btn.setEnabled(True)
            self._top_bar.port_input.setEnabled(True)
            return

        self._running = True
        self._parser = ProtocolParser(packet_callback=self._on_packet)
        self._packet_thread = threading.Thread(target=self._packet_reader, daemon=True)
        self._packet_thread.start()

        # Request device info — the ESP32 only responds to commands, so we
        # must NOT wait here. Instead, start a timeout. If RSP_INFO doesn't
        # arrive within 5s, the ESP32 is offline even though the port opened.
        #
        # The reader thread handles draining stale ESP32 data automatically.
        # We simply send the command and wait for RSP_INFO (with a 5s timeout).
        self._send_raw(cmd_get_info())
        self._connection_timeout_timer = QTimer()
        self._connection_timeout_timer.setSingleShot(True)
        self._connection_timeout_timer.timeout.connect(self._on_connection_failed)
        self._connection_timeout_timer.start(5000)  # 5-second connection verification timeout

    def _drain_stale_then_wait_for_live(self) -> None:
        """Drain stale ESP32 output from the receive queue, then wait for fresh data.

        ESP32 sometimes sends a partial "AA" byte before the full "55 ..." packet
        (separate serial reads). This is stale data from the previous session and
        must be discarded. We drain with short timeouts to let all serial buffering
        complete, then re-inject any complete frames (starting with 0xAA 0x55)
        so the reader can process them normally.
        """
        from stasys.protocol.parser import ProtocolParser
        stale_chunks = 0
        live_bytes = b""
        wait_count = 0
        max_waits = 3  # ~3 seconds max to collect live data

        # Drain all currently queued stale data (from before we were ready).
        # Use short timeouts so we don't block the GUI thread.
        while self._running:
            try:
                self._transport.read_queue.get(timeout=0.05)
                stale_chunks += 1
            except queue.Empty:
                break

        if stale_chunks > 0:
            self._logger.debug("Drained %d stale chunk(s) from receive queue", stale_chunks)

        # Wait for the ESP32's response to CMD_GET_INFO to arrive.
        # The SerialTransport read thread puts data in the queue; we consume it here.
        # Bound the wait so we don't block forever if ESP32 doesn't respond.
        while self._running and wait_count < max_waits:
            try:
                chunk = self._transport.read_queue.get(timeout=1.0)
                live_bytes += chunk
                break  # got data — done waiting
            except queue.Empty:
                wait_count += 1

        if live_bytes:
            # Re-inject complete 0xAA 0x55 frames so the reader processes them.
            # Discard incomplete "AA" prefix if the frame was split.
            idx = live_bytes.find(b"\xaa\x55")
            if idx >= 0:
                complete = live_bytes[idx:]
                self._logger.debug(
                    "Re-injecting %d bytes of stale-complete frame: %s",
                    len(complete), complete.hex(),
                )
                self._transport.read_queue.put(complete)
            else:
                self._logger.debug(
                    "Stale response had no complete frame, discarding %d bytes: %s",
                    len(live_bytes), live_bytes.hex(),
                )

    def _packet_reader(self) -> None:
        """Background thread: read from transport queue and feed parser.

        On startup, drains any stale bytes from a previous session that arrived
        before the parser was ready. ESP32 sometimes sends a partial "AA" byte
        before the full "55 81..." packet (separate serial reads). These are
        discarded. Any complete frames (0xAA 0x55 ...) found in stale data are
        re-injected so the reader can process them normally.
        """
        # Drain stale ESP32 output that arrived before we were ready.
        # Use a short timeout per drain iteration so we don't block indefinitely.
        stale_chunks = 0
        stale_complete_frames = b""
        while self._running:
            try:
                chunk = self._transport.read_queue.get(timeout=0.05)
                stale_chunks += 1
                # Accumulate complete 0xAA 0x55 frames for re-injection.
                # If the frame is split ("AA" + "55 81..."), accumulate both.
                stale_complete_frames += chunk
            except queue.Empty:
                break

        if stale_chunks > 0:
            # Check if we accumulated a complete frame in the stale data.
            idx = stale_complete_frames.find(b"\xaa\x55")
            if idx >= 0:
                complete = stale_complete_frames[idx:]
                self._logger.debug(
                    "Re-injecting %d-byte complete frame from stale drain: %s",
                    len(complete), complete.hex(),
                )
                self._transport.read_queue.put(complete)
            else:
                self._logger.debug(
                    "Discarded %d stale byte chunk(s) (no complete frame)", stale_chunks,
                )

        # Normal: read live data and feed the parser.
        while self._running:
            try:
                data = self._transport.read_queue.get(timeout=0.5)
                if self._parser:
                    self._parser.feed(data)
            except queue.Empty:
                continue
            except Exception:
                self._logger.exception("Packet reader thread crashed — restart needed")
                break

    def _send_raw(self, data: bytes) -> None:
        if self._flow is not None:
            self._flow.write(data)
        elif self._transport and self._transport.is_connected:
            self._transport.write(data)

    def _on_packet(self, packet: object) -> None:
        """Route packet to GUI via signals (thread-safe)."""
        if isinstance(packet, DataRawSample):
            # Feed to calibrator if in calibration mode
            if self._calibration_mode:
                self._calibrator.feed(
                    gyro_x=packet.gyro_x,
                    gyro_y=packet.gyro_y,
                    gyro_z=packet.gyro_z,
                    accel_x=packet.accel_x,
                    accel_y=packet.accel_y,
                    accel_z=packet.accel_z,
                )
                progress = self._calibrator.progress
                self._router.calibration_progress.emit(progress)
                if self._calibrator.is_calibrated:
                    # Calibration complete — switch to normal plotting
                    self._calibration_mode = False
                    self._router.calibrating.emit(False)
                    self._logger.info(
                        "IMU calibration complete: gyro_bias=(%.2f, %.2f, %.2f) deg/s  "
                        "accel_bias=(%.1f, %.1f, %.1f) raw",
                        self._calibrator.bias.gyro_x,
                        self._calibrator.bias.gyro_y,
                        self._calibrator.bias.gyro_z,
                        self._calibrator.bias.accel_x,
                        self._calibrator.bias.accel_y,
                        self._calibrator.bias.accel_z,
                    )
            self._router.sample_received.emit(packet)
        elif isinstance(packet, EvtShotDetected):
            self._router.shot_received.emit(packet)
        elif isinstance(packet, EvtSensorHealth):
            self._router.health_received.emit(packet)
            # Check for firmware-side degraded mode (sensor permanently failed).
            # degraded_flag: 0=healthy, 1=degraded (MPU failed), 2=recovery in progress.
            if packet.degraded_flag == 1:
                self._logger.warning(
                    "ESP32 sensor permanently degraded (MPU6050 I2C failure). "
                    "Raw data stream suppressed. Check physical connection."
                )
        elif isinstance(packet, EvtSessionStarted):
            # Session started — begin calibration mode
            self._router.session_started.emit(packet)
            self._calibration_mode = True
            self._calibrator.reset()
            self._router.calibrating.emit(True)
        elif isinstance(packet, EvtSessionStopped):
            self._router.session_stopped.emit(packet)
        elif isinstance(packet, RspInfo):
            self._router.info_received.emit(packet)

    def _on_transport_status(self, status: str) -> None:
        if status == "disconnected":
            self._on_disconnect()
        elif status == "connected":
            self._router.connection_changed.emit(True)

    def _on_connection_changed(self, connected: bool) -> None:
        if not connected:
            self._on_disconnect()

    def _on_disconnect(self) -> None:
        self._running = False
        self._session_pending = False
        self._session_timeout_timer.stop()
        if hasattr(self, "_connection_timeout_timer"):
            self._connection_timeout_timer.stop()
        if self._transport:
            self._transport.disconnect()
            self._transport = None
        self._flow = None
        self._parser = None
        self._session_active = False
        self._top_bar.set_disconnected()
        self._top_bar.set_session_active(False)
        self._status_bar.set_status("Disconnected")
        self._tab_live.on_disconnect()
        self._tab_shot_detail.on_disconnect()
        self._tab_analysis.on_disconnect()

    def _on_info(self, info: RspInfo) -> None:
        # Cancel connection verification timeout — ESP32 is confirmed connected.
        if hasattr(self, "_connection_timeout_timer"):
            self._connection_timeout_timer.stop()
        port = self._transport._port if self._transport else "?"
        version = info.firmware_version_str
        self._top_bar.set_connected(port, version)
        self._status_bar.set_firmware_info(version, port)
        self._status_bar.set_status(f"Connected to {port} | {version}", "success")
        # Auto-start session if checkbox is checked
        if self._top_bar.auto_start_checkbox.isChecked():
            self._on_session_toggle()

    # ── Session ────────────────────────────────────────────────────────────────

    def _on_session_toggle(self) -> None:
        if self._session_active:
            # Stop is always safe — cancel any pending start timeout
            self._session_pending = False
            self._session_timeout_timer.stop()
            self._send_raw(cmd_stop_session())
        elif self._session_pending:
            # Already waiting for EVT_SESSION_STARTED — ignore
            return
        else:
            # Start: guard against double-click and show pending state
            self._session_pending = True
            self._top_bar.set_session_starting(True)
            self._session_timeout_timer.start(5000)  # 5-second timeout (bumped from 3s for Windows BT SPP batching)
            raw = cmd_start_session()
            self._logger.info(
                "TX CMD_START_SESSION: %s  (expecting EVT_SESSION_STARTED reply within 5s)",
                " ".join(f"{b:02X}" for b in raw),
            )
            self._send_raw(raw)
            self._shot_scores.clear()
            self._top_bar.update_shots(0, None)

    def _on_session_started(self, evt: EvtSessionStarted) -> None:
        # Cancel any pending timeout — we got our response
        self._session_pending = False
        self._session_timeout_timer.stop()
        self._session_active = True
        self._current_firmware_session_id = evt.session_id
        self._top_bar.set_session_active(True)
        self._logger.info(
            "Session started: id=%u batt=%d%% health=0x%02X heap=%u",
            evt.session_id, evt.battery_percent, evt.sensor_health, evt.free_heap,
        )
        self._status_bar.set_status("Session active — recording data...", "success")

        # Open DB session
        from stasys.storage.session_store import SessionStore
        self._session_store = SessionStore()
        self._current_session_db_id = self._session_store.open_session(
            firmware_session_id=evt.session_id,
            battery_start=evt.battery_percent,
        )

    def _on_connection_failed(self) -> None:
        """Called when RSP_INFO is not received within 5s of connecting.

        This means the COM port opened successfully but the ESP32 is not
        responding — it is either offline or out of BT range.
        """
        self._logger.warning(
            "Connection verification timed out — COM port opened but ESP32 "
            "is not responding. The device may be offline or out of BT range."
        )
        self._status_bar.set_status(
            "COM port opened but ESP32 is not responding — "
            "make sure STASYS is powered on and in BT range.", "error",
        )
        self._on_disconnect()

    def _on_session_start_failed(self) -> None:
        """Called when 5-second timeout fires with no EVT_SESSION_STARTED."""
        self._session_pending = False
        self._top_bar.set_session_starting(False)   # revert button
        self._top_bar.set_session_active(False)
        self._logger.warning(
            "Session start timed out — no EVT_SESSION_STARTED received in 5s. "
            "Check ESP32 serial output for [BT] handleStartSession and sendPacket: type=0x10. "
            "Check parser debug logs for 0x10 packet arrival and CRC validation."
        )
        self._status_bar.set_status(
            "Session start timed out — is the device still connected?", "error",
        )

    def _on_session_stopped(self, evt: EvtSessionStopped) -> None:
        # Clear pending in case a stale response arrives
        self._session_pending = False
        self._session_timeout_timer.stop()
        self._session_active = False
        self._top_bar.set_session_active(False)
        if self._current_session_db_id:
            self._session_store.update_shot_count(self._current_session_db_id, evt.shot_count)
            self._session_store.update_battery_end(self._current_session_db_id, evt.battery_end)
            self._session_store.close_session(self._current_session_db_id)
            self._current_session_db_id = None
        avg = sum(self._shot_scores) / len(self._shot_scores) if self._shot_scores else None
        self._status_bar.set_status(
            f"Session stopped — {evt.shot_count} shots recorded", "info"
        )

    def _on_health(self, health: EvtSensorHealth) -> None:
        pass  # Sub-classes can override

    def _on_shot(self, shot: EvtShotDetected) -> None:
        """Called when a shot is received — compute score and update."""
        # Compute score: based on displacement from gyro peak
        gyro_x = shot.gyro_x_peak / 65.5
        gyro_y = shot.gyro_y_peak / 65.5
        displacement = (gyro_x ** 2 + gyro_y ** 2) ** 0.5
        # Base score: 100 at 0 deg, 0 at 5 deg
        base = max(0.0, 100.0 - (displacement / 5.0) * 100.0)
        score = min(100.0, base)
        self._shot_scores.append(score)
        self._top_bar.update_shots(len(self._shot_scores), sum(self._shot_scores) / len(self._shot_scores))

        # Save to DB
        if self._current_session_db_id is not None:
            try:
                self._session_store.record_shot(self._current_session_db_id, shot)
            except Exception:
                pass

    def _on_rezero(self) -> None:
        """Re-zero the IMU baseline from current gyro readings."""
        self._zero_angle_x = self._tab_live.current_angle_x
        self._zero_angle_y = self._tab_live.current_angle_y
        self._zero_set = True
        self._tab_live.on_rezero()
        self._tab_shot_detail.on_rezero()
        self._settings["calibrated"] = True
        self._status_bar.set_status("IMU re-zeroed at current position", "success")

    def _on_calibrating_done(self, calibrating: bool) -> None:
        """Called when calibration completes (calibrating=False)."""
        if not calibrating and self._session_active:
            self._status_bar.set_status("Calibration complete — recording data...", "success")

    # ── Settings access ───────────────────────────────────────────────────────

    def get_settings(self) -> dict:
        return self._settings

    def get_zero_offset(self) -> tuple[float, float]:
        if self._zero_set:
            return (self._zero_angle_x, self._zero_angle_y)
        return (0.0, 0.0)

    def is_zero_set(self) -> bool:
        return self._zero_set

    def get_calibrator(self) -> IMUCalibrator:
        """Return the IMU calibrator for use in tabs."""
        return self._calibrator

    def is_calibration_mode(self) -> bool:
        """True while calibration is in progress."""
        return self._calibration_mode

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._running = False
        if self._transport:
            self._transport.disconnect()
        event.accept()
