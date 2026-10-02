"""Camera capture helpers shared by the Inspect and Cameras pages.

Opening a camera on macOS triggers a TCC permission prompt the first time.
That call must never run on the Qt main thread: it can block for as long as
the user takes to answer and, when the bundle lacks the usage description,
the OS aborts the process. ``CameraStream`` opens and reads frames entirely
inside its worker thread and reports typed errors back to the UI.
"""

from __future__ import annotations

import sys

import cv2
from PySide6.QtCore import QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from desktop.worker import safe_stop  # noqa: F401 - re-exported for pages

# Error kinds
OPEN_FAILED = "open_failed"
STREAM_LOST = "stream_lost"

_CAMERA_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"
)


class CameraStream(QThread):
    """Continuously read frames from a USB index or RTSP URL.

    Signals:
        opened: capture opened successfully
        frame_ready(object): a BGR numpy frame
        error(str): one of OPEN_FAILED / STREAM_LOST

    A stream that drops after opening is retried a few times before the
    error is reported, so a brief USB/network hiccup does not end preview.
    """

    opened = Signal()
    frame_ready = Signal(object)
    error = Signal(str)

    MAX_RECONNECTS = 3
    RECONNECT_DELAY_MS = 2000

    def __init__(self, kind: str, address: str, interval_ms: int = 40):
        super().__init__()
        self.kind = kind
        self.address = address
        self.interval_ms = interval_ms
        self.running = True

    def _source(self) -> int | str:
        if self.kind == "usb" and str(self.address).isdigit():
            return int(self.address)
        return self.address

    def run(self) -> None:  # noqa: D102
        reconnects = 0
        first_open = True
        while self.running:
            try:
                capture = cv2.VideoCapture(self._source())
            except Exception:  # noqa: BLE001 - report, never crash the thread
                self.error.emit(OPEN_FAILED if first_open else STREAM_LOST)
                return
            if not capture.isOpened():
                capture.release()
                if first_open:
                    self.error.emit(OPEN_FAILED)
                    return
                reconnects += 1
                if reconnects > self.MAX_RECONNECTS:
                    self.error.emit(STREAM_LOST)
                    return
                self.msleep(self.RECONNECT_DELAY_MS)
                continue

            if first_open:
                first_open = False
                self.opened.emit()

            misses = 0
            stable = 0
            while self.running:
                ok, frame = capture.read()
                if ok:
                    misses = 0
                    stable += 1
                    if stable == 25:
                        # About a second of healthy frames: the stream is
                        # stable again, restore the reconnect budget.
                        reconnects = 0
                    self.frame_ready.emit(frame)
                    self.msleep(self.interval_ms)
                    continue
                stable = 0
                misses += 1
                # ~1.2 s of failed reads: treat the device as lost.
                if misses > 30:
                    break
                self.msleep(self.interval_ms)
            capture.release()

            if not self.running:
                return
            reconnects += 1
            if reconnects > self.MAX_RECONNECTS:
                self.error.emit(STREAM_LOST)
                return
            self.msleep(self.RECONNECT_DELAY_MS)

    def stop(self) -> None:
        safe_stop(self)


def camera_error_message(kind: str, address: str) -> str:
    """User-facing text for a camera failure."""
    if kind == STREAM_LOST:
        return (
            f"The stream from {address} was lost. Check the cable/network "
            "and start the preview again."
        )
    if sys.platform == "darwin":
        return (
            f"Could not open {address}.\n\n"
            "If macOS asked for camera access, allow it. Otherwise open "
            "System Settings → Privacy & Security → Camera and enable "
            "VisionQC, then try again."
        )
    return (
        f"Could not open {address}. Check that the camera is connected and "
        "not in use by another application."
    )


def show_camera_error(parent, kind: str, address: str) -> None:
    """Error dialog with a shortcut to macOS camera privacy settings."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Camera unavailable")
    box.setText(camera_error_message(kind, address))
    if sys.platform == "darwin":
        settings_button = box.addButton(
            "Open System Settings", QMessageBox.ButtonRole.ActionRole
        )
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is settings_button:
            QDesktopServices.openUrl(QUrl(_CAMERA_SETTINGS_URL))
        return
    box.addButton(QMessageBox.StandardButton.Close)
    box.exec()
