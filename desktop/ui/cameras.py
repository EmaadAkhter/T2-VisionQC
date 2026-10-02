"""Cameras page: register factory cameras, pair phones, one step at a time.

The list stays simple; selecting a camera reveals only the section you need
(overview, model, preview, delete). "Connect phone" shows a QR code that the
phone app scans — no typing camera IDs or keys.
"""

from __future__ import annotations

import threading
import time

import cv2
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from db import database as db
from desktop import theme
from desktop.auth import AuthService, OrgContext
from desktop.model_store import ModelStore, log_inspection, run_inspection
from desktop.server_client import ServerClient
from desktop.ui.camera_stream import (
    OPEN_FAILED,
    CameraStream,
    camera_error_message,
    safe_stop,
    show_camera_error,
)
from desktop.ui.pairing import (
    PairingDialog,
    load_relay_settings,
    relay_ws_url,
    save_relay_settings,
)
from desktop.ui.widgets import (
    CollapsibleSection,
    bgr_to_pixmap,
    card,
    muted,
    page_header,
)
from desktop.worker import FunctionWorker

KIND_LABELS = {
    "usb": "USB / built-in",
    "rtsp": "RTSP / IP camera",
    "mobile": "Mobile (paired)",
}


class LineCameraWorker(QThread):
    """Sample one camera on an interval, inspect locally, log every result.

    Capture and inference run sequentially inside this worker; the shared
    inference lock keeps multiple camera workers from overloading the CPU.
    """

    result_ready = Signal(str, object, object, str)
    error = Signal(str, str)

    def __init__(self, camera: dict, model_store: ModelStore, interval_s: float,
                 inference_lock):
        super().__init__()
        self.camera = camera
        self.model_store = model_store
        self.interval_s = interval_s
        self.inference_lock = inference_lock
        self.running = True

    def run(self) -> None:  # noqa: D102
        source: int | str = (
            int(self.camera["address"])
            if self.camera["kind"] == "usb" and self.camera["address"].isdigit()
            else self.camera["address"]
        )
        capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            self.error.emit(
                self.camera["id"],
                camera_error_message(OPEN_FAILED, self.camera["address"]),
            )
            return
        while self.running:
            ok, frame = capture.read()
            if not ok:
                self.msleep(500)
                continue
            model = self.model_store.get_for_camera(self.camera["id"])
            if model is None:
                self.error.emit(self.camera["id"], "no active model")
                self.msleep(2000)
                continue
            try:
                from db import database as db

                settings = db.get_settings()
                artifacts = self.model_store.artifacts()
                with self.inference_lock:
                    result = run_inspection(frame, model, settings, artifacts)
                    if result.get("no_product"):
                        self.result_ready.emit(self.camera["id"], frame,
                                               result, "")
                    else:
                        uid = log_inspection(
                            result, frame, model.model_version,
                            camera_id=self.camera["id"],
                        )
                        self.result_ready.emit(self.camera["id"], frame,
                                               result, uid)
            except Exception as exc:  # noqa: BLE001
                self.error.emit(self.camera["id"], str(exc))
            self.msleep(int(self.interval_s * 1000))
        capture.release()

    def stop(self) -> None:
        safe_stop(self)


class PhonePairDialog(QDialog):
    """Collect the relay details, then show the pairing QR."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect phone")
        self.setMinimumWidth(460)
        relay_url, relay_token = load_relay_settings()

        form = QFormLayout(self)
        self.url_input = QLineEdit(relay_url)
        form.addRow("Relay server", self.url_input)

        self.token_input = QLineEdit(relay_token)
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Dashboard token", self.token_input)

        self.name_input = QLineEdit("Phone camera")
        form.addRow("Camera name", self.name_input)

        form.addRow(muted(
            "The phone scans the QR code and connects by itself — no typing. "
            "The dashboard token is the server's VISIONQC_DASHBOARD_TOKEN."
        ))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Show pairing code"
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _validate(self) -> None:
        if (not self.url_input.text().strip()
                or not self.token_input.text().strip()
                or not self.name_input.text().strip()):
            QMessageBox.information(
                self, "Missing details",
                "Server, token and camera name are all needed.",
            )
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return (
            self.url_input.text().strip(),
            self.token_input.text().strip(),
            self.name_input.text().strip(),
        )


class CameraWizard(QDialog):
    """Add or edit a camera one step at a time: name, type, assignment."""

    STEPS = 3

    def __init__(self, lines: list[dict], products: list[dict],
                 camera: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit camera" if camera else "Add camera")
        self.setMinimumWidth(430)

        root = QVBoxLayout(self)
        root.setSpacing(theme.SPACE_S)
        self.step_label = muted("Step 1 of 3")
        root.addWidget(self.step_label)

        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # Step 1 — name.
        page = QWidget()
        form = QFormLayout(page)
        self.name_input = QLineEdit(camera["name"] if camera else "")
        self.name_input.setPlaceholderText("e.g. Assembly line camera")
        form.addRow("Name", self.name_input)
        self.stack.addWidget(page)

        # Step 2 — type and address.
        page = QWidget()
        form = QFormLayout(page)
        self.kind_combo = QComboBox()
        for kind, label in KIND_LABELS.items():
            self.kind_combo.addItem(label, kind)
        if camera:
            index = self.kind_combo.findData(camera["kind"])
            self.kind_combo.setCurrentIndex(max(0, index))
        self.kind_combo.currentIndexChanged.connect(self._kind_changed)
        form.addRow("Type", self.kind_combo)
        self.address_input = QLineEdit(camera["address"] if camera else "0")
        self.address_input.setPlaceholderText(
            "USB index (e.g. 0) or rtsp:// URL"
        )
        form.addRow("Address", self.address_input)
        self.stack.addWidget(page)

        # Step 3 — line and product.
        page = QWidget()
        form = QFormLayout(page)
        self.line_combo = QComboBox()
        self.line_combo.addItem("— none —", None)
        for line in lines:
            self.line_combo.addItem(line["name"], line["id"])
        if camera and camera.get("line_id"):
            index = self.line_combo.findData(camera["line_id"])
            self.line_combo.setCurrentIndex(max(0, index))
        form.addRow("Line", self.line_combo)
        self.product_combo = QComboBox()
        self.product_combo.addItem("— none —", None)
        for product in products:
            self.product_combo.addItem(product["name"], product["id"])
        if camera and camera.get("product_id"):
            index = self.product_combo.findData(camera["product_id"])
            self.product_combo.setCurrentIndex(max(0, index))
        form.addRow("Product", self.product_combo)
        self.stack.addWidget(page)

        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self._back)
        buttons.addWidget(self.back_button)
        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("Primary")
        self.next_button.clicked.connect(self._next)
        buttons.addWidget(self.next_button)
        root.addLayout(buttons)

        self._kind_changed()
        self._sync_buttons()

    def _kind_changed(self) -> None:
        mobile = self.kind_combo.currentData() == "mobile"
        self.address_input.setEnabled(not mobile)
        if mobile:
            self.address_input.setText("0")

    def _sync_buttons(self) -> None:
        index = self.stack.currentIndex()
        self.step_label.setText(f"Step {index + 1} of {self.STEPS}")
        self.back_button.setEnabled(index > 0)
        last = index == self.STEPS - 1
        self.next_button.setText("Save" if last else "Next")

    def _back(self) -> None:
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._sync_buttons()

    def _next(self) -> None:
        if not self.name_input.text().strip():
            QMessageBox.information(self, "Name required",
                                    "Give the camera a name first.")
            return
        if self.stack.currentIndex() == self.STEPS - 1:
            self.accept()
            return
        self.stack.setCurrentIndex(self.stack.currentIndex() + 1)
        self._sync_buttons()

    def payload(self) -> dict:
        return {
            "name": self.name_input.text().strip(),
            "kind": self.kind_combo.currentData(),
            "address": self.address_input.text().strip() or "0",
            "line_id": self.line_combo.currentData(),
            "product_id": self.product_combo.currentData(),
        }


class CamerasPage(QWidget):
    def __init__(self, auth: AuthService, org: OrgContext, status_bar, edge=None,
                 smoke: bool = False, sync_engine=None):
        super().__init__()
        self.auth = auth
        self.org = org
        self.status_bar = status_bar
        self.smoke = smoke
        self.sync_engine = sync_engine
        self.stream: CameraStream | None = None
        self.cameras: list[dict] = []
        self.line_workers: dict[str, LineCameraWorker] = {}
        self.line_tiles: dict[str, dict] = {}
        self.inference_lock = threading.Lock()
        self.model_store = ModelStore()
        self._line_names: dict = {}
        self._product_names: dict = {}
        self._worker: FunctionWorker | None = None

        self._build_ui()
        if not smoke:
            self._load()
        else:
            self._rebuild_line_tiles()

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.PAGE_MARGIN, theme.PAGE_MARGIN,
                                theme.PAGE_MARGIN, theme.PAGE_MARGIN)
        root.setSpacing(theme.SPACE_M)

        header = QHBoxLayout()
        header.addWidget(page_header(
            "Cameras",
            "Add factory cameras, or connect a phone with one QR scan.",
        ), 1)
        connect = QPushButton("Connect phone")
        connect.setObjectName("Primary")
        connect.clicked.connect(self._connect_phone)
        header.addWidget(connect)
        add = QPushButton("Add camera")
        add.clicked.connect(self._add_camera)
        header.addWidget(add)
        root.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(14)

        list_card, list_layout = card("Registered cameras")
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._selection_changed)
        list_layout.addWidget(self.list, 1)
        self.list_hint = muted("No cameras yet. Add one, or connect a phone.")
        list_layout.addWidget(self.list_hint)
        columns.addWidget(list_card, 2)

        detail_card, detail_layout = card("Details")
        detail_layout.addWidget(self._build_detail_pane(), 1)
        columns.addWidget(detail_card, 3)

        root.addLayout(columns, 1)
        root.addWidget(self._build_line_section())

    def _build_detail_pane(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_S)

        # 1 — Overview.
        self.overview_section = CollapsibleSection("Overview", expanded=True)
        form = QFormLayout()
        self.overview_labels: dict[str, QLabel] = {}
        for key in ("Type", "Address", "Line", "Product"):
            value = muted("—")
            self.overview_labels[key] = value
            form.addRow(key, value)
        self.overview_section.body_layout.addLayout(form)
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self._edit_camera)
        self.overview_section.body_layout.addWidget(
            self.edit_button, alignment=Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(self.overview_section)

        # 2 — Model.
        self.model_section = CollapsibleSection("Model")
        self.model_label = muted("Active model")
        self.model_section.body_layout.addWidget(self.model_label)
        self.assign_button = QPushButton("Assign model…")
        self.assign_button.clicked.connect(self._assign_model)
        self.model_section.body_layout.addWidget(
            self.assign_button, alignment=Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(self.model_section)

        # 3 — Preview.
        self.preview_section = CollapsibleSection("Preview")
        self.preview_status = muted("Select a camera and press Preview.")
        self.preview_section.body_layout.addWidget(self.preview_status)
        self.preview_label = QLabel("Preview appears here")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(320, 200)
        self.preview_label.setStyleSheet(
            f"background: {theme.SIDEBAR_BG}; color: {theme.SIDEBAR_TEXT}; "
            f"border-radius: {theme.RADIUS_CONTROL}px;"
        )
        self.preview_section.body_layout.addWidget(self.preview_label, 1)
        preview_controls = QHBoxLayout()
        self.preview_button = QPushButton("Preview")
        self.preview_button.setObjectName("Primary")
        self.preview_button.clicked.connect(self._start_preview)
        preview_controls.addWidget(self.preview_button)
        self.stop_button = QPushButton("Stop preview")
        self.stop_button.clicked.connect(self._stop_preview)
        self.stop_button.setEnabled(False)
        preview_controls.addWidget(self.stop_button)
        preview_controls.addStretch(1)
        self.preview_section.body_layout.addLayout(preview_controls)
        layout.addWidget(self.preview_section)

        # 4 — Delete.
        self.danger_section = CollapsibleSection("Delete")
        self.delete_button = QPushButton("Delete camera")
        self.delete_button.setObjectName("Danger")
        self.delete_button.clicked.connect(self._delete_camera)
        self.danger_section.body_layout.addWidget(
            self.delete_button, alignment=Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(self.danger_section)

        layout.addStretch(1)
        scroll.setWidget(container)
        self._set_detail_enabled(False)
        return scroll

    def _build_line_section(self) -> QWidget:
        section = CollapsibleSection("Line monitoring", expanded=False)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Inspect every"))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.5, 30.0)
        self.interval_spin.setSingleStep(0.5)
        self.interval_spin.setValue(2.0)
        self.interval_spin.setSuffix(" s")
        controls.addWidget(self.interval_spin)
        controls.addWidget(QLabel("per local camera"))

        self.line_start = QPushButton("Start line")
        self.line_start.setObjectName("Primary")
        self.line_start.clicked.connect(self._start_line)
        controls.addWidget(self.line_start)

        self.line_stop = QPushButton("Stop line")
        self.line_stop.setEnabled(False)
        self.line_stop.clicked.connect(self._stop_line)
        controls.addWidget(self.line_stop)
        controls.addStretch(1)
        section.body_layout.addLayout(controls)

        self.line_grid = QGridLayout()
        section.body_layout.addLayout(self.line_grid)

        self.line_status = muted(
            "Add local cameras above, then start the line. Every sampled frame "
            "is inspected locally and logged."
        )
        section.body_layout.addWidget(self.line_status)
        return section

    # ------------------------------------------------------------------ data

    def _load(self) -> None:
        try:
            self.cameras = (
                self.auth.client.table("cameras")
                .select("*")
                .order("created_at")
                .execute()
                .data
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Cloud unavailable",
                                f"Could not load cameras: {exc}")
            self.cameras = []
        self._render_list()
        if self.line_workers:
            self._stop_line()
        self._rebuild_line_tiles()

    def _lookups(self) -> tuple[list[dict], list[dict]]:
        try:
            lines = self.auth.client.table("lines").select("id,name").execute().data
            products = self.auth.client.table("products").select("id,name").execute().data
        except Exception:  # noqa: BLE001
            lines, products = [], []
        return lines, products

    def _render_list(self) -> None:
        lines, products = self._lookups()
        self._line_names = {line["id"]: line["name"] for line in lines}
        self._product_names = {product["id"]: product["name"] for product in products}

        previous = self._selected()
        previous_id = previous["id"] if previous else None

        self.list.blockSignals(True)
        self.list.clear()
        for camera in self.cameras:
            kind = KIND_LABELS.get(camera["kind"], camera["kind"])
            item = QListWidgetItem(f"{camera['name']}\n{kind} · {camera['address']}")
            item.setData(Qt.ItemDataRole.UserRole, camera["id"])
            self.list.addItem(item)
        self.list.blockSignals(False)

        if self.list.count():
            row = 0
            if previous_id:
                for index in range(self.list.count()):
                    if (self.list.item(index).data(Qt.ItemDataRole.UserRole)
                            == previous_id):
                        row = index
                        break
            self.list.setCurrentRow(row)
        else:
            self._selection_changed()
        self.list_hint.setVisible(not self.cameras)

    def _selected(self) -> dict | None:
        item = self.list.currentItem()
        if item is None:
            return None
        camera_id = item.data(Qt.ItemDataRole.UserRole)
        return next(
            (camera for camera in self.cameras if camera["id"] == camera_id),
            None,
        )

    def _set_detail_enabled(self, enabled: bool) -> None:
        for button in (self.edit_button, self.assign_button,
                       self.preview_button, self.delete_button):
            button.setEnabled(enabled)

    def _selection_changed(self) -> None:
        camera = self._selected()
        if camera is None:
            for label in self.overview_labels.values():
                label.setText("—")
            self.model_label.setText("Active model")
            self.preview_status.setText("Select a camera and press Preview.")
            self.preview_label.clear()
            self.preview_label.setText("Preview appears here")
            self._stop_preview()
            self._set_detail_enabled(False)
            return

        self._set_detail_enabled(True)
        self.overview_labels["Type"].setText(
            KIND_LABELS.get(camera["kind"], camera["kind"])
        )
        self.overview_labels["Address"].setText(camera["address"])
        self.overview_labels["Line"].setText(
            self._line_names.get(camera.get("line_id"), "—")
        )
        self.overview_labels["Product"].setText(
            self._product_names.get(camera.get("product_id"), "—")
        )

        assignment = db.get_camera_model(camera["id"])
        version = assignment.get("model_version") if assignment else None
        self.model_label.setText(
            self.model_store.model_name(version) if version else "Active model"
        )

        mobile = camera["kind"] == "mobile"
        self.preview_button.setEnabled(not mobile)
        self.preview_section.set_expanded(not mobile)
        if mobile:
            self.preview_status.setText(
                "This camera streams through the phone app — watch it on the "
                "Multi-camera page."
            )
        else:
            self.preview_status.setText(
                f"{camera['name']} · {camera['kind']} · {camera['address']}"
            )

    # --------------------------------------------------------------- actions

    def _add_camera(self) -> None:
        lines, products = self._lookups()
        dialog = CameraWizard(lines, products, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        payload["org_id"] = self.org.org_id
        try:
            self.auth.client.table("cameras").insert(payload).execute()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not save", str(exc))
            return
        self.status_bar.showMessage(f"Camera '{payload['name']}' added", 4000)
        self._load()

    def _edit_camera(self) -> None:
        camera = self._selected()
        if not camera:
            return
        lines, products = self._lookups()
        dialog = CameraWizard(lines, products, camera=camera, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        try:
            self.auth.client.table("cameras").update(payload).eq(
                "id", camera["id"]
            ).execute()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not save", str(exc))
            return
        self._load()

    def _assign_model(self) -> None:
        camera = self._selected()
        if not camera:
            return
        models = db.get_all_models()
        if not models:
            QMessageBox.information(
                self, "No models",
                "Train a model on the Train page first.",
            )
            return
        labels = ["Active model"] + [
            f"{m.get('name') or m['version']} ({m['version']})"
            for m in models
        ]
        choice, ok = QInputDialog.getItem(
            self, "Assign model", f"Model for '{camera['name']}':",
            labels, 0, False,
        )
        if not ok:
            return
        index = labels.index(choice)
        version = None if index == 0 else models[index - 1]["version"]
        db.set_camera_model(camera["id"], version)
        self._selection_changed()
        self.status_bar.showMessage(f"{camera['name']} now uses {choice}", 4000)

    def _delete_camera(self) -> None:
        camera = self._selected()
        if not camera:
            return
        confirm = QMessageBox.question(
            self, "Delete camera",
            f"Delete '{camera['name']}'? Past inspections are kept.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self.auth.client.table("cameras").delete().eq(
                "id", camera["id"]
            ).execute()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not delete", str(exc))
            return
        self._load()

    # ---------------------------------------------------------------- phone

    def _connect_phone(self) -> None:
        dialog = PhonePairDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        url, token, name = dialog.values()
        save_relay_settings(url, token)

        client = ServerClient()
        client.configure(url, token)
        worker = FunctionWorker(client.start_pairing, name)
        worker.finished_ok.connect(
            lambda result, url=url: self._pairing_started(url, result)
        )
        worker.failed.connect(self._pairing_failed)
        self._worker = worker
        worker.start()
        self.status_bar.showMessage("Creating pairing code…", 3000)

    def _pairing_started(self, url: str, result: dict) -> None:
        dialog = PairingDialog(
            health_url=url,
            camera_id=result["camera_id"],
            server_ws=relay_ws_url(url),
            token=result["token"],
            expires_in=int(result.get("expires_in", 600)),
            parent=self,
        )
        dialog.exec()
        self.status_bar.showMessage(
            f"“{result.get('name', 'Camera')}” pairs with the phone app", 5000
        )

    def _pairing_failed(self, trace: str) -> None:
        last = trace.strip().splitlines()[-1] if trace.strip() else "unknown error"
        QMessageBox.warning(
            self, "Pairing failed",
            "The relay server could not be reached or rejected the request.\n\n"
            f"{last}",
        )

    # ------------------------------------------------------------------ line

    def _rebuild_line_tiles(self) -> None:
        while self.line_grid.count():
            item = self.line_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.line_tiles = {}
        for index, camera in enumerate(self.cameras[:4]):
            tile = QWidget()
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(0, 0, 0, 0)
            thumb = QLabel("—")
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setMinimumSize(240, 150)
            thumb.setStyleSheet(
                f"background: {theme.SIDEBAR_BG}; color: {theme.SIDEBAR_TEXT}; "
                f"border-radius: {theme.RADIUS_CONTROL}px;"
            )
            name = QLabel(camera["name"])
            name.setStyleSheet("font-weight:600;")
            verdict = QLabel("idle")
            score = QLabel("")
            tile_layout.addWidget(name)
            tile_layout.addWidget(thumb, 1)
            tile_layout.addWidget(verdict)
            tile_layout.addWidget(score)
            self.line_grid.addWidget(tile, index // 2, index % 2)
            self.line_tiles[camera["id"]] = {
                "thumb": thumb, "verdict": verdict, "score": score,
            }

    def _start_line(self) -> None:
        model_store = ModelStore()
        if model_store.get() is None:
            QMessageBox.warning(self, "No model",
                                "Train a model before starting the line.")
            return
        if not self.cameras:
            QMessageBox.information(self, "No cameras",
                                    "Register at least one camera first.")
            return
        self._stop_line()
        self._rebuild_line_tiles()
        interval = self.interval_spin.value()
        started = 0
        for camera in self.cameras[:4]:
            if camera["kind"] == "mobile":
                continue
            worker = LineCameraWorker(
                camera, model_store, interval, self.inference_lock
            )
            worker.result_ready.connect(self._on_line_result)
            worker.error.connect(self._on_line_error)
            worker.start()
            self.line_workers[camera["id"]] = worker
            started += 1
        self.line_start.setEnabled(False)
        self.line_stop.setEnabled(True)
        self.line_status.setText(
            f"Monitoring {started} camera(s) every {interval:.1f}s — "
            "results are logged automatically."
        )

    def _stop_line(self) -> None:
        for worker in self.line_workers.values():
            worker.stop()
        self.line_workers = {}
        self.line_start.setEnabled(True)
        self.line_stop.setEnabled(False)
        self.line_status.setText("Line monitoring stopped.")

    def _on_line_result(self, camera_id: str, frame, result: dict, uid: str) -> None:
        tile = self.line_tiles.get(camera_id)
        if tile is None:
            return
        tile["thumb"].setPixmap(bgr_to_pixmap(frame, 260, 160))
        color = theme.VERDICT_COLORS.get(result["verdict"], "#0f172a")
        suffix = uid[-4:] if uid else "no product"
        tile["verdict"].setText(f"{result['verdict']} · {suffix}")
        tile["verdict"].setStyleSheet(f"color: {color}; font-weight: 700;")
        tile["score"].setText(
            f"score {result['score']:.2f} · {result['latency_ms']} ms"
        )
        if uid:
            self.status_bar.showMessage(f"{uid} logged from line camera", 2000)
            if self.sync_engine is not None:
                self.sync_engine.kick()

    def _on_line_error(self, camera_id: str, message: str) -> None:
        tile = self.line_tiles.get(camera_id)
        if tile is not None:
            tile["verdict"].setText(f"error: {message[:60]}")
            tile["verdict"].setStyleSheet("color: #dc2626;")
        self.line_status.setText(f"Camera error: {message[:120]}")

    # --------------------------------------------------------------- preview

    def _start_preview(self) -> None:
        camera = self._selected()
        if not camera:
            QMessageBox.information(self, "Select a camera",
                                    "Choose a camera in the list first.")
            return
        if camera["kind"] == "mobile":
            self.preview_status.setText(
                "This camera streams through the phone app — watch it on the "
                "Multi-camera page."
            )
            return
        self._stop_preview()
        self.stream = CameraStream(camera["kind"], camera["address"])
        self.stream.frame_ready.connect(self._on_frame)
        self.stream.error.connect(
            lambda kind, address=camera["address"]:
                self._on_stream_error(kind, address)
        )
        self.stream.start()
        self.preview_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.preview_status.setText(f"Connecting to {camera['address']}…")

    def _on_frame(self, frame) -> None:
        self.preview_label.setPixmap(bgr_to_pixmap(frame, 520, 360))
        self.preview_status.setText(
            f"Live · {time.strftime('%H:%M:%S')}"
        )

    def _on_stream_error(self, kind: str, address: str) -> None:
        self._stop_preview()
        self.preview_status.setText("Preview stopped — camera error.")
        show_camera_error(self, kind, address)

    def _stop_preview(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream = None
        camera = self._selected()
        self.preview_button.setEnabled(
            camera is not None and camera["kind"] != "mobile"
        )
        self.stop_button.setEnabled(False)
        if camera is not None and camera["kind"] != "mobile":
            self.preview_status.setText("Preview stopped.")

    def on_leave(self) -> None:
        """Stop all live capture before the page is hidden or destroyed."""
        self._stop_preview()
        self._stop_line()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.on_leave()
        super().closeEvent(event)
