"""Shared Qt components for a consistent, professional UI.

All colors/sizes come from desktop.theme. Pages should use these components
instead of ad-hoc styling. ScoreBar and VerdictBanner are re-exported here so
pages keep importing them from desktop.ui.widgets; their implementations live
in desktop.ui.signature.
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from desktop import theme
from desktop.ui.signature import ToleranceStrip as ScoreBar
from desktop.ui.signature import VerdictBanner


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def bgr_to_pixmap(image: np.ndarray, max_width: int, max_height: int) -> QPixmap:
    """Convert a BGR numpy image to a scaled QPixmap (keeps aspect ratio)."""
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    height, width, _ = rgb.shape
    scale = min(max_width / width, max_height / height, 1.0)
    target_w = max(1, int(width * scale))
    target_h = max(1, int(height * scale))
    if scale < 1.0:
        rgb = cv2.resize(rgb, (target_w, target_h), interpolation=cv2.INTER_AREA)
    rgb = np.ascontiguousarray(rgb)
    qimage = QImage(rgb.data, rgb.shape[1], rgb.shape[0],
                    rgb.strides[0], QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


def rgb_to_pixmap(image: np.ndarray, max_width: int, max_height: int) -> QPixmap:
    return bgr_to_pixmap(cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
                         max_width, max_height)


# ---------------------------------------------------------------------------
# Layout primitives
# ---------------------------------------------------------------------------

def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(theme.CARD_PADDING, theme.CARD_PADDING - 4,
                              theme.CARD_PADDING, theme.CARD_PADDING - 4)
    layout.setSpacing(theme.SPACE_S)
    if title:
        label = QLabel(title)
        label.setObjectName("Section")
        layout.addWidget(label)
    return frame, layout


def muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


def caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Caption")
    label.setWordWrap(True)
    return label


def page_header(title: str, subtitle: str = "") -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    title_label = QLabel(title)
    title_label.setObjectName("Title")
    layout.addWidget(title_label)
    if subtitle:
        layout.addWidget(muted(subtitle))
    return widget


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line


def empty_state(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    label.setMinimumHeight(80)
    return label


def status_dot(color: str) -> QLabel:
    dot = QLabel()
    dot.setFixedSize(8, 8)
    dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
    return dot


# ---------------------------------------------------------------------------
# Result components
# ---------------------------------------------------------------------------

class MetricTile(QFrame):
    """Compact metric: big value over a label."""

    def __init__(self, label: str, value: str = "—", color: str = theme.TEXT,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("Metric")
        self.value_label.setStyleSheet(f"color: {color};")
        self.name_label = QLabel(label)
        self.name_label.setObjectName("Caption")
        layout.addWidget(self.value_label)
        layout.addWidget(self.name_label)

    def set_value(self, value: str, color: str | None = None) -> None:
        self.value_label.setText(value)
        if color:
            self.value_label.setStyleSheet(f"color: {color};")


def make_table(headers: list[str]) -> QTableWidget:
    from PySide6.QtWidgets import QHeaderView

    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    header = table.horizontalHeader()
    header.setStretchLastSection(True)
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    return table
