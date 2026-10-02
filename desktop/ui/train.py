"""Train page: build a product model from good images and manage versions."""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import database as db
from desktop.auth import AuthService, OrgContext
from desktop.theme import MUTED
from desktop.ui.widgets import card, muted
from desktop.worker import FunctionWorker
from service.inference import MIN_TRAIN_IMAGES, PatchCoreModel, TARGET_TRAIN_IMAGES

MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "models")


def train_model(image_paths: list[str]) -> dict:
    """Train and persist a new model version (runs on a worker thread)."""
    model = PatchCoreModel()
    stats = model.fit(image_paths)

    models_dir = os.path.abspath(MODELS_DIR)
    os.makedirs(models_dir, exist_ok=True)
    model_path = os.path.join(models_dir, f"{model.model_version}.pt")
    model.save(model_path)

    db.save_model_metadata(
        version=model.model_version,
        product_id="default",
        created_at=model.created_at,
        n_images=model.n_training_images,
        backbone="WideResNet-50",
        coreset_ratio=model.coreset_ratio,
        ref_score=model.ref_score,
        baseline_brightness=model.training_stats.get("mean_brightness", 0),
        baseline_blur=model.training_stats.get("median_blur", 0),
        parent_version=None,
        model_path=model_path,
    )
    db.update_settings("default", "active_model_version", model.model_version)
    stats["model_path"] = model_path
    return stats


class TrainPage(QWidget):
    def __init__(self, auth: AuthService, org: OrgContext, status_bar):
        super().__init__()
        self.auth = auth
        self.org = org
        self.status_bar = status_bar
        self.selected_paths: list[str] = []
        self.worker: FunctionWorker | None = None

        self._build_ui()
        self._refresh_models()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel("Train")
        title.setObjectName("Title")
        root.addWidget(title)
        root.addWidget(muted(
            f"Upload {TARGET_TRAIN_IMAGES}–30 photos of GOOD units only. "
            "No defect images are needed — the model learns what normal looks like."
        ))

        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addWidget(self._build_upload_card(), 3)
        columns.addWidget(self._build_models_card(), 2)
        root.addLayout(columns, 1)

    def _build_upload_card(self) -> QWidget:
        frame, layout = card("Training images")

        buttons = QHBoxLayout()
        choose = QPushButton("Choose images…")
        choose.clicked.connect(self._choose_files)
        buttons.addWidget(choose)
        folder = QPushButton("Choose folder…")
        folder.clicked.connect(self._choose_folder)
        buttons.addWidget(folder)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.selection_label = muted("No images selected.")
        layout.addWidget(self.selection_label)

        self.thumb_label = QLabel("")
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.thumb_label.setMinimumHeight(260)
        layout.addWidget(self.thumb_label, 1)

        self.train_button = QPushButton("Train model")
        self.train_button.setObjectName("Primary")
        self.train_button.setEnabled(False)
        self.train_button.clicked.connect(self._train)
        layout.addWidget(self.train_button)

        self.progress_label = muted("")
        layout.addWidget(self.progress_label)
        return frame

    def _build_models_card(self) -> QWidget:
        frame, layout = card("Model versions")
        self.models_table = QTableWidget(0, 4)
        self.models_table.setHorizontalHeaderLabels(
            ["Version", "Images", "Created", "Active"]
        )
        self.models_table.horizontalHeader().setStretchLastSection(True)
        self.models_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.models_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.models_table)

        activate = QPushButton("Activate selected version")
        activate.clicked.connect(self._activate_selected)
        layout.addWidget(activate)
        return frame

    # -------------------------------------------------------------- selection

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose good-unit photos", "", "Images (*.png *.jpg *.jpeg)"
        )
        if paths:
            self._set_selection(paths)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder")
        if not folder:
            return
        paths = sorted(
            os.path.join(folder, name)
            for name in os.listdir(folder)
            if name.lower().endswith((".png", ".jpg", ".jpeg"))
        )
        if not paths:
            QMessageBox.warning(self, "No images",
                                "No PNG/JPG images found in that folder.")
            return
        self._set_selection(paths)

    def _set_selection(self, paths: list[str]) -> None:
        self.selected_paths = paths
        count = len(paths)
        message = f"{count} image(s) selected."
        if count < TARGET_TRAIN_IMAGES:
            message += f" Accuracy drops below {TARGET_TRAIN_IMAGES} photos."
        self.selection_label.setText(message)
        self.train_button.setEnabled(count >= MIN_TRAIN_IMAGES)
        self._render_thumbs()

    def _render_thumbs(self) -> None:
        from desktop.ui.widgets import bgr_to_pixmap
        import cv2

        self.thumb_label.clear()
        self.thumb_label.setPixmap(
            self._make_contact_sheet(self.selected_paths, cv2, bgr_to_pixmap)
        )

    @staticmethod
    def _make_contact_sheet(paths, cv2, bgr_to_pixmap):
        from PySide6.QtGui import QPixmap, QPainter, QColor

        thumbs = []
        for path in paths[:8]:
            image = cv2.imread(path)
            if image is None:
                continue
            thumbs.append(bgr_to_pixmap(image, 110, 110))
        if not thumbs:
            return QPixmap()
        cols = min(4, len(thumbs))
        rows = (len(thumbs) + cols - 1) // cols
        sheet = QPixmap(cols * 120, rows * 120)
        sheet.fill(QColor("#ffffff"))
        painter = QPainter(sheet)
        for index, pixmap in enumerate(thumbs):
            x = (index % cols) * 120 + 5
            y = (index // cols) * 120 + 5
            painter.drawPixmap(x, y, pixmap)
        painter.end()
        return sheet

    def _clear(self) -> None:
        self.selected_paths = []
        self.selection_label.setText("No images selected.")
        self.thumb_label.clear()
        self.train_button.setEnabled(False)

    # ---------------------------------------------------------------- training

    def _train(self) -> None:
        if len(self.selected_paths) < MIN_TRAIN_IMAGES:
            return
        self.train_button.setEnabled(False)
        self.progress_label.setText(
            f"Training on {len(self.selected_paths)} images… "
            "(a few seconds to a minute on CPU)"
        )
        self.worker = FunctionWorker(train_model, list(self.selected_paths))
        self.worker.finished_ok.connect(self._train_done)
        self.worker.failed.connect(self._train_failed)
        self.worker.start()

    def _train_done(self, stats: dict) -> None:
        self.train_button.setEnabled(True)
        self.progress_label.setText(
            f"Trained {stats['model_version']} on {stats['n_training_images']} "
            f"images · bank {stats['memory_bank_size']} · ref {stats['ref_score']:.2f}"
        )
        self.status_bar.showMessage("Model trained and activated", 5000)
        self._refresh_models()
        window = self.window()
        if hasattr(window, "refresh_model_label"):
            window.refresh_model_label()

    def _train_failed(self, trace: str) -> None:
        self.train_button.setEnabled(True)
        self.progress_label.setText("Training failed.")
        QMessageBox.critical(self, "Training failed", trace[-800:])

    # ------------------------------------------------------------ model table

    def _refresh_models(self) -> None:
        models = db.get_all_models()
        active = db.get_settings().get("active_model_version")
        self.models_table.setRowCount(len(models))
        for row, model in enumerate(models):
            values = [
                model.get("version", ""),
                str(model.get("n_images", "")),
                str(model.get("created_at", ""))[:19],
                "●" if model.get("version") == active else "",
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 3 and text:
                    item.setForeground(Qt.GlobalColor.darkGreen)
                self.models_table.setItem(row, col, item)
        self.models_table.resizeColumnsToContents()

    def _activate_selected(self) -> None:
        rows = self.models_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Select a version",
                                    "Choose a model version in the table first.")
            return
        version = self.models_table.item(rows[0].row(), 0).text()
        db.update_settings("default", "active_model_version", version)
        self._refresh_models()
        window = self.window()
        if hasattr(window, "refresh_model_label"):
            window.refresh_model_label()
        self.status_bar.showMessage(f"Active model set to {version}", 5000)
