"""STASYS GUI theme: color constants and QSS stylesheet."""

from __future__ import annotations

# ── Color Palette ──────────────────────────────────────────────────────────────
BG: str = "#0d0d0d"          # Main background
BG2: str = "#111111"         # Slightly lighter panels
BG3: str = "#1a1a1a"         # Cards / tab backgrounds
BG4: str = "#222222"         # Borders, dividers
BG5: str = "#2a2a2a"         # Disabled / inactive backgrounds

FG: str = "#e0e0e0"          # Primary text
FG_DIM: str = "#888888"      # Secondary / label text
FG_BRIGHT: str = "#ffffff"    # Emphasis text

ACCENT: str = "#00ff88"      # Primary accent (green)
ACCENT_DIM: str = "#00cc6a"  # Dimmed accent
ACCENT_BG: str = "#1a3a2a"   # Accent background (connected badge, selection)

ORANGE: str = "#ff6600"      # Warning / press phase / coaching tip
ORANGE_DIM: str = "#cc5200"
ORANGE_BG: str = "#3a2a1a"

RED: str = "#ff3333"         # Error / recoil phase / worst shot
RED_DIM: str = "#cc2929"
RED_BG: str = "#3a1a1a"

BLUE: str = "#3399ff"        # Hold phase
BLUE_DIM: str = "#2277cc"
BLUE_BG: str = "#1a2a3a"

YELLOW: str = "#ffcc00"      # Caution / improving trend
YELLOW_DIM: str = "#cc9900"
YELLOW_BG: str = "#3a3a1a"

# ── Fonts ────────────────────────────────────────────────────────────────────
FONT_MONO: str = "'JetBrains Mono', 'Courier New', 'Consolas', monospace"
FONT_SIZE_SM: int = 10
FONT_SIZE_MD: int = 11
FONT_SIZE_LG: int = 14
FONT_SIZE_XL: int = 18
FONT_SIZE_LOGO: int = 22

# ── QSS Stylesheet ───────────────────────────────────────────────────────────
DARK_QSS: str = f"""
QMainWindow, QDialog {{
    background: {BG};
    color: {FG};
}}

QWidget {{
    background: {BG};
    color: {FG};
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_MD}px;
}}

QTabWidget::pane {{
    border: 1px solid {BG4};
    background: {BG2};
    border-radius: 4px;
    margin-top: -1px;
}}

QTabBar::tab {{
    background: {BG3};
    color: {FG_DIM};
    padding: 8px 20px;
    border: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SM}px;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-right: 2px;
}}

QTabBar::tab:selected {{
    background: {BG2};
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}

QTabBar::tab:hover:!selected {{
    background: {BG4};
    color: {FG};
}}

QPushButton {{
    background: {BG3};
    color: {ACCENT};
    border: 1px solid {ACCENT};
    border-radius: 3px;
    padding: 6px 16px;
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SM}px;
    letter-spacing: 1px;
    text-transform: uppercase;
    min-height: 24px;
}}

QPushButton:hover {{
    background: {ACCENT};
    color: {BG};
}}

QPushButton:pressed {{
    background: {ACCENT_DIM};
    color: {BG};
}}

QPushButton:disabled {{
    background: {BG3};
    color: #444444;
    border-color: #444444;
}}

QPushButton#danger {{
    border-color: {RED};
    color: {RED};
}}

QPushButton#danger:hover {{
    background: {RED};
    color: {BG};
}}

QPushButton#secondary {{
    border-color: {FG_DIM};
    color: {FG_DIM};
}}

QPushButton#secondary:hover {{
    background: {FG_DIM};
    color: {BG};
}}

QPushButton#connect {{
    border-color: {ACCENT};
    color: {ACCENT};
    font-weight: bold;
}}

QPushButton#connect:hover {{
    background: {ACCENT};
    color: {BG};
}}

QPushButton#connect:disabled {{
    border-color: #444444;
    color: #444444;
}}

QPushButton#action {{
    border-color: {ORANGE};
    color: {ORANGE};
}}

QPushButton#action:hover {{
    background: {ORANGE};
    color: {BG};
}}

QLabel {{
    background: transparent;
    color: {FG};
}}

QLabel#logo {{
    color: {ACCENT};
    font-size: {FONT_SIZE_LOGO}px;
    font-weight: bold;
    font-family: {FONT_MONO};
}}

QLabel#badge_disconnected {{
    background: {BG5};
    color: {FG_DIM};
    padding: 4px 12px;
    border-radius: 3px;
    font-size: {FONT_SIZE_SM}px;
    letter-spacing: 1px;
    text-transform: uppercase;
}}

QLabel#badge_connected {{
    background: {ACCENT_BG};
    color: {ACCENT};
    padding: 4px 12px;
    border-radius: 3px;
    font-size: {FONT_SIZE_SM}px;
    letter-spacing: 1px;
    text-transform: uppercase;
}}

QLabel#section_title {{
    color: {FG_DIM};
    font-size: {FONT_SIZE_SM}px;
    letter-spacing: 2px;
    text-transform: uppercase;
    padding-bottom: 2px;
    border-bottom: 1px solid {BG4};
}}

QLabel#value_large {{
    color: {FG_BRIGHT};
    font-size: {FONT_SIZE_XL}px;
    font-weight: bold;
    font-family: {FONT_MONO};
}}

QLabel#value_accent {{
    color: {ACCENT};
    font-size: {FONT_SIZE_LG}px;
    font-weight: bold;
    font-family: {FONT_MONO};
}}

QLabel#value_orange {{
    color: {ORANGE};
    font-size: {FONT_SIZE_LG}px;
    font-weight: bold;
    font-family: {FONT_MONO};
}}

QLabel#value_red {{
    color: {RED};
    font-size: {FONT_SIZE_LG}px;
    font-weight: bold;
    font-family: {FONT_MONO};
}}

QLabel#value_green {{
    color: {ACCENT};
    font-size: {FONT_SIZE_LG}px;
    font-weight: bold;
    font-family: {FONT_MONO};
}}

QTableWidget, QTableView {{
    background: {BG2};
    alternate-background-color: {BG3};
    gridline-color: {BG4};
    border: 1px solid {BG4};
    border-radius: 4px;
    selection-background-color: {ACCENT_BG};
    selection-color: {ACCENT};
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SM}px;
}}

QHeaderView::section {{
    background: {BG3};
    color: {FG_DIM};
    border: none;
    border-right: 1px solid {BG4};
    border-bottom: 1px solid {BG4};
    padding: 6px 8px;
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SM}px;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

QHeaderView::section:last {{
    border-right: none;
}}

QScrollBar:vertical {{
    background: {BG2};
    width: 10px;
    border-radius: 5px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {BG4};
    border-radius: 4px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background: {FG_DIM};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background: {BG2};
    height: 10px;
    border-radius: 5px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {BG4};
    border-radius: 4px;
    min-width: 30px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {FG_DIM};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

QProgressBar {{
    background: {BG4};
    border: none;
    border-radius: 3px;
    height: 8px;
    text-align: center;
    color: transparent;
}}

QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 3px;
}}

QProgressBar::chunk[warning=\"true\"] {{
    background: {ORANGE};
}}

QProgressBar::chunk[critical=\"true\"] {{
    background: {RED};
}}

QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG3};
    color: {FG};
    border: 1px solid {BG4};
    border-radius: 3px;
    padding: 4px 8px;
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SM}px;
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {FG_DIM};
    margin-right: 6px;
}}

QComboBox QAbstractItemView {{
    background: {BG3};
    color: {FG};
    border: 1px solid {BG4};
    selection-background-color: {ACCENT_BG};
    selection-color: {ACCENT};
}}

QSlider::groove:horizontal {{
    background: {BG4};
    height: 4px;
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 14px;
    height: 14px;
    border-radius: 7px;
    margin: -5px 0;
}}

QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}

QTextEdit, QPlainTextEdit {{
    background: {BG2};
    color: {FG};
    border: 1px solid {BG4};
    border-radius: 4px;
    padding: 8px;
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SM}px;
}}

QCheckBox {{
    spacing: 8px;
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SM}px;
}}

QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {FG_DIM};
    border-radius: 2px;
    background: {BG3};
}}

QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

QMenuBar {{
    background: {BG3};
    color: {FG};
    border-bottom: 1px solid {BG4};
}}

QMenuBar::item {{
    padding: 6px 12px;
}}

QMenuBar::item:selected {{
    background: {BG4};
    color: {ACCENT};
}}

QMenu {{
    background: {BG3};
    color: {FG};
    border: 1px solid {BG4};
}}

QMenu::item:selected {{
    background: {ACCENT_BG};
    color: {ACCENT};
}}

QStatusBar {{
    background: {BG3};
    color: {FG_DIM};
    border-top: 1px solid {BG4};
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SM}px;
}}

QGroupBox {{
    background: {BG3};
    border: 1px solid {BG4};
    border-radius: 4px;
    padding: 12px 12px 8px;
    margin-top: 8px;
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SM}px;
    color: {FG_DIM};
    text-transform: uppercase;
    letter-spacing: 1px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: {FG_DIM};
}}

QSplitter::handle {{
    background: {BG4};
}}

QSplitter::handle:horizontal {{
    width: 2px;
}}

QSplitter::handle:vertical {{
    height: 2px;
}}

QToolTip {{
    background: {BG3};
    color: {FG};
    border: 1px solid {BG4};
    border-radius: 3px;
    padding: 4px 8px;
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_SM}px;
}}
"""
