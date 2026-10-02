"""Station page: one-key inspection loop on top of the live InspectPage.

Space = capture + inspect. After a result: PASS auto-clears after 2 s,
REVIEW/FAIL hold the frozen photo until Space (next unit) or a review click.

The live feed keeps running underneath (visible while idle); a capture freezes
the display and ignores incoming live results so the held photo and its
heatmap stay exactly the frames that were scored.

Use: in window.py replace `from desktop.ui.inspect import InspectPage`
with `from desktop.ui.station import StationPage as InspectPage`.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QMessageBox, QPushButton

from db import database as db
from desktop.model_store import run_inspection
from desktop.ui.inspect import InspectPage
from desktop.worker import FunctionWorker

AUTO_CLEAR_MS = 2000
READY_TEXT = "Ready. Press Space to capture the next unit."


class StationPage(InspectPage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._frozen = False      # live preview paused on a captured frame
        self._showing = False     # a result is on screen
        self._epoch = 0           # invalidates stale auto-clear timers
        self.banner.set_result("NONE", READY_TEXT)
        self.auto_check.setChecked(False)
        self.auto_check.setToolTip(
            "Station mode captures with Space. Enable to also auto-log FAILs "
            "and REVIEWs (with evidence images) while the feed is live."
        )
        QShortcut(QKeySequence("Space"), self, activated=self._space)
        # Space must never click a focused button.
        for button in self.findChildren(QPushButton):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ------------------------------------------------------- one-key loop

    def _space(self) -> None:
        if not self.isVisible():
            return
        if self._showing:
            self._next_unit()
        else:
            self._capture_and_inspect()

    def _capture_and_inspect(self) -> None:
        if self._inspect_worker is not None and self._inspect_worker.isRunning():
            return
        frame = self.current_frame if self.stream is not None else None
        if frame is None:
            self.status_bar.showMessage(
                "Start the camera or upload an image first.", 3000
            )
            return
        model = self._selected_model()
        if model is None:
            QMessageBox.warning(self, "No model",
                                "Train a model on the Train page first.")
            return
        # Freeze the preview now: the frame on screen is the frame scored.
        self._frozen = True
        self.banner.set_result("NONE", "Inspecting…")
        self.score_bar.set_score(None)
        self.status_bar.showMessage("Inspecting…")
        settings = db.get_settings()
        artifacts = self.model_store.artifacts()
        worker = FunctionWorker(run_inspection, frame, model, settings, artifacts)
        worker.finished_ok.connect(
            lambda result: self._station_result(result, frame, model)
        )
        worker.failed.connect(self._inspect_failed)
        self._inspect_worker = worker
        worker.start()

    def _station_result(self, result: dict, frame: np.ndarray, model) -> None:
        result = dict(result)
        result["frame"] = frame     # score and log exactly the same frame
        self._frozen = True
        self._showing = True
        self._apply_result(result)
        self._log_result(result, reason="Station capture", model=model)
        if result["verdict"] == "PASS":
            epoch = self._epoch
            QTimer.singleShot(AUTO_CLEAR_MS, lambda: self._auto_clear(epoch))
        else:
            self.status_bar.showMessage("Press Space for the next unit", 4000)

    def _next_unit(self) -> None:
        self._epoch += 1
        self._showing = False
        self._frozen = False
        self.latest_result = None
        self.banner.set_result("NONE", READY_TEXT)
        self.score_bar.set_score(None)
        self.explanation_label.clear()
        self.detail_label.clear()
        self.overlay_label.setText("Heatmap appears here during inspection")
        self.review_widget.setVisible(False)
        if self.stream is None:
            self.preview_label.clear()
            self.preview_label.setText(
                "Start the camera or upload an image to begin"
            )

    def _auto_clear(self, epoch: int) -> None:
        if epoch == self._epoch and self._showing:
            self._next_unit()

    # ------------------------------------------------------------ overrides

    def _toggle_camera(self) -> None:
        self._epoch += 1
        self._frozen = False
        self._showing = False
        super()._toggle_camera()

    def _on_frame(self, frame) -> None:
        # Keep the newest frame for the next capture, but never repaint over a
        # held result.
        if self._frozen:
            self.current_frame = frame
            return
        super()._on_frame(frame)

    def _on_live_result(self, result: dict) -> None:
        # Live results are suppressed while a capture is held.
        if self._frozen:
            return
        super()._on_live_result(result)

    def _on_image_result(self, result: dict, image: np.ndarray, model) -> None:
        self._station_result(result, image, model)

    def _inspect_failed(self, trace: str) -> None:
        self._frozen = False
        self._showing = False
        self.banner.set_result("NONE", READY_TEXT)
        super()._inspect_failed(trace)

    def _review(self, disposition: str) -> None:
        super()._review(disposition)
        if self._showing:
            self._next_unit()

    def on_leave(self) -> None:
        self._epoch += 1
        super().on_leave()
