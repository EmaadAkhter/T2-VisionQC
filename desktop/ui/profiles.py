"""Product profiles: onboarding wizard, mask approval and profile management.

Generalization principle: nothing product-specific lives in code. The operator
captures background and good-unit frames, approves one mask, and the system
stores a profile (canonical mask, background model, presence regions, model)
that drives fully automatic runtime inspection.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
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

import paths
from db import database as db
from desktop.auth import AuthService, OrgContext
from desktop.ui.mask_editor import MaskEditorDialog
from desktop import theme
from desktop.ui.errors import show_error
from desktop.ui.widgets import bgr_to_pixmap, card, make_table, muted, page_header
from desktop.worker import FunctionWorker
from service.foreground import (
    BackgroundModel,
    background_false_activation,
    foreground_from_border,
    mask_metrics,
    presence_regions,
    proposal_from_frames,
)
from service.inference import INPUT_SIZE, PatchCoreModel
from service.backbones import engine_display_name, resolve_default_engine


def _load_frames(folder: str, limit: int = 40) -> list[tuple[str, np.ndarray]]:
    frames = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        path = os.path.join(folder, name)
        image = cv2.imread(path)
        if image is not None:
            frames.append((path, image))
        if len(frames) >= limit:
            break
    return frames


def propose_mask_job(bg_folder: str, good_folder: str) -> dict:
    """Build the background model and propose a canonical mask."""
    background = None
    if bg_folder:
        bg_frames = [frame for _, frame in _load_frames(bg_folder, limit=20)]
        if len(bg_frames) >= 3:
            background = BackgroundModel.build(bg_frames)

    good = _load_frames(good_folder, limit=40)
    if len(good) < 5:
        raise ValueError("Need at least 5 good-unit images for a proposal")

    good_frames = [frame for _, frame in good]
    proposal = proposal_from_frames(good_frames, background=background)

    if background is not None:
        fg_masks = [background.foreground_mask(f) for f in good_frames]
    else:
        fg_masks = [foreground_from_border(f) for f in good_frames]
    regions = presence_regions(fg_masks)

    return {
        "background": background,
        "proposal": proposal,
        "regions": regions,
        "good_paths": [path for path, _ in good],
        "sample": good[0][1],
    }


def build_profile_job(name: str, camera_id: str | None, good_paths: list[str],
                      mask: np.ndarray, background: BackgroundModel | None,
                      regions: np.ndarray | None,
                      proposal: np.ndarray | None,
                      threshold: float = 0.55,
                      delta: float = 0.05) -> dict:
    """Train the model, save artifacts and activate the profile.

    Default threshold 0.55: the worst held-out good image sits at 0.50 by
    construction of the normalisation, so a small onboarding set keeps PASS
    below 0.50 and sends 0.50–0.60 to REVIEW. Editable per profile.
    """
    profile_id = uuid.uuid4().hex
    art_dir = paths.data_dir() / "profiles" / profile_id
    art_dir.mkdir(parents=True, exist_ok=True)

    mask_path = art_dir / "canonical_mask.png"
    cv2.imwrite(str(mask_path), mask.astype(np.uint8) * 255)

    regions_path = None
    if regions is not None and regions.sum() > 0:
        regions_path = art_dir / "presence_regions.png"
        cv2.imwrite(str(regions_path), regions.astype(np.uint8) * 255)

    background_path = None
    if background is not None:
        background_path = art_dir / "background.npz"
        background.save(background_path)

    sample_path = art_dir / "sample.png"
    shutil.copyfile(good_paths[0], sample_path)

    engine_name, engine_kwargs = resolve_default_engine(len(good_paths))
    model = PatchCoreModel(backbone=engine_name, backbone_kwargs=engine_kwargs)
    stats = model.fit(good_paths, score_mask=mask)
    model_path = paths.models_dir() / f"{model.model_version}.pt"
    model.save(str(model_path))
    db.save_model_metadata(
        version=model.model_version,
        product_id="default",
        created_at=model.created_at,
        n_images=model.n_training_images,
        backbone=engine_display_name(model.backbone.config()["name"]),
        coreset_ratio=model.coreset_ratio,
        ref_score=model.ref_score,
        baseline_brightness=model.training_stats.get("mean_brightness", 0),
        baseline_blur=model.training_stats.get("median_blur", 0),
        parent_version=None,
        model_path=str(model_path),
    )
    db.update_settings("default", "active_model_version", model.model_version)

    metrics: dict = {
        "mask_area_fraction": float(mask.mean()),
        "n_good_images": len(good_paths),
    }
    if proposal is not None:
        metrics["approved_vs_proposed_iou"] = mask_metrics(
            proposal, mask
        )["iou"]
    if background is not None:
        frames = _load_frames(str(Path(good_paths[0]).parent), limit=10)
        activations = [
            background_false_activation(frame, mask, background)
            for _, frame in frames
        ]
        metrics["background_false_activation"] = (
            float(np.mean(activations)) if activations else None
        )

    profile_id = db.save_profile({
        "id": profile_id,
        "product_id": "default",
        "camera_id": camera_id,
        "name": name,
        "canonical_mask_path": str(mask_path),
        "background_model_path": str(background_path) if background_path else None,
        "presence_regions_path": str(regions_path) if regions_path else None,
        "coverage_floor": 0.9,
        "threshold": threshold,
        "delta": delta,
        "model_version": model.model_version,
        "ref_score": model.ref_score,
        "status": "active",
        "metrics": metrics,
    })
    db.activate_profile(profile_id)
    return {"profile_id": profile_id, "metrics": metrics,
            "model_version": model.model_version}


class ProfileWizard(QDialog):
    def __init__(self, auth: AuthService, org: OrgContext, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New product profile")
        self.setMinimumWidth(640)
        self.auth = auth
        self.org = org

        self.background = None
        self.proposal: np.ndarray | None = None
        self.regions: np.ndarray | None = None
        self.approved: np.ndarray | None = None
        self.good_paths: list[str] = []
        self.sample: np.ndarray | None = None
        self.worker: FunctionWorker | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(muted(
            "1) Capture background frames (recommended) · 2) Capture good "
            "units · 3) Propose and approve the mask · 4) Build the profile. "
            "This is the only manual step; runtime is fully automatic."
        ))

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Profile name, e.g. Bottle line A")
        layout.addWidget(self.name_input)

        self.camera_combo = QComboBox()
        layout.addWidget(self.camera_combo)
        self._load_cameras()

        self.bg_input = self._folder_row(layout, "Background frames",
                                         self._browse_background)
        self.good_input = self._folder_row(layout, "Good-unit images",
                                           self._browse_good)

        self.preview = QLabel("Mask preview will appear here")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(360, 220)
        self.preview.setStyleSheet(
            "background:#0f172a; color:#94a3b8; border-radius:8px;"
        )
        layout.addWidget(self.preview)

        buttons = QHBoxLayout()
        self.propose_button = QPushButton("Propose mask")
        self.propose_button.clicked.connect(self._propose)
        buttons.addWidget(self.propose_button)
        self.edit_button = QPushButton("Edit / approve mask…")
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(self._edit_mask)
        buttons.addWidget(self.edit_button)
        buttons.addStretch(1)
        self.build_button = QPushButton("Build profile")
        self.build_button.setObjectName("Primary")
        self.build_button.setEnabled(False)
        self.build_button.clicked.connect(self._build)
        buttons.addWidget(self.build_button)
        layout.addLayout(buttons)

        self.status = muted("")
        layout.addWidget(self.status)

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)

    # ------------------------------------------------------------------ ui bits

    def _folder_row(self, layout, label: str, browse) -> QLineEdit:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        field = QLineEdit()
        row.addWidget(field, 1)
        button = QPushButton("Browse…")
        button.clicked.connect(lambda: browse(field))
        row.addWidget(button)
        layout.addLayout(row)
        return field

    def _load_cameras(self) -> None:
        self.camera_combo.addItem("— no camera binding —", None)
        try:
            cameras = (
                self.auth.client.table("cameras").select("id,name")
                .execute().data
            )
            for camera in cameras:
                self.camera_combo.addItem(camera["name"], camera["id"])
        except Exception:  # noqa: BLE001
            pass

    def _browse_background(self, field: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Background frames")
        if folder:
            field.setText(folder)

    def _browse_good(self, field: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Good-unit images")
        if folder:
            field.setText(folder)

    # ------------------------------------------------------------------ steps

    def _propose(self) -> None:
        if not self.good_input.text().strip():
            QMessageBox.information(self, "Good images needed",
                                    "Choose a folder with 20–30 good-unit photos.")
            return
        self.propose_button.setEnabled(False)
        self.propose_button.setText("Proposing…")
        self.status.setText("Building background model and proposing mask…")
        self.worker = FunctionWorker(
            propose_mask_job, self.bg_input.text().strip(),
            self.good_input.text().strip(),
        )
        self.worker.finished_ok.connect(self._proposed)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _proposed(self, result: dict) -> None:
        self.propose_button.setEnabled(True)
        self.propose_button.setText("Propose mask")
        self.background = result["background"]
        self.proposal = result["proposal"]
        self.regions = result["regions"]
        self.good_paths = result["good_paths"]
        self.sample = result["sample"]
        self.approved = self.proposal.copy()
        self.edit_button.setEnabled(True)
        self.build_button.setEnabled(True)
        self._render_preview()
        self.status.setText(
            f"Proposal ready · {len(self.good_paths)} good images · "
            f"mask {self.proposal.mean():.1%} of frame"
        )

    def _render_preview(self) -> None:
        if self.sample is None or self.approved is None:
            return
        image = cv2.resize(self.sample, INPUT_SIZE)
        overlay = image.copy()
        tint = np.zeros_like(image)
        tint[..., 1] = 255
        active = self.approved.astype(bool)
        overlay[active] = (
            0.55 * image[active].astype(np.float32)
            + 0.45 * tint[active].astype(np.float32)
        ).clip(0, 255).astype(np.uint8)
        contours, _ = cv2.findContours(
            (active.astype(np.uint8) * 255), cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        cv2.drawContours(overlay, contours, -1, (255, 40, 40), 2)
        self.preview.setPixmap(bgr_to_pixmap(overlay, 420, 260))

    def _edit_mask(self) -> None:
        if self.sample is None or self.approved is None:
            return
        dialog = MaskEditorDialog(self.sample, self.approved, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.approved = dialog.approved_mask()
            self._render_preview()
            self.status.setText(
                f"Mask approved · {self.approved.mean():.1%} of frame"
            )

    def _build(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.information(self, "Name needed",
                                    "Give the profile a name.")
            return
        if self.approved is None or not self.good_paths:
            return
        self.build_button.setEnabled(False)
        self.build_button.setText("Building…")
        self.status.setText("Training model on the approved region…")
        self.worker = FunctionWorker(
            build_profile_job, name, self.camera_combo.currentData(),
            list(self.good_paths), self.approved.copy(), self.background,
            self.regions, self.proposal,
        )
        self.worker.finished_ok.connect(self._built)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _built(self, result: dict) -> None:
        metrics = result["metrics"]
        QMessageBox.information(
            self, "Profile built",
            f"Profile active · model {result['model_version']}\n"
            f"Mask area: {metrics.get('mask_area_fraction', 0):.1%}\n"
            f"Approved vs proposed IoU: "
            f"{metrics.get('approved_vs_proposed_iou', 0):.2f}\n"
            f"Background false activation: "
            f"{metrics.get('background_false_activation', 0) or 0:.1%}",
        )
        self.accept()

    def _failed(self, trace: str) -> None:
        self.propose_button.setEnabled(True)
        self.propose_button.setText("Propose mask")
        self.build_button.setEnabled(True)
        self.build_button.setText("Build profile")
        self.status.setText("Failed — see the message.")
        show_error(
            self, "Profile step failed",
            "The profile step could not finish. Check the selected folders "
            "and try again.",
            trace,
        )


class ProfilesPage(QWidget):
    def __init__(self, auth: AuthService, org: OrgContext, status_bar,
                 sync_engine=None):
        super().__init__()
        self.auth = auth
        self.org = org
        self.status_bar = status_bar
        self.sync_engine = sync_engine

        root = QVBoxLayout(self)
        root.setContentsMargins(theme.PAGE_MARGIN, theme.PAGE_MARGIN,
                                theme.PAGE_MARGIN, theme.PAGE_MARGIN)
        root.setSpacing(theme.SPACE_M)

        header = QHBoxLayout()
        header.addWidget(page_header(
            "Product profiles",
            "One profile per product/camera view. The mask is approved once "
            "during onboarding; runtime is fully automatic.",
        ), 1)
        new_button = QPushButton("New profile…")
        new_button.setObjectName("Primary")
        new_button.clicked.connect(self._new_profile)
        header.addWidget(new_button)
        root.addLayout(header)

        table_card, table_layout = card()
        self.table = make_table(
            ["Name", "Status", "Model", "Images", "Mask IoU", "Created"]
        )
        table_layout.addWidget(self.table)

        buttons = QHBoxLayout()
        edit = QPushButton("Edit mask…")
        edit.clicked.connect(self._edit_mask)
        buttons.addWidget(edit)
        activate = QPushButton("Activate")
        activate.clicked.connect(self._activate)
        buttons.addWidget(activate)
        delete = QPushButton("Delete")
        delete.setObjectName("Danger")
        delete.clicked.connect(self._delete)
        buttons.addWidget(delete)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh)
        buttons.addWidget(refresh)
        table_layout.addLayout(buttons)
        root.addWidget(table_card, 1)

        self._refresh()

    def _new_profile(self) -> None:
        wizard = ProfileWizard(self.auth, self.org, parent=self)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            self._refresh()
            window = self.window()
            if hasattr(window, "refresh_model_label"):
                window.refresh_model_label()

    def _refresh(self) -> None:
        self.profiles = db.list_profiles()
        self.table.setRowCount(len(self.profiles))
        for row, profile in enumerate(self.profiles):
            try:
                metrics = json.loads(profile.get("metrics_json") or "{}")
            except json.JSONDecodeError:
                metrics = {}
            values = [
                profile["name"],
                profile["status"],
                (profile.get("model_version") or "")[:16],
                str(metrics.get("n_good_images", "")),
                (
                    f"{metrics['approved_vs_proposed_iou']:.2f}"
                    if metrics.get("approved_vs_proposed_iou") is not None
                    else "—"
                ),
                str(profile.get("created_at", ""))[:19],
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 1 and text == "active":
                    item.setForeground(Qt.GlobalColor.darkGreen)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()

    def _selected(self) -> dict | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.profiles[rows[0].row()]

    def _edit_mask(self) -> None:
        profile = self._selected()
        if not profile:
            QMessageBox.information(self, "Select a profile",
                                    "Choose a profile in the table first.")
            return
        sample_path = Path(profile["canonical_mask_path"]).parent / "sample.png"
        sample = cv2.imread(str(sample_path)) if sample_path.exists() else None
        mask = cv2.imread(profile["canonical_mask_path"],
                          cv2.IMREAD_GRAYSCALE)
        if sample is None or mask is None:
            QMessageBox.warning(self, "Files missing",
                                "Profile artifacts are incomplete.")
            return
        dialog = MaskEditorDialog(sample, mask > 127, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            approved = dialog.approved_mask()
            cv2.imwrite(profile["canonical_mask_path"],
                        approved.astype(np.uint8) * 255)
            try:
                metrics = json.loads(profile.get("metrics_json") or "{}")
            except json.JSONDecodeError:
                metrics = {}
            metrics["mask_area_fraction"] = float(approved.mean())
            db.save_profile({**profile, "metrics": metrics})
            self._refresh()
            self.status_bar.showMessage(
                "Mask updated — applies to the next inspection", 5000
            )

    def _activate(self) -> None:
        profile = self._selected()
        if not profile:
            return
        db.activate_profile(profile["id"])
        if profile.get("model_version"):
            db.update_settings("default", "active_model_version",
                               profile["model_version"])
        self._refresh()
        window = self.window()
        if hasattr(window, "refresh_model_label"):
            window.refresh_model_label()
        self.status_bar.showMessage(f"Profile '{profile['name']}' active", 4000)

    def _delete(self) -> None:
        profile = self._selected()
        if not profile:
            return
        confirm = QMessageBox.question(
            self, "Delete profile",
            f"Delete '{profile['name']}'? Inspections stay in the log.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        db.delete_profile(profile["id"])
        self._refresh()
