"""ANALYSIS tab — direction wheel, session stats, and score trend."""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QPen
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
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
from gui.widgets.direction_wheel import DirectionWheel
from stasys.protocol.packets import EvtShotDetected


class AnalysisTab(QWidget):
    """Analysis tab showing session statistics and trend charts."""

    def __init__(self, router: DataRouter, main_window: MainWindow) -> None:
        super().__init__()
        self._router = router
        self._mw = main_window

        self._shots: list[EvtShotDetected] = []
        self._scores: list[float] = []
        self._directions: Counter = Counter()

        self._build_ui()
        self._connect_signals()
        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(500)

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # Top row: direction wheel + stats
        top_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Direction wheel
        wheel_container = QWidget()
        wheel_container.setStyleSheet(f"background: {BG2}; border-radius: 4px;")
        wv = QVBoxLayout(wheel_container)
        wv.setContentsMargins(8, 8, 8, 8)
        header = QLabel("SHOT DIRECTION")
        header.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 10px; letter-spacing: 2px; padding: 4px 8px; "
            "border-bottom: 1px solid #222; background: transparent;"
        )
        wv.addWidget(header)
        self._wheel = DirectionWheel()
        wv.addWidget(self._wheel, 1)
        top_splitter.addWidget(wheel_container)

        # Right: Stats panel
        stats_container = QWidget()
        stats_container.setStyleSheet(f"background: {BG2}; border-radius: 4px;")
        sv = QVBoxLayout(stats_container)
        sv.setContentsMargins(8, 8, 8, 8)
        sv_header = QLabel("SESSION STATISTICS")
        sv_header.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 10px; letter-spacing: 2px; padding: 4px 8px; "
            "border-bottom: 1px solid #222; background: transparent;"
        )
        sv.addWidget(sv_header)
        self._stats_grid = self._build_stats_grid()
        sv.addLayout(self._stats_grid)
        sv.addStretch()
        top_splitter.addWidget(stats_container)

        top_splitter.setSizes([int(self.width() * 0.45), int(self.width() * 0.55)])
        main_layout.addWidget(top_splitter, 1)

        # Bottom: Score trend chart
        chart_container = QWidget()
        chart_container.setStyleSheet(f"background: {BG2}; border-radius: 4px;")
        cv = QVBoxLayout(chart_container)
        cv.setContentsMargins(4, 4, 4, 4)
        chart_header = QLabel("SCORE TREND")
        chart_header.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 10px; letter-spacing: 2px; padding: 4px 8px; "
            "border-bottom: 1px solid #222; background: transparent;"
        )
        cv.addWidget(chart_header)

        self._trend_widget = pg.PlotWidget(
            title="", background="#111", foreground="#e0e0e0"
        )
        self._trend_widget.setLabel("bottom", "Shot #", color="#888",
                                     font={"family": "JetBrains Mono", "size": "9px"})
        self._trend_widget.setLabel("left", "Score", color="#888",
                                    font={"family": "JetBrains Mono", "size": "9px"})
        self._trend_widget.showGrid(x=True, y=True, alpha=0.15)
        self._trend_widget.setYRange(0, 105)
        self._trend_widget.setXRange(0, 10)

        # Threshold lines
        self._line_80 = pg.InfiniteLine(angle=0, pos=80,
                                        pen=QPen(QColor(YELLOW), 1.0, Qt.PenStyle.DashLine))
        self._line_60 = pg.InfiniteLine(angle=0, pos=60,
                                        pen=QPen(QColor(ORANGE), 1.0, Qt.PenStyle.DashLine))
        self._trend_widget.addItem(self._line_80)
        self._trend_widget.addItem(self._line_60)

        self._trend_curve = self._trend_widget.plot([], [], pen=QPen(QColor(ACCENT), 2.0))
        self._trend_dots = self._trend_widget.plot([], [], pen=None,
                                                     symbol="o", symbolSize=6,
                                                     symbolBrush=QColor(ACCENT))

        cv.addWidget(self._trend_widget, 1)
        main_layout.addWidget(chart_container, 1)

    def _build_stats_grid(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(8)

        stat_items = [
            ("Shots fired", "shot_count", "—", ""),
            ("Average score", "avg_score", "—", ACCENT),
            ("Best shot", "best_score", "—", ACCENT),
            ("Worst shot", "worst_score", "—", RED),
            ("Common issue", "common_issue", "—", ORANGE),
            ("Trend", "trend", "—", FG),
            ("Avg hold stability", "avg_stability", "—", ""),
            ("Hold consistency", "hold_consistency", "—", ""),
            ("Best hold", "best_hold", "—", ""),
            ("Stability trend", "stability_trend", "—", ""),
        ]

        for i, (label, attr, default, color) in enumerate(stat_items):
            row = i // 2
            col = (i % 2) * 2
            lbl = QLabel(label)
            lbl.setStyleSheet(
                "color: #555; font-family: 'JetBrains Mono', monospace; "
                "font-size: 9px; letter-spacing: 1px; text-transform: uppercase; "
                "background: transparent;"
            )
            grid.addWidget(lbl, row, col)

            val = QLabel(default)
            val.setObjectName(f"_stat_{attr}")
            color_str = color if color else FG
            val.setStyleSheet(
                f"color: {color_str}; font-family: 'JetBrains Mono', monospace; "
                "font-size: 16px; font-weight: bold; background: transparent;"
            )
            setattr(self, f"_stat_{attr}", val)
            grid.addWidget(val, row, col + 1)

        return grid

    def _connect_signals(self) -> None:
        self._router.shot_received.connect(self._on_shot)

    def _on_shot(self, shot: EvtShotDetected) -> None:
        self._shots.append(shot)

        # Compute score
        gyro_x = shot.gyro_x_peak / 65.5
        gyro_y = shot.gyro_y_peak / 65.5
        displacement = (gyro_x ** 2 + gyro_y ** 2) ** 0.5
        score = min(100.0, max(0.0, 100.0 - (displacement / 5.0) * 100.0))
        self._scores.append(score)

        # Direction
        axis = shot.recoil_axis
        sign = shot.recoil_sign
        self._wheel.add_shot(axis, sign)
        dir_label = self._dir_label(axis, sign)
        self._directions[dir_label] += 1

        self._refresh()

    def _dir_label(self, axis: int, sign: int) -> str:
        if axis == 0:
            return "RIGHT" if sign > 0 else "LEFT"
        elif axis == 1:
            return "DOWN" if sign > 0 else "UP"
        return "CENTER"

    def _refresh(self) -> None:
        n = len(self._shots)
        if n == 0:
            return

        # Update stats
        avg = sum(self._scores) / n
        best = max(self._scores)
        worst = min(self._scores)

        self._stat_shot_count.setText(str(n))
        self._stat_avg_score.setText(f"{avg:.0f}")
        self._stat_best_score.setText(f"{best:.0f}")
        self._stat_best_score.setStyleSheet(
            "color: #00ff88; font-family: 'JetBrains Mono', monospace; "
            "font-size: 16px; font-weight: bold; background: transparent;"
        )
        self._stat_worst_score.setText(f"{worst:.0f}")
        self._stat_worst_score.setStyleSheet(
            "color: #ff3333; font-family: 'JetBrains Mono', monospace; "
            "font-size: 16px; font-weight: bold; background: transparent;"
        )

        # Trend
        if n >= 3:
            first_half = sum(self._scores[:n // 2]) / (n // 2)
            second_half = sum(self._scores[n // 2:]) / (n - n // 2)
            if second_half > first_half + 3:
                trend = "↑ Improving"
                trend_color = ACCENT
            elif second_half < first_half - 3:
                trend = "↓ Declining"
                trend_color = RED
            else:
                trend = "→ Stable"
                trend_color = FG
        else:
            trend = "—"
            trend_color = FG
        self._stat_trend.setText(trend)
        self._stat_trend.setStyleSheet(
            f"color: {trend_color}; font-family: 'JetBrains Mono', monospace; "
            "font-size: 16px; font-weight: bold; background: transparent;"
        )

        # Common issue
        if self._directions:
            most_common_dir = self._directions.most_common(1)[0][0]
            if most_common_dir == "UP":
                common = "Anticipating recoil"
                cc = ORANGE
            elif most_common_dir == "DOWN":
                common = "Flinch downward"
                cc = ORANGE
            else:
                common = most_common_dir
                cc = FG
        else:
            common = "—"
            cc = FG
        self._stat_common_issue.setText(common)
        self._stat_common_issue.setStyleSheet(
            f"color: {cc}; font-family: 'JetBrains Mono', monospace; "
            "font-size: 16px; font-weight: bold; background: transparent;"
        )

        # Stability (estimated from wobble)
        self._stat_avg_stability.setText(f"{avg:.0f}%")
        self._stat_hold_consistency.setText(f"{100 - abs(worst - avg):.0f}%")
        self._stat_best_hold.setText(f"{best:.0f}%")
        self._stat_stability_trend.setText(trend)
        self._stat_stability_trend.setStyleSheet(
            f"color: {trend_color}; font-family: 'JetBrains Mono', monospace; "
            "font-size: 16px; font-weight: bold; background: transparent;"
        )

        # Update trend chart
        if n > 0:
            x = list(range(n))
            colors = []
            for s in self._scores:
                if s < 50:
                    colors.append(RED)
                elif s < 75:
                    colors.append(ORANGE)
                else:
                    colors.append(ACCENT)

            self._trend_curve.setData(x, self._scores)
            # Create colored dots
            brushes = [QColor(c) for c in colors]
            self._trend_dots.setData(x, self._scores, symbol="o", symbolSize=7,
                                     symbolBrush=brushes)
            self._trend_widget.setXRange(0, max(n + 1, 10))

    def on_disconnect(self) -> None:
        self._shots.clear()
        self._scores.clear()
        self._directions.clear()
        self._wheel.clear()
        self._trend_curve.setData([], [])
        self._trend_dots.setData([], [])
        # Reset stats
        self._stat_shot_count.setText("—")
        self._stat_avg_score.setText("—")
        self._stat_best_score.setText("—")
        self._stat_worst_score.setText("—")
        self._stat_common_issue.setText("—")
        self._stat_trend.setText("—")
        self._stat_avg_stability.setText("—")
        self._stat_hold_consistency.setText("—")
        self._stat_best_hold.setText("—")
        self._stat_stability_trend.setText("—")
