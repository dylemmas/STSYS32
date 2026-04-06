"""SETTINGS tab — detection mode, weapon type, thresholds, calibration."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.main_window import DataRouter, MainWindow
from gui.theme import (
    ACCENT,
    BG2,
    BG3,
    BG4,
    FG,
    FG_DIM,
    ORANGE,
)
from stasys.protocol.commands import cmd_set_mount_mode


class SettingsTab(QWidget):
    """Settings tab with detection, weapon, and calibration controls."""

    def __init__(self, router: DataRouter, main_window: MainWindow) -> None:
        super().__init__()
        self._router = router
        self._mw = main_window
        self._current_mount_mode = 0
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(24)

        # Left column
        left = self._build_left_column()
        main_layout.addWidget(left, 1)

        # Right column
        right = self._build_right_column()
        main_layout.addWidget(right, 1)

    def _build_left_column(self) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet(f"background: {BG2}; border-radius: 6px;")
        vlayout = QVBoxLayout(widget)
        vlayout.setContentsMargins(16, 16, 16, 16)
        vlayout.setSpacing(16)

        # Detection Mode
        group = QGroupBox("DETECTION MODE")
        vlayout.addWidget(group)
        gl = QVBoxLayout(group)
        gl.setSpacing(8)

        self._fire_mode_combo = QComboBox()
        self._fire_mode_combo.addItems(["Live Fire", "Dry Fire"])
        self._fire_mode_combo.setStyleSheet(
            f"background: {BG3}; color: {FG}; border: 1px solid {BG4}; "
            "border-radius: 3px; padding: 6px 10px; "
            "font-family: 'JetBrains Mono', monospace; font-size: 11px;"
        )
        gl.addWidget(QLabel("Fire mode:"))
        gl.addWidget(self._fire_mode_combo)
        gl.addWidget(QLabel(
            "Live Fire: dual threshold (piezo + accel jerk)\n"
            "Dry Fire: jerk threshold only",
            styleSheet="color: #555; font-family: 'JetBrains Mono', monospace; font-size: 10px;"
        ))

        # Weapon Type
        group2 = QGroupBox("WEAPON TYPE")
        vlayout.addWidget(group2)
        g2l = QVBoxLayout(group2)
        g2l.setSpacing(8)

        weapon_row = QWidget()
        wr_layout = QHBoxLayout(weapon_row)
        wr_layout.setContentsMargins(0, 0, 0, 0)
        self._pistol_btn = QPushButton("  Pistol")
        self._pistol_btn.setCheckable(True)
        self._pistol_btn.setChecked(True)
        self._rifle_btn = QPushButton("  Rifle")
        self._rifle_btn.setCheckable(True)
        for btn in [self._pistol_btn, self._rifle_btn]:
            btn.setStyleSheet(
                f"QPushButton {{ background: {BG3}; color: {FG}; border: 1px solid {BG4}; "
                f"border-radius: 3px; padding: 6px 16px; font-family: 'JetBrains Mono', monospace; "
                f"font-size: 11px; }}"
                f"QPushButton:checked {{ background: {ACCENT}; color: #0d0d0d; border-color: {ACCENT}; }}"
            )
            wr_layout.addWidget(btn)
        g2l.addWidget(weapon_row)
        g2l.addWidget(QLabel(
            "Pistol / Rifle changes penalty scaling in score computation",
            styleSheet="color: #555; font-family: 'JetBrains Mono', monospace; font-size: 10px;"
        ))

        self._pistol_btn.clicked.connect(self._on_pistol)
        self._rifle_btn.clicked.connect(self._on_rifle)

        # Jerk Threshold
        group3 = QGroupBox("JERK THRESHOLD")
        vlayout.addWidget(group3)
        g3l = QVBoxLayout(group3)
        g3l.setSpacing(8)

        jerk_row = QWidget()
        jr_layout = QHBoxLayout(jerk_row)
        jr_layout.setContentsMargins(0, 0, 0, 0)
        jr_layout.addWidget(QLabel("Acc threshold (m/s\u00b2):"))
        self._jerk_spin = QDoubleSpinBox()
        self._jerk_spin.setRange(0.5, 20.0)
        self._jerk_spin.setSingleStep(0.25)
        self._jerk_spin.setValue(5.0)
        self._jerk_spin.setSuffix(" m/s\u00b2")
        self._jerk_spin.setStyleSheet(
            f"background: {BG3}; color: {FG}; border: 1px solid {BG4}; "
            "border-radius: 3px; padding: 4px 8px; "
            "font-family: 'JetBrains Mono', monospace; font-size: 11px;"
        )
        jr_layout.addWidget(self._jerk_spin)
        g3l.addWidget(jerk_row)
        g3l.addWidget(QLabel(
            "Minimum accel change rate to detect trigger press",
            styleSheet="color: #555; font-family: 'JetBrains Mono', monospace; font-size: 10px;"
        ))

        self._jerk_spin.valueChanged.connect(self._on_jerk_changed)

        # Mount Position
        group4 = QGroupBox("MOUNT POSITION")
        vlayout.addWidget(group4)
        g4l = QVBoxLayout(group4)
        g4l.setSpacing(8)

        self._mount_combo = QComboBox()
        self._mount_combo.addItems([
            "0 — Standard (upright, Z up)",
            "1 — Rotated 90° CW (Z yaw)",
            "2 — Inverted 180° (Z yaw)",
            "3 — Rotated 270° CW (Z yaw)",
            "4 — Barrel-under (Z along barrel)",
            "5 — Barrel-under inverted",
            "6 — Side mount (X along barrel)",
        ])
        self._mount_combo.setStyleSheet(
            f"background: {BG3}; color: {FG}; border: 1px solid {BG4}; "
            "border-radius: 3px; padding: 6px 10px; "
            "font-family: 'JetBrains Mono', monospace; font-size: 11px;"
        )
        g4l.addWidget(QLabel("Sensor orientation on weapon:"))
        g4l.addWidget(self._mount_combo)
        g4l.addWidget(QLabel(
            "Standard: device upright on rail.\n"
            "Barrel-under: device mounted under barrel (Picatinny).\n"
            "Side: device on side, USB port faces target.\n"
            "Calibrate (right column) after changing orientation.",
            styleSheet="color: #555; font-family: 'JetBrains Mono', monospace; font-size: 10px;"
        ))

        self._mount_apply_btn = QPushButton("Apply Mount Position")
        self._mount_apply_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT}; color: #0d0d0d; border: none; "
            f"border-radius: 3px; padding: 6px 16px; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 11px; }}"
        )
        g4l.addWidget(self._mount_apply_btn)

        self._mount_combo.currentIndexChanged.connect(self._on_mount_changed)
        self._mount_apply_btn.clicked.connect(self._on_mount_apply)

        vlayout.addStretch()
        return widget

    def _build_right_column(self) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet(f"background: {BG2}; border-radius: 6px;")
        vlayout = QVBoxLayout(widget)
        vlayout.setContentsMargins(16, 16, 16, 16)
        vlayout.setSpacing(16)

        # IMU Calibration
        group = QGroupBox("IMU CALIBRATION")
        vlayout.addWidget(group)
        gl = QVBoxLayout(group)
        gl.setSpacing(12)

        gl.addWidget(QLabel(
            "Hold the device flat and steady at your target before calibrating.\n"
            "Calibration captures the reference orientation (zero) used for all\n"
            "subsequent angle deviation calculations.",
            styleSheet="color: #555; font-family: 'JetBrains Mono', monospace; font-size: 10px; line-height: 1.6;"
        ))

        self._rezero_btn = QPushButton("Re-zero IMU")
        self._rezero_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {ACCENT}; border: 1px solid {ACCENT}; "
            f"border-radius: 3px; padding: 8px 20px; font-family: 'JetBrains Mono', monospace; "
            f"font-size: 11px; font-weight: bold; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ background: {ACCENT}; color: #0d0d0d; }}"
        )
        gl.addWidget(self._rezero_btn)

        self._calib_status = QLabel("Not calibrated")
        self._calib_status.setStyleSheet(
            f"color: {ORANGE}; font-family: 'JetBrains Mono', monospace; font-size: 10px;"
        )
        gl.addWidget(self._calib_status)

        self._rezero_btn.clicked.connect(self._on_calibrate)

        vlayout.addStretch()
        return widget

    def _on_pistol(self) -> None:
        self._pistol_btn.setChecked(True)
        self._rifle_btn.setChecked(False)
        self._mw.get_settings()["weapon_type"] = "Pistol"

    def _on_rifle(self) -> None:
        self._rifle_btn.setChecked(True)
        self._pistol_btn.setChecked(False)
        self._mw.get_settings()["weapon_type"] = "Rifle"

    def _on_jerk_changed(self, value: float) -> None:
        self._mw.get_settings()["jerk_threshold"] = value

    def _on_mount_changed(self, index: int) -> None:
        self._current_mount_mode = index

    def _on_mount_apply(self) -> None:
        mode = self._current_mount_mode
        self._mw._send_raw(cmd_set_mount_mode(mode))
        self._mount_apply_btn.setText(f"Applied: mode {mode}")
        # Reset button text after 2s
        import threading
        timer = threading.Timer(2.0, lambda: self._mount_apply_btn.setText("Apply Mount Position"))
        timer.start()

    def _on_calibrate(self) -> None:
        self._mw._on_rezero()
        self._calib_status.setText("Calibrated ✓")
        self._calib_status.setStyleSheet(
            f"color: {ACCENT}; font-family: 'JetBrains Mono', monospace; font-size: 10px;"
        )
