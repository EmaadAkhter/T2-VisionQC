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

from db import database as db
from desktop.auth import AuthService, OrgContext
from desktop.edge_server import EdgeServer
from desktop.sync import SyncEngine
from desktop.worker import FunctionWorker, safe_stop
from desktop import theme

NAV_ITEMS = [
    ("inspect", "Inspect"),
    ("train", "Train"),
    ("profiles", "Profiles"),
    ("cameras", "Cameras"),
    ("kpi", "KPI"),
    ("settings", "Settings"),
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
    def __init__(self, auth: AuthService, orgs: list[OrgContext],
                 org: OrgContext, smoke: bool = False):
        super().__init__()
        self.auth = auth
        self.orgs = orgs
        self.org = org
        self.smoke = smoke
        self.pages: dict[str, QWidget] = {}
        self._current_key = "inspect"

        from desktop import __version__

        self.setWindowTitle(f"VisionQC {__version__}")
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

        # Local edge server + sync for the active organization.
        self.edge = None
        self.sync_engine = None
        self._bind_org(org)

        self._build_status_bar()
        self._switch("inspect")

        self._online_timer = QTimer(self)
        self._online_timer.setInterval(30_000)
        self._online_timer.timeout.connect(self._refresh_online)
        self._online_timer.start()
        self._refresh_online()

    def _bind_org(self, org: OrgContext) -> None:
        """Start/restart edge + sync for an organization."""
        if self.smoke:
            return
        if self.edge is not None:
            self.edge.stop()
        if self.sync_engine is not None:
            self.sync_engine.stop()
        self.edge = EdgeServer(org_id=org.org_id)
        self.edge.start()
        self.sync_engine = SyncEngine(self.auth, org.org_id)
        self.sync_engine.state_changed.connect(self._on_sync_state)
        self.sync_engine.progress.connect(self._on_sync_progress)
        self.sync_engine.start()

    # --------------------------------------------------------------- sidebar

    def _build_sidebar(self) -> QWidget:
        from PySide6.QtWidgets import QComboBox

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(8)

        title = QLabel("VisionQC")
        title.setObjectName("SidebarTitle")
        layout.addWidget(title)

        if len(self.orgs) > 1:
            self.org_combo = QComboBox()
            self.org_combo.setObjectName("SidebarCombo")
            for org in self.orgs:
                self.org_combo.addItem(org.org_name, org)
            index = next(
                (i for i, o in enumerate(self.orgs)
                 if o.org_id == self.org.org_id), 0
            )
            self.org_combo.setCurrentIndex(index)
            self.org_combo.currentIndexChanged.connect(self._switch_org)
            layout.addWidget(self.org_combo)
            self.org_label = QLabel("")
            self.org_label.setObjectName("SidebarMuted")
            self.org_label.setWordWrap(True)
            layout.addWidget(self.org_label)
        else:
            self.org_combo = None
            self.org_label = QLabel("")
            self.org_label.setObjectName("SidebarMuted")
            self.org_label.setWordWrap(True)
            layout.addWidget(self.org_label)
        self._refresh_org_label()
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
        self.user_label.setObjectName("SidebarMuted")
        self.user_label.setWordWrap(True)
        layout.addWidget(self.user_label)

        logout = QPushButton("Sign out")
        logout.clicked.connect(self._sign_out)
        layout.addWidget(logout)
        return sidebar

    def _refresh_org_label(self) -> None:
        role = ROLE_LABELS.get(self.org.role, self.org.role)
        if len(self.orgs) > 1:
            self.org_label.setText(f"Role: {role}")
        else:
            self.org_label.setText(f"{self.org.org_name}\nRole: {role}")

    def _switch_org(self, index: int) -> None:
        if index < 0 or index >= len(self.orgs):
            return
        org = self.orgs[index]
        if org.org_id == self.org.org_id:
            return
        self.org = org
        self.auth.remember_org(org.org_id)
        self._refresh_org_label()
        self._bind_org(org)
        self._rebuild_pages()

    def _rebuild_pages(self) -> None:
        """Recreate pages so they bind to the newly selected organization."""
        current = self._current_key
        for page in self.pages.values():
            if hasattr(page, "on_leave"):
                page.on_leave()
            self.stack.removeWidget(page)
            page.deleteLater()
        self.pages = {}
        self._switch(current)
        self.refresh_model_label()

    # ----------------------------------------------------------------- pages

    def _switch(self, key: str) -> None:
        current = self.pages.get(self._current_key)
        if current is not None and key != self._current_key:
            if hasattr(current, "on_leave"):
                current.on_leave()
        self._current_key = key
        if key not in self.pages:
            self.pages[key] = self._create_page(key)
            self.stack.addWidget(self.pages[key])
        self.stack.setCurrentWidget(self.pages[key])
        for button in self.nav_group.buttons():
            text = button.text().lower()
            button.setChecked(key in text)

    def _create_page(self, key: str) -> QWidget:
        if key == "inspect":
            from desktop.ui.station import StationPage as InspectPage
            return InspectPage(self.auth, self.org, self.statusBar(),
                               sync_engine=self.sync_engine)
        if key == "train":
            from desktop.ui.train import TrainPage
            return TrainPage(self.auth, self.org, self.statusBar())
        if key == "profiles":
            from desktop.ui.profiles import ProfilesPage
            return ProfilesPage(self.auth, self.org, self.statusBar(),
                                sync_engine=self.sync_engine)
        if key == "cameras":
            from desktop.ui.cameras import CamerasPage
            return CamerasPage(self.auth, self.org, self.statusBar(), self.edge,
                               smoke=self.smoke, sync_engine=self.sync_engine)
        if key == "kpi":
            from desktop.ui.kpi import KpiPage
            return KpiPage(self.auth, self.org, sync_engine=self.sync_engine)
        if key == "settings":
            from desktop.ui.settings import SettingsPage
            return SettingsPage(self.auth, self.org, self.statusBar(),
                                sync_engine=self.sync_engine)
        raise KeyError(key)

    # ------------------------------------------------------------ status bar

    def _build_status_bar(self) -> None:
        from desktop.ui.widgets import status_dot

        bar = QStatusBar()
        self.setStatusBar(bar)

        self.online_dot = status_dot(theme.MUTED)
        self.online_label = QLabel("offline")
        bar.addWidget(self.online_dot)
        bar.addWidget(self.online_label)

        self.sync_label = QLabel("Sync: idle")
        self.model_label = QLabel("Model: none")
        bar.addPermanentWidget(self.model_label)
        bar.addPermanentWidget(self.sync_label)
        self.refresh_model_label()
        self._on_sync_progress({
            "pushed": 0,
            "remaining": db.count_unsynced_inspections(),
            "retrying": 0,
        })

    def _on_sync_state(self, state: str) -> None:
        colors = {
            "idle": theme.PASS,
            "syncing": theme.ACCENT,
            "offline": theme.REVIEW,
            "error": theme.FAIL,
        }
        labels = {
            "idle": "Sync: idle",
            "syncing": "Sync: syncing…",
            "offline": "Sync: offline",
            "error": "Sync: error",
        }
        self.sync_label.setText(labels.get(state, f"Sync: {state}"))
        self.sync_label.setStyleSheet(
            f"color: {colors.get(state, theme.MUTED)};"
        )

    def _on_sync_progress(self, info: dict) -> None:
        remaining = info.get("remaining", 0)
        retrying = info.get("retrying", 0)
        if remaining == 0:
            self.sync_label.setText("Sync: all synced")
        else:
            extra = f" ({retrying} retrying)" if retrying else ""
            self.sync_label.setText(f"Sync: {remaining} pending{extra}")

    def refresh_model_label(self) -> None:
        settings = db.get_settings()
        version = settings.get("active_model_version")
        self.model_label.setText(
            f"Model: {version[:16]}…" if version else "Model: none"
        )

    def _refresh_online(self) -> None:
        # Network check runs off the main thread: a slow/unreachable server
        # must never freeze the UI.
        worker = getattr(self, "_online_worker", None)
        if worker is not None and worker.isRunning():
            return
        self._online_worker = FunctionWorker(self.auth.is_online)
        self._online_worker.finished_ok.connect(self._on_online_result)
        self._online_worker.start()

    def _on_online_result(self, online: bool) -> None:
        self.online_label.setText("online" if online else "offline")
        self.online_dot.setStyleSheet(
            f"background: {theme.PASS if online else theme.REVIEW}; "
            "border-radius: 4px;"
        )

    def _sign_out(self) -> None:
        self.auth.sign_out()
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        current = self.pages.get(self._current_key)
        if current is not None and hasattr(current, "on_leave"):
            current.on_leave()
        online_worker = getattr(self, "_online_worker", None)
        if online_worker is not None and online_worker.isRunning():
            safe_stop(online_worker, timeout_ms=2000)
        if self.edge is not None:
            self.edge.stop()
        if self.sync_engine is not None:
            self.sync_engine.stop()
        super().closeEvent(event)
