"""Mask editor: brush add/remove over an auto-proposed canonical mask.

The operator approves the product region once during onboarding. Left-drag
paints "inside", right-drag erases, brush size is adjustable, and every stroke
is undoable. This is the only manual step in the whole pipeline.
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from service.inference import INPUT_SIZE


class MaskEditorDialog(QDialog):
    """Edit a boolean mask at the model input size."""

    def __init__(self, image_bgr: np.ndarray, mask: np.ndarray, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Approve product mask")
        self.image = cv2.resize(image_bgr, INPUT_SIZE)
        self.mask = (mask.astype(np.uint8) > 0).astype(np.uint8)
        self._undo: list[np.ndarray] = []
        self._painting = False
        self._paint_value = 1

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Left-drag: include · Right-drag: exclude · "
            "The green area is scored at runtime. Approve when the product "
            "region looks right."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.canvas = QLabel()
        self.canvas.setFixedSize(*INPUT_SIZE)
        self.canvas.setMouseTracking(True)
        layout.addWidget(self.canvas)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Brush"))
        self.brush_slider = QSlider(Qt.Orientation.Horizontal)
        self.brush_slider.setRange(4, 80)
        self.brush_slider.setValue(24)
        controls.addWidget(self.brush_slider, 1)

        undo_button = QPushButton("Undo")
        undo_button.clicked.connect(self._undo_stroke)
        controls.addWidget(undo_button)
        reset_button = QPushButton("Reset")
        reset_button.clicked.connect(self._reset)
        controls.addWidget(reset_button)
        layout.addLayout(controls)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._render()

    # ------------------------------------------------------------- rendering

    def _render(self) -> None:
        base = cv2.cvtColor(self.image, cv2.COLOR_BGR2RGB)
        overlay = base.copy()
        tint = np.zeros_like(base)
        tint[..., 1] = 255
        active = self.mask > 0
        overlay[active] = (
            0.55 * base[active].astype(np.float32)
            + 0.45 * tint[active].astype(np.float32)
        ).clip(0, 255).astype(np.uint8)
        contours, _ = cv2.findContours(
            (self.mask * 255).astype(np.uint8),
            cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
        )
        cv2.drawContours(overlay, contours, -1, (255, 40, 40), 2)
        overlay = np.ascontiguousarray(overlay)
        qimage = QImage(overlay.data, overlay.shape[1], overlay.shape[0],
                        overlay.strides[0], QImage.Format.Format_RGB888)
        self.canvas.setPixmap(QPixmap.fromImage(qimage.copy()))

    # -------------------------------------------------------------- painting

    def _paint_at(self, pos: QPoint, value: int) -> None:
        x = int(pos.x() * self.mask.shape[1] / self.canvas.width())
        y = int(pos.y() * self.mask.shape[0] / self.canvas.height())
        radius = max(2, self.brush_slider.value() // 2)
        cv2.circle(self.mask, (x, y), radius, int(value), -1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.canvas.geometry().contains(event.position().toPoint()):
            self._undo.append(self.mask.copy())
            self._painting = True
            self._paint_value = (
                1 if event.button() == Qt.MouseButton.LeftButton else 0
            )
            self._paint_at(self._to_canvas(event.position().toPoint()),
                           self._paint_value)
            self._render()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._painting:
            self._paint_at(self._to_canvas(event.position().toPoint()),
                           self._paint_value)
            self._render()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._painting = False

    def _to_canvas(self, global_pos: QPoint) -> QPoint:
        local = self.canvas.mapFrom(self, global_pos)
        return local

    def _undo_stroke(self) -> None:
        if self._undo:
            self.mask = self._undo.pop()
            self._render()

    def _reset(self) -> None:
        if self._undo:
            self.mask = self._undo[0]
            self._undo.clear()
            self._render()

    def approved_mask(self) -> np.ndarray:
        return self.mask.astype(bool)
