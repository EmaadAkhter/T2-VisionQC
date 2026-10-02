"""Design system for the VisionQC desktop app.

One source of truth for typography, spacing, colors and widget styling.
No emojis, no inline hex outside this module (except verdict colors exposed
as tokens below).
"""

from __future__ import annotations

from PySide6.QtGui import QFont

# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

FONT_FAMILIES = [".AppleSystemUIFont", "Helvetica Neue", "Segoe UI", "Arial"]
FONT_SIZE = 13

BG = "#F6F7F9"
CARD = "#FFFFFF"
BORDER = "#E5E7EB"
BORDER_STRONG = "#D1D5DB"
TEXT = "#111827"
MUTED = "#6B7280"
FAINT = "#9CA3AF"

ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"
ACCENT_SOFT = "#EFF6FF"

SIDEBAR_BG = "#111827"
SIDEBAR_HOVER = "#1F2937"
SIDEBAR_TEXT = "#9CA3AF"
SIDEBAR_TEXT_STRONG = "#E5E7EB"

PASS = "#15803D"
PASS_BG = "#F0FDF4"
REVIEW = "#B45309"
REVIEW_BG = "#FFFBEB"
FAIL = "#B91C1C"
FAIL_BG = "#FEF2F2"
NO_PRODUCT = "#4B5563"

VERDICT_COLORS = {"PASS": PASS, "REVIEW": REVIEW, "FAIL": FAIL,
                  "NO_PRODUCT": NO_PRODUCT}
VERDICT_BACKGROUNDS = {"PASS": PASS_BG, "REVIEW": REVIEW_BG, "FAIL": FAIL_BG,
                       "NO_PRODUCT": "#F3F4F6"}

# Spacing scale (px)
SPACE_XS, SPACE_S, SPACE_M, SPACE_L, SPACE_XL = 4, 8, 12, 16, 24
PAGE_MARGIN = 24
CARD_PADDING = 18

# Type scale (px / weight)
TITLE_SIZE, TITLE_WEIGHT = 22, 700
SECTION_SIZE, SECTION_WEIGHT = 15, 600
METRIC_SIZE, METRIC_WEIGHT = 28, 700
VERDICT_SIZE, VERDICT_WEIGHT = 30, 700
CAPTION_SIZE = 12

RADIUS_CARD = 10
RADIUS_CONTROL = 8


def apply_app_font(app) -> None:
    """Apply the application font (avoids the Qt 'Sans Serif' fallback)."""
    from PySide6.QtGui import QFontDatabase

    available = set(QFontDatabase.families())
    chosen = next((name for name in FONT_FAMILIES if name in available), None)
    font = QFont()
    if chosen:
        font.setFamily(chosen)
    else:
        font.setFamilies(FONT_FAMILIES)
    font.setPointSize(FONT_SIZE)
    app.setFont(font)


# ---------------------------------------------------------------------------
# Global stylesheet
# ---------------------------------------------------------------------------

STYLESHEET = f"""
QMainWindow, QWidget#Page {{
    background: {BG};
    color: {TEXT};
}}
QWidget {{
    font-size: {FONT_SIZE}px;
}}

QLabel {{ background: transparent; }}
QLabel#Title {{ font-size: {TITLE_SIZE}px; font-weight: {TITLE_WEIGHT}; }}
QLabel#Section {{ font-size: {SECTION_SIZE}px; font-weight: {SECTION_WEIGHT}; }}
QLabel#Metric {{ font-size: {METRIC_SIZE}px; font-weight: {METRIC_WEIGHT}; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Caption {{ color: {MUTED}; font-size: {CAPTION_SIZE}px; }}

QFrame#Card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; border: none; }}

QFrame#Sidebar {{
    background: {SIDEBAR_BG};
    border: none;
}}
QLabel#SidebarTitle {{ color: white; font-size: 17px; font-weight: 700; }}
QLabel#SidebarMuted {{ color: {SIDEBAR_TEXT}; font-size: 12px; }}

QPushButton {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_CONTROL}px;
    padding: 7px 14px;
    color: {TEXT};
}}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT_DARK}; }}
QPushButton:pressed {{ background: {ACCENT_SOFT}; }}
QPushButton:disabled {{ color: {FAINT}; border-color: {BORDER}; }}

QPushButton#Primary {{
    background: {ACCENT};
    color: white;
    border: 1px solid {ACCENT};
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; }}
QPushButton#Primary:disabled {{ background: #BFDBFE; border-color: #BFDBFE; color: white; }}

QPushButton#Danger {{ color: {FAIL}; }}
QPushButton#Danger:hover {{ border-color: {FAIL}; color: {FAIL}; }}

QPushButton#Pass {{ background: {PASS}; color: white; border: none; font-weight: 600; }}
QPushButton#Reject {{ background: {FAIL}; color: white; border: none; font-weight: 600; }}

QPushButton#Nav {{
    background: transparent;
    color: {SIDEBAR_TEXT};
    border: none;
    border-left: 3px solid transparent;
    border-radius: 6px;
    text-align: left;
    padding: 9px 12px;
    font-size: 14px;
}}
QPushButton#Nav:hover {{ background: {SIDEBAR_HOVER}; color: {SIDEBAR_TEXT_STRONG}; }}
QPushButton#Nav:checked {{
    background: {SIDEBAR_HOVER};
    color: white;
    font-weight: 600;
    border-left: 3px solid {ACCENT};
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_CONTROL}px;
    padding: 6px 10px;
    min-height: 20px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit:disabled {{ background: {BG}; color: {FAINT}; }}

QComboBox#SidebarCombo {{
    background: {SIDEBAR_HOVER};
    color: white;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 6px 10px;
}}
QComboBox#SidebarCombo::drop-down {{ border: none; width: 22px; }}
QComboBox#SidebarCombo QAbstractItemView {{
    background: {CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}

QTableWidget {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CARD}px;
    gridline-color: transparent;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 6px 8px; border-bottom: 1px solid #F3F4F6; }}
QTableWidget::item:alternate {{ background: #FAFAFB; }}
QHeaderView::section {{
    background: #F9FAFB;
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px;
    color: {MUTED};
    font-weight: 600;
    font-size: {CAPTION_SIZE}px;
}}

QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    background: {CARD};
    text-align: center;
    height: 18px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}

QStatusBar {{ background: {CARD}; color: {MUTED}; }}
QStatusBar::item {{ border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {FAINT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_STRONG}; border-radius: 5px; min-width: 30px; }}

QToolTip {{
    background: {TEXT};
    color: white;
    border: none;
    padding: 6px 8px;
    border-radius: 6px;
}}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: {RADIUS_CARD}px; background: {CARD}; }}
QTabBar::tab {{ padding: 8px 16px; color: {MUTED}; }}
QTabBar::tab:selected {{ color: {ACCENT}; font-weight: 600; }}
"""
