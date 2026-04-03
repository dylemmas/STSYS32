"""Direction wheel widget drawn with QPainter for shot distribution."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from gui.theme import ACCENT, BG, BG4, FG, FG_DIM, ORANGE


class DirectionWheel(QWidget):
    """Polar sector chart showing shot direction distribution.

    The wheel is divided into 9 sectors:
        U, UL, UR,
        L,  C,  R,
       DL,  D, DR

    Sector fill opacity is proportional to shot count in that direction.
    The center displays the total shot count.
    Cardinal labels (UP/DOWN/LEFT/RIGHT) surround the wheel.
    """

    # 9 sectors in order: U, UR, R, DR, D, DL, L, UL, C
    SECTOR_LABELS = ["U", "UR", "R", "DR", "D", "DL", "L", "UL", "C"]

    # Center angle (degrees) for each sector, 0=up, clockwise
    # C sector is always center
    SECTOR_ANGLES = [
        270,  # U
        315,  # UR
        0,    # R
        45,   # DR
        90,   # D
        135,  # DL
        180,  # L
        225,  # UL
        -1,   # C (center)
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._counts: dict[str, int] = {label: 0 for label in self.SECTOR_LABELS}
        self._total: int = 0
        self.setMinimumSize(200, 200)

    def set_counts(self, counts: dict[str, int]) -> None:
        """Set shot counts per direction label."""
        self._counts = {label: counts.get(label, 0) for label in self.SECTOR_LABELS}
        self._total = sum(self._counts.values())
        self.update()

    def total(self) -> int:
        return self._total

    def _sector_from_axis(self, axis: int, sign: int) -> str:
        """Map firmware recoil_axis + recoil_sign to a direction label."""
        # axis: 0=X, 1=Y, 2=Z
        # sign: +1 or -1
        # For typical mount: X=horizontal, Y=vertical
        # sign tells direction on that axis
        if axis == 0:  # X axis
            if sign > 0:
                return "R"
            else:
                return "L"
        elif axis == 1:  # Y axis
            if sign > 0:
                return "D"  # Down
            else:
                return "U"  # Up
        elif axis == 2:  # Z axis (if used)
            return "C"
        return "C"

    def add_shot(self, axis: int, sign: int) -> None:
        """Record a single shot in its direction."""
        sector = self._sector_from_axis(axis, sign)
        if sector in self._counts:
            self._counts[sector] += 1
        else:
            self._counts["C"] += 1
        self._total += 1
        self.update()

    def clear(self) -> None:
        self._counts = {label: 0 for label in self.SECTOR_LABELS}
        self._total = 0
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        cx = w / 2
        cy = h / 2
        outer_r = min(w, h) * 0.46
        inner_r = outer_r * 0.38
        center_r = inner_r * 0.6

        max_count = max(self._counts.get(l, 0) for l in self.SECTOR_LABELS if l != "C") or 1

        # Draw 8 outer sectors
        sectors_per_ring = 8
        for i in range(8):
            label = self.SECTOR_LABELS[i]
            count = self._counts.get(label, 0)
            # Two rings: inner and outer
            for ring_idx, (r_inner, r_outer) in enumerate([(inner_r, outer_r * 0.7), (outer_r * 0.7, outer_r)]):
                start_angle = self.SECTOR_ANGLES[i] - (45 if ring_idx == 0 else 22.5)
                span = 90 if ring_idx == 0 else 45
                opacity = (count / max_count) if count > 0 else 0.0
                if opacity < 0.05:
                    opacity = 0.05
                alpha = int(255 * opacity)
                alpha = max(alpha, 30)
                color = QColor(ORANGE)
                color.setAlpha(alpha)
                pen = QPen(color, 1)
                painter.setPen(pen)
                brush_color = QColor(ORANGE)
                brush_color.setAlpha(alpha)
                painter.setBrush(brush_color)
                rect = QRectF(cx - r_outer, cy - r_outer, r_outer * 2, r_outer * 2)
                painter.drawPie(rect, int(-(start_angle + span) * 16), int(span * 16))

        # Draw sector labels (centered on position using QRectF)
        label_font = QFont("JetBrains Mono", 8)
        label_font.setWeight(QFont.Weight.Bold)
        for i, label in enumerate(self.SECTOR_LABELS):
            if label == "C":
                continue
            angle_deg = self.SECTOR_ANGLES[i]
            angle_rad = angle_deg * math.pi / 180
            label_r = outer_r * 1.08
            lx = cx + label_r * math.cos(angle_rad)
            ly = cy + label_r * math.sin(angle_rad)
            painter.setPen(QColor(FG))
            painter.setFont(label_font)
            lrect = QRectF(lx - 12, ly - 6, 24, 12)
            painter.drawText(lrect, Qt.AlignmentFlag.AlignCenter, label)

        # Draw cardinal labels
        cardinal_font = QFont("JetBrains Mono", 9)
        cardinal_font.setWeight(QFont.Weight.Bold)
        cardinals = [
            ("UP", 270, outer_r * 1.25),
            ("DOWN", 90, outer_r * 1.25),
            ("LEFT", 180, outer_r * 1.25),
            ("RIGHT", 0, outer_r * 1.25),
        ]
        for text, angle_deg, r in cardinals:
            angle_rad = angle_deg * math.pi / 180
            tx = cx + r * math.cos(angle_rad)
            ty = cy + r * math.sin(angle_rad)
            painter.setPen(QColor(FG_DIM))
            painter.setFont(cardinal_font)
            rect = QRectF(tx - 24, ty - 7, 48, 14)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        # Draw center circle
        painter.setBrush(QColor(BG4))
        painter.setPen(QPen(QColor(FG_DIM), 1))
        painter.drawEllipse(QPointF(cx, cy), center_r, center_r)

        # Draw total count in center
        total_font = QFont("JetBrains Mono", max(10, int(center_r * 0.7)))
        total_font.setWeight(QFont.Weight.Bold)
        painter.setFont(total_font)
        painter.setPen(QColor(FG))
        count_rect = QRectF(cx - center_r, cy - center_r * 0.5, center_r * 2, center_r)
        painter.drawText(count_rect, Qt.AlignmentFlag.AlignCenter, str(self._total))

        # "SHOTS" label below count
        shots_label_font = QFont("JetBrains Mono", 7)
        painter.setFont(shots_label_font)
        painter.setPen(QColor(FG_DIM))
        shots_rect = QRectF(cx - center_r, cy + center_r * 0.1, center_r * 2, center_r * 0.6)
        painter.drawText(shots_rect, Qt.AlignmentFlag.AlignCenter, "SHOTS")

        # Draw ring circles
        painter.setPen(QPen(QColor(BG4), 1))
        for r in [inner_r, outer_r * 0.7, outer_r]:
            painter.drawEllipse(QPointF(cx, cy), r, r)
