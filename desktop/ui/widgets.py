"""Small shared Qt widgets and image helpers."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


def bgr_to_pixmap(image: np.ndarray, max_width: int, max_height: int) -> QPixmap:
    """Convert a BGR numpy image to a scaled QPixmap (keeps aspect ratio)."""
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    height, width, _ = rgb.shape
    scale = min(max_width / width, max_height / height, 1.0)
    target_w = max(1, int(width * scale))
    target_h = max(1, int(height * scale))
    if scale < 1.0:
        rgb = cv2.resize(rgb, (target_w, target_h),
                         interpolation=cv2.INTER_AREA)
    rgb = np.ascontiguousarray(rgb)
    qimage = QImage(rgb.data, rgb.shape[1], rgb.shape[0],
                    rgb.strides[0], QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


def rgb_to_pixmap(image: np.ndarray, max_width: int, max_height: int) -> QPixmap:
    return bgr_to_pixmap(cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
                         max_width, max_height)


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    """Create a styled card frame with an optional title."""
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)
    if title:
        label = QLabel(title)
        label.setObjectName("CardTitle")
        layout.addWidget(label)
    return frame, layout


def muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label
