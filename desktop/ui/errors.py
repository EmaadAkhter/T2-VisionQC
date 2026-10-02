"""User-facing error reporting.

Full tracebacks go to ``<data>/logs/visionqc.log``; the dialog shows a short,
actionable message instead of a Python trace.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QMessageBox

import paths


def log_trace(title: str, trace: str) -> str:
    """Append a traceback to the local log file; return the log path."""
    log_path = paths.logs_dir() / "visionqc.log"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n[{stamp}] {title}\n{trace}\n")
    except OSError:
        pass
    return str(log_path)


def show_error(parent, title: str, message: str, trace: str = "") -> None:
    """Show a short message; write the full traceback to the log."""
    log_path = log_trace(title, trace) if trace else None
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    text = message
    if log_path:
        text += f"\n\nDetails were written to:\n{log_path}"
    box.setText(text)
    box.addButton(QMessageBox.StandardButton.Close)
    box.exec()
