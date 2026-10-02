"""Inspect page: live camera view with a side-by-side anomaly heatmap.

The camera feeds frames continuously; a worker thread runs the model on the
newest frame and the page shows the original view (with the product outline)
next to the calibrated heatmap. A FAIL that persists for a couple of frames is
logged automatically, so defective units are captured without any clicking.
"""

from __future__ import annotations

import time

import cv2
import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import database as db
from desktop import theme
from desktop.auth import AuthService, OrgContext
from desktop.live_logic import AutoCaptureDecider
from desktop.model_store import ModelStore, log_inspection, run_inspection
from desktop.server_client import ServerClient
from desktop.ui.camera_stream import CameraStream, show_camera_error
from desktop.ui.errors import show_error
from desktop.ui.pairing import load_relay_settings, save_relay_settings
from desktop.ui.widgets import (
    ScoreBar,
    VerdictBanner,
    bgr_to_pixmap,
    card,
    caption,
    make_table,
    muted,
    page_header,
    rgb_to_pixmap,
)
from desktop.worker import FunctionWorker, safe_stop
from desktop.workers.live_inference_worker import LiveInferenceWorker
from service.foreground import foreground_from_border

ACTION_TEXT = {
    "PASS": "Release unit.",
    "REVIEW": "Inspect the highlighted area manually, then record a decision.",
    "FAIL": "Set aside and inspect the highlighted area.",
}

# A manually logged live result must be reasonably fresh.
LATEST_RESULT_TTL_S = 3.0
# How often to re-check the active profile mask for the outline overlay.
OUTLINE_CHECK_INTERVAL_S = 1.0
# Sentinel: keep the current combo selection when rebuilding the list.
_KEEP_SELECTION = object()


def _fetch_relay_cameras(url: str, token: str) -> list[dict]:
    """Online phone cameras on the relay (called from a worker thread)."""
    client = ServerClient()
    client.configure(url, token)
    cameras = client.list_cameras()
    online = set(client.health().get("online_cameras", []))
    for camera in cameras:
        camera["online"] = camera["id"] in online
    return [camera for camera in cameras if camera["online"]]


class InspectPage(QWidget):
    def __init__(self, auth: AuthService, org: OrgContext, status_bar,
                 sync_engine=None):
        super().__init__()
        self.auth = auth
        self.org = org
        self.status_bar = status_bar
        self.sync_engine = sync_engine
        self.model_store = ModelStore()
        self.stream: CameraStream | None = None
        self.live_worker: LiveInferenceWorker | None = None
        self._live_model = None
        self.latest_result: dict | None = None
        self.latest_result_at = 0.0
        self.last_uid: str | None = None
        self.current_frame: np.ndarray | None = None
        self.decider = AutoCaptureDecider(min_consecutive=2, cooldown_s=3.0)
        self._live_error_shown = False
        self._inspect_worker: FunctionWorker | None = None
        self.relay_client: ServerClient | None = None
        self._relay_camera_id: str | None = None
        self._relay_worker: FunctionWorker | None = None

        # Cached product-outline overlay for the live preview.
        self._outline_key: tuple | None = None
        self._outline_contours: list = []
        self._outline_checked_at = 0.0

        self._build_ui()
        self._refresh_model_combo()
        self._on_camera_index_changed()
        self._refresh_model_status()
        self._refresh_recent()
        # Deferred: constructing the page (smoke test, no event loop) must not
        # leave a fetch worker running behind it.
        QTimer.singleShot(0, self._refresh_relay_cameras)

    # ------------------------------------------------------------- models

    def _selected_source(self) -> tuple[str, str]:
        """("usb", index) or ("relay", camera_id) for the combo selection."""
        data = self.camera_combo.currentData()
        if isinstance(data, tuple) and len(data) == 2:
            return str(data[0]), str(data[1])
        return "usb", "0"

    def _camera_key(self) -> str:
        kind, ident = self._selected_source()
        return f"{kind}:{ident}"

    def _refresh_model_combo(self, select_version=_KEEP_SELECTION) -> None:
        """Rebuild the model picker, keeping or applying a selection.

        ``select_version=None`` explicitly selects "Active model"; omit the
        argument to keep whatever the operator selected.
        """
        current = (self.model_combo.currentData()
                   if select_version is _KEEP_SELECTION else select_version)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem("Active model", None)
        for meta in self.model_store.versions():
            label = meta.get("name") or meta["version"]
            self.model_combo.addItem(label, meta["version"])
        index = self.model_combo.findData(current)
        self.model_combo.setCurrentIndex(index if index >= 0 else 0)
        self.model_combo.blockSignals(False)

    def _on_camera_index_changed(self) -> None:
        assignment = db.get_camera_model(self._camera_key())
        version = assignment.get("model_version") if assignment else None
        self._refresh_model_combo(select_version=version)

    def _on_model_changed(self) -> None:
        db.set_camera_model(self._camera_key(), self.model_combo.currentData())
        if self.live_worker is not None:
            self._restart_live()
        self._refresh_model_status()

    def _selected_model(self):
        """Model for the current camera selection (None -> active model)."""
        return self.model_store.get(self.model_combo.currentData())

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.PAGE_MARGIN, theme.PAGE_MARGIN,
                                theme.PAGE_MARGIN, theme.PAGE_MARGIN)
        root.setSpacing(theme.SPACE_M)

        header = QHBoxLayout()
        header.addWidget(page_header(
            "Inspect",
            "Live inspection — the model runs on the newest camera frame",
        ), 1)
        self.model_status = caption("")
        header.addWidget(self.model_status, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(theme.SPACE_M)
        columns.addWidget(self._build_live_card(), 3)
        columns.addWidget(self._build_result_card(), 2)
        root.addLayout(columns, 1)

        root.addWidget(self._build_recent_card(), 1)

    def _build_live_card(self) -> QWidget:
        frame, layout = card("Live view")

        self.preview_label = QLabel(
            "Start live inspection to see the camera feed"
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
        self.model_combo = QComboBox()
        self.model_combo.setToolTip(
            "Model used for live inference on this camera; saved per camera."
        )
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        controls.addWidget(self.model_combo)

        self.camera_combo = QComboBox()
        self.camera_combo.setToolTip(
            "Local USB cameras and online phone cameras on the relay."
        )
        self.camera_combo.addItem("Camera 0 (built-in)", ("usb", "0"))
        self.camera_combo.addItem("Camera 1", ("usb", "1"))
        self.camera_combo.currentIndexChanged.connect(self._on_camera_index_changed)
        controls.addWidget(self.camera_combo)

        self.relay_refresh = QPushButton("Relay cameras")
        self.relay_refresh.setToolTip(
            "Find phone cameras that are online on the relay server"
        )
        self.relay_refresh.clicked.connect(self._refresh_relay_cameras)
        controls.addWidget(self.relay_refresh)

        self.camera_button = QPushButton("Start live inspection")
        self.camera_button.clicked.connect(self._toggle_camera)
        controls.addWidget(self.camera_button)

        self.auto_check = QCheckBox("Auto-log FAIL & REVIEW")
        self.auto_check.setChecked(True)
        self.auto_check.setToolTip(
            "Log a FAIL or REVIEW automatically (with its evidence images) "
            "when it persists for two frames."
        )
        controls.addWidget(self.auto_check)

        self.log_button = QPushButton("Log current result")
        self.log_button.setEnabled(False)
        self.log_button.clicked.connect(self._log_current)
        controls.addWidget(self.log_button)

        upload = QPushButton("Inspect image…")
        upload.clicked.connect(self._upload_image)
        controls.addWidget(upload)
        layout.addLayout(controls)

        self.live_status = caption(
            "Live inference always runs on the newest frame; slower hardware "
            "just updates less often. Auto-capture has a short cooldown so one "
            "unit is logged once."
        )
        layout.addWidget(self.live_status)
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

        self.overlay_label = QLabel("Heatmap appears here during live inspection")
        self.overlay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_label.setMinimumHeight(230)
        self.overlay_label.setStyleSheet(
            f"background: {theme.SIDEBAR_BG}; color: {theme.SIDEBAR_TEXT}; "
            f"border-radius: {theme.RADIUS_CONTROL}px;"
        )
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
        if self.stream is not None or self.relay_client is not None:
            self._stop_camera()
            return
        # Pick up models trained since this page was built.
        self._refresh_model_combo()
        if self._selected_model() is None:
            QMessageBox.warning(self, "No model",
                                "Train a model on the Train page first.")
            return
        kind, ident = self._selected_source()
        if kind == "relay":
            self._start_relay(ident)
            return
        self.stream = CameraStream("usb", ident)
        self.stream.opened.connect(self._on_camera_opened)
        self.stream.frame_ready.connect(self._on_frame)
        self.stream.error.connect(
            lambda kind, address=ident: self._on_stream_error(kind, address)
        )
        self.camera_button.setEnabled(False)
        self.camera_button.setText("Starting…")
        self.stream.start()

    def _start_relay(self, camera_id: str) -> None:
        """Subscribe to a phone camera on the relay and inspect its frames."""
        url, token = load_relay_settings()
        if not token:
            resolved = self._ask_relay_settings()
            if resolved is None:
                return
            url, token = resolved
        client = ServerClient()
        client.configure(url, token)
        client.connected.connect(self._on_camera_opened)
        client.disconnected.connect(self._on_relay_disconnected)
        client.frame_received.connect(
            lambda cid, jpeg, expected=camera_id:
                self._on_relay_frame(expected, cid, jpeg)
        )
        self.relay_client = client
        self._relay_camera_id = camera_id
        self.camera_button.setEnabled(False)
        self.camera_button.setText("Connecting…")
        self.live_status.setText(
            f"Connecting to {self.camera_combo.currentText()}…"
        )
        client.start()

    def _on_relay_frame(self, expected_id: str, camera_id: str,
                        jpeg: bytes) -> None:
        if expected_id != camera_id:
            return
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is not None:
            self._on_frame(frame)

    def _on_relay_disconnected(self, reason: str) -> None:
        self.status_bar.showMessage(f"Relay connection problem: {reason}", 5000)

    def _on_camera_opened(self) -> None:
        model = self._selected_model()
        if model is None:
            self._stop_camera()
            return
        self.camera_button.setEnabled(True)
        self.camera_button.setText("Stop live inspection")
        self._start_live_worker(model)

    def _start_live_worker(self, model) -> None:
        self.decider.reset()
        self._live_model = model
        self.live_worker = LiveInferenceWorker(
            model, db.get_settings(), self.model_store.artifacts()
        )
        self.live_worker.result_ready.connect(self._on_live_result)
        self.live_worker.failed.connect(self._on_live_failed)
        self.live_worker.start()

    def _restart_live(self) -> None:
        """Swap the running live worker onto the newly selected model."""
        model = self._selected_model()
        if model is None or self.live_worker is None:
            return
        self.live_worker.running = False
        safe_stop(self.live_worker)
        self.live_worker = None
        self._start_live_worker(model)
        self.status_bar.showMessage(
            f"Live model switched to {self.model_combo.currentText()}", 3000
        )

    def _on_stream_error(self, kind: str, address: str) -> None:
        self._stop_camera()
        show_camera_error(self, kind, address)

    def _stop_camera(self) -> None:
        if self.live_worker is not None:
            self.live_worker.running = False
            safe_stop(self.live_worker)
            self.live_worker = None
        self._live_model = None
        if self.stream is not None:
            self.stream.stop()
            self.stream = None
        if self.relay_client is not None:
            self.relay_client.stop()
            self.relay_client = None
            self._relay_camera_id = None
        self.camera_button.setEnabled(True)
        self.camera_button.setText("Start live inspection")
        self.log_button.setEnabled(False)

    # -------------------------------------------------------------- relay list

    def _refresh_relay_cameras(self, *_args) -> None:
        """Fetch online phone cameras; ask for the token on explicit refresh."""
        url, token = load_relay_settings()
        if not token:
            if self.sender() is not self.relay_refresh:
                return
            resolved = self._ask_relay_settings()
            if resolved is None:
                return
            url, token = resolved
        worker = FunctionWorker(_fetch_relay_cameras, url, token)
        worker.finished_ok.connect(self._render_camera_combo)
        worker.failed.connect(self._relay_list_failed)
        self._relay_worker = worker
        worker.start()

    def _relay_list_failed(self, trace: str) -> None:
        last = trace.strip().splitlines()[-1] if trace.strip() else ""
        self.status_bar.showMessage(
            f"Could not list relay cameras: {last}", 5000
        )

    def _render_camera_combo(self, cameras: list) -> None:
        current = self.camera_combo.currentData()
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        self.camera_combo.addItem("Camera 0 (built-in)", ("usb", "0"))
        self.camera_combo.addItem("Camera 1", ("usb", "1"))
        if cameras:
            self.camera_combo.insertSeparator(self.camera_combo.count())
            for camera in cameras:
                label = f"{camera.get('name') or camera['id']} (phone)"
                self.camera_combo.addItem(label, ("relay", camera["id"]))
        for index in range(self.camera_combo.count()):
            if self.camera_combo.itemData(index) == current:
                self.camera_combo.setCurrentIndex(index)
                break
        self.camera_combo.blockSignals(False)
        self._on_camera_index_changed()
        if cameras:
            self.status_bar.showMessage(
                f"{len(cameras)} phone camera(s) online", 3000
            )

    def _ask_relay_settings(self) -> tuple[str, str] | None:
        url, token = load_relay_settings()
        url, ok = QInputDialog.getText(
            self, "Relay server", "Relay server URL:", text=url
        )
        if not ok:
            return None
        token, ok = QInputDialog.getText(
            self, "Dashboard token", "Dashboard token:",
            QLineEdit.EchoMode.Password, token,
        )
        if not ok:
            return None
        url, token = url.strip(), token.strip()
        if not url or not token:
            return None
        save_relay_settings(url, token)
        return url, token

    def on_leave(self) -> None:
        """Stop live capture when the user navigates away."""
        self._stop_camera()

    # ------------------------------------------------------------------ frames

    def _on_frame(self, frame) -> None:
        self.current_frame = frame
        if self.live_worker is not None:
            self.live_worker.submit(frame)
        self.preview_label.setPixmap(
            bgr_to_pixmap(self._with_outline(frame), 640, 420)
        )

    def _with_outline(self, frame: np.ndarray) -> np.ndarray:
        """Draw the canonical product outline on a copy of the frame.

        Re-checked once per second; only recomputed when the active profile or
        the frame size changes, so the per-frame cost is one draw call.
        """
        now = time.monotonic()
        if now - self._outline_checked_at > OUTLINE_CHECK_INTERVAL_S:
            self._outline_checked_at = now
            artifacts = self.model_store.artifacts()
            mask = artifacts.mask if artifacts else None
            key = (
                id(mask), None if mask is None else mask.shape,
                frame.shape[0], frame.shape[1],
            )
            if key != self._outline_key:
                self._outline_key = key
                self._outline_contours = self._contours_for(frame, mask)
        if not self._outline_contours:
            return frame
        out = frame.copy()
        cv2.drawContours(out, self._outline_contours, -1, (60, 200, 90), 2)
        return out

    @staticmethod
    def _contours_for(frame: np.ndarray, mask: np.ndarray | None) -> list:
        height, width = frame.shape[:2]
        if mask is not None and mask.sum() > 0:
            binary = cv2.resize(mask.astype(np.uint8), (width, height),
                                interpolation=cv2.INTER_NEAREST)
        else:
            binary = foreground_from_border(frame).astype(np.uint8)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        min_area = 0.002 * height * width
        kept = [c for c in contours if cv2.contourArea(c) >= min_area]
        kept.sort(key=cv2.contourArea, reverse=True)
        return kept[:4]

    # ------------------------------------------------------------------- live

    def _on_live_result(self, result: dict) -> None:
        if result.get("no_product"):
            # Empty scene: never auto-capture the background. Feed a
            # non-capture verdict so the decider re-arms when the next unit
            # arrives.
            self.latest_result = None
            self.log_button.setEnabled(False)
            self.banner.set_result(
                "NONE", "No product in view — waiting for a unit.", ""
            )
            self.score_bar.set_score(None)
            self.explanation_label.setText("")
            self.detail_label.setText("")
            self.overlay_label.setText("No product in view")
            self.decider.update("PASS")
            return
        self.latest_result = result
        self.latest_result_at = time.monotonic()
        self._apply_result(result)
        self.log_button.setEnabled(True)
        if self.auto_check.isChecked() and self.decider.update(result["verdict"]):
            self._log_result(result, reason="Auto-captured")

    def _on_live_failed(self, trace: str) -> None:
        self.status_bar.showMessage("Live inference error — frame skipped", 5000)
        if not self._live_error_shown:
            self._live_error_shown = True
            show_error(
                self, "Live inference failed",
                "A frame could not be analysed. Live mode keeps running; "
                "retrain the model if this repeats.",
                trace,
            )

    def _apply_result(self, result: dict) -> None:
        verdict = result["verdict"]
        certainty = (
            f"Decision certainty: {result['certainty']} — "
            f"{result['certainty_reason']}"
        )
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
        self.overlay_label.setPixmap(
            rgb_to_pixmap(result["overlay"], 460, 320)
        )
        self.review_widget.setVisible(verdict == "REVIEW")
        if "frame" in result:
            self.preview_label.setPixmap(
                bgr_to_pixmap(self._with_outline(result["frame"]), 640, 420)
            )

    # ------------------------------------------------------------------ log

    def _log_result(self, result: dict, reason: str, model=None) -> str | None:
        model = model or self._live_model or self._selected_model()
        if model is None or "frame" not in result:
            return None
        camera_id = self._relay_camera_id if self.relay_client is not None else None
        uid = log_inspection(result, result["frame"], model.model_version,
                             camera_id=camera_id)
        self.last_uid = uid
        self.status_bar.showMessage(f"{reason}: logged {uid}", 4000)
        if self.sync_engine is not None:
            self.sync_engine.kick()
        self._refresh_recent()
        return uid

    def _log_current(self) -> None:
        if self.latest_result is None:
            return
        age = time.monotonic() - self.latest_result_at
        if age > LATEST_RESULT_TTL_S:
            self.status_bar.showMessage(
                "No fresh result to log — wait for the next live update.", 3000
            )
            return
        self._log_result(self.latest_result, reason="Logged")

    # ---------------------------------------------------------------- images

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
        # Stop the live feed so it cannot overwrite the uploaded image.
        self._stop_camera()
        self.preview_label.setPixmap(
            bgr_to_pixmap(self._with_outline(image), 640, 420)
        )
        model = self._selected_model()
        if model is None:
            QMessageBox.warning(self, "No model",
                                "Train a model on the Train page first.")
            return
        settings = db.get_settings()
        artifacts = self.model_store.artifacts()
        self.status_bar.showMessage("Inspecting image…")
        worker = FunctionWorker(run_inspection, image, model, settings, artifacts)
        worker.finished_ok.connect(
            lambda result: self._on_image_result(result, image, model)
        )
        worker.failed.connect(self._inspect_failed)
        self._inspect_worker = worker
        worker.start()

    def _on_image_result(self, result: dict, image: np.ndarray, model) -> None:
        result = dict(result)
        result["frame"] = image
        self._apply_result(result)
        self._log_result(result, reason="Logged image", model=model)

    def _inspect_failed(self, trace: str) -> None:
        show_error(
            self, "Inspection failed",
            "The inspection could not finish. Try again, or retrain the "
            "model if this keeps happening.",
            trace,
        )

    # ----------------------------------------------------------------- review

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
