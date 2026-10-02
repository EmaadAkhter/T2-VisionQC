"""Inspect page: capture a unit, run the local model, show and log the result."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from db import database as db
from desktop.auth import AuthService, OrgContext
from desktop.model_store import ModelStore, log_inspection, run_inspection
from desktop.theme import REVIEW, VERDICT_COLORS
from desktop.ui.widgets import bgr_to_pixmap, card, muted
from desktop.worker import FunctionWorker


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

        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(40)
        self.preview_timer.timeout.connect(self._pull_frame)

        self._build_ui()
        self._refresh_model_status()
        self._refresh_recent()

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Inspect")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)
        self.model_status = muted("")
        header.addWidget(self.model_status)
        root.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addWidget(self._build_capture_card(), 3)
        columns.addWidget(self._build_result_card(), 2)
        root.addLayout(columns, 1)

        root.addWidget(self._build_recent_card(), 1)

    def _build_capture_card(self) -> QWidget:
        frame, layout = card("Capture unit")

        self.preview_label = QLabel("Camera preview\n\nStart the camera or upload an image")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(480, 360)
        self.preview_label.setStyleSheet(
            "background:#0f172a; color:#94a3b8; border-radius:8px;"
        )
        layout.addWidget(self.preview_label, 1)

        controls = QHBoxLayout()
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

        self.verdict_label = QLabel("—")
        self.verdict_label.setObjectName("Metric")
        self.verdict_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.verdict_label)

        self.score_bar = QProgressBar()
        self.score_bar.setRange(0, 100)
        self.score_bar.setFormat("score —")
        layout.addWidget(self.score_bar)

        self.score_detail = muted("")
        layout.addWidget(self.score_detail)

        self.certainty_label = muted("")
        layout.addWidget(self.certainty_label)

        self.explanation_label = muted("")
        self.explanation_label.setWordWrap(True)
        layout.addWidget(self.explanation_label)

        self.setup_label = muted("")
        layout.addWidget(self.setup_label)

        self.overlay_label = QLabel("")
        self.overlay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_label.setMinimumHeight(180)
        layout.addWidget(self.overlay_label, 1)

        self.review_row = QHBoxLayout()
        self.accept_button = QPushButton("Accept as PASS")
        self.accept_button.setObjectName("Pass")
        self.accept_button.clicked.connect(lambda: self._review("PASS"))
        self.reject_button = QPushButton("Reject as FAIL")
        self.reject_button.setObjectName("Reject")
        self.reject_button.clicked.connect(lambda: self._review("FAIL"))
        self.review_row.addWidget(self.accept_button)
        self.review_row.addWidget(self.reject_button)
        self.review_row_widget = QWidget()
        self.review_row_widget.setLayout(self.review_row)
        self.review_row_widget.setVisible(False)
        layout.addWidget(self.review_row_widget)
        return frame

    def _build_recent_card(self) -> QWidget:
        frame, layout = card("Recent inspections")
        self.recent_grid = QGridLayout()
        layout.addLayout(self.recent_grid)
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
                f"Could not open camera {index}. Check permissions or use upload.",
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
        frame = getattr(self, "current_frame", None)
        if frame is None:
            return
        self.pending_image = frame.copy()
        self.preview_label.setPixmap(bgr_to_pixmap(frame, 640, 420))
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
        self.model_status.setText(
            f"Active model: {version}" if version
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
        self.inspect_button.setEnabled(False)
        self.inspect_button.setText("Analysing…")
        worker = FunctionWorker(run_inspection, self.pending_image, model, settings)
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

        verdict = result["verdict"]
        color = VERDICT_COLORS[verdict]
        self.verdict_label.setText(verdict)
        self.verdict_label.setStyleSheet(f"color: {color}; font-size: 26px; font-weight: 700;")

        self.score_bar.setValue(int(round(result["score"] * 100)))
        self.score_bar.setFormat(f"score {result['score']:.2f}")
        self.score_detail.setText(
            f"Threshold {result['threshold']:.2f} · review band ±{result['delta']:.2f} · "
            f"{result['latency_ms']} ms"
        )
        self.certainty_label.setText(
            f"Decision certainty: {result['certainty']} — {result['certainty_reason']}"
        )
        self.explanation_label.setText(f"“{result['explanation']}”")
        self.setup_label.setText(
            f"Setup: {result['setup_status']} · "
            + "; ".join(result["setup_reasons"])
        )
        self.overlay_label.setPixmap(
            bgr_to_pixmap(result["overlay"], 420, 240)
        )
        self.review_row_widget.setVisible(verdict == "REVIEW")
        if verdict == "REVIEW":
            self.review_row_widget.setStyleSheet(f"color: {REVIEW};")

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
        self.review_row_widget.setVisible(False)
        self.status_bar.showMessage(f"{self.last_uid} recorded as {disposition}", 4000)
        self._refresh_recent()

    # ----------------------------------------------------------------- recent

    def _refresh_recent(self) -> None:
        while self.recent_grid.count():
            item = self.recent_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        headers = ["Time", "UID", "Score", "Verdict", "Disposition"]
        for col, text in enumerate(headers):
            label = QLabel(text)
            label.setStyleSheet("font-weight:600; color:#64748b;")
            self.recent_grid.addWidget(label, 0, col)

        rows = db.get_inspections(limit=6)
        for row_idx, row in enumerate(rows, start=1):
            values = [
                str(row.get("timestamp", ""))[11:19],
                row.get("uid", ""),
                f"{row.get('score') or 0:.2f}",
                row.get("verdict", ""),
                row.get("disposition", ""),
            ]
            for col, text in enumerate(values):
                label = QLabel(text)
                if col == 3:
                    label.setStyleSheet(
                        f"color: {VERDICT_COLORS.get(text, '#0f172a')}; font-weight:600;"
                    )
                self.recent_grid.addWidget(label, row_idx, col)
