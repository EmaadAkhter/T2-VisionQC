"""KPI page: today's quality numbers, recent inspections and cloud sync."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import database as db
from desktop import theme
from desktop.auth import AuthService, OrgContext
from desktop.ui.widgets import (
    MetricTile,
    caption,
    card,
    make_table,
    muted,
    page_header,
)
from desktop.ui.errors import show_error
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
    def __init__(self, auth: AuthService, org: OrgContext, sync_engine=None):
        super().__init__()
        self.auth = auth
        self.org = org
        self.sync_engine = sync_engine
        self.worker: FunctionWorker | None = None

        self._build_ui()
        self.refresh()
        if sync_engine is not None:
            sync_engine.progress.connect(self._on_engine_progress)
            sync_engine.state_changed.connect(self._on_engine_state)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.PAGE_MARGIN, theme.PAGE_MARGIN,
                                theme.PAGE_MARGIN, theme.PAGE_MARGIN)
        root.setSpacing(theme.SPACE_M)

        header = QHBoxLayout()
        header.addWidget(page_header(
            "KPI", "Today's numbers from this machine; sync shares them"
        ), 1)
        self.sync_status = caption("")
        header.addWidget(self.sync_status, 0, Qt.AlignmentFlag.AlignVCenter)
        self.sync_button = QPushButton("Sync now")
        self.sync_button.setObjectName("Primary")
        self.sync_button.clicked.connect(self._sync)
        header.addWidget(self.sync_button)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        root.addLayout(header)

        self.date_label = caption("")
        root.addWidget(self.date_label)

        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(theme.SPACE_M)
        self.tiles: dict[str, MetricTile] = {}
        for key, label in [
            ("total", "Inspected"),
            ("passed", "Passed"),
            ("failed", "Failed"),
            ("pending", "Pending review"),
            ("rejection", "Rejection rate"),
        ]:
            tile = MetricTile(label)
            self.tiles[key] = tile
            tiles_row.addWidget(tile)
        root.addLayout(tiles_row)

        table_card, table_layout = card("Recent inspections")
        self.table = make_table(
            ["Time", "Inspection", "Score", "Verdict", "Disposition",
             "Certainty", "Explanation"]
        )
        table_layout.addWidget(self.table)
        root.addWidget(table_card, 1)

    # --------------------------------------------------------------- refresh

    def refresh(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        stats = db.get_dashboard_stats(today)
        self.date_label.setText(f"Local calendar day · {today}")

        self.tiles["total"].set_value(str(stats["total"]))
        self.tiles["passed"].set_value(str(stats["passed"]), theme.PASS)
        self.tiles["failed"].set_value(str(stats["failed"]), theme.FAIL)
        self.tiles["pending"].set_value(str(stats["pending_review"]), theme.REVIEW)
        rejection = stats["rejection_rate"]
        self.tiles["rejection"].set_value(
            "—" if rejection is None else f"{rejection:.1%}"
        )

        unsynced = db.count_unsynced_inspections()
        self.sync_status.setText(
            f"{unsynced} inspection(s) waiting to sync" if unsynced
            else "All synced"
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
                (row.get("explanation") or "")[:70],
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 3 and text in theme.VERDICT_COLORS:
                    item.setForeground(QColor(theme.VERDICT_COLORS[text]))
                self.table.setItem(row_idx, col, item)
        self.table.resizeColumnsToContents()

    # ------------------------------------------------------------------ sync

    def _sync(self) -> None:
        if self.sync_engine is not None:
            self.sync_status.setText("Sync requested…")
            self.sync_engine.sync_now()
            return
        self.sync_button.setEnabled(False)
        self.sync_status.setText("Syncing…")
        self.worker = FunctionWorker(sync_inspections, self.auth, self.org.org_id)
        self.worker.finished_ok.connect(self._sync_done)
        self.worker.failed.connect(self._sync_failed)
        self.worker.start()

    def _on_engine_state(self, state: str) -> None:
        labels = {
            "idle": "All synced",
            "syncing": "Syncing…",
            "offline": "Cloud offline — will retry automatically",
            "error": "Sync error — will retry automatically",
        }
        self.sync_status.setText(labels.get(state, state))

    def _on_engine_progress(self, info: dict) -> None:
        remaining = info.get("remaining", 0)
        pushed = info.get("pushed", 0)
        retrying = info.get("retrying", 0)
        if info.get("error"):
            self.sync_status.setText(f"Sync error: {info['error'][:80]}")
        else:
            self.sync_status.setText(
                f"Pushed {pushed} · {remaining} remaining"
                + (f" · {retrying} retrying" if retrying else "")
            )
        self.refresh()

    def _sync_done(self, result: dict) -> None:
        self.sync_button.setEnabled(True)
        self.sync_status.setText(
            f"Pushed {result['pushed']} · {result['remaining']} remaining"
        )
        self.refresh()

    def _sync_failed(self, trace: str) -> None:
        self.sync_button.setEnabled(True)
        self.sync_status.setText("Sync failed — will retry later.")
        show_error(
            self, "Sync failed",
            "Cloud unreachable. Inspections are safe locally and can be "
            "synced later.",
            trace,
        )
