"""VisionQC 'light table' theme. Colour is reserved for verdicts.

Keeps every constant name the existing pages import. Set VISIONQC_THEME=light
to switch; dark is the factory default.
"""

from __future__ import annotations

import os

from PySide6.QtGui import QFont

MODE = os.environ.get("VISIONQC_THEME", "dark")
_D = MODE == "dark"

BG = "#14161A" if _D else "#F3F1EC"          # bench
SURFACE = "#1C1F25" if _D else "#FFFFFF"     # slab
BORDER = SEAM = "#2B3038" if _D else "#D9D5CB"
TEXT = "#E8E6E1" if _D else "#1B1D21"        # ink
MUTED = "#8B9099" if _D else "#6B6F77"
ACCENT = TEXT                                 # chrome is neutral on purpose

PASS = "#3FB68B" if _D else "#1F8F68"
REVIEW = "#E8A93A" if _D else "#B97A0C"
FAIL = "#E5533D" if _D else "#C8402B"
PASS_BG = "#1B2B26" if _D else "#E3F2EC"
REVIEW_BG = "#2E2818" if _D else "#F8EDD5"
FAIL_BG = "#2F1D1A" if _D else "#F7E1DC"
NO_PRODUCT = MUTED                            # kept for the edge-server verdict
VERDICT_COLORS = {"PASS": PASS, "REVIEW": REVIEW, "FAIL": FAIL,
                  "NO_PRODUCT": NO_PRODUCT}
VERDICT_BACKGROUNDS = {"PASS": PASS_BG, "REVIEW": REVIEW_BG, "FAIL": FAIL_BG,
                       "NO_PRODUCT": SURFACE}

SIDEBAR_BG = "#0F1114" if _D else "#E9E6DF"
SIDEBAR_TEXT = "#B9BCC2" if _D else "#3A3D44"

PAGE_MARGIN, CARD_PADDING = 24, 18
SPACE_XS, SPACE_S, SPACE_M, SPACE_L, SPACE_XL = 4, 8, 14, 22, 30
RADIUS_CARD = RADIUS_CONTROL = 6
VERDICT_SIZE, VERDICT_WEIGHT = 34, 600

MONO = ["IBM Plex Mono", "Menlo", "Consolas", "monospace"]
SANS = "'IBM Plex Sans', 'Segoe UI', 'Helvetica Neue', sans-serif"

FONT_FAMILIES = ["IBM Plex Sans", "Segoe UI", "Helvetica Neue", "Arial"]
FONT_SIZE = 13


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


def stylesheet() -> str:
    return f"""
* {{ font-family: {SANS}; font-size: 14px; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#Page {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#Title {{ font-size: 24px; font-weight: 600; }}
QLabel#Subtitle, QLabel#Muted {{ color: {MUTED}; }}
QLabel#Caption {{ color: {MUTED}; font-size: 12px; }}
QLabel#Section {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 2px; }}
QLabel#Metric {{ font-family: 'IBM Plex Mono', Menlo, Consolas; font-size: 28px; font-weight: 600; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid {SEAM}; border-radius: {RADIUS_CARD}px; }}
QFrame#Card QLabel {{ border: none; }}
QFrame#Divider {{ background: {SEAM}; max-height: 1px; border: none; }}
QFrame#Sidebar {{ background: {SIDEBAR_BG}; border-right: 1px solid {SEAM}; }}
QLabel#SidebarTitle {{ color: {TEXT}; font-size: 17px; font-weight: 600; letter-spacing: 3px; }}
QLabel#SidebarMuted {{ color: {MUTED}; font-size: 12px; }}
QPushButton {{ background: {SURFACE}; border: 1px solid {SEAM}; border-radius: {RADIUS_CONTROL}px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {MUTED}; }}
QPushButton:disabled {{ color: {MUTED}; background: {BG}; }}
QPushButton#Primary {{ background: {TEXT}; color: {BG}; border-color: {TEXT}; font-weight: 600; }}
QPushButton#Primary:disabled {{ background: {SEAM}; color: {MUTED}; border-color: {SEAM}; }}
QPushButton#Pass {{ border-color: {PASS}; color: {PASS}; }}
QPushButton#Reject, QPushButton#Danger {{ border-color: {FAIL}; color: {FAIL}; }}
QPushButton#Nav {{ text-align: left; border: none; border-left: 3px solid transparent; border-radius: 0;
  background: transparent; color: {SIDEBAR_TEXT}; padding: 10px 14px; }}
QPushButton#Nav:hover {{ background: {SURFACE}; color: {TEXT}; }}
QPushButton#Nav:checked {{ border-left: 3px solid {TEXT}; background: {SURFACE}; color: {TEXT}; font-weight: 600; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
  background: {BG}; border: 1px solid {SEAM}; border-radius: {RADIUS_CONTROL}px; padding: 6px 8px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus {{ border: 1px solid {TEXT}; }}
QLineEdit:disabled {{ color: {MUTED}; }}
QComboBox#SidebarCombo {{ background: {SIDEBAR_BG}; color: {TEXT}; border: 1px solid {SEAM}; padding: 6px 10px; }}
QComboBox#SidebarCombo::drop-down {{ border: none; width: 22px; }}
QComboBox#SidebarCombo QAbstractItemView {{ background: {SURFACE}; color: {TEXT};
  border: 1px solid {SEAM}; selection-background-color: {BG}; selection-color: {TEXT}; }}
QCheckBox {{ spacing: 8px; }}
QTableWidget {{ background: {SURFACE}; alternate-background-color: {BG}; border: none; gridline-color: transparent; }}
QTableWidget::item {{ padding: 6px 8px; }}
QTableWidget::item:selected {{ background: {BG}; color: {TEXT}; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none;
  border-bottom: 1px solid {SEAM}; padding: 6px; font-size: 11px; }}
QProgressBar {{ border: 1px solid {SEAM}; border-radius: {RADIUS_CONTROL}px;
  background: {SURFACE}; text-align: center; height: 18px; }}
QProgressBar::chunk {{ background: {TEXT}; border-radius: 4px; }}
QSlider::groove:horizontal {{ height: 4px; background: {SEAM}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SEAM}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {SEAM}; border-radius: 5px; min-width: 30px; }}
QToolTip {{ background: {TEXT}; color: {BG}; border: none; padding: 6px 8px; border-radius: 6px; }}
QTabWidget::pane {{ border: 1px solid {SEAM}; border-radius: {RADIUS_CARD}px; background: {SURFACE}; }}
QTabBar::tab {{ padding: 8px 16px; color: {MUTED}; }}
QTabBar::tab:selected {{ color: {TEXT}; font-weight: 600; }}
QStatusBar {{ background: {SIDEBAR_BG}; color: {MUTED}; }}
QStatusBar::item {{ border: none; }}
"""


STYLESHEET = stylesheet()
