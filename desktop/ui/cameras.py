"""Cameras page: register factory cameras and preview their streams."""

from __future__ import annotations

import socket
import threading
import time
from io import BytesIO

import cv2
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop.auth import AuthError, AuthService, OrgContext
from desktop.model_store import ModelStore, log_inspection, run_inspection
from desktop.theme import VERDICT_COLORS
from desktop.ui.widgets import bgr_to_pixmap, card, muted


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
            self.error.emit(self.camera["id"], "could not open the stream")
            return
        while self.running:
            ok, frame = capture.read()
            if not ok:
                self.msleep(500)
                continue
            model = self.model_store.get()
            if model is None:
                self.error.emit(self.camera["id"], "no active model")
                self.msleep(2000)
                continue
            try:
                from db import database as db

                settings = db.get_settings()
                with self.inference_lock:
                    result = run_inspection(frame, model, settings)
                    uid = log_inspection(
                        result, frame, model.model_version,
                        camera_id=self.camera["id"],
                    )
                self.result_ready.emit(self.camera["id"], frame, result, uid)
            except Exception as exc:  # noqa: BLE001
                self.error.emit(self.camera["id"], str(exc))
            self.msleep(int(self.interval_s * 1000))
        capture.release()

    def stop(self) -> None:
        self.running = False
        self.wait(3000)


def _qr_pixmap(payload: str, size: int) -> QPixmap:
    """Render a QR code for the pairing payload."""
    import qrcode

    image = qrcode.make(payload)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue())
    return pixmap.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


class CameraStream(QThread):
    """Continuously read frames from a USB index or RTSP URL."""

    frame_ready = Signal(object)

    def __init__(self, kind: str, address: str):
        super().__init__()
        self.kind = kind
        self.address = address
        self.running = True

    def run(self) -> None:  # noqa: D102
        source: int | str = (
            int(self.address) if self.kind == "usb" and self.address.isdigit()
            else self.address
        )
        capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            self.frame_ready.emit(None)
            return
        while self.running:
            ok, frame = capture.read()
            if not ok:
                self.msleep(200)
                continue
            self.frame_ready.emit(frame)
            self.msleep(40)
        capture.release()

    def stop(self) -> None:
        self.running = False
        self.wait(3000)


class CameraDialog(QDialog):
    """Add or edit one camera."""

    def __init__(self, lines: list[dict], products: list[dict], camera: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit camera" if camera else "Add camera")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        self.name_input = QLineEdit(camera["name"] if camera else "")
        form.addRow("Name", self.name_input)

        self.kind_combo = QComboBox()
        for kind, label in [("usb", "USB / built-in"), ("rtsp", "RTSP / IP camera"),
                            ("mobile", "Mobile (paired)")]:
            self.kind_combo.addItem(label, kind)
        if camera:
            index = self.kind_combo.findData(camera["kind"])
            self.kind_combo.setCurrentIndex(max(0, index))
        form.addRow("Type", self.kind_combo)

        self.address_input = QLineEdit(camera["address"] if camera else "0")
        self.address_input.setPlaceholderText("USB index (e.g. 0) or rtsp:// URL")
        form.addRow("Address", self.address_input)

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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

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
        self.edge = edge
        self.smoke = smoke
        self.sync_engine = sync_engine
        self.stream: CameraStream | None = None
        self.cameras: list[dict] = []
        self.line_workers: dict[str, LineCameraWorker] = {}
        self.line_tiles: dict[str, dict] = {}
        self.inference_lock = threading.Lock()

        self._build_ui()
        if not smoke:
            self._load()
        else:
            self._rebuild_line_tiles()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Cameras")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)
        add_button = QPushButton("Add camera")
        add_button.setObjectName("Primary")
        add_button.clicked.connect(self._add_camera)
        header.addWidget(add_button)
        root.addLayout(header)
        root.addWidget(muted(
            "Cameras are registered in the cloud so the whole team shares the "
            "same line layout. Live preview runs locally."
        ))

        columns = QHBoxLayout()
        columns.setSpacing(14)

        table_card, table_layout = card("Registered cameras")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Type", "Address", "Line", "Product"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        table_layout.addWidget(self.table)

        buttons = QHBoxLayout()
        preview = QPushButton("Preview selected")
        preview.clicked.connect(self._start_preview)
        buttons.addWidget(preview)
        stop = QPushButton("Stop preview")
        stop.clicked.connect(self._stop_preview)
        buttons.addWidget(stop)
        edit = QPushButton("Edit")
        edit.clicked.connect(self._edit_camera)
        buttons.addWidget(edit)
        delete = QPushButton("Delete")
        delete.setObjectName("Danger")
        delete.clicked.connect(self._delete_camera)
        buttons.addWidget(delete)
        table_layout.addLayout(buttons)
        columns.addWidget(table_card, 3)

        preview_card, preview_layout = card("Preview")
        self.preview_label = QLabel("Select a camera and press Preview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(420, 320)
        self.preview_label.setStyleSheet(
            "background:#0f172a; color:#94a3b8; border-radius:8px;"
        )
        preview_layout.addWidget(self.preview_label, 1)
        self.preview_status = muted("")
        preview_layout.addWidget(self.preview_status)
        columns.addWidget(preview_card, 2)

        root.addLayout(columns, 1)

        root.addWidget(self._build_line_card())

        if self.edge is not None:
            root.addWidget(self._build_pairing_card())

    def _build_line_card(self) -> QWidget:
        frame, layout = card("Live line monitoring")
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Inspect every"))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.5, 30.0)
        self.interval_spin.setSingleStep(0.5)
        self.interval_spin.setValue(2.0)
        self.interval_spin.setSuffix(" s")
        controls.addWidget(self.interval_spin)
        controls.addWidget(QLabel("per camera"))

        self.line_start = QPushButton("Start line")
        self.line_start.setObjectName("Primary")
        self.line_start.clicked.connect(self._start_line)
        controls.addWidget(self.line_start)

        self.line_stop = QPushButton("Stop line")
        self.line_stop.setEnabled(False)
        self.line_stop.clicked.connect(self._stop_line)
        controls.addWidget(self.line_stop)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.line_grid = QGridLayout()
        layout.addLayout(self.line_grid)
        self.line_status = muted(
            "Register cameras above, then start the line. Every sampled frame "
            "is inspected locally and logged."
        )
        layout.addWidget(self.line_status)
        return frame

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
                "background:#0f172a; color:#94a3b8; border-radius:8px;"
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
        color = VERDICT_COLORS.get(result["verdict"], "#0f172a")
        tile["verdict"].setText(f"{result['verdict']} · {uid[-4:]}")
        tile["verdict"].setStyleSheet(f"color: {color}; font-weight: 700;")
        tile["score"].setText(
            f"score {result['score']:.2f} · {result['latency_ms']} ms"
        )
        self.status_bar.showMessage(f"{uid} logged from line camera", 2000)
        if self.sync_engine is not None:
            self.sync_engine.kick()

    def _on_line_error(self, camera_id: str, message: str) -> None:
        tile = self.line_tiles.get(camera_id)
        if tile is not None:
            tile["verdict"].setText(f"error: {message[:60]}")
            tile["verdict"].setStyleSheet("color: #dc2626;")
        self.line_status.setText(f"Camera error: {message[:120]}")

    def _build_pairing_card(self) -> QWidget:
        frame, layout = card("Mobile pairing")
        row = QHBoxLayout()
        row.setSpacing(16)

        info = QVBoxLayout()
        self.edge_url_label = muted("")
        info.addWidget(self.edge_url_label)

        code_label = QLabel("Pairing code")
        code_label.setObjectName("Muted")
        info.addWidget(code_label)
        self.code_value = QLabel(self.edge.pairing_code)
        self.code_value.setStyleSheet(
            "font-size: 30px; font-weight: 700; letter-spacing: 6px;"
        )
        info.addWidget(self.code_value)

        info.addWidget(muted(
            "On the phone app: sign in, choose 'Pair with edge', then scan the "
            "QR or enter the address and code. Only devices on this network "
            "can pair. Frames are analysed on this machine and never leave it."
        ))

        devices_row = QHBoxLayout()
        self.devices_label = muted("No devices paired yet.")
        devices_row.addWidget(self.devices_label, 1)
        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self._refresh_devices)
        devices_row.addWidget(refresh)
        info.addLayout(devices_row)
        info.addStretch(1)
        row.addLayout(info, 1)

        self.qr_label = QLabel("")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.qr_label)
        layout.addLayout(row)

        self.edge_url_label.setText(f"Edge address: {self.edge.base_url}")
        payload = (
            f'{{"host": "{self.edge.base_url}", "code": "{self.edge.pairing_code}", '
            f'"name": "{socket.gethostname()}"}}'
        )
        self.qr_label.setPixmap(_qr_pixmap(payload, 170))
        self._refresh_devices()
        return frame

    def _refresh_devices(self) -> None:
        if self.edge is None:
            return
        devices = list(self.edge.tokens.values())
        if not devices:
            self.devices_label.setText("No devices paired yet.")
            return
        names = ", ".join(device["name"] for device in devices)
        self.devices_label.setText(f"Paired: {names}")

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
        self._render_table()
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

    def _render_table(self) -> None:
        line_names = {line["id"]: line["name"] for line in self._lookups()[0]}
        product_names = {p["id"]: p["name"] for p in self._lookups()[1]}
        self.table.setRowCount(len(self.cameras))
        for row, camera in enumerate(self.cameras):
            values = [
                camera["name"],
                camera["kind"],
                camera["address"],
                line_names.get(camera.get("line_id"), "—"),
                product_names.get(camera.get("product_id"), "—"),
            ]
            for col, text in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def _selected(self) -> dict | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.cameras[rows[0].row()]

    def _selection_changed(self) -> None:
        camera = self._selected()
        if camera:
            self.preview_status.setText(
                f"{camera['name']} · {camera['kind']} · {camera['address']}"
            )

    # --------------------------------------------------------------- actions

    def _add_camera(self) -> None:
        lines, products = self._lookups()
        dialog = CameraDialog(lines, products, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if not payload["name"]:
            QMessageBox.warning(self, "Name required", "Give the camera a name.")
            return
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
        dialog = CameraDialog(lines, products, camera=camera, parent=self)
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
            self.auth.client.table("cameras").delete().eq("id", camera["id"]).execute()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not delete", str(exc))
            return
        self._load()

    # --------------------------------------------------------------- preview

    def _start_preview(self) -> None:
        camera = self._selected()
        if not camera:
            QMessageBox.information(self, "Select a camera",
                                    "Choose a camera in the table first.")
            return
        if camera["kind"] == "mobile":
            self.preview_status.setText(
                "Mobile cameras stream from the phone app (Phase C)."
            )
            return
        self._stop_preview()
        self.stream = CameraStream(camera["kind"], camera["address"])
        self.stream.frame_ready.connect(self._on_frame)
        self.stream.start()
        self.preview_status.setText(f"Connecting to {camera['address']}…")

    def _on_frame(self, frame) -> None:
        if frame is None:
            self.preview_status.setText(
                "Could not open the stream. Check the address and network."
            )
            return
        self.preview_label.setPixmap(bgr_to_pixmap(frame, 520, 360))
        self.preview_status.setText(
            f"Live · {time.strftime('%H:%M:%S')}"
        )

    def _stop_preview(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream = None
        self.preview_status.setText("Preview stopped.")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_preview()
        self._stop_line()
        super().closeEvent(event)
