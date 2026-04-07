"""LIVE tab — real-time trace plot and steadiness stats."""

from __future__ import annotations

from collections import deque

import pyqtgraph as pg
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.main_window import DataRouter, MainWindow
from gui.theme import (
    BG2,
    BG3,
    FG_DIM,
    ORANGE,
)
from gui.widgets.score_gauge import ScoreGauge
from stasys.protocol.packets import DataRawSample, EvtShotDetected


class _CalibrationOverlay(QWidget):
    """Full-panel overlay shown during IMU calibration."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        # Cover the entire parent
        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(
            "background: rgba(13, 13, 13, 0.88); border-radius: 4px;"
        )
        self.hide()

        vlayout = QVBoxLayout(self)
        vlayout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vlayout.setSpacing(12)

        # Icon
        icon_lbl = QLabel("◉")
        icon_lbl.setStyleSheet(
            "color: #00ff88; font-size: 48px; background: transparent;"
        )
        vlayout.addWidget(icon_lbl)

        # Title
        title = QLabel("CALIBRATING")
        title.setStyleSheet(
            "color: #00ff88; font-family: 'JetBrains Mono', monospace; "
            "font-size: 22px; font-weight: bold; letter-spacing: 4px; "
            "background: transparent;"
        )
        vlayout.addWidget(title)

        # Instruction
        self._instruction = QLabel("Keep the device still...")
        self._instruction.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 13px; background: transparent;"
        )
        vlayout.addWidget(self._instruction)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1000)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedWidth(300)
        self._progress_bar.setFormat("")
        self._progress_bar.setStyleSheet(
            "QProgressBar { background: #2a2a2a; border: none; border-radius: 4px; "
            "height: 10px; } "
            "QProgressBar::chunk { background: #00ff88; border-radius: 4px; }"
        )
        vlayout.addWidget(self._progress_bar)

        # Progress label
        self._progress_label = QLabel("0 / 500 samples")
        self._progress_label.setStyleSheet(
            "color: #555; font-family: 'JetBrains Mono', monospace; "
            "font-size: 11px; background: transparent;"
        )
        vlayout.addWidget(self._progress_label)

        vlayout.addSpacing(20)

        # Static indicator
        self._static_indicator = QLabel("◎ Checking motion...")
        self._static_indicator.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 12px; background: transparent;"
        )
        vlayout.addWidget(self._static_indicator)

        # Skip button
        self._skip_btn = QPushButton("Skip Calibration")
        self._skip_btn.setStyleSheet(
            "background: transparent; color: #555; border: 1px solid #333; "
            "border-radius: 3px; padding: 6px 20px; font-family: 'JetBrains Mono', monospace; "
            "font-size: 11px; letter-spacing: 1px;"
        )
        vlayout.addWidget(self._skip_btn)

    def set_progress(self, progress: float, count: int, total: int) -> None:
        self._progress_bar.setValue(int(progress * 1000))
        self._progress_label.setText(f"{count} / {total} samples")

    def set_static_status(self, is_static: bool) -> None:
        if is_static:
            self._static_indicator.setText("◉ Device is steady")
            self._static_indicator.setStyleSheet(
                "color: #00ff88; font-family: 'JetBrains Mono', monospace; "
                "font-size: 12px; background: transparent;"
            )
            self._instruction.setText("Keep the device still...")
        else:
            self._static_indicator.setText("◎ Motion detected — hold steady")
            self._static_indicator.setStyleSheet(
                "color: #ff6600; font-family: 'JetBrains Mono', monospace; "
                "font-size: 12px; background: transparent;"
            )
            self._instruction.setText("⚠ Motion detected! Hold still...")

    def show_calibrating(self, show: bool) -> None:
        if show:
            # Size overlay to match PlotWidget before showing
            pw = self.parent().findChild(pg.PlotWidget) if self.parent() else None
            if pw:
                self.setGeometry(pw.rect())
            else:
                self.setGeometry(self.parent().rect() if self.parent() else self.rect())
            self.show()
            self.raise_()
        else:
            self.hide()


class LiveTab(QWidget):
    """Live monitoring tab with real-time trace and stats."""

    TRAIL_LENGTH = 500            # rolling window: ~5s at 100Hz
    TRAIL_SEGMENTS = 10           # number of fading segments
    WOBBLE_WINDOW = 20            # samples for RMS calculation

    def __init__(self, router: DataRouter, main_window: MainWindow) -> None:
        super().__init__()
        self._router = router
        self._mw = main_window

        # Trace state (protected by GIL since only main thread writes)
        self._angle_x = 0.0
        self._angle_y = 0.0
        self.current_angle_x = 0.0
        self.current_angle_y = 0.0
        self._trace_x: deque[float] = deque(maxlen=self.TRAIL_LENGTH)
        self._trace_y: deque[float] = deque(maxlen=self.TRAIL_LENGTH)
        self._timestamps: deque[float] = deque(maxlen=self.TRAIL_LENGTH)

        # Gyro bias: captured on first sample / re-zero
        self._gyro_bias_x = 0.0
        self._gyro_bias_y = 0.0
        self._bias_captured = False

        # Previous timestamp for dt calculation
        self._prev_timestamp_us: int | None = None

        # Steadiness state
        self._hold_time = 0.0
        self._wobble_rms = 0.0
        self._npa_deviation = 0.0
        self._wobble_window: deque[float] = deque(maxlen=self.WOBBLE_WINDOW)
        self._is_stable = True
        self._last_stable_check = 0.0
        self._hold_start_time: float = 0.0
        self._stable_since: float = 0.0
        self._jerk_mag = 0.0
        self._phase = "—"

        # Dot goes out of range — implement auto-scrolling so the view follows
        # the current position. We track the rolling min/max of disp_x/y and
        # keep the current position near the center with a ±3° padding margin.
        self._disp_min_x: float = 0.0
        self._disp_max_x: float = 0.0
        self._disp_min_y: float = 0.0
        self._disp_max_y: float = 0.0

        # Score
        self._last_score = 0.0
        self._last_displacement = 0.0
        self._last_recoil_text = "—"

        self._build_ui()
        self._connect_signals()

        # Update timer (GUI refresh) — 30ms = ~33 fps.
        # Plot is updated HERE on the timer tick rather than per-packet from the
        # signal handler, decoupling data reception from rendering.
        # Stats are updated on every tick (acceptable at 33 fps).
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_ui)
        self._timer.start(30)  # ~33 Hz UI refresh

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: trace plot
        self._plot_container = self._build_trace_plot()
        splitter.addWidget(self._plot_container)

        # Calibration overlay (positioned inside plot container on top of plot)
        self._calibration_overlay = _CalibrationOverlay(self._plot_container)
        # Ensure overlay stays on top when plot container resizes
        self._plot_container.resizeEvent = self._plot_container_resize

        # Right: stats panel
        right_panel = self._build_stats_panel()
        splitter.addWidget(right_panel)

        splitter.setSizes([int(self.width() * 0.65), int(self.width() * 0.35)])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)

    def _build_trace_plot(self) -> QWidget:
        container = QWidget()
        vlayout = QVBoxLayout(container)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(4)

        # Header
        header = QWidget()
        hlayout = QHBoxLayout(header)
        hlayout.setContentsMargins(4, 4, 4, 0)
        title = QLabel("REAL-TIME TRACE")
        title.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 10px; letter-spacing: 2px; background: transparent;"
        )
        hlayout.addWidget(title)

        self._uncalibrated_badge = QLabel("UNCALIBRATED")
        self._uncalibrated_badge.setStyleSheet(
            "background: #3a2a1a; color: #ff6600; padding: 2px 8px; "
            "border-radius: 3px; font-family: 'JetBrains Mono', monospace; "
            "font-size: 9px; letter-spacing: 1px;"
        )
        hlayout.addWidget(self._uncalibrated_badge)
        hlayout.addStretch()

        # Coordinate readout
        self._coord_label = QLabel("X: 0.00°  Y: 0.00°")
        self._coord_label.setStyleSheet(
            "color: #555; font-family: 'JetBrains Mono', monospace; "
            "font-size: 9px; background: transparent;"
        )
        hlayout.addWidget(self._coord_label)
        vlayout.addWidget(header)

        # pyqtgraph plot
        self._pw = pg.PlotWidget(
            title="",
            background="#0d0d0d",
            foreground="#e0e0e0",
        )
        self._pw.setLabel("bottom", "degrees", color="#888", font={"family": "JetBrains Mono", "size": "9px"})
        self._pw.setLabel("left", "degrees", color="#888", font={"family": "JetBrains Mono", "size": "9px"})
        self._pw.showGrid(x=True, y=True, alpha=0.15)
        # Fixed view range — no auto-range, no camera-following.
        # Trace stays within ±4° window.
        self._pw.setXRange(-4, 4, padding=0)
        self._pw.setYRange(-4, 4, padding=0)
        self._pw.setAspectLocked(True)
        self._pw.enableAutoRange(False)

        # Fading trail: 10 segments, alpha increases from tail to head.
        # Each segment is a separate curve so pyqtgraph can render them
        # with individual alpha without compositing issues.
        self._trail_curves: list = []
        for i in range(self.TRAIL_SEGMENTS):
            # Segment 0 = oldest (most transparent), SEGMENTS-1 = newest (most opaque)
            alpha = int(255 * (i + 1) / self.TRAIL_SEGMENTS)
            curve = self._pw.plot(
                [],
                [],
                pen=pg.mkPen(color=(0, 255, 136, alpha), width=1.5),
                antialias=True,
            )
            self._trail_curves.append(curve)

        # Current position dot — solid filled circle, bright green
        self._dot = pg.ScatterPlotItem(
            size=7,
            pen=pg.mkPen(None),
            brush=pg.mkBrush(0, 255, 136, 255),
            pxMode=True,
        )
        self._pw.addItem(self._dot)
        self._dot.setData([0.0], [0.0])  # start at origin

        vlayout.addWidget(self._pw, 1)
        return container

    def _build_stats_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background: {BG2}; border-radius: 4px;")
        vlayout = QVBoxLayout(panel)
        vlayout.setContentsMargins(12, 12, 12, 12)
        vlayout.setSpacing(16)

        # ── STEADINESS ───────────────────────────────────────────────────────
        vlayout.addWidget(self._section_label("STEADINESS"))

        # Hold indicator dot + HOLD STEADY label
        steadiness_header = QWidget()
        sh_layout = QHBoxLayout(steadiness_header)
        sh_layout.setContentsMargins(0, 0, 0, 0)
        self._hold_indicator = QLabel()
        self._hold_indicator.setFixedSize(12, 12)
        self._hold_indicator.setStyleSheet(
            "background: #00ff88; border-radius: 6px;"
        )
        sh_layout.addWidget(self._hold_indicator)
        sh_layout.addWidget(QLabel("HOLD STEADY"))
        sh_layout.addStretch()
        steadiness_header.setStyleSheet(
            "font-family: 'JetBrains Mono', monospace; font-size: 10px; "
            "color: #888; letter-spacing: 1px; background: transparent;"
        )
        vlayout.addWidget(steadiness_header)

        # Progress bar
        self._steadiness_bar = QProgressBar()
        self._steadiness_bar.setRange(0, 100)
        self._steadiness_bar.setValue(100)
        self._steadiness_bar.setFormat("")
        vlayout.addWidget(self._steadiness_bar)

        # Hold / Wobble / NPA row
        grid = self._build_stat_grid()
        vlayout.addLayout(grid)

        vlayout.addSpacing(8)

        # ── LAST SCORE ───────────────────────────────────────────────────────
        vlayout.addWidget(self._section_label("LAST SCORE"))

        score_row = QWidget()
        sr_layout = QHBoxLayout(score_row)
        sr_layout.setContentsMargins(0, 4, 0, 4)
        self._score_gauge = ScoreGauge()
        sr_layout.addWidget(self._score_gauge)
        sr_layout.addStretch()
        vlayout.addWidget(score_row)

        vlayout.addSpacing(8)

        # ── LAST DIRECTION ───────────────────────────────────────────────────
        vlayout.addWidget(self._section_label("LAST DIRECTION"))

        self._direction_bar = QLabel("— No shot yet —")
        self._direction_bar.setStyleSheet(
            "background: #1a1a1a; border: 1px solid #333; border-radius: 4px; "
            "padding: 10px; font-family: 'JetBrains Mono', monospace; "
            "font-size: 12px; color: #888; text-align: center;"
        )
        vlayout.addWidget(self._direction_bar)

        vlayout.addSpacing(8)

        # ── DEVICE STATUS ────────────────────────────────────────────────────
        vlayout.addWidget(self._section_label("DEVICE STATUS"))

        status_grid = QHBoxLayout()
        status_grid.setSpacing(16)

        jer_l = QVBoxLayout()
        jer_l.setSpacing(2)
        jer_l.addWidget(QLabel("JERK"))
        self._jerk_val = QLabel("—")
        self._jerk_val.setObjectName("value_accent")
        self._jerk_val.setStyleSheet(
            "color: #00ff88; font-family: 'JetBrains Mono', monospace; "
            "font-size: 14px; font-weight: bold; background: transparent;"
        )
        jer_l.addWidget(self._jerk_val)
        status_grid.addLayout(jer_l)

        phase_l = QVBoxLayout()
        phase_l.setSpacing(2)
        phase_l.addWidget(QLabel("PHASE"))
        self._phase_val = QLabel("—")
        self._phase_val.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 14px; font-weight: bold; background: transparent;"
        )
        phase_l.addWidget(self._phase_val)
        status_grid.addLayout(phase_l)

        vlayout.addLayout(status_grid)

        vlayout.addStretch()
        return panel

    def _build_stat_grid(self) -> QHBoxLayout:
        grid = QHBoxLayout()
        grid.setSpacing(12)

        for label, value_id in [
            ("HOLD", "hold_val"),
            ("WOBBLE", "wobble_val"),
            ("NPA", "npa_val"),
        ]:
            col = QVBoxLayout()
            col.setSpacing(2)
            lbl = QLabel(label)
            lbl.setStyleSheet(
                "color: #555; font-family: 'JetBrains Mono', monospace; "
                "font-size: 9px; letter-spacing: 1px; background: transparent;"
            )
            val = QLabel("—")
            val.setObjectName(value_id)
            val.setStyleSheet(
                "color: #e0e0e0; font-family: 'JetBrains Mono', monospace; "
                "font-size: 14px; font-weight: bold; background: transparent;"
            )
            setattr(self, f"_{value_id}", val)
            col.addWidget(lbl)
            col.addWidget(val)
            grid.addLayout(col)

        return grid

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color: #555; font-family: 'JetBrains Mono', monospace; "
            "font-size: 9px; letter-spacing: 2px; text-transform: uppercase; "
            "padding-bottom: 4px; border-bottom: 1px solid #222; "
            "background: transparent;"
        )
        return lbl

    # ── Signal connections ────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        self._router.sample_received.connect(self._on_sample)
        self._router.shot_received.connect(self._on_shot)
        self._router.calibrating.connect(self._on_calibrating)
        self._router.calibration_progress.connect(self._on_calibration_progress)

        # Skip button inside calibration overlay
        self._calibration_overlay._skip_btn.clicked.connect(self._on_skip_calibration)

    def _plot_container_resize(self, event) -> None:
        """Resize calibration overlay to match plot widget inside container."""
        # First call the original resize
        QWidget.resizeEvent(self._plot_container, event)
        # Then resize overlay to match the PlotWidget inside
        pw = self._plot_container.findChild(pg.PlotWidget)
        if pw and self._calibration_overlay:
            self._calibration_overlay.setGeometry(pw.rect())

    def _on_calibrating(self, calibrating: bool) -> None:
        """Show/hide calibration overlay and reset plot on calibration state change."""
        if calibrating:
            # Reset angle integration for fresh calibration
            self._prev_timestamp_us = None
            self._bias_captured = False
            self._trace_x.clear()
            self._trace_y.clear()
            self._timestamps.clear()
            self._wobble_window.clear()
            self._angle_x = 0.0
            self._angle_y = 0.0
            self._hold_start_time = 0.0
            self._stable_since = 0.0
            # Reset auto-scroll tracking
            self._disp_min_x = 0.0
            self._disp_max_x = 0.0
            self._disp_min_y = 0.0
            self._disp_max_y = 0.0
            # Reset plot view to center (0,0) with ±4° range
            self._pw.setXRange(-4, 4, padding=0)
            self._pw.setYRange(-4, 4, padding=0)
            self._calibration_overlay.show_calibrating(True)
        else:
            self._calibration_overlay.show_calibrating(False)

    def _on_calibration_progress(self, progress: float) -> None:
        """Update calibration overlay with current progress and static status."""
        calibrator = self._mw.get_calibrator()
        count = calibrator.sample_count
        total = calibrator.calibration_target
        self._calibration_overlay.set_progress(progress, count, total)
        self._calibration_overlay.set_static_status(calibrator.is_static)

    def _on_skip_calibration(self) -> None:
        """User pressed Skip — complete calibration with current samples."""
        calibrator = self._mw.get_calibrator()
        if not calibrator.is_calibrated and calibrator.sample_count > 0:
            # Commit partial calibration with what we have
            n = float(calibrator.sample_count)
            from stasys.core.imu_calibrator import CalibrationBias
            calibrator._bias = CalibrationBias(
                gyro_x=calibrator._gyro_x_sum / n,
                gyro_y=calibrator._gyro_y_sum / n,
                gyro_z=calibrator._gyro_z_sum / n,
                accel_x=calibrator._accel_x_sum / n,
                accel_y=calibrator._accel_y_sum / n,
                accel_z=calibrator._accel_z_sum / n,
            )
            calibrator._is_calibrated = True
        self._router.calibrating.emit(False)

    # ── Packet handlers ───────────────────────────────────────────────────────

    def _on_sample(self, sample: DataRawSample) -> None:
        """Process a raw IMU sample — called on main thread via signal."""
        # Session start / wrap-around: reset integration state
        if sample.sample_counter == 0:
            self._prev_timestamp_us = None
            self._bias_captured = False
            self._trace_x.clear()
            self._trace_y.clear()
            self._timestamps.clear()
            self._wobble_window.clear()
            self._angle_x = 0.0
            self._angle_y = 0.0
            self._hold_start_time = 0.0
            self._stable_since = 0.0

        # Compute dt from actual firmware timestamp (must be in SECONDS)
        if self._prev_timestamp_us is not None:
            dt = (sample.timestamp_us - self._prev_timestamp_us) / 1_000_000.0
            # Clamp: ignore gaps > 50ms (e.g. after missed packets)
            dt = min(dt, 0.05)
        else:
            dt = 0.01  # seed dt on first sample
        self._prev_timestamp_us = sample.timestamp_us

        calibrator = self._mw.get_calibrator()

        # ── Bias-corrected gyro for integration ─────────────────────────────
        # Two paths:
        #   - Calibrated: subtract calibrator's mean bias from raw gyro,
        #     then integrate (one correction only, no double-subtraction)
        #   - Not calibrated: capture first-sample gyro as session bias,
        #     then subtract it each subsequent sample
        raw_gyro_x = sample.gyro_x
        raw_gyro_y = sample.gyro_y

        if calibrator.is_calibrated:
            # Use calibrator's pre-computed bias directly in integration.
            # No per-session bias capture — calibrator owns this.
            if not self._bias_captured:
                self._gyro_bias_x = calibrator.bias.gyro_x
                self._gyro_bias_y = calibrator.bias.gyro_y
                self._bias_captured = True
            gyro_x_dps = (raw_gyro_x - self._gyro_bias_x) / 65.5
            gyro_y_dps = (raw_gyro_y - self._gyro_bias_y) / 65.5
        else:
            # Not yet calibrated — capture first sample as session bias.
            if not self._bias_captured:
                self._gyro_bias_x = raw_gyro_x
                self._gyro_bias_y = raw_gyro_y
                self._bias_captured = True
            gyro_x_dps = (raw_gyro_x - self._gyro_bias_x) / 65.5
            gyro_y_dps = (raw_gyro_y - self._gyro_bias_y) / 65.5

        # Integrate bias-corrected gyro: gyro_x_dps is already bias-corrected
        # (raw minus calibrator or first-sample bias, divided by 65.5).
        # Do NOT subtract _gyro_bias_x again here — that's the double-subtraction bug.
        self._angle_x += gyro_x_dps * dt
        self._angle_y += gyro_y_dps * dt

        # Apply zero offset (RE-ZERO baseline)
        offset_x, offset_y = self._mw.get_zero_offset()
        disp_x = self._angle_x - offset_x
        disp_y = self._angle_y - offset_y

        self.current_angle_x = self._angle_x
        self.current_angle_y = self._angle_y

        # Store for fading trail
        self._trace_x.append(disp_x)
        self._trace_y.append(disp_y)
        self._timestamps.append(sample.timestamp_us / 1_000_000.0)

        # Compute jerk magnitude (use raw accel here as it's not critical for calibration)
        accel_x_raw = sample.accel_x
        jerk_x = accel_x_raw / 8192.0 * 9.81 / dt if dt > 0 else 0
        self._jerk_mag = abs(jerk_x)

        # Steadiness metrics — gyro_x_dps is already bias-corrected,
        # so use it directly (no further subtraction needed).
        gyro_mag = (gyro_x_dps ** 2 + gyro_y_dps ** 2) ** 0.5
        self._wobble_window.append(gyro_mag)
        self._wobble_rms = (
            sum(v ** 2 for v in self._wobble_window) / len(self._wobble_window)
        ) ** 0.5
        self._npa_deviation = (disp_x ** 2 + disp_y ** 2) ** 0.5

        # Auto-scroll: keep current position centered with ±3° padding.
        # If the dot reaches within 1° of any edge, shift the view window.
        MARGIN = 1.0        # degrees — trigger scroll when within this of edge
        PADDING = 3.0       # degrees — new view padding around the position
        center_x = disp_x
        center_y = disp_y
        x_range = self._pw.viewRange()[0]
        y_range = self._pw.viewRange()[1]
        x_lo, x_hi = x_range
        y_lo, y_hi = y_range

        needs_scroll = (
            center_x < x_lo + MARGIN or
            center_x > x_hi - MARGIN or
            center_y < y_lo + MARGIN or
            center_y > y_hi - MARGIN
        )
        if needs_scroll:
            # Re-center the view on the current dot, with ±PADDING° range
            self._pw.setXRange(center_x - PADDING, center_x + PADDING, padding=0)
            self._pw.setYRange(center_y - PADDING, center_y + PADDING, padding=0)
            # After re-centering, reset trailing curves so they re-draw in the new coordinate space
            self._trace_x.clear()
            self._trace_y.clear()

        # Phase detection
        jerk_threshold = self._mw.get_settings().get("jerk_threshold", 5.0) * 9.81
        if sample.piezo > 200:  # Piezo activity → recoil
            self._phase = "Recoil"
        elif abs(jerk_x) > jerk_threshold:
            self._phase = "Press"
        else:
            self._phase = "Hold"

        # Stability
        self._is_stable = self._wobble_rms < 2.0  # deg/s threshold
        if self._is_stable:
            if self._stable_since == 0.0:
                self._stable_since = self._timestamps[-1]
            self._hold_time = self._timestamps[-1] - self._stable_since
        else:
            self._stable_since = 0.0

    def _on_shot(self, shot: EvtShotDetected) -> None:
        """Called when a shot is detected."""
        gyro_x = shot.gyro_x_peak / 65.5
        gyro_y = shot.gyro_y_peak / 65.5
        displacement = (gyro_x ** 2 + gyro_y ** 2) ** 0.5
        base_score = max(0.0, 100.0 - (displacement / 5.0) * 100.0)
        self._last_score = min(100.0, base_score)
        self._last_displacement = displacement

        # Direction
        axis = shot.recoil_axis
        sign = shot.recoil_sign
        parts = []
        if axis == 0:
            parts.append("RIGHT" if sign > 0 else "LEFT")
        elif axis == 1:
            parts.append("DOWN" if sign > 0 else "UP")
        if gyro_x > 1 or gyro_y > 1:
            parts.append("HIGH")
        elif gyro_x > 0.5 or gyro_y > 0.5:
            parts.append("MED")
        self._last_recoil_text = "—".join(parts) if parts else "CENTER"
        if not parts:
            self._last_recoil_text = "CENTER"

        self._score_gauge.set_score(self._last_score)

    def _update_ui(self) -> None:
        """Called on timer (~33 Hz) — update plot and stats (always on main thread)."""
        # Current dot position (written by _on_sample, read here)
        dot_x = self.current_angle_x
        dot_y = self.current_angle_y

        # Update dot
        self._dot.setData([dot_x], [dot_y])

        # Coordinate readout
        self._coord_label.setText(f"X: {dot_x:+.2f}°  Y: {dot_y:+.2f}°")

        # Update trail segments — split into TRAIL_SEGMENTS slices;
        # oldest = most transparent. Only redraw when there's data.
        if self._trace_x and self._trace_y:
            self._trace_frame_counter = 0
            xs = list(self._trace_x)
            ys = list(self._trace_y)
            n = len(xs)
            segs = self.TRAIL_SEGMENTS
            for i, curve in enumerate(self._trail_curves):
                start = int(n * i / segs)
                end = int(n * (i + 1) / segs)
                if end > start:
                    curve.setData(xs[start:end], ys[start:end])
                else:
                    curve.setData([], [])

        # Update steadiness
        wobble_pct = max(0, min(100, int(100 - self._wobble_rms * 20)))
        self._steadiness_bar.setValue(wobble_pct)
        bar_color = "#00ff88" if wobble_pct > 60 else "#ff6600" if wobble_pct > 30 else "#ff3333"
        self._steadiness_bar.setStyleSheet(
            f"QProgressBar {{ background: #222; border: none; border-radius: 3px; height: 8px; }}"
            f"QProgressBar::chunk {{ background: {bar_color}; border-radius: 3px; }}"
        )

        self._hold_val.setStyleSheet(
            f"color: #e0e0e0; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 14px; font-weight: bold; background: transparent;"
        )
        self._hold_val.setText(f"{self._hold_time:.1f}s")

        self._wobble_val.setText(f"{self._wobble_rms:.1f}°/s")
        wobble_color = "#00ff88" if self._wobble_rms < 1.5 else "#ff6600" if self._wobble_rms < 3.0 else "#ff3333"
        self._wobble_val.setStyleSheet(
            f"color: {wobble_color}; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 14px; font-weight: bold; background: transparent;"
        )

        self._npa_val.setText(f"{self._npa_deviation:.2f}°")
        npa_color = "#00ff88" if self._npa_deviation < 0.5 else "#ff6600" if self._npa_deviation < 2.0 else "#ff3333"
        self._npa_val.setStyleSheet(
            f"color: {npa_color}; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 14px; font-weight: bold; background: transparent;"
        )

        # Hold indicator
        if self._is_stable:
            self._hold_indicator.setStyleSheet("background: #00ff88; border-radius: 6px;")
        else:
            self._hold_indicator.setStyleSheet("background: #ff3333; border-radius: 6px;")

        # Device status
        self._jerk_val.setText(f"{self._jerk_mag:.1f}")
        self._phase_val.setText(self._phase)
        phase_color = {"Hold": "#00ff88", "Press": "#ff6600", "Recoil": "#ff3333"}.get(self._phase, "#888")
        self._phase_val.setStyleSheet(
            f"color: {phase_color}; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 14px; font-weight: bold; background: transparent;"
        )

        # Direction
        self._direction_bar.setText(self._last_recoil_text)
        self._direction_bar.setStyleSheet(
            "background: #1a1a1a; border: 1px solid #333; border-radius: 4px; "
            "padding: 10px; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 14px; color: {ORANGE}; text-align: center; font-weight: bold;"
        )

        # Uncalibrated badge
        if self._mw.is_zero_set():
            self._uncalibrated_badge.hide()
        else:
            self._uncalibrated_badge.show()

    def on_disconnect(self) -> None:
        """Clear all live data."""
        self._trace_x.clear()
        self._trace_y.clear()
        self._timestamps.clear()
        for curve in self._trail_curves:
            curve.setData([], [])
        self._dot.setData([0.0], [0.0])
        self._angle_x = 0.0
        self._angle_y = 0.0
        self.current_angle_x = 0.0
        self.current_angle_y = 0.0
        self._prev_timestamp_us = None
        self._bias_captured = False
        self._hold_time = 0.0
        self._wobble_rms = 0.0
        self._npa_deviation = 0.0
        self._wobble_window.clear()
        self._jerk_mag = 0.0
        self._phase = "—"

    def on_rezero(self) -> None:
        """Reset zero baseline and clear trail."""
        self._angle_x = 0.0
        self._angle_y = 0.0
        self._trace_x.clear()
        self._trace_y.clear()
        self._timestamps.clear()
        for curve in self._trail_curves:
            curve.setData([], [])
        self._dot.setData([0.0], [0.0])
        self._hold_time = 0.0
        self._stable_since = 0.0
        # Re-capture gyro bias so integration resets cleanly
        self._bias_captured = False
        self._prev_timestamp_us = None