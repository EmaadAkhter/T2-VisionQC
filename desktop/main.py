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

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from desktop.auth import AuthError, AuthService  # noqa: E402
from desktop.config import is_configured, load_config, save_config  # noqa: E402
from desktop.theme import STYLESHEET  # noqa: E402
from desktop.ui.login import LoginWindow, NoAccessDialog  # noqa: E402
from desktop.ui.window import MainWindow  # noqa: E402
from db import database as db  # noqa: E402


class ConfigDialog(QDialog):
    """First-run setup: where is the Supabase control plane?"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VisionQC — Connect to your server")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Enter the Supabase URL and anon key for your organization's "
            "control plane.\n\n"
            "Local development: run `supabase start` and "
            "`python3 tools/write_supabase_env.py`, or paste the values from "
            "`supabase status`."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.url_input = QLineEdit("http://127.0.0.1:54321")
        form.addRow("Supabase URL", self.url_input)
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("anon / publishable key")
        form.addRow("Anon key", self.key_input)
        layout.addLayout(form)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        test_button = QPushButton("Test connection")
        test_button.clicked.connect(self._test)
        layout.addWidget(test_button)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _test(self) -> None:
        import requests

        url = self.url_input.text().strip().rstrip("/")
        key = self.key_input.text().strip()
        if not url or not key:
            self.status.setText("Enter both values first.")
            return
        try:
            response = requests.get(
                f"{url}/auth/v1/health",
                headers={"apikey": key},
                timeout=6,
            )
        except requests.RequestException as exc:
            self.status.setText(f"Unreachable: {exc}")
            return
        if response.status_code == 200:
            self.status.setText("Server reachable. Save to continue.")
        else:
            self.status.setText(
                f"Server answered HTTP {response.status_code}. Check the URL/key."
            )

    def _save(self) -> None:
        url = self.url_input.text().strip()
        key = self.key_input.text().strip()
        if not url or not key:
            self.status.setText("URL and key are required.")
            return
        save_config(url, key)
        self.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("VisionQC")
    app.setStyleSheet(STYLESHEET)

    import paths

    paths.ensure_torch_home()
    db.init_db()

    config = load_config()
    if not is_configured(config):
        dialog = ConfigDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return 0
        config = load_config()

    try:
        auth = AuthService(config)
    except AuthError as exc:
        QMessageBox.critical(None, "Configuration error", str(exc))
        return 1

    # If a cached session exists, go straight to org selection.
    if auth.user_email and auth._restore_session():  # noqa: SLF001 - intentional
        try:
            auth.claim_invitations()
            auth.refresh_orgs()
        except AuthError:
            pass

    login = LoginWindow(auth)
    if login.exec() != QDialog.DialogCode.Accepted:
        return 0
    orgs = login.orgs

    if not orgs:
        no_access = NoAccessDialog(auth)
        if no_access.exec() != QDialog.DialogCode.Accepted or not no_access.orgs:
            return 0
        orgs = no_access.orgs

    window = MainWindow(auth, orgs, orgs[0])
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
