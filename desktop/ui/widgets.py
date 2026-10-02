"""Shared Qt components for a consistent, professional UI.

All colors/sizes come from desktop.theme. Pages should use these components
instead of ad-hoc styling.
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from desktop import theme


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

class VerdictBanner(QFrame):
    """Unmissable verdict block: large label, next action, certainty."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VerdictBanner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(16)

        self.verdict_label = QLabel("—")
        self.verdict_label.setObjectName("Metric")
        layout.addWidget(self.verdict_label, 0, Qt.AlignmentFlag.AlignVCenter)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        self.action_label = QLabel("")
        self.action_label.setWordWrap(True)
        self.action_label.setStyleSheet(f"color: {theme.TEXT};")
        self.certainty_label = QLabel("")
        self.certainty_label.setObjectName("Caption")
        text_column.addWidget(self.action_label)
        text_column.addWidget(self.certainty_label)
        layout.addLayout(text_column, 1)

        self.set_result("NONE", "")

    def set_result(self, verdict: str, action: str, certainty: str = "") -> None:
        if verdict in theme.VERDICT_COLORS:
            color = theme.VERDICT_COLORS[verdict]
            background = theme.VERDICT_BACKGROUNDS[verdict]
        else:
            color, background = theme.MUTED, "#F3F4F6"
        self.verdict_label.setText(verdict if verdict != "NONE" else "—")
        self.verdict_label.setStyleSheet(
            f"color: {color}; font-size: {theme.VERDICT_SIZE}px; "
            f"font-weight: {theme.VERDICT_WEIGHT};"
        )
        self.action_label.setText(action)
        self.certainty_label.setText(certainty)
        self.setStyleSheet(
            f"QFrame#VerdictBanner {{ background: {background}; "
            f"border: 1px solid {color}33; border-left: 4px solid {color}; "
            f"border-radius: {theme.RADIUS_CARD}px; }}"
        )


class ScoreBar(QWidget):
    """Score track with pass/review/fail zones and a threshold marker."""

    def __init__(self, threshold: float = 0.46, delta: float = 0.05,
                 parent=None):
        super().__init__(parent)
        self.threshold = threshold
        self.delta = delta
        self.score: float | None = None
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_score(self, score: float | None, threshold: float | None = None,
                  delta: float | None = None) -> None:
        self.score = score
        if threshold is not None:
            self.threshold = threshold
        if delta is not None:
            self.delta = delta
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        track_y, track_h = 6, 16

        def x_of(value: float) -> int:
            return max(0, min(width, int(value * width)))

        low = max(0.0, self.threshold - self.delta)
        high = min(1.0, self.threshold + self.delta)

        painter.fillRect(0, track_y, width, track_h, QColor(theme.BORDER))
        painter.fillRect(0, track_y, x_of(low), track_h, QColor(theme.PASS_BG))
        painter.fillRect(x_of(high), track_y, width - x_of(high), track_h,
                         QColor(theme.FAIL_BG))
        painter.fillRect(x_of(low), track_y, x_of(high) - x_of(low), track_h,
                         QColor(theme.REVIEW_BG))

        if self.score is not None:
            color = QColor(theme.ACCENT)
            if self.score >= high:
                color = QColor(theme.FAIL)
            elif self.score >= low:
                color = QColor(theme.REVIEW)
            else:
                color = QColor(theme.PASS)
            painter.fillRect(0, track_y, x_of(self.score), track_h, color)

        painter.setPen(QColor(theme.TEXT))
        painter.fillRect(x_of(self.threshold) - 1, track_y - 4, 2, track_h + 8,
                         QColor(theme.TEXT))

        painter.setPen(QColor(theme.MUTED))
        small = QFont(self.font())
        small.setPointSize(9)
        painter.setFont(small)
        painter.drawText(0, 42, f"0")
        painter.drawText(width - 14, 42, "1")
        label = f"threshold {self.threshold:.2f} · band ±{self.delta:.2f}"
        painter.drawText(x_of(self.threshold) - 70, 42, label)

        if self.score is not None:
            painter.setPen(QColor(theme.TEXT))
            bold = QFont(self.font())
            bold.setPointSize(12)
            bold.setBold(True)
            painter.setFont(bold)
            text = f"{self.score:.2f}"
            metrics = painter.fontMetrics()
            painter.drawText(width - metrics.horizontalAdvance(text), 14, text)


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
