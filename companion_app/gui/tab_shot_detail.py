"""SHOT DETAIL tab — target plot and coaching."""

from __future__ import annotations

import math
from collections import deque

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPainterPath
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.main_window import DataRouter, MainWindow
from gui.theme import (
    ACCENT,
    BG2,
    BG3,
    BG4,
    BLUE,
    FG,
    FG_DIM,
    ORANGE,
    RED,
    YELLOW,
)
from gui.widgets.score_gauge import ScoreGauge
from stasys.protocol.packets import DataRawSample, EvtShotDetected


class TargetPlot(QWidget):
    """Custom painted target with concentric rings and colored trace overlay."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._shot_x: float = 0.0
        self._shot_y: float = 0.0
        self._has_shot = False
        # Trace path: list of (x, y, phase) tuples
        # phase: "hold", "press", "recoil"
        self._trace_points: list[tuple[float, float, str]] = []
        self.setMinimumSize(300, 300)

    def set_shot(self, x: float, y: float) -> None:
        self._shot_x = x
        self._shot_y = y
        self._has_shot = True
        self.update()

    def set_trace(self, points: list[tuple[float, float, str]]) -> None:
        self._trace_points = points
        self.update()

    def clear(self) -> None:
        self._shot_x = 0.0
        self._shot_y = 0.0
        self._has_shot = False
        self._trace_points.clear()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        cx = w / 2
        cy = h / 2
        max_r = min(w, h) * 0.45
        deg_per_pixel = 5.0 / max_r  # 5 degrees full scale

        # Background
        painter.fillRect(0, 0, w, h, QColor(BG2))

        # Concentric rings at 1°, 2°, 3.5°, 5°
        ring_radii = [1.0, 2.0, 3.5, 5.0]
        for r_deg in ring_radii:
            r_px = r_deg * max_r / 5.0
            painter.setPen(QPen(QColor("#333"), 1.0 if r_deg < 3.0 else 1.5))
            painter.drawEllipse(
                int(cx - r_px), int(cy - r_px), int(r_px * 2), int(r_px * 2)
            )
            # Label
            painter.setPen(QColor("#555"))
            painter.setFont(QFont("JetBrains Mono", 8))
            painter.drawText(int(cx + r_px + 4), int(cy + 4), f"{r_deg}°")

        # Crosshair
        painter.setPen(QPen(QColor("#444"), 1.0))
        painter.drawLine(int(cx), 0, int(cx), h)
        painter.drawLine(0, int(cy), w, int(cy))

        # Bullseye center
        painter.setPen(QPen(QColor(ACCENT), 1.5))
        painter.drawEllipse(int(cx - 4), int(cy - 4), 8, 8)

        # Draw trace path with phase coloring
        if self._trace_points:
            phase_colors = {
                "hold": QColor(BLUE).lighter(140),
                "press": QColor(ORANGE).lighter(130),
                "recoil": QColor(RED).lighter(130),
            }
            for i in range(1, len(self._trace_points)):
                x1, y1, phase1 = self._trace_points[i - 1]
                x2, y2, phase2 = self._trace_points[i]
                color = phase_colors.get(phase2, QColor(FG))
                pen = QPen(color, 1.5)
                painter.setPen(pen)
                px1 = cx + x1 * max_r / 5.0
                py1 = cy - y1 * max_r / 5.0
                px2 = cx + x2 * max_r / 5.0
                py2 = cy - y2 * max_r / 5.0
                painter.drawLine(int(px1), int(py1), int(px2), int(py2))

        # Shot impact dot
        if self._has_shot:
            sx = cx + self._shot_x * max_r / 5.0
            sy = cy - self._shot_y * max_r / 5.0
            painter.setBrush(QColor(ACCENT))
            painter.setPen(QPen(QColor(ACCENT), 1.5))
            painter.drawEllipse(int(sx - 6), int(sy - 6), 12, 12)

            # Distance label
            dist = (self._shot_x ** 2 + self._shot_y ** 2) ** 0.5
            painter.setFont(QFont("JetBrains Mono", 8))
            painter.setPen(QColor(FG_DIM))
            painter.drawText(
                int(sx + 8), int(sy - 4),
                f"{dist:.2f}°"
            )


class ShotDetailTab(QWidget):
    """Shot Detail tab showing target view and coaching."""

    def __init__(self, router: DataRouter, main_window: MainWindow) -> None:
        super().__init__()
        self._router = router
        self._mw = main_window

        # Trace state
        self._trace_buffer: deque[tuple[float, float, float]] = deque(maxlen=5000)
        self._current_angle_x = 0.0
        self._current_angle_y = 0.0
        self._session_start_ts: float = 0.0
        self._dt = 1.0 / 100.0

        # Shot data
        self._last_shot: EvtShotDetected | None = None
        self._last_score = 0.0
        self._hold_stability = 100.0
        self._hold_time_s = 0.0
        self._wobble_at_shot = 0.0

        self._build_ui()
        self._connect_signals()

        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(50)

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Target plot
        left = QWidget()
        vlayout = QVBoxLayout(left)
        vlayout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("SHOT ANALYSIS")
        header.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 10px; letter-spacing: 2px; padding: 8px 12px; "
            "background: #1a1a1a; border-bottom: 1px solid #222;"
        )
        vlayout.addWidget(header)

        self._target = TargetPlot()
        vlayout.addWidget(self._target, 1)

        splitter.addWidget(left)

        # Right: Info panel
        right = self._build_info_panel()
        splitter.addWidget(right)

        splitter.setSizes([int(self.width() * 0.55), int(self.width() * 0.45)])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)

    def _build_info_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background: {BG2}; border-radius: 4px;")
        vlayout = QVBoxLayout(panel)
        vlayout.setContentsMargins(12, 12, 12, 12)
        vlayout.setSpacing(16)

        # Shot score
        vlayout.addWidget(self._section_label("SHOT SCORE"))
        score_row = QWidget()
        sr_layout = QHBoxLayout(score_row)
        sr_layout.setContentsMargins(0, 4, 0, 4)
        self._score_gauge = ScoreGauge()
        sr_layout.addWidget(self._score_gauge)
        sr_layout.addStretch()
        vlayout.addWidget(score_row)

        vlayout.addSpacing(8)

        # Coaching tip
        vlayout.addWidget(self._section_label("COACHING TIP"))
        self._coaching_text = QTextEdit()
        self._coaching_text.setReadOnly(True)
        self._coaching_text.setMaximumHeight(100)
        self._coaching_text.setPlainText("Fire a shot to receive coaching.")
        self._coaching_text.setStyleSheet(
            f"background: {BG3}; color: {FG_DIM}; border: 1px solid {BG4}; "
            "border-radius: 4px; padding: 8px; "
            "font-family: 'JetBrains Mono', monospace; font-size: 11px;"
        )
        vlayout.addWidget(self._coaching_text)

        vlayout.addSpacing(8)

        # Pre-shot hold
        vlayout.addWidget(self._section_label("PRE-SHOT HOLD"))
        hold_grid = QHBoxLayout()
        hold_grid.setSpacing(12)
        for label, attr in [
            ("STABILITY", "stability"),
            ("HOLD TIME", "hold_time"),
            ("WOBBLE", "wobble"),
        ]:
            col = QVBoxLayout()
            col.setSpacing(2)
            lbl = QLabel(label)
            lbl.setStyleSheet(
                "color: #555; font-family: 'JetBrains Mono', monospace; "
                "font-size: 9px; letter-spacing: 1px; background: transparent;"
            )
            val = QLabel("—")
            val.setObjectName(f"_{attr}_val")
            val.setStyleSheet(
                "color: #e0e0e0; font-family: 'JetBrains Mono', monospace; "
                "font-size: 16px; font-weight: bold; background: transparent;"
            )
            setattr(self, f"_{attr}_label", val)
            col.addWidget(lbl)
            col.addWidget(val)
            hold_grid.addLayout(col)
        vlayout.addLayout(hold_grid)

        vlayout.addSpacing(8)

        # Trace legend
        vlayout.addWidget(self._section_label("TRACE LEGEND"))
        for color, label in [
            (BLUE, "Hold phase"),
            (ORANGE, "Press phase"),
            (RED, "Recoil phase"),
        ]:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            dot = QLabel()
            dot.setFixedSize(20, 4)
            dot.setStyleSheet(f"background: {color}; border-radius: 2px;")
            row_layout.addWidget(dot)
            lbl = QLabel(label)
            lbl.setStyleSheet(
                "color: #888; font-family: 'JetBrains Mono', monospace; "
                "font-size: 10px; background: transparent;"
            )
            row_layout.addWidget(lbl)
            row_layout.addStretch()
            vlayout.addWidget(row)

        vlayout.addStretch()
        return panel

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color: #555; font-family: 'JetBrains Mono', monospace; "
            "font-size: 9px; letter-spacing: 2px; text-transform: uppercase; "
            "padding-bottom: 4px; border-bottom: 1px solid #222; "
            "background: transparent;"
        )
        return lbl

    def _connect_signals(self) -> None:
        self._router.sample_received.connect(self._on_sample)
        self._router.shot_received.connect(self._on_shot)

    def _on_sample(self, sample: DataRawSample) -> None:
        if sample.sample_counter == 0:
            self._trace_buffer.clear()
            self._session_start_ts = sample.timestamp_us / 1_000_000.0
            self._current_angle_x = 0.0
            self._current_angle_y = 0.0

        dt = self._dt
        gyro_x_dps = sample.gyro_x / 65.5
        gyro_y_dps = sample.gyro_y / 65.5
        self._current_angle_x += gyro_x_dps * dt
        self._current_angle_y += gyro_y_dps * dt

        offset_x, offset_y = self._mw.get_zero_offset()
        disp_x = self._current_angle_x - offset_x
        disp_y = self._current_angle_y - offset_y
        ts = sample.timestamp_us / 1_000_000.0 - self._session_start_ts

        self._trace_buffer.append((disp_x, disp_y, ts))

    def _on_shot(self, shot: EvtShotDetected) -> None:
        self._last_shot = shot

        # Compute score
        gyro_x = shot.gyro_x_peak / 65.5
        gyro_y = shot.gyro_y_peak / 65.5
        displacement = (gyro_x ** 2 + gyro_y ** 2) ** 0.5
        self._last_score = min(100.0, max(0.0, 100.0 - (displacement / 5.0) * 100.0))

        # Stability from wobble
        gyro_mag = (gyro_x ** 2 + gyro_y ** 2) ** 0.5
        self._hold_stability = max(0.0, min(100.0, 100.0 - gyro_mag * 15))

        # Hold time estimate
        self._hold_time_s = max(0.0, min(10.0, 5.0 - displacement))

        # Wobble at shot
        self._wobble_at_shot = gyro_mag

        # Compute phase-colored trace
        shot_ts = shot.timestamp_us / 1_000_000.0 - self._session_start_ts
        trace_with_phase: list[tuple[float, float, str]] = []
        for dx, dy, ts_val in self._trace_buffer:
            if ts_val < shot_ts - 0.5:
                phase = "hold"
            elif ts_val < shot_ts:
                phase = "press"
            else:
                phase = "recoil"
            trace_with_phase.append((dx, dy, phase))

        # Update target plot
        self._target.set_trace(trace_with_phase)
        self._target.set_shot(dx=displacement * 0.5, y=displacement * 0.3)

        self._score_gauge.set_score(self._last_score)

        # Coaching
        self._update_coaching(shot, gyro_mag)

    def _update_coaching(self, shot: EvtShotDetected, gyro_mag: float) -> None:
        axis = shot.recoil_axis
        sign = shot.recoil_sign
        tips: list[str] = []

        if axis == 1 and sign < 0:  # Consistent upward
            tips.append("Anticipating recoil — try follow-through drills")
        if gyro_mag > 2.0:
            tips.append("Hold instability detected — improve NPA before pressing")
        if self._hold_time_s < 1.0:
            tips.append("Hold longer before pressing the trigger")
        if not tips:
            tips.append("Good shot technique — stay consistent")

        self._coaching_text.setPlainText(" | ".join(tips))

        # Color coaching text
        if "Good" in tips[0]:
            color = ACCENT
        elif "Anticipating" in tips[0]:
            color = ORANGE
        elif "instability" in tips[0]:
            color = RED
        else:
            color = FG_DIM
        self._coaching_text.setStyleSheet(
            f"background: {BG3}; color: {color}; border: 1px solid {BG4}; "
            "border-radius: 4px; padding: 8px; "
            "font-family: 'JetBrains Mono', monospace; font-size: 11px;"
        )

    def _refresh(self) -> None:
        # Update pre-shot hold display
        stab = self._stability_label
        stab.setText(f"{self._hold_stability:.0f}%")
        stab.setStyleSheet(
            f"color: {'#00ff88' if self._hold_stability > 60 else '#ff6600' if self._hold_stability > 30 else '#ff3333'}; "
            "font-family: 'JetBrains Mono', monospace; font-size: 16px; "
            "font-weight: bold; background: transparent;"
        )

        self._hold_time_label.setText(f"{self._hold_time_s:.1f}s")
        wob_color = "#00ff88" if self._wobble_at_shot < 1.5 else "#ff6600" if self._wobble_at_shot < 3.0 else "#ff3333"
        self._wobble_label.setText(f"{self._wobble_at_shot:.1f}°/s")
        self._wobble_label.setStyleSheet(
            f"color: {wob_color}; font-family: 'JetBrains Mono', monospace; "
            "font-size: 16px; font-weight: bold; background: transparent;"
        )

    def on_disconnect(self) -> None:
        self._trace_buffer.clear()
        self._target.clear()
        self._last_shot = None
        self._last_score = 0.0
        self._coaching_text.setPlainText("Fire a shot to receive coaching.")

    def on_rezero(self) -> None:
        self._trace_buffer.clear()
        self._current_angle_x = 0.0
        self._current_angle_y = 0.0
        self._target.clear()
