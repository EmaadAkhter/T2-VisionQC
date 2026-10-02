"""Settings page: decision threshold, review band and local data controls."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from db import database as db
from desktop.auth import AuthService, OrgContext
from desktop.ui.widgets import card, muted


def impact_preview(model_version: str, threshold: float, delta: float,
                   current_threshold: float, current_delta: float) -> dict:
    scores = db.get_recent_scores(model_version, limit=200)

    def counts(t: float, d: float) -> tuple[int, int, int]:
        passed = sum(1 for s in scores if s < t - d)
        review = sum(1 for s in scores if t - d <= s < t + d)
        failed = sum(1 for s in scores if s >= t + d)
        return passed, review, failed

    new = counts(threshold, delta)
    old = counts(current_threshold, current_delta)
    return {
        "n": len(scores),
        "new": new,
        "old": old,
        "delta": tuple(n - o for n, o in zip(new, old)),
    }


class SettingsPage(QWidget):
    def __init__(self, auth: AuthService, org: OrgContext, status_bar):
        super().__init__()
        self.auth = auth
        self.org = org
        self.status_bar = status_bar

        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel("Settings")
        title.setObjectName("Title")
        root.addWidget(title)

        columns = QHBoxLayout()
        columns.setSpacing(14)

        threshold_card, threshold_layout = card("Decision threshold")
        form = QFormLayout()
        self.threshold_input = QDoubleSpinBox()
        self.threshold_input.setRange(0.0, 1.0)
        self.threshold_input.setSingleStep(0.01)
        form.addRow("Threshold", self.threshold_input)

        self.delta_input = QDoubleSpinBox()
        self.delta_input.setRange(0.0, 0.2)
        self.delta_input.setSingleStep(0.01)
        form.addRow("Review band (±)", self.delta_input)
        threshold_layout.addLayout(form)

        self.preview_label = muted("")
        threshold_layout.addWidget(self.preview_label)

        apply_row = QHBoxLayout()
        apply_button = QPushButton("Apply")
        apply_button.setObjectName("Primary")
        apply_button.clicked.connect(self._apply)
        apply_row.addWidget(apply_button)
        preview_button = QPushButton("Preview impact")
        preview_button.clicked.connect(self._preview)
        apply_row.addWidget(preview_button)
        threshold_layout.addLayout(apply_row)
        columns.addWidget(threshold_card, 1)

        data_card, data_layout = card("Local data")
        self.data_label = muted("")
        data_layout.addWidget(self.data_label)
        clear_button = QPushButton("Clear all local data…")
        clear_button.setObjectName("Danger")
        clear_button.clicked.connect(self._clear_data)
        data_layout.addWidget(clear_button)
        data_layout.addStretch(1)
        columns.addWidget(data_card, 1)

        cloud_card, cloud_layout = card("Cloud")
        self.cloud_label = muted("")
        cloud_layout.addWidget(self.cloud_label)
        cloud_layout.addStretch(1)
        columns.addWidget(cloud_card, 1)

        root.addLayout(columns)
        root.addStretch(1)

    def _load(self) -> None:
        settings = db.get_settings()
        self.threshold_input.setValue(settings.get("threshold", 0.46))
        self.delta_input.setValue(settings.get("delta", 0.05))
        self._preview()

        inspections = db.get_inspections(limit=100000)
        self.data_label.setText(
            f"{len(inspections)} inspections · "
            f"{db.count_unsynced_inspections()} waiting to sync · "
            f"{len(db.get_all_models())} model versions\n"
            f"Database: data/visionqc.db · images: data/images/"
        )
        self.cloud_label.setText(
            f"Organization: {self.org.org_name} ({self.org.role})\n"
            f"URL: {self.auth.config.get('supabase_url', '')}\n"
            "Inspections sync when you press Sync on the KPI page."
        )

    def _preview(self) -> None:
        model_version = db.get_settings().get("active_model_version")
        if not model_version:
            self.preview_label.setText(
                "Not enough inspections to estimate impact (no active model)."
            )
            return
        current = db.get_settings()
        result = impact_preview(
            model_version,
            self.threshold_input.value(),
            self.delta_input.value(),
            current.get("threshold", 0.46),
            current.get("delta", 0.05),
        )
        if result["n"] < 20:
            self.preview_label.setText(
                f"Not enough inspections to estimate impact "
                f"({result['n']} of 20)."
            )
            return
        passed, review, failed = result["new"]
        d_pass, d_review, d_fail = result["delta"]
        self.preview_label.setText(
            f"Based on the last {result['n']} inspections: "
            f"{passed} PASS ({d_pass:+d}) · {review} REVIEW ({d_review:+d}) · "
            f"{failed} FAIL ({d_fail:+d})"
        )

    def _apply(self) -> None:
        db.update_settings("default", "threshold", self.threshold_input.value())
        db.update_settings("default", "delta", self.delta_input.value())
        self.status_bar.showMessage("Settings saved", 4000)
        self._load()

    def _clear_data(self) -> None:
        confirm = QMessageBox.question(
            self, "Clear local data",
            "This deletes local inspections, settings history and image "
            "evidence on this machine. Cloud copies are not touched.\n\nContinue?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        import os
        import shutil

        from desktop.model_store import IMAGES_DIR

        with db.get_connection() as conn:
            conn.execute("DELETE FROM inspections")
            conn.execute("DELETE FROM settings_history")
        if os.path.isdir(IMAGES_DIR):
            shutil.rmtree(IMAGES_DIR, ignore_errors=True)
        self.status_bar.showMessage("Local data cleared", 4000)
        self._load()
