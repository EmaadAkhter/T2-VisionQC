"""Phone pairing: render the QR and watch for the phone to come online.

The QR carries only a short-lived, single-use token; the phone exchanges it
for camera credentials over HTTPS. Nothing secret is visible in the code.
"""

from __future__ import annotations

import json
import time
from io import BytesIO

import httpx
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from desktop import theme
from desktop.ui.widgets import muted

POPULAR_RELAY_URL = "https://relay.tavesglobal.com"


def load_relay_settings() -> tuple[str, str]:
    """Relay URL + dashboard token, remembered on this machine."""
    settings = QSettings("VisionQC", "Desktop")
    url = str(settings.value("relay/url", POPULAR_RELAY_URL)
              or POPULAR_RELAY_URL)
    token = str(settings.value("relay/token", "") or "")
    return url, token


def save_relay_settings(url: str, token: str) -> None:
    settings = QSettings("VisionQC", "Desktop")
    settings.setValue("relay/url", url)
    settings.setValue("relay/token", token)


def qr_pixmap(payload: str, size: int = 220) -> QPixmap:
    """Render a QR code for the pairing payload."""
    import qrcode

    image = qrcode.make(payload)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue())
    return pixmap.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def relay_ws_url(base_url: str) -> str:
    """`https://host` -> `wss://host` (and http/ws accepted)."""
    base = base_url.rstrip("/")
    return base.replace("https://", "wss://").replace("http://", "ws://")


def pairing_payload(server_ws: str, token: str) -> str:
    return json.dumps({"server": server_ws, "token": token})


class PairingDialog(QDialog):
    """Show the pairing QR and flag the moment the phone connects."""

    def __init__(self, health_url: str, camera_id: str, server_ws: str,
                 token: str, expires_in: int = 600, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect phone")
        self.setMinimumWidth(380)
        self._health_url = health_url.rstrip("/")
        self._camera_id = camera_id
        self._deadline = time.time() + expires_in

        layout = QVBoxLayout(self)
        layout.setSpacing(theme.SPACE_S)
        layout.setContentsMargins(24, 20, 24, 20)

        heading = QLabel("Scan with the VisionQC phone app")
        heading.setStyleSheet("font-size: 16px; font-weight: 600;")
        heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(heading)

        layout.addWidget(muted(
            "Open the app and tap “Scan QR”. The phone connects by itself; "
            "no typing needed."
        ))

        qr_label = QLabel()
        qr_label.setPixmap(qr_pixmap(pairing_payload(server_ws, token), 220))
        qr_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(qr_label)

        code_row = QHBoxLayout()
        code = QLabel(token)
        code.setStyleSheet(
            "font-family: 'IBM Plex Mono', Menlo, Consolas; font-size: 12px;"
        )
        code_row.addWidget(code, 1)
        copy = QPushButton("Copy code")
        copy.clicked.connect(lambda: self._copy(token))
        code_row.addWidget(copy)
        layout.addLayout(code_row)

        self.status = muted("")
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.status)

        close = QPushButton("Done")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._check_online)
        self._timer.start()
        self._refresh_status(connected=False)

    # -------------------------------------------------------------- behaviour

    def _copy(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.status.setText("Code copied — you can also type it in the app.")
        self.status.setStyleSheet(f"color: {theme.PASS};")

    def _check_online(self) -> None:
        try:
            response = httpx.get(f"{self._health_url}/health", timeout=3)
            online = response.json().get("online_cameras", [])
        except Exception:  # noqa: BLE001 - server may be briefly unreachable
            return
        if self._camera_id in online:
            self._timer.stop()
            self.status.setText("Phone connected ✓ — you can close this window.")
            self.status.setStyleSheet(f"color: {theme.PASS};")
            return
        self._refresh_status(connected=False)

    def _refresh_status(self, connected: bool) -> None:
        if connected:
            return
        remaining = int(self._deadline - time.time())
        if remaining <= 0:
            self.status.setText("Code expired — create a new one.")
            self.status.setStyleSheet(f"color: {theme.FAIL};")
            self._timer.stop()
        else:
            self.status.setText(
                f"Waiting for the phone… code expires in {max(1, remaining // 60)} min."
            )
            self.status.setStyleSheet(f"color: {theme.MUTED};")

    def done(self, result: int) -> None:  # noqa: D102
        self._timer.stop()
        super().done(result)
