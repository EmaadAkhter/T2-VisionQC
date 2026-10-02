"""Multi-camera dashboard: watch every relayed camera in one grid.

Connects to the VisionQC relay server, shows each phone / edge camera tile
with its latest frame and the model assigned to it, and lets the operator
upload the active local model and assign models per camera.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from db import database as db
from desktop import theme
from desktop.auth import AuthService, OrgContext
from desktop.model_store import ModelStore
from desktop.server_client import ServerClient
from desktop.ui.pairing import (
    PairingDialog,
    load_relay_settings,
    relay_ws_url,
    save_relay_settings,
)
from desktop.ui.widgets import (
    bgr_to_pixmap,
    card,
    caption,
    muted,
    page_header,
)
from desktop.worker import FunctionWorker

GRID_COLUMNS = 3
GRID_TILES = 6


class CameraTile(QFrame):
    """One camera in the grid: live frame, name, model, selection."""

    clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.camera_id: str | None = None
        self.setObjectName("Card")
        self.setMinimumSize(260, 200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self.preview = QLabel("No camera")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(240, 150)
        self.preview.setStyleSheet(
            f"background: {theme.SIDEBAR_BG}; color: {theme.SIDEBAR_TEXT}; "
            f"border-radius: {theme.RADIUS_CONTROL}px;"
        )
        layout.addWidget(self.preview, 1)

        self.name_label = caption("—")
        layout.addWidget(self.name_label)
        self.detail_label = caption("")
        layout.addWidget(self.detail_label)

    def set_camera(self, camera: dict | None) -> None:
        self.camera_id = camera["id"] if camera else None
        if camera is None:
            self.preview.setText("No camera")
            self.name_label.setText("—")
            self.detail_label.setText("")
            return
        online = "online" if camera.get("online") else "offline"
        model = camera.get("model_version") or "active model"
        self.name_label.setText(camera.get("name", camera["id"]))
        self.detail_label.setText(f"{online} · {model}")

    def update_frame(self, jpeg: bytes) -> None:
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return
        self.preview.setPixmap(bgr_to_pixmap(image, 360, 240))

    def set_selected(self, selected: bool) -> None:
        color = theme.ACCENT if selected else theme.BORDER
        self.setStyleSheet(
            f"QFrame#Card {{ border: 2px solid {color}; "
            f"border-radius: {theme.RADIUS_CARD}px; }}"
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.camera_id:
            self.clicked.emit(self.camera_id)
        super().mousePressEvent(event)


class MultiCameraPage(QWidget):
    def __init__(self, auth: AuthService, org: OrgContext, status_bar,
                 sync_engine=None):
        super().__init__()
        self.auth = auth
        self.org = org
        self.status_bar = status_bar
        self.sync_engine = sync_engine
        self.model_store = ModelStore()
        self.client = ServerClient()
        self.client.connected.connect(self._on_connected)
        self.client.disconnected.connect(self._on_disconnected)
        self.client.cameras_changed.connect(self._on_cameras)
        self.client.frame_received.connect(self._on_frame)
        self._tiles: list[CameraTile] = []
        self._cameras: dict[str, dict] = {}
        self._selected_id: str | None = None
        self._worker: FunctionWorker | None = None

        self._build_ui()

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.PAGE_MARGIN, theme.PAGE_MARGIN,
                                theme.PAGE_MARGIN, theme.PAGE_MARGIN)
        root.setSpacing(theme.SPACE_M)

        root.addWidget(page_header(
            "Multi-camera",
            "Phones connect with a QR scan; assign a model per camera. "
            "Click a tile to select it.",
        ))

        relay_url, relay_token = load_relay_settings()
        connection, connection_layout = card("Relay server")
        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_S)
        self.url_input = QLineEdit(relay_url)
        self.url_input.setPlaceholderText("https://qc.example.com")
        row.addWidget(self.url_input, 2)
        self.token_input = QLineEdit(relay_token)
        self.token_input.setPlaceholderText("Dashboard token")
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        row.addWidget(self.token_input, 1)

        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("Primary")
        self.connect_button.clicked.connect(self._toggle_connection)
        row.addWidget(self.connect_button)
        connection_layout.addLayout(row)

        self.status = muted("Not connected.")
        connection_layout.addWidget(self.status)
        root.addWidget(connection)

        self.actions_card, actions_layout = card("Cameras")
        actions = QHBoxLayout()
        actions.setSpacing(theme.SPACE_S)
        register = QPushButton("Connect phone…")
        register.setObjectName("Primary")
        register.clicked.connect(self._register_camera)
        actions.addWidget(register)

        upload = QPushButton("Upload active model…")
        upload.clicked.connect(self._upload_active_model)
        actions.addWidget(upload)

        assign = QPushButton("Assign model…")
        assign.clicked.connect(self._assign_model)
        actions.addWidget(assign)
        actions.addStretch(1)
        actions_layout.addLayout(actions)
        actions_layout.addWidget(muted(
            "Connect the relay first; then pair a phone or manage models."
        ))
        self.actions_card.setVisible(False)
        root.addWidget(self.actions_card)

        grid_card, grid_layout = card("Camera grid")
        grid = QGridLayout()
        grid.setSpacing(theme.SPACE_S)
        for index in range(GRID_TILES):
            tile = CameraTile()
            tile.clicked.connect(self._select_tile)
            self._tiles.append(tile)
            grid.addWidget(tile, index // GRID_COLUMNS, index % GRID_COLUMNS)
        grid_layout.addLayout(grid)
        root.addWidget(grid_card, 1)

    # ------------------------------------------------------------- connection

    def _toggle_connection(self) -> None:
        if self.client.running:
            self.client.stop()
            self._set_status("Disconnected.")
            self.connect_button.setText("Connect")
            self.actions_card.setVisible(False)
            return
        url = self.url_input.text().strip()
        token = self.token_input.text().strip()
        if not url or not token:
            QMessageBox.information(self, "Server details",
                                    "Enter the relay server URL and token.")
            return
        self.client.configure(url, token)
        try:
            self.client.start()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not connect", str(exc))
            return
        save_relay_settings(url, token)
        self.connect_button.setText("Disconnect")
        self._set_status("Connecting…")

    def _on_connected(self) -> None:
        self._set_status("Connected.")
        self.actions_card.setVisible(True)
        self._refresh_cameras()

    def _on_disconnected(self, reason: str) -> None:
        self._set_status(f"Connection problem: {reason}")
        if not self.client.running:
            self.actions_card.setVisible(False)

    def on_leave(self) -> None:
        if self.client.running:
            self.client.stop()
            self.connect_button.setText("Connect")

    # ---------------------------------------------------------------- cameras

    def _refresh_cameras(self) -> None:
        self._run_rest(self.client.list_cameras,
                       ok=self._on_cameras,
                       label="Refresh cameras")

    def _on_cameras(self, cameras: list) -> None:
        self._cameras = {camera["id"]: camera for camera in cameras}
        for index, tile in enumerate(self._tiles):
            tile.set_camera(cameras[index] if index < len(cameras) else None)
        self._set_status(f"{len(cameras)} camera(s) registered.")

    def _select_tile(self, camera_id: str) -> None:
        self._selected_id = camera_id
        for tile in self._tiles:
            tile.set_selected(tile.camera_id == camera_id)

    def _on_frame(self, camera_id: str, jpeg: bytes) -> None:
        for tile in self._tiles:
            if tile.camera_id == camera_id:
                tile.update_frame(jpeg)
                return

    def _register_camera(self) -> None:
        name, ok = QInputDialog.getText(self, "Connect a phone",
                                        "Camera name:")
        if not ok or not name.strip():
            return
        self._run_rest(self.client.start_pairing, name.strip(),
                       ok=self._pairing_started,
                       label="Create pairing code")

    def _pairing_started(self, result: dict) -> None:
        dialog = PairingDialog(
            health_url=self.client.base_url,
            camera_id=result["camera_id"],
            server_ws=relay_ws_url(self.client.base_url),
            token=result["token"],
            expires_in=int(result.get("expires_in", 600)),
            parent=self,
        )
        dialog.exec()
        self._refresh_cameras()

    # ----------------------------------------------------------------- models

    def _upload_active_model(self) -> None:
        version = self.model_store.active_version()
        meta = db.get_model(version) if version else None
        if not meta or not meta.get("model_path"):
            QMessageBox.information(self, "No active model",
                                    "Train a model on the Train page first.")
            return
        path = meta["model_path"]
        if not os.path.exists(path):
            QMessageBox.warning(self, "Model missing",
                                f"Model file not found:\n{path}")
            return
        self._set_status(f"Uploading {meta.get('name') or version}…")
        self._run_rest(
            self.client.upload_model, path,
            meta.get("name") or version, meta.get("backbone", ""),
            meta.get("n_images", 0),
            ok=self._model_uploaded,
            label="Upload model",
        )

    def _model_uploaded(self, result: dict) -> None:
        self._set_status(
            f"Uploaded {result.get('name')} ({result.get('version')})."
        )
        self.status_bar.showMessage("Model uploaded to relay server", 4000)

    def _assign_model(self) -> None:
        if not self._selected_id:
            QMessageBox.information(self, "Select a camera",
                                    "Click a camera tile first.")
            return
        self._run_rest(self.client.list_models,
                       ok=self._show_assign_dialog,
                       label="List server models")

    def _show_assign_dialog(self, models: list) -> None:
        labels = ["Active model (default)"] + [
            f"{m.get('name') or m['version']} ({m['version']})"
            for m in models
        ]
        choice, ok = QInputDialog.getItem(
            self, "Assign model",
            f"Model for '{self._selected_id}':", labels, 0, False,
        )
        if not ok:
            return
        index = labels.index(choice)
        version = None if index == 0 else models[index - 1]["version"]
        self._run_rest(
            self.client.assign_model, self._selected_id, version,
            ok=lambda _: self._refresh_cameras(),
            label="Assign model",
        )

    # ------------------------------------------------------------------ rest

    def _run_rest(self, fn, *args, ok, label: str) -> None:
        """Run a REST call on a worker thread; show failures in the status."""
        worker = FunctionWorker(fn, *args)
        worker.finished_ok.connect(ok)
        worker.failed.connect(
            lambda trace, name=label: self._rest_failed(name, trace)
        )
        self._worker = worker
        worker.start()

    def _rest_failed(self, label: str, trace: str) -> None:
        self._set_status(f"{label} failed.")
        QMessageBox.warning(
            self, f"{label} failed",
            "The relay server could not be reached or rejected the request.\n\n"
            f"{trace.strip().splitlines()[-1] if trace.strip() else ''}",
        )

    def _set_status(self, text: str) -> None:
        self.status.setText(text)
