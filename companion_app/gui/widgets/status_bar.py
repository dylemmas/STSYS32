"""Bottom status bar widget for the STASYS GUI."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QStatusBar, QWidget

from gui.theme import BG3, FG_DIM


class StatusBar(QStatusBar):
    """Custom status bar with connection status and info."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setStyleSheet(f"""
            QStatusBar {{
                background: {BG3};
                color: {FG_DIM};
                border-top: 1px solid #222;
                font-family: 'JetBrains Mono', monospace;
                font-size: 10px;
            }}
        """)
        self._label = QLabel("Enter COM port and click Connect to start.")
        self._label.setStyleSheet("color: #666; font-family: 'JetBrains Mono', monospace; font-size: 10px;")
        self.addWidget(self._label, 1)

    def set_status(self, text: str, level: str = "info") -> None:
        """Update the status message.

        Args:
            text: Status message text.
            level: "info" (default), "success" (green), "warning" (orange), "error" (red).
        """
        colors = {
            "info": "#888888",
            "success": "#00ff88",
            "warning": "#ff6600",
            "error": "#ff3333",
        }
        color = colors.get(level, FG_DIM)
        self._label.setText(text)
        self._label.setStyleSheet(
            f"color: {color}; font-family: 'JetBrains Mono', monospace; font-size: 10px;"
        )

    def set_firmware_info(self, version: str, port: str) -> None:
        """Show firmware version and port."""
        self.set_status(f"Connected to {port} | Firmware {version}", "success")
