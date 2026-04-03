"""HISTORY tab — session list, shot grouping, replay."""

from __future__ import annotations

import math
import time
from collections import deque

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
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
from stasys.protocol.packets import DataRawSample, EvtShotDetected
from stasys.storage.session_store import SessionStore


class HistoryTab(QWidget):
    """History tab with session list, shot grouping, and replay."""

    def __init__(self, router: DataRouter, main_window: MainWindow) -> None:
        super().__init__()
        self._router = router
        self._mw = main_window
        self._store = SessionStore()
        self._sessions: list[dict] = []
        self._selected_session_id: int | None = None
        self._shots: list[dict] = []
        self._replay_idx = 0
        self._replay_paused = True
        self._replay_speed = 1.0
        self._replay_timer: QTimer | None = None
        self._replay_trace: deque = deque(maxlen=2000)
        self._shot_scores: list[float] = []
        self._shot_positions: list[tuple[float, float]] = []
        self._session_start_ts: float = 0.0

        self._build_ui()
        self._connect_signals()
        self._load_sessions()

    def _build_ui(self) -> None:
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Sessions list
        left_widget = QWidget()
        left_widget.setStyleSheet(f"background: {BG2}; border-radius: 4px;")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("SESSIONS")
        header.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 10px; letter-spacing: 2px; padding: 8px 12px; "
            "background: #1a1a1a; border-bottom: 1px solid #222;"
        )
        left_layout.addWidget(header)

        self._sessions_table = QTableWidget()
        self._sessions_table.setColumnCount(4)
        self._sessions_table.setHorizontalHeaderLabels(["Date", "Shots", "Avg", "Duration"])
        self._sessions_table.setColumnWidth(0, 150)
        self._sessions_table.setColumnWidth(1, 60)
        self._sessions_table.setColumnWidth(2, 60)
        self._sessions_table.setColumnWidth(3, 80)
        self._sessions_table.setStyleSheet(
            f"background: #111; gridline-color: #222; border: none; "
            "selection-background-color: #1a3a2a; font-family: 'JetBrains Mono', monospace; font-size: 10px;"
        )
        self._sessions_table.itemClicked.connect(self._on_session_selected)
        left_layout.addWidget(self._sessions_table, 1)

        self._delete_btn = QPushButton("Delete Session")
        self._delete_btn.setObjectName("danger")
        self._delete_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {RED}; border: 1px solid {RED}; "
            f"border-radius: 3px; padding: 6px 12px; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 10px; letter-spacing: 1px; text-transform: uppercase; }}"
            f"QPushButton:hover {{ background: {RED}; color: #fff; }}"
        )
        self._delete_btn.clicked.connect(self._on_delete_session)
        left_layout.addWidget(self._delete_btn)

        main_splitter.addWidget(left_widget)

        # Center+Right: Grouping + Metrics + Table
        center_right = QWidget()
        cr_layout = QVBoxLayout(center_right)
        cr_layout.setContentsMargins(0, 0, 0, 0)
        cr_layout.setSpacing(4)

        # Top row: Shot grouping + Group metrics
        top_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Shot grouping plot
        grouping_widget = QWidget()
        grouping_widget.setStyleSheet(f"background: {BG2}; border-radius: 4px;")
        gw_layout = QVBoxLayout(grouping_widget)
        gw_layout.setContentsMargins(0, 0, 0, 0)
        gh = QLabel("SHOT GROUPING")
        gh.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 10px; letter-spacing: 2px; padding: 6px 10px; "
            "background: #1a1a1a; border-bottom: 1px solid #222;"
        )
        gw_layout.addWidget(gh)
        self._group_plot = pg.PlotWidget(title="", background="#111", foreground="#e0e0e0")
        self._group_plot.showGrid(x=True, y=True, alpha=0.15)
        self._group_plot.setXRange(-6, 6)
        self._group_plot.setYRange(-6, 6)
        self._group_plot.setAspectLocked(True)
        self._group_plot.setLabel("bottom", "deg", color="#888",
                                    font={"family": "JetBrains Mono", "size": "9px"})
        self._group_plot.setLabel("left", "deg", color="#888",
                                  font={"family": "JetBrains Mono", "size": "9px"})
        # Rings
        for r in [1.0, 2.0, 3.5, 5.0]:
            ring = pg.QtWidgets.QGraphicsEllipseItem(-r, -r, r * 2, r * 2)
            ring.setPen(QPen(QColor("#333"), 1))
            self._group_plot.addItem(ring)
        # Crosshair
        self._group_plot.plot([0, 0], [-10, 10], pen=QPen(QColor("#444"), 0.5))
        self._group_plot.plot([-10, 10], [0, 0], pen=QPen(QColor("#444"), 0.5))
        # Bullseye
        bull = pg.QtWidgets.QGraphicsEllipseItem(-3, -3, 6, 6)
        bull.setPen(QPen(QColor(ACCENT), 1.5))
        self._group_plot.addItem(bull)
        self._group_scatter = self._group_plot.plot([], [], pen=None, symbol="o",
                                                      symbolSize=8, symbolBrush=QColor(ACCENT))
        # Replay trace
        self._replay_trace_curve = self._group_plot.plot([], [], pen=QPen(QColor(BLUE), 1.5))

        gw_layout.addWidget(self._group_plot, 1)
        top_splitter.addWidget(grouping_widget)

        # Group metrics
        metrics_widget = QWidget()
        metrics_widget.setStyleSheet(f"background: {BG2}; border-radius: 4px;")
        mw_layout = QVBoxLayout(metrics_widget)
        mw_layout.setContentsMargins(8, 8, 8, 8)
        mh = QLabel("GROUP METRICS")
        mh.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 10px; letter-spacing: 2px; padding: 4px 8px; "
            "border-bottom: 1px solid #222;"
        )
        mw_layout.addWidget(mh)
        self._metrics_grid = self._build_metrics_grid()
        mw_layout.addLayout(self._metrics_grid)
        mw_layout.addStretch()
        top_splitter.addWidget(metrics_widget)
        top_splitter.setSizes([int(self.width() * 0.55), int(self.width() * 0.45)])

        cr_layout.addWidget(top_splitter, 1)

        # Bottom: Shots table + Replay controls
        bottom_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Shots table
        shots_widget = QWidget()
        shots_widget.setStyleSheet(f"background: {BG2}; border-radius: 4px;")
        sw_layout = QVBoxLayout(shots_widget)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        sh = QLabel("SHOTS")
        sh.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 10px; letter-spacing: 2px; padding: 6px 10px; "
            "background: #1a1a1a; border-bottom: 1px solid #222;"
        )
        sw_layout.addWidget(sh)
        self._shots_table = QTableWidget()
        self._shots_table.setColumnCount(5)
        self._shots_table.setHorizontalHeaderLabels(["#", "Score", "Direction", "Displacement", "Method"])
        self._shots_table.setColumnWidth(0, 30)
        self._shots_table.setColumnWidth(1, 60)
        self._shots_table.setColumnWidth(2, 80)
        self._shots_table.setColumnWidth(3, 100)
        self._shots_table.setColumnWidth(4, 80)
        self._shots_table.setStyleSheet(
            f"background: #111; gridline-color: #222; border: none; "
            "font-family: 'JetBrains Mono', monospace; font-size: 10px;"
        )
        sw_layout.addWidget(self._shots_table, 1)
        bottom_splitter.addWidget(shots_widget)

        # Replay controls
        replay_widget = QWidget()
        replay_widget.setStyleSheet(f"background: {BG2}; border-radius: 4px;")
        rw_layout = QVBoxLayout(replay_widget)
        rw_layout.setContentsMargins(8, 8, 8, 8)
        rh = QLabel("TRACE REPLAY")
        rh.setStyleSheet(
            "color: #888; font-family: 'JetBrains Mono', monospace; "
            "font-size: 10px; letter-spacing: 2px; padding: 4px 8px; "
            "border-bottom: 1px solid #222;"
        )
        rw_layout.addWidget(rh)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)
        self._play_btn = QPushButton("Play")
        self._play_btn.setStyleSheet(
            f"QPushButton {{ background: {BG3}; color: {ACCENT}; border: 1px solid {ACCENT}; "
            f"border-radius: 3px; padding: 5px 12px; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 10px; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ background: {ACCENT}; color: #0d0d0d; }}"
        )
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setEnabled(False)
        self._pause_btn.setStyleSheet(
            f"QPushButton {{ background: {BG3}; color: {FG_DIM}; border: 1px solid #444; "
            f"border-radius: 3px; padding: 5px 12px; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 10px; letter-spacing: 1px; }}"
        )
        self._step_btn = QPushButton("Step →")
        self._step_btn.setStyleSheet(
            f"QPushButton {{ background: {BG3}; color: {FG}; border: 1px solid #444; "
            f"border-radius: 3px; padding: 5px 12px; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 10px; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ background: {BG4}; }}"
        )
        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setStyleSheet(
            f"QPushButton {{ background: {BG3}; color: {FG_DIM}; border: 1px solid #444; "
            f"border-radius: 3px; padding: 5px 12px; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 10px; letter-spacing: 1px; }}"
        )
        self._play_btn.clicked.connect(self._on_play)
        self._pause_btn.clicked.connect(self._on_pause)
        self._step_btn.clicked.connect(self._on_step)
        self._reset_btn.clicked.connect(self._on_replay_reset)
        controls_row.addWidget(self._play_btn)
        controls_row.addWidget(self._pause_btn)
        controls_row.addWidget(self._step_btn)
        controls_row.addWidget(self._reset_btn)
        rw_layout.addLayout(controls_row)

        self._shot_counter = QLabel("0 / 0")
        self._shot_counter.setStyleSheet(
            f"color: {FG}; font-family: 'JetBrains Mono', monospace; "
            "font-size: 14px; font-weight: bold; background: transparent;"
        )
        rw_layout.addWidget(self._shot_counter)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed:"))
        from PyQt6.QtWidgets import QSlider
        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(1, 40)
        self._speed_slider.setValue(10)
        self._speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._speed_slider.setTickInterval(10)
        self._speed_slider.valueChanged.connect(self._on_speed_changed)
        speed_row.addWidget(self._speed_slider)
        self._speed_label = QLabel("1.0x")
        self._speed_label.setStyleSheet(
            f"color: {FG}; font-family: 'JetBrains Mono', monospace; "
            "font-size: 10px; background: transparent;"
        )
        speed_row.addWidget(self._speed_label)
        rw_layout.addLayout(speed_row)

        # Legend
        for color, label in [
            (BLUE, "Hold"),
            (ORANGE, "Press"),
            (RED, "Recoil"),
        ]:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 2)
            dot = QLabel()
            dot.setFixedSize(16, 4)
            dot.setStyleSheet(f"background: {color}; border-radius: 2px;")
            rl.addWidget(dot)
            lbl = QLabel(label)
            lbl.setStyleSheet(
                "color: #888; font-family: 'JetBrains Mono', monospace; "
                "font-size: 9px; background: transparent;"
            )
            rl.addWidget(lbl)
            rl.addStretch()
            rw_layout.addWidget(row)

        rw_layout.addStretch()
        bottom_splitter.addWidget(replay_widget)

        bottom_splitter.setSizes([int(self.width() * 0.7), int(self.width() * 0.3)])
        cr_layout.addWidget(bottom_splitter, 0)

        main_splitter.addWidget(center_right)
        main_splitter.setSizes([250, int(self.width() - 250)])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(main_splitter)

    def _build_metrics_grid(self) -> QVBoxLayout:
        grid = QVBoxLayout()
        grid.setSpacing(8)

        items = [
            ("Shots fired", "sh_count", "—"),
            ("Extreme spread", "extreme_spread", "—"),
            ("Mean radius", "mean_radius", "—"),
            ("POA→POI bias", "poa_bias", "—"),
            ("Avg score", "avg_score", "—"),
            ("Best / Worst", "best_worst", "—"),
            ("Dominant error", "dom_error", "—"),
        ]
        for label, attr, default in items:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet(
                "color: #555; font-family: 'JetBrains Mono', monospace; "
                "font-size: 9px; letter-spacing: 1px; text-transform: uppercase; "
                "background: transparent;"
            )
            row.addWidget(lbl)
            val = QLabel(default)
            val.setObjectName(f"_m_{attr}")
            val.setStyleSheet(
                "color: #e0e0e0; font-family: 'JetBrains Mono', monospace; "
                "font-size: 14px; font-weight: bold; background: transparent;"
            )
            setattr(self, f"_m_{attr}", val)
            row.addWidget(val)
            grid.addLayout(row)

        return grid

    def _connect_signals(self) -> None:
        self._router.shot_received.connect(self._on_shot)

    def _load_sessions(self) -> None:
        self._sessions = self._store.get_sessions()
        self._sessions_table.setRowCount(len(self._sessions))
        for i, s in enumerate(self._sessions):
            ts = s.get("started_at", 0)
            date = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"
            shots = s.get("shot_count", 0)
            avg = "—"  # Will compute if needed
            dur_ms = 0
            if s.get("ended_at") and s.get("started_at"):
                dur_ms = int((s["ended_at"] - s["started_at"]) * 1000)
            dur = f"{dur_ms // 60000}m" if dur_ms > 0 else "—"

            for col, val in enumerate([date, str(shots), avg, dur]):
                item = QTableWidgetItem(val)
                item.setForeground(QColor(FG))
                self._sessions_table.setItem(i, col, item)

    def _on_session_selected(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if 0 <= row < len(self._sessions):
            self._selected_session_id = self._sessions[row]["id"]
            self._load_session_data(self._selected_session_id)

    def _load_session_data(self, session_id: int) -> None:
        self._shots = self._store.get_shots(session_id)
        self._shot_scores.clear()
        self._shot_positions.clear()

        # Populate shots table
        self._shots_table.setRowCount(len(self._shots))
        shot_x: list[float] = []
        shot_y: list[float] = []

        for i, s in enumerate(self._shots):
            shot_num = s.get("shot_number", i + 1)
            gyro_x = s.get("gyro_x", 0) / 65.5
            gyro_y = s.get("gyro_y", 0) / 65.5
            displacement = (gyro_x ** 2 + gyro_y ** 2) ** 0.5
            score = min(100.0, max(0.0, 100.0 - (displacement / 5.0) * 100.0))
            self._shot_scores.append(score)
            # Estimate shot position from gyro
            px = displacement * 0.8 * (1 if i % 2 == 0 else -0.8)
            py = displacement * 0.6 * (-1 if i % 3 == 0 else 0.5)
            shot_x.append(px)
            shot_y.append(py)
            self._shot_positions.append((px, py))

            axis = s.get("recoil_axis", 0)
            sign = s.get("recoil_sign", 1)
            dir_str = self._dir_name(axis, sign)
            method = "Live Fire"

            for col, val in enumerate([
                str(shot_num), f"{score:.0f}", dir_str,
                f"{displacement:.2f}°", method
            ]):
                item = QTableWidgetItem(val)
                color = (ACCENT if score >= 75 else ORANGE if score >= 50 else RED)
                item.setForeground(QColor(color))
                self._shots_table.setItem(i, col, item)

        # Update group plot
        if shot_x:
            self._group_scatter.setData(shot_x, shot_y)
        else:
            self._group_scatter.setData([], [])

        # Compute metrics
        n = len(self._shot_scores)
        if n > 0:
            avg = sum(self._shot_scores) / n
            best = max(self._shot_scores)
            worst = min(self._shot_scores)
            self._m_sh_count.setText(str(n))
            self._m_avg_score.setText(f"{avg:.0f}")
            self._m_best_worst.setText(f"{best:.0f} / {worst:.0f}")
        else:
            self._m_sh_count.setText("0")
            self._m_avg_score.setText("—")
            self._m_best_worst.setText("—")

        if len(shot_x) > 1:
            pts = np.array(list(zip(shot_x, shot_y)))
            centroid = pts.mean(axis=0)
            deviations = np.sqrt(((pts - centroid) ** 2).sum(axis=1))
            extreme = float(deviations.max()) * 2
            mean_r = float(deviations.mean())
            poa_bias = float(np.sqrt(centroid[0] ** 2 + centroid[1] ** 2))
            self._m_extreme_spread.setText(f"{extreme:.2f}°")
            self._m_mean_radius.setText(f"{mean_r:.2f}°")
            self._m_poa_bias.setText(f"{poa_bias:.2f}°")
        else:
            self._m_extreme_spread.setText("—")
            self._m_mean_radius.setText("—")
            self._m_poa_bias.setText("—")

        # Dominant error
        if self._shots:
            axes = Counter(s.get("recoil_axis", 0) for s in self._shots)
            dom = axes.most_common(1)[0][0] if axes else 0
            dom_str = ["X (horizontal)", "Y (vertical)", "Z"][dom] if dom < 3 else "?"
            self._m_dom_error.setText(dom_str)
        else:
            self._m_dom_error.setText("—")

        self._shot_counter.setText(f"{self._replay_idx} / {n}")
        self._replay_idx = 0
        self._replay_trace.clear()
        self._replay_trace_curve.setData([], [])

    def _dir_name(self, axis: int, sign: int) -> str:
        if axis == 0:
            return "RIGHT" if sign > 0 else "LEFT"
        elif axis == 1:
            return "DOWN" if sign > 0 else "UP"
        return "CENTER"

    def _on_delete_session(self) -> None:
        if self._selected_session_id is None:
            return
        reply = QMessageBox.question(
            self, "Delete Session",
            "Are you sure you want to delete this session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            # Delete from DB (cascade deletes shots)
            import sqlite3
            conn = sqlite3.connect("stasys.db")
            conn.execute("DELETE FROM sessions WHERE id = ?", (self._selected_session_id,))
            conn.commit()
            conn.close()
            self._load_sessions()
            self._selected_session_id = None
            self._shots_table.setRowCount(0)
            self._group_scatter.setData([], [])

    def _on_play(self) -> None:
        self._replay_paused = False
        self._play_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._start_replay_timer()

    def _on_pause(self) -> None:
        self._replay_paused = True
        self._play_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        if self._replay_timer:
            self._replay_timer.stop()

    def _on_step(self) -> None:
        if self._replay_idx < len(self._shot_positions):
            x, y = self._shot_positions[self._replay_idx]
            self._replay_idx += 1
            self._shot_counter.setText(f"{self._replay_idx} / {len(self._shot_positions)}")

    def _on_replay_reset(self) -> None:
        self._replay_idx = 0
        self._replay_paused = True
        self._play_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        if self._replay_timer:
            self._replay_timer.stop()
        self._replay_trace_curve.setData([], [])
        self._shot_counter.setText(f"0 / {len(self._shot_positions)}")

    def _on_speed_changed(self, val: int) -> None:
        self._replay_speed = val / 10.0
        self._speed_label.setText(f"{self._replay_speed:.1f}x")

    def _start_replay_timer(self) -> None:
        if self._replay_timer:
            self._replay_timer.stop()
        else:
            self._replay_timer = QTimer()
        interval = max(50, int(1000 / self._replay_speed))
        self._replay_timer.timeout.connect(self._replay_step)
        self._replay_timer.start(interval)

    def _replay_step(self) -> None:
        if self._replay_idx >= len(self._shot_positions):
            self._on_pause()
            return
        x, y = self._shot_positions[self._replay_idx]
        self._replay_trace.append((x, y))
        if len(self._replay_trace) > 1:
            trace_x = [p[0] for p in self._replay_trace]
            trace_y = [p[1] for p in self._replay_trace]
            self._replay_trace_curve.setData(trace_x, trace_y)
        self._replay_idx += 1
        self._shot_counter.setText(f"{self._replay_idx} / {len(self._shot_positions)}")

    def _on_shot(self, shot: EvtShotDetected) -> None:
        pass  # Handled by session_store in main_window

    def reload(self) -> None:
        self._store = SessionStore()
        self._load_sessions()
