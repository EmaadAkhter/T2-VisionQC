"""Background workers so training and inference never block the UI."""

from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QThread, Signal


class FunctionWorker(QThread):
    """Run a plain function on a worker thread.

    Emits `finished_ok` with the return value or `failed` with the traceback
    string. One-shot: create a new worker per task.
    """

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # noqa: D102
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception:  # noqa: BLE001
            self.failed.emit(traceback.format_exc())
            return
        self.finished_ok.emit(result)
