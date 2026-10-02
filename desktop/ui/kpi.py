"""KPI page: today's quality numbers, recent inspections and cloud sync."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
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
from desktop.theme import VERDICT_COLORS
from desktop.ui.widgets import card, muted
from desktop.worker import FunctionWorker


def sync_inspections(auth: AuthService, org_id: str) -> dict:
    """Push unsynced local inspections to Supabase (idempotent by uid)."""
    rows = db.get_unsynced_inspections()
    if not rows:
        return {"pushed": 0, "remaining": 0}

    payload = []
    for row in rows:
        payload.append({
            "org_id": org_id,
            "uid": row["uid"],
            "model_version": row.get("model_version"),
            "timestamp": row.get("timestamp"),
            "raw_score": row.get("raw_score"),
            "score": row.get("score"),
            "threshold": row.get("threshold"),
            "delta": row.get("delta"),
            "verdict": row.get("verdict"),
            "certainty": row.get("certainty"),
            "disposition": row.get("disposition") or "PENDING",
            "disposition_by_override": bool(row.get("disposition_by_override")),
            "operator_note": row.get("operator_note"),
            "explanation": row.get("explanation"),
            "region_label": row.get("region_label"),
            "area_pct": row.get("area_pct"),
            "setup_status": row.get("setup_status"),
            "latency_ms": row.get("latency_ms"),
            "source": "edge",
        })

    auth.client.table("inspections").upsert(
        payload, on_conflict="org_id,uid", ignore_duplicates=True
    ).execute()
    db.mark_inspections_synced([row["id"] for row in rows])
    return {"pushed": len(rows), "remaining": db.count_unsynced_inspections()}


class KpiPage(QWidget):
    def __init__(self, auth: AuthService, org: OrgContext):
        super().__init__()
        self.auth = auth
        self.org = org
        self.worker: FunctionWorker | None = None

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("KPI")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)
        self.sync_status = muted("")
        header.addWidget(self.sync_status)
        self.sync_button = QPushButton("Sync to cloud")
        self.sync_button.setObjectName("Primary")
        self.sync_button.clicked.connect(self._sync)
        header.addWidget(self.sync_button)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        root.addLayout(header)

        self.date_label = muted("")
        root.addWidget(self.date_label)

        tiles_card, tiles_layout = card()
        self.tiles_grid = QGridLayout()
        tiles_layout.addLayout(self.tiles_grid)
        root.addWidget(tiles_card)

        table_card, table_layout = card("Recent inspections")
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Time", "UID", "Score", "Verdict", "Disposition", "Certainty", "Explanation"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table_layout.addWidget(self.table)
        root.addWidget(table_card, 1)

    # --------------------------------------------------------------- refresh

    def refresh(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        stats = db.get_dashboard_stats(today)
        self.date_label.setText(f"Today · {today} (local machine day)")

        while self.tiles_grid.count():
            item = self.tiles_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        rejection = stats["rejection_rate"]
        tiles = [
            ("Inspected", str(stats["total"]), "#0f172a"),
            ("Passed", str(stats["passed"]), VERDICT_COLORS["PASS"]),
            ("Failed", str(stats["failed"]), VERDICT_COLORS["FAIL"]),
            ("Pending review", str(stats["pending_review"]), VERDICT_COLORS["REVIEW"]),
            (
                "Rejection rate",
                "—" if rejection is None else f"{rejection:.1%}",
                "#0f172a",
            ),
        ]
        for col, (label, value, color) in enumerate(tiles):
            name = QLabel(label)
            name.setObjectName("Muted")
            value_label = QLabel(value)
            value_label.setObjectName("Metric")
            value_label.setStyleSheet(f"color: {color};")
            self.tiles_grid.addWidget(name, 0, col)
            self.tiles_grid.addWidget(value_label, 1, col)

        unsynced = db.count_unsynced_inspections()
        self.sync_status.setText(
            f"{unsynced} inspection(s) waiting to sync" if unsynced else "All synced"
        )

        rows = db.get_inspections(limit=25)
        self.table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            values = [
                str(row.get("timestamp", ""))[:19].replace("T", " "),
                row.get("uid", ""),
                f"{row.get('score') or 0:.2f}",
                row.get("verdict", ""),
                row.get("disposition", ""),
                row.get("certainty", ""),
                (row.get("explanation") or "")[:60],
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 3:
                    item.setForeground(Qt.GlobalColor.black)
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)
                self.table.setItem(row_idx, col, item)
        self.table.resizeColumnsToContents()

    # ------------------------------------------------------------------ sync

    def _sync(self) -> None:
        self.sync_button.setEnabled(False)
        self.sync_status.setText("Syncing…")
        self.worker = FunctionWorker(sync_inspections, self.auth, self.org.org_id)
        self.worker.finished_ok.connect(self._sync_done)
        self.worker.failed.connect(self._sync_failed)
        self.worker.start()

    def _sync_done(self, result: dict) -> None:
        self.sync_button.setEnabled(True)
        self.sync_status.setText(
            f"Pushed {result['pushed']} · {result['remaining']} remaining"
        )
        self.refresh()

    def _sync_failed(self, trace: str) -> None:
        self.sync_button.setEnabled(True)
        self.sync_status.setText("Sync failed — will retry later.")
        QMessageBox.warning(
            self, "Sync failed",
            "Cloud unreachable. Inspections are safe locally and can be "
            "synced later.\n\n" + trace[-400:],
        )
