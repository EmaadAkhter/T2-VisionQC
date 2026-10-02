"""Main application window: sidebar navigation and page stack."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from desktop.auth import AuthService, OrgContext
from desktop.edge_server import EdgeServer
from desktop.theme import ACCENT, MUTED

NAV_ITEMS = [
    ("inspect", "🔍  Inspect"),
    ("train", "🎓  Train"),
    ("cameras", "📷  Cameras"),
    ("kpi", "📊  KPI"),
    ("settings", "⚙️  Settings"),
]

ROLE_LABELS = {
    "owner": "Owner",
    "admin": "Admin",
    "quality_manager": "Quality Manager",
    "operator": "Operator",
    "analyst": "Analyst",
    "technician": "Technician",
}


class MainWindow(QMainWindow):
    def __init__(self, auth: AuthService, org: OrgContext):
        super().__init__()
        self.auth = auth
        self.org = org
        self.pages: dict[str, QWidget] = {}

        self.setWindowTitle("VisionQC")
        self.resize(1280, 820)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.setObjectName("Page")
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # Local edge server for the mobile companion (LAN only).
        self.edge = EdgeServer(org_id=org.org_id)
        self.edge.start()

        self._build_status_bar()
        self._switch("inspect")

        self._online_timer = QTimer(self)
        self._online_timer.setInterval(30_000)
        self._online_timer.timeout.connect(self._refresh_online)
        self._online_timer.start()
        self._refresh_online()

    # --------------------------------------------------------------- sidebar

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(8)

        title = QLabel("VisionQC")
        title.setObjectName("SidebarTitle")
        layout.addWidget(title)

        self.org_label = QLabel(
            f"{self.org.org_name}\n{ROLE_LABELS.get(self.org.role, self.org.role)}"
        )
        self.org_label.setObjectName("SidebarOrg")
        self.org_label.setWordWrap(True)
        layout.addWidget(self.org_label)
        layout.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for key, label in NAV_ITEMS:
            button = QPushButton(label)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.clicked.connect(lambda _, k=key: self._switch(k))
            self.nav_group.addButton(button)
            layout.addWidget(button)

        layout.addStretch(1)

        self.user_label = QLabel(self.auth.user_email or "")
        self.user_label.setObjectName("SidebarOrg")
        self.user_label.setWordWrap(True)
        layout.addWidget(self.user_label)

        logout = QPushButton("Sign out")
        logout.clicked.connect(self._sign_out)
        layout.addWidget(logout)
        return sidebar

    # ----------------------------------------------------------------- pages

    def _switch(self, key: str) -> None:
        if key not in self.pages:
            self.pages[key] = self._create_page(key)
            self.stack.addWidget(self.pages[key])
        self.stack.setCurrentWidget(self.pages[key])
        for button in self.nav_group.buttons():
            text = button.text().lower()
            button.setChecked(key in text)

    def _create_page(self, key: str) -> QWidget:
        if key == "inspect":
            from desktop.ui.inspect import InspectPage
            return InspectPage(self.auth, self.org, self.statusBar())
        if key == "train":
            from desktop.ui.train import TrainPage
            return TrainPage(self.auth, self.org, self.statusBar())
        if key == "cameras":
            from desktop.ui.cameras import CamerasPage
            return CamerasPage(self.auth, self.org, self.statusBar(), self.edge)
        if key == "kpi":
            from desktop.ui.kpi import KpiPage
            return KpiPage(self.auth, self.org)
        if key == "settings":
            from desktop.ui.settings import SettingsPage
            return SettingsPage(self.auth, self.org, self.statusBar())
        raise KeyError(key)

    # ------------------------------------------------------------ status bar

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        self.online_label = QLabel("● offline")
        self.sync_label = QLabel("Cloud: not synced")
        self.model_label = QLabel("Model: none")
        bar.addWidget(self.online_label)
        bar.addPermanentWidget(self.model_label)
        bar.addPermanentWidget(self.sync_label)
        self.refresh_model_label()

    def refresh_model_label(self) -> None:
        from db import database as db
        settings = db.get_settings()
        version = settings.get("active_model_version")
        self.model_label.setText(
            f"Model: {version[:16]}…" if version else "Model: none"
        )

    def _refresh_online(self) -> None:
        online = self.auth.is_online()
        self.online_label.setText("● online" if online else "● offline")
        self.online_label.setStyleSheet(
            f"color: {'#16a34a' if online else '#d97706'};"
        )

    def _sign_out(self) -> None:
        self.auth.sign_out()
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.edge.stop()
        super().closeEvent(event)
