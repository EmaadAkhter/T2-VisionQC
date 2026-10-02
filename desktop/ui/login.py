"""Sign-in window: email and password only.

Organization access is managed by admins in the web console. The desktop
claims any pending invitation after sign-in and simply uses whatever
memberships the account has.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from desktop.auth import AuthError, AuthService, OrgContext


class LoginWindow(QDialog):
    def __init__(self, auth: AuthService, parent=None):
        super().__init__(parent)
        self.auth = auth
        self.orgs: list[OrgContext] = []

        self.setWindowTitle("VisionQC — Sign in")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 32, 36, 32)
        layout.setSpacing(12)

        title = QLabel("VisionQC")
        title.setObjectName("Title")
        layout.addWidget(title)
        subtitle = QLabel("Sign in with your organization account")
        subtitle.setObjectName("Subtitle")
        layout.addWidget(subtitle)
        layout.addSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.email_input = QLineEdit(auth.user_email or "")
        self.email_input.setPlaceholderText("you@factory.com")
        form.addRow("Email", self.email_input)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Password")
        form.addRow("Password", self.password_input)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #dc2626;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        self.sign_in_button = QPushButton("Sign in")
        self.sign_in_button.setObjectName("Primary")
        self.sign_in_button.setDefault(True)
        self.sign_in_button.clicked.connect(self._sign_in)
        layout.addWidget(self.sign_in_button)

        self.password_input.returnPressed.connect(self._sign_in)
        if auth.user_email:
            self.password_input.setFocus()
        else:
            self.email_input.setFocus()

        hint = QLabel(
            "No account? Ask your admin to invite you from the VisionQC web "
            "console. Forgot your password? Reset it from the web console too."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _sign_in(self) -> None:
        email = self.email_input.text().strip()
        password = self.password_input.text()
        if not email or not password:
            self.error_label.setText("Enter your email and password.")
            return

        self.error_label.setText("")
        self.sign_in_button.setEnabled(False)
        self.sign_in_button.setText("Signing in…")
        try:
            self.auth.sign_in(email, password)
            self.auth.claim_invitations()
            self.orgs = self.auth.refresh_orgs()
        except AuthError as exc:
            self.error_label.setText(str(exc))
            self.sign_in_button.setEnabled(True)
            self.sign_in_button.setText("Sign in")
            return
        self.sign_in_button.setEnabled(True)
        self.sign_in_button.setText("Sign in")
        self.accept()


class NoAccessDialog(QDialog):
    """Signed in, but the account has no organization membership yet."""

    def __init__(self, auth: AuthService, parent=None):
        super().__init__(parent)
        self.auth = auth
        self.orgs: list[OrgContext] = []

        self.setWindowTitle("VisionQC — No access yet")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(12)

        title = QLabel("No access yet")
        title.setObjectName("Title")
        layout.addWidget(title)

        message = QLabel(
            f"Signed in as {auth.user_email}.\n\n"
            "This account is not a member of any organization. Ask your "
            "administrator to invite you from the VisionQC web console, then "
            "press “Check again”."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        check = QPushButton("Check again")
        check.setObjectName("Primary")
        check.clicked.connect(lambda: self._check())
        buttons.addWidget(check)

        sign_out = QPushButton("Sign out")
        sign_out.clicked.connect(self.reject)
        buttons.addWidget(sign_out)
        layout.addLayout(buttons)

        # Seamless access: poll for the admin's invitation automatically.
        self._timer = QTimer(self)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(lambda: self._check(silent=True))
        self._timer.start()
        self.status.setText("Waiting for an invitation… checking automatically.")

    def _check(self, silent: bool = False) -> None:
        if not silent:
            self.status.setText("Checking for invitations…")
        try:
            self.auth.claim_invitations()
            self.orgs = self.auth.refresh_orgs()
        except AuthError as exc:
            if not silent:
                self.status.setText(str(exc))
            return
        if self.orgs:
            self._timer.stop()
            self.accept()
        elif not silent:
            self.status.setText(
                "Still no access. Ask your admin to send the invitation — "
                "this screen will continue checking automatically."
            )
