"""Arc gauge widget drawn with QPainter for shot scores."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from gui.theme import ACCENT, BG4, FG, ORANGE, RED, YELLOW


class ScoreGauge(QWidget):
    """Circular arc gauge displaying a score from 0-100.

    The arc sweeps from -225 to +45 degrees (270 total).
    Fill color transitions: red (0) -> yellow (50) -> green (100).
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._score: float = 0.0
        self.setMinimumSize(160, 120)
        self.setMaximumSize(200, 160)

    def set_score(self, score: float) -> None:
        """Set the displayed score (0-100)."""
        self._score = max(0.0, min(100.0, score))
        self.update()

    def score(self) -> float:
        return self._score

    def _score_color(self, score: float) -> QColor:
        """Return a single color for the current score range."""
        if score < 50:
            return QColor(RED)
        elif score < 75:
            return QColor(ORANGE)
        else:
            return QColor(ACCENT)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        cx = float(w) / 2.0
        cy = float(h) * 0.55

        outer_r = min(w, h) * 0.45
        inner_r = outer_r * 0.72
        bar_w = outer_r - inner_r

        # Background track arc
        track_rect = QRectF(cx - outer_r, cy - outer_r, outer_r * 2, outer_r * 2)
        pen = QPen(QColor(BG4), bar_w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(track_rect, int(-45 * 16 - 135 * 16), int(270 * 16))

        # Filled arc
        if self._score > 0:
            score_angle = int((self._score / 100.0) * 270 * 16)
            pen = QPen(self._score_color(self._score), bar_w)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(track_rect, int(-45 * 16), -score_angle)

        # Tick marks
        tick_angles = [0, 25, 50, 75, 100]
        for tick_val in tick_angles:
            angle_deg = -45 + (tick_val / 100.0) * 270
            rad = math.radians(angle_deg)
            inner_tick = outer_r * 0.88
            outer_tick = outer_r * 1.02
            x1 = cx + inner_tick * math.cos(rad)
            y1 = cy + inner_tick * math.sin(rad)
            x2 = cx + outer_tick * math.cos(rad)
            y2 = cy + outer_tick * math.sin(rad)
            pen = QPen(QColor(FG), 1.5)
            painter.setPen(pen)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Score number (centered using QRectF)
        font_size = max(10, int(min(w, h) * 0.22))
        score_font = QFont("JetBrains Mono", font_size)
        score_font.setWeight(QFont.Weight.Bold)
        painter.setFont(score_font)
        painter.setPen(self._score_color(self._score))
        score_text = f"{int(round(self._score))}"
        score_rect = QRectF(cx - outer_r * 0.6, cy - outer_r * 0.4, outer_r * 1.2, outer_r * 0.8)
        painter.drawText(score_rect, Qt.AlignmentFlag.AlignCenter, score_text)

        # "SCORE" label
        label_font = QFont("JetBrains Mono", 8)
        label_font.setWeight(QFont.Weight.Normal)
        painter.setFont(label_font)
        painter.setPen(QColor(FG))
        label_rect = QRectF(cx - outer_r, cy + outer_r * 0.3, outer_r * 2, outer_r * 0.5)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, "SCORE")
