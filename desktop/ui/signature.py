"""VisionQC signature widgets: ToleranceStrip, VerdictGlyph, VerdictBanner.

Drop-in replacements for ScoreBar and VerdictBanner in widgets.py:

    from desktop.ui.signature import ToleranceStrip as ScoreBar, VerdictBanner

Verdicts are readable without colour: circle = PASS, diamond = REVIEW,
square = FAIL.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from desktop import theme


def _mono(size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(theme.MONO)
    font.setPointSize(size)
    font.setBold(bold)
    return font


class VerdictGlyph(QWidget):
    """Circle = PASS, diamond = REVIEW, square = FAIL. Readable without colour."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.verdict = "NONE"
        self.setFixedSize(40, 40)

    def set_verdict(self, verdict: str) -> None:
        self.verdict = verdict
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        color = theme.VERDICT_COLORS.get(self.verdict)
        if color is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        if self.verdict == "PASS":
            painter.drawEllipse(QRectF(6, 6, 28, 28))
        elif self.verdict == "REVIEW":
            painter.drawPolygon(QPolygonF([
                QPointF(20, 3), QPointF(37, 20),
                QPointF(20, 37), QPointF(3, 20),
            ]))
        else:
            painter.drawRoundedRect(QRectF(7, 7, 26, 26), 3, 3)


class VerdictBanner(QFrame):
    """Verdict glyph, large label, next action and certainty."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VerdictBanner")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(14)
        self.glyph = VerdictGlyph()
        row.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        self.verdict_label = QLabel("")
        row.addWidget(self.verdict_label, 0, Qt.AlignmentFlag.AlignVCenter)
        column = QVBoxLayout()
        column.setSpacing(2)
        self.action_label = QLabel("")
        self.action_label.setWordWrap(True)
        self.certainty_label = QLabel("")
        self.certainty_label.setObjectName("Caption")
        column.addWidget(self.action_label)
        column.addWidget(self.certainty_label)
        row.addLayout(column, 1)
        self.set_result("NONE", "")

    def set_result(self, verdict: str, action: str, certainty: str = "") -> None:
        color = theme.VERDICT_COLORS.get(verdict, theme.MUTED)
        self.glyph.set_verdict(verdict)
        self.verdict_label.setText("" if verdict == "NONE" else verdict)
        self.verdict_label.setStyleSheet(
            f"color:{color}; font-family:'IBM Plex Mono',Menlo,Consolas;"
            f"font-size:{theme.VERDICT_SIZE}px; font-weight:{theme.VERDICT_WEIGHT};"
            "letter-spacing:2px; border:none;"
        )
        self.action_label.setText(action)
        self.certainty_label.setText(certainty)
        self.setStyleSheet(
            f"QFrame#VerdictBanner {{ background:{theme.SURFACE}; "
            f"border:1px solid {theme.SEAM}; border-left:5px solid {color}; "
            f"border-radius:{theme.RADIUS_CARD}px; }}"
            "QFrame#VerdictBanner QLabel { border:none; background:transparent; }"
        )


class ToleranceStrip(QWidget):
    """Score against learned normal: hatched normal zone, review slot,
    dashed 'worst good unit' tick at 0.50, threshold line, score marker."""

    WORST_GOOD = 0.50

    def __init__(self, threshold: float = 0.46, delta: float = 0.05,
                 parent=None):
        super().__init__(parent)
        self.threshold, self.delta = threshold, delta
        self.score: float | None = None
        self.setMinimumHeight(78)
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
        left, width, top, height = 2, max(10, self.width() - 4), 36, 20

        def x_of(value: float) -> float:
            return left + max(0.0, min(1.0, value)) * width

        low = max(0.0, self.threshold - self.delta)
        high = min(1.0, self.threshold + self.delta)

        painter.fillRect(QRectF(x_of(0), top, x_of(low) - x_of(0), height),
                         QColor(theme.PASS_BG))
        painter.fillRect(QRectF(x_of(0), top, x_of(low) - x_of(0), height),
                         QBrush(QColor(theme.PASS), Qt.BrushStyle.BDiagPattern))
        painter.fillRect(QRectF(x_of(low), top, x_of(high) - x_of(low), height),
                         QColor(theme.REVIEW_BG))
        painter.fillRect(QRectF(x_of(high), top, x_of(1) - x_of(high), height),
                         QColor(theme.FAIL_BG))
        painter.setPen(QPen(QColor(theme.REVIEW), 1.5))
        painter.drawRect(QRectF(x_of(low), top, x_of(high) - x_of(low), height))
        painter.setPen(QPen(QColor(theme.SEAM), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(left, top, width, height), 3, 3)

        # ruler ticks
        painter.setFont(_mono(8))
        for index in range(11):
            tick_x = x_of(index / 10)
            painter.setPen(QColor(theme.MUTED))
            painter.drawLine(
                QPointF(tick_x, top + height),
                QPointF(tick_x, top + height + (6 if index % 5 == 0 else 3)),
            )
        painter.drawText(QPointF(left, top + height + 20), "0")
        painter.drawText(QPointF(left + width - 8, top + height + 20), "1")

        # worst good training unit
        worst_x = x_of(self.WORST_GOOD)
        painter.setPen(QPen(QColor(theme.MUTED), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(worst_x, top - 6), QPointF(worst_x, top + height))
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(QPointF(worst_x - 52, 12), "worst good 0.50")

        # threshold
        threshold_x = x_of(self.threshold)
        painter.fillRect(QRectF(threshold_x - 1, top - 3, 2, height + 6),
                         QColor(theme.TEXT))
        painter.drawText(QPointF(max(left, threshold_x - 58), top + height + 20),
                         f"T {self.threshold:.2f} ±{self.delta:.2f}")

        if self.score is not None:
            score_x = x_of(self.score)
            color = (theme.FAIL if self.score >= high
                     else theme.REVIEW if self.score >= low else theme.PASS)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.TEXT))
            painter.drawPolygon(QPolygonF([
                QPointF(score_x - 8, top - 20), QPointF(score_x + 8, top - 20),
                QPointF(score_x + 8, top - 9), QPointF(score_x, top - 1),
                QPointF(score_x - 8, top - 9),
            ]))
            painter.setFont(_mono(15, True))
            painter.setPen(QColor(color))
            text = f"{self.score:.2f}"
            painter.drawText(
                QPointF(left + width - painter.fontMetrics().horizontalAdvance(text), 14),
                text,
            )
