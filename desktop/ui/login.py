"""Sign-in, sign-up and organization onboarding window."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from desktop.auth import AuthError, AuthService, OrgContext
from desktop.theme import ACCENT, MUTED


class LoginWindow(QDialog):
    """Modal flow: authenticate, then pick or create an organization."""

    def __init__(self, auth: AuthService, parent=None):
        super().__init__(parent)
        self.auth = auth
        self.org: OrgContext | None = None

        self.setWindowTitle("VisionQC — Sign in")
        self.setMinimumSize(430, 520)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_auth_page())
        self.stack.addWidget(self._build_org_page())

        layout = QVBoxLayout(self)
        layout.addWidget(self.stack)

        self._show_auth_page()

    # ------------------------------------------------------------------ auth

    def _build_auth_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(12)

        title = QLabel("VisionQC")
        title.setObjectName("Title")
        layout.addWidget(title)
        subtitle = QLabel("Offline visual inspection for your factory")
        subtitle.setObjectName("Subtitle")
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Full name (sign-up only)")
        layout.addWidget(self.name_input)

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("you@factory.com")
        layout.addWidget(self.email_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.password_input)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #dc2626;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        self.sign_in_button = QPushButton("Sign in")
        self.sign_in_button.setObjectName("Primary")
        self.sign_in_button.clicked.connect(self._sign_in)
        layout.addWidget(self.sign_in_button)

        self.sign_up_button = QPushButton("Create account")
        self.sign_up_button.clicked.connect(self._sign_up)
        layout.addWidget(self.sign_up_button)

        layout.addStretch(1)
        hint = QLabel(
            "Local development: owner@visionqc.local / visionqc123\n"
            "(seeded by supabase/seed.sql)"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return page

    def _show_auth_page(self) -> None:
        self.stack.setCurrentIndex(0)
        self.sign_in_button.setDefault(True)

    def _sign_in(self) -> None:
        self._authenticate(lambda: self.auth.sign_in(
            self.email_input.text().strip(), self.password_input.text()
        ))

    def _sign_up(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            self._set_error("Enter your full name before creating an account.")
            return
        self._authenticate(lambda: self.auth.sign_up(
            self.email_input.text().strip(), self.password_input.text(), name
        ))

    def _authenticate(self, action) -> None:
        self._set_error("")
        self.sign_in_button.setEnabled(False)
        try:
            action()
        except AuthError as exc:
            self._set_error(str(exc))
            self.sign_in_button.setEnabled(True)
            return
        self.sign_in_button.setEnabled(True)
        self._load_orgs()

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)

    # ------------------------------------------------------------------- orgs

    def _build_org_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(12)

        title = QLabel("Choose your organization")
        title.setObjectName("Title")
        layout.addWidget(title)
        self.org_hint = QLabel("")
        self.org_hint.setObjectName("Subtitle")
        layout.addWidget(self.org_hint)
        layout.addSpacing(10)

        self.org_combo = QComboBox()
        layout.addWidget(self.org_combo)

        continue_button = QPushButton("Continue")
        continue_button.setObjectName("Primary")
        continue_button.clicked.connect(self._accept_org)
        layout.addWidget(continue_button)

        layout.addSpacing(24)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)

        new_org_label = QLabel("Create a new organization")
        new_org_label.setObjectName("CardTitle")
        layout.addWidget(new_org_label)

        self.new_org_input = QLineEdit()
        self.new_org_input.setPlaceholderText("e.g. Acme Plastics")
        layout.addWidget(self.new_org_input)

        self.org_error = QLabel("")
        self.org_error.setStyleSheet("color: #dc2626;")
        self.org_error.setWordWrap(True)
        layout.addWidget(self.org_error)

        create_button = QPushButton("Create organization")
        create_button.clicked.connect(self._create_org)
        layout.addWidget(create_button)

        layout.addStretch(1)
        sign_out = QPushButton("Sign out")
        sign_out.setObjectName("Danger")
        sign_out.clicked.connect(self._sign_out)
        layout.addWidget(sign_out)
        return page

    def _load_orgs(self) -> None:
        self.org_combo.clear()
        try:
            orgs = self.auth.refresh_orgs()
        except AuthError as exc:
            self._set_error(str(exc))
            return
        if orgs:
            for org in orgs:
                self.org_combo.addItem(
                    f"{org.org_name}  ({org.role.replace('_', ' ')})", org
                )
            self.stack.setCurrentIndex(1)
        else:
            self.org_hint.setText(
                f"Signed in as {self.auth.user_email}. "
                "You are not a member of any organization yet."
            )
            self.stack.setCurrentIndex(1)

    def _accept_org(self) -> None:
        org = self.org_combo.currentData()
        if org is None:
            self.org_error.setText("Create an organization to continue.")
            return
        self.org = org
        self.accept()

    def _create_org(self) -> None:
        name = self.new_org_input.text().strip()
        if len(name) < 2:
            self.org_error.setText("Enter an organization name (2+ characters).")
            return
        try:
            self.org = self.auth.create_org(name)
        except AuthError as exc:
            self.org_error.setText(str(exc))
            return
        self.accept()

    def _sign_out(self) -> None:
        self.auth.sign_out()
        self._show_auth_page()
        self.org_combo.clear()
        self.new_org_input.clear()
        self.org_error.clear()
        self._set_error("")
