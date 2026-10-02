"""Shared visual theme for the desktop app."""

ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
PASS = "#16a34a"
REVIEW = "#d97706"
FAIL = "#dc2626"
TEXT = "#0f172a"
MUTED = "#64748b"
BORDER = "#e2e8f0"
BG = "#f8fafc"
CARD = "#ffffff"

STYLESHEET = f"""
QMainWindow, QWidget#Page {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QLabel#Title {{ font-size: 20px; font-weight: 700; }}
QLabel#Subtitle {{ color: {MUTED}; }}
QLabel#CardTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Metric {{ font-size: 26px; font-weight: 700; }}

QFrame#Card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#Sidebar {{
    background: #0f172a;
    border: none;
}}
QLabel#SidebarTitle {{ color: white; font-size: 18px; font-weight: 700; }}
QLabel#SidebarOrg {{ color: #94a3b8; }}

QPushButton {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #94a3b8; }}
QPushButton#Primary {{
    background: {ACCENT};
    color: white;
    border: none;
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#Primary:disabled {{ background: #93c5fd; }}

QPushButton#Nav {{
    background: transparent;
    color: #cbd5e1;
    border: none;
    text-align: left;
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 14px;
}}
QPushButton#Nav:hover {{ background: #1e293b; color: white; }}
QPushButton#Nav:checked {{ background: {ACCENT}; color: white; font-weight: 600; }}

QPushButton#Danger {{ color: {FAIL}; }}
QPushButton#Pass {{ background: {PASS}; color: white; border: none; font-weight: 600; }}
QPushButton#Reject {{ background: {FAIL}; color: white; border: none; font-weight: 600; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 20px;
}}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}

QTableWidget {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: {BORDER};
}}
QHeaderView::section {{
    background: {BG};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px;
    font-weight: 600;
}}

QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    background: {CARD};
    text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

QStatusBar {{ background: {CARD}; color: {MUTED}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; background: {CARD}; }}
QTabBar::tab {{ padding: 8px 16px; }}
QTabBar::tab:selected {{ color: {ACCENT}; font-weight: 600; }}
"""

VERDICT_COLORS = {"PASS": PASS, "REVIEW": REVIEW, "FAIL": FAIL}
