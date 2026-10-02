"""Continuous inference worker for the live Inspect page.

The camera pushes frames at capture rate; the worker always processes the
newest submitted frame and drops the rest, so the update rate follows the
inference speed (or the hardware) without any queue growth or lag build-up.
"""

from __future__ import annotations

import threading
import traceback

import numpy as np
from PySide6.QtCore import QThread, Signal

from desktop.model_store import ProfileArtifacts, run_inspection


class LiveInferenceWorker(QThread):
    """Runs :func:`run_inspection` on the latest frame, forever.

    Signals:
        result_ready(object): inspection result dict plus ``"frame"`` (BGR).
        failed(str): traceback for a frame that could not be processed.
    """

    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, model, settings: dict,
                 artifacts: ProfileArtifacts | None = None, parent=None):
        super().__init__(parent)
        self._model = model
        self._settings = dict(settings)
        self._artifacts = artifacts
        self._latest: np.ndarray | None = None
        self._lock = threading.Lock()
        self._pending = threading.Event()
        self.running = True

    def submit(self, frame: np.ndarray) -> None:
        """Hand the newest frame to the worker, replacing any unprocessed one."""
        with self._lock:
            self._latest = frame
        self._pending.set()

    def run(self) -> None:  # noqa: D102
        while self.running:
            if not self._pending.wait(0.2):
                continue
            self._pending.clear()
            with self._lock:
                frame = self._latest
                self._latest = None
            if frame is None:
                continue
            try:
                result = run_inspection(frame, self._model, self._settings,
                                        self._artifacts)
            except Exception:  # noqa: BLE001 - one bad frame must not end live mode
                self.failed.emit(traceback.format_exc())
                continue
            result["frame"] = frame
            if self.running:
                self.result_ready.emit(result)
