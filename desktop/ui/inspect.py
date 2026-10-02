"""Inspect page: capture a unit, run the local model, show and log the result."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import database as db
from desktop import theme
from desktop.auth import AuthService, OrgContext
from desktop.model_store import ModelStore, log_inspection, run_inspection
from desktop.ui.widgets import (
    ScoreBar,
    VerdictBanner,
    bgr_to_pixmap,
    card,
    caption,
    make_table,
    muted,
    page_header,
)
from desktop.worker import FunctionWorker

ACTION_TEXT = {
    "PASS": "Release unit.",
    "REVIEW": "Inspect the highlighted area manually, then record a decision.",
    "FAIL": "Set aside and inspect the highlighted area.",
}


class InspectPage(QWidget):
    def __init__(self, auth: AuthService, org: OrgContext, status_bar,
                 sync_engine=None):
        super().__init__()
        self.auth = auth
        self.org = org
        self.status_bar = status_bar
        self.sync_engine = sync_engine
        self.model_store = ModelStore()
        self.capture: cv2.VideoCapture | None = None
        self.pending_image: np.ndarray | None = None
        self.last_uid: str | None = None
        self.current_frame: np.ndarray | None = None

        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(40)
        self.preview_timer.timeout.connect(self._pull_frame)

        self._build_ui()
        self._refresh_model_status()
        self._refresh_recent()

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.PAGE_MARGIN, theme.PAGE_MARGIN,
                                theme.PAGE_MARGIN, theme.PAGE_MARGIN)
        root.setSpacing(theme.SPACE_M)

        header = QHBoxLayout()
        header.addWidget(page_header(
            "Inspect", "Capture a unit and inspect it on this machine"
        ), 1)
        self.model_status = caption("")
        header.addWidget(self.model_status, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(theme.SPACE_M)
        columns.addWidget(self._build_capture_card(), 3)
        columns.addWidget(self._build_result_card(), 2)
        root.addLayout(columns, 1)

        root.addWidget(self._build_recent_card(), 1)

    def _build_capture_card(self) -> QWidget:
        frame, layout = card("Capture unit")

        self.preview_label = QLabel(
            "Start the camera or upload an image to begin"
        )
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(480, 340)
        self.preview_label.setStyleSheet(
            f"background: {theme.SIDEBAR_BG}; color: {theme.SIDEBAR_TEXT}; "
            f"border-radius: {theme.RADIUS_CONTROL}px;"
        )
        layout.addWidget(self.preview_label, 1)

        controls = QHBoxLayout()
        controls.setSpacing(theme.SPACE_S)
        self.camera_combo = QComboBox()
        self.camera_combo.addItem("Camera 0 (built-in)", 0)
        self.camera_combo.addItem("Camera 1", 1)
        controls.addWidget(self.camera_combo)

        self.camera_button = QPushButton("Start camera")
        self.camera_button.clicked.connect(self._toggle_camera)
        controls.addWidget(self.camera_button)

        self.capture_button = QPushButton("Capture")
        self.capture_button.setEnabled(False)
        self.capture_button.clicked.connect(self._capture_frame)
        controls.addWidget(self.capture_button)

        upload = QPushButton("Upload image…")
        upload.clicked.connect(self._upload_image)
        controls.addWidget(upload)
        layout.addLayout(controls)

        self.inspect_button = QPushButton("Inspect")
        self.inspect_button.setObjectName("Primary")
        self.inspect_button.setEnabled(False)
        self.inspect_button.clicked.connect(self._inspect)
        layout.addWidget(self.inspect_button)
        return frame

    def _build_result_card(self) -> QWidget:
        frame, layout = card("Result")

        self.banner = VerdictBanner()
        layout.addWidget(self.banner)

        self.score_bar = ScoreBar()
        layout.addWidget(self.score_bar)

        self.explanation_label = muted("")
        layout.addWidget(self.explanation_label)

        self.detail_label = caption("")
        layout.addWidget(self.detail_label)

        self.overlay_label = QLabel("")
        self.overlay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_label.setMinimumHeight(170)
        layout.addWidget(self.overlay_label, 1)

        review_row = QHBoxLayout()
        review_row.setSpacing(theme.SPACE_S)
        self.accept_button = QPushButton("Accept as PASS")
        self.accept_button.setObjectName("Pass")
        self.accept_button.clicked.connect(lambda: self._review("PASS"))
        self.reject_button = QPushButton("Reject as FAIL")
        self.reject_button.setObjectName("Reject")
        self.reject_button.clicked.connect(lambda: self._review("FAIL"))
        review_row.addWidget(self.accept_button)
        review_row.addWidget(self.reject_button)
        self.review_widget = QWidget()
        self.review_widget.setLayout(review_row)
        self.review_widget.setVisible(False)
        layout.addWidget(self.review_widget)
        return frame

    def _build_recent_card(self) -> QWidget:
        frame, layout = card("Recent inspections")
        self.recent_table = make_table(
            ["Time", "Inspection", "Score", "Verdict", "Disposition"]
        )
        self.recent_table.setMaximumHeight(220)
        layout.addWidget(self.recent_table)
        return frame

    # ----------------------------------------------------------------- camera

    def _toggle_camera(self) -> None:
        if self.capture is not None:
            self._stop_camera()
            return
        index = self.camera_combo.currentData()
        capture = cv2.VideoCapture(index)
        if not capture.isOpened():
            QMessageBox.warning(
                self, "Camera unavailable",
                f"Could not open camera {index}. Check permissions or use "
                "the upload fallback.",
            )
            return
        self.capture = capture
        self.camera_button.setText("Stop camera")
        self.capture_button.setEnabled(True)
        self.preview_timer.start()

    def _stop_camera(self) -> None:
        self.preview_timer.stop()
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        self.camera_button.setText("Start camera")
        self.capture_button.setEnabled(False)

    def _pull_frame(self) -> None:
        if self.capture is None:
            return
        ok, frame = self.capture.read()
        if not ok:
            return
        self.current_frame = frame
        self.preview_label.setPixmap(bgr_to_pixmap(frame, 640, 420))

    def _capture_frame(self) -> None:
        if self.current_frame is None:
            return
        self.pending_image = self.current_frame.copy()
        self.preview_label.setPixmap(bgr_to_pixmap(self.pending_image, 640, 420))
        self.inspect_button.setEnabled(True)

    def _upload_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an image", "", "Images (*.png *.jpg *.jpeg)"
        )
        if not path:
            return
        image = cv2.imread(path)
        if image is None:
            QMessageBox.warning(self, "Unreadable image",
                                "The selected file could not be decoded.")
            return
        self.pending_image = image
        self.preview_label.setPixmap(bgr_to_pixmap(image, 640, 420))
        self.inspect_button.setEnabled(True)

    # ---------------------------------------------------------------- inspect

    def _refresh_model_status(self) -> None:
        version = self.model_store.active_version()
        profile = self.model_store.active_profile()
        parts = []
        if version:
            parts.append(f"Model {version[:14]}…")
        if profile:
            parts.append(f"Profile “{profile['name']}”")
        self.model_status.setText(
            " · ".join(parts) if parts
            else "No model trained yet — open Train first."
        )

    def _inspect(self) -> None:
        if self.pending_image is None:
            return
        model = self.model_store.get()
        if model is None:
            QMessageBox.warning(self, "No model",
                                "Train a model on the Train page first.")
            return
        settings = db.get_settings()
        artifacts = self.model_store.artifacts()
        self.inspect_button.setEnabled(False)
        self.inspect_button.setText("Analysing…")
        worker = FunctionWorker(run_inspection, self.pending_image, model,
                                settings, artifacts)
        worker.finished_ok.connect(lambda result: self._show_result(result, model))
        worker.failed.connect(self._inspect_failed)
        self._inspect_worker = worker
        worker.start()

    def _inspect_failed(self, trace: str) -> None:
        self.inspect_button.setEnabled(True)
        self.inspect_button.setText("Inspect")
        QMessageBox.critical(self, "Inspection failed", trace[-800:])

    def _show_result(self, result: dict, model) -> None:
        self.inspect_button.setEnabled(True)
        self.inspect_button.setText("Inspect")

        if result.get("no_product"):
            self.banner.set_result(
                "NONE", "No product detected in the expected region. "
                        "Adjust the unit or camera and capture again — "
                        "nothing was logged."
            )
            self.score_bar.set_score(None)
            self.explanation_label.setText("")
            self.detail_label.setText("")
            self.review_widget.setVisible(False)
            self.status_bar.showMessage("No product — not logged", 4000)
            return

        verdict = result["verdict"]
        certainty = f"Decision certainty: {result['certainty']} — {result['certainty_reason']}"
        self.banner.set_result(verdict, ACTION_TEXT.get(verdict, ""), certainty)
        self.score_bar.set_score(result["score"], result["threshold"],
                                 result["delta"])
        self.explanation_label.setText(result["explanation"])
        details = []
        if result.get("region_label"):
            details.append(f"Location {result['region_label']}")
        details.append(f"Area {result['area_pct']:.1f}%")
        details.append(f"Setup {result['setup_status']}")
        details.append(f"{result['latency_ms']} ms")
        self.detail_label.setText(" · ".join(details))
        self.overlay_label.setPixmap(rgb_to_pixmap_safe(result["overlay"]))
        self.review_widget.setVisible(verdict == "REVIEW")

        uid = log_inspection(result, self.pending_image, model.model_version)
        self.last_uid = uid
        self.status_bar.showMessage(f"Logged {uid}", 4000)
        if self.sync_engine is not None:
            self.sync_engine.kick()
        self._refresh_recent()

    def _review(self, disposition: str) -> None:
        if not self.last_uid:
            return
        rows = db.get_inspections(limit=200)
        target = next((r for r in rows if r["uid"] == self.last_uid), None)
        if target is None:
            return
        db.update_disposition(target["id"], disposition, override=True,
                              note=f"Reviewed from desktop as {disposition}")
        self.review_widget.setVisible(False)
        self.status_bar.showMessage(
            f"{self.last_uid} recorded as {disposition}", 4000
        )
        self._refresh_recent()

    # ----------------------------------------------------------------- recent

    def _refresh_recent(self) -> None:
        rows = db.get_inspections(limit=6)
        self.recent_table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            values = [
                str(row.get("timestamp", ""))[11:19],
                row.get("uid", ""),
                f"{row.get('score') or 0:.2f}",
                row.get("verdict", ""),
                row.get("disposition", ""),
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 3 and text in theme.VERDICT_COLORS:
                    item.setForeground(QColor(theme.VERDICT_COLORS[text]))
                self.recent_table.setItem(row_idx, col, item)
        self.recent_table.resizeColumnsToContents()


def rgb_to_pixmap_safe(image: np.ndarray):
    from desktop.ui.widgets import rgb_to_pixmap

    return rgb_to_pixmap(image, 420, 230)
