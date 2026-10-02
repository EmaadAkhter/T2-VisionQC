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

# Error kinds
OPEN_FAILED = "open_failed"
STREAM_LOST = "stream_lost"

_CAMERA_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"
)

# QThreads that did not stop within the timeout are kept referenced here until
# they finish on their own: deleting a running QThread crashes the process.
_ORPHANS: list[QThread] = []


def safe_stop(thread: QThread, timeout_ms: int = 3000) -> None:
    """Set ``running = False`` and wait; never let a running thread be freed."""
    if hasattr(thread, "running"):
        thread.running = False
    if thread.wait(timeout_ms):
        return
    _ORPHANS.append(thread)

    def _release() -> None:
        if thread in _ORPHANS:
            _ORPHANS.remove(thread)

    thread.finished.connect(_release)


class CameraStream(QThread):
    """Continuously read frames from a USB index or RTSP URL.

    Signals:
        opened: capture opened successfully
        frame_ready(object): a BGR numpy frame
        error(str): one of OPEN_FAILED / STREAM_LOST
    """

    opened = Signal()
    frame_ready = Signal(object)
    error = Signal(str)

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
        try:
            capture = cv2.VideoCapture(self._source())
        except Exception:  # noqa: BLE001 - report, never crash the thread
            self.error.emit(OPEN_FAILED)
            return
        if not capture.isOpened():
            capture.release()
            self.error.emit(OPEN_FAILED)
            return
        self.opened.emit()

        misses = 0
        while self.running:
            ok, frame = capture.read()
            if not ok:
                misses += 1
                # ~1.2 s of failed reads means the device is gone.
                if misses > 30:
                    capture.release()
                    if self.running:
                        self.error.emit(STREAM_LOST)
                    return
                self.msleep(self.interval_ms)
                continue
            misses = 0
            self.frame_ready.emit(frame)
            self.msleep(self.interval_ms)
        capture.release()

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
