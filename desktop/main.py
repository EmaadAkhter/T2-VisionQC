"""VisionQC desktop entry point.

Usage:
    python3 -m desktop.main
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from desktop.auth import AuthError, AuthService  # noqa: E402
from desktop.config import is_configured, load_config  # noqa: E402
from desktop.theme import STYLESHEET  # noqa: E402
from desktop.ui.login import LoginWindow  # noqa: E402
from desktop.ui.window import MainWindow  # noqa: E402
from db import database as db  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("VisionQC")
    app.setStyleSheet(STYLESHEET)

    db.init_db()

    config = load_config()
    if not is_configured(config):
        QMessageBox.critical(
            None, "Supabase not configured",
            "Start the local stack and generate the desktop config:\n\n"
            "  supabase start\n"
            "  python3 tools/write_supabase_env.py\n\n"
            "Then run this app again.",
        )
        return 1

    try:
        auth = AuthService(config)
    except AuthError as exc:
        QMessageBox.critical(None, "Configuration error", str(exc))
        return 1

    # If a cached session exists, go straight to org selection.
    if auth.user_email and auth._restore_session():  # noqa: SLF001 - intentional
        try:
            auth.refresh_orgs()
        except AuthError:
            pass

    while True:
        login = LoginWindow(auth)
        if login.exec() != QDialog.DialogCode.Accepted or login.org is None:
            return 0
        window = MainWindow(auth, login.org)
        window.show()
        app.exec()
        # After sign-out (window closed) loop back to the login screen.
        if auth.user_email:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
