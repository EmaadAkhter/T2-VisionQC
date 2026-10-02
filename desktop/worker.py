"""Background workers so training and inference never block the UI."""

from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QThread, Signal

# QThreads that did not stop within the timeout are kept referenced here until
# they finish on their own: deleting a running QThread crashes the process.
_ORPHANS: list[QThread] = []


def safe_stop(thread: QThread, timeout_ms: int = 3000) -> None:
    """Set ``running = False`` (when present) and wait; never free a runner."""
    if hasattr(thread, "running"):
        thread.running = False
    if thread.wait(timeout_ms):
        return
    _ORPHANS.append(thread)

    def _release() -> None:
        if thread in _ORPHANS:
            _ORPHANS.remove(thread)

    thread.finished.connect(_release)


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
