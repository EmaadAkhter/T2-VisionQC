"""Background sync engine.

Pushes local inspections (and, per policy, evidence images) to Supabase with
automatic retries, exponential backoff and idempotent upserts. Also registers
the edge station and mirrors settings/model metadata when a cloud product is
resolved.

The engine runs network calls on plain threads; Qt signals carry results back
to the UI thread.
"""

from __future__ import annotations

import random
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

import paths
from db import database as db
from desktop.auth import AuthService

BATCH_SIZE = 200
BASE_RETRY_SECONDS = 30
MAX_RETRY_SECONDS = 15 * 60


def _log_sync(message: str) -> None:
    """Append one line to <data>/logs/sync.log (best effort)."""
    try:
        log_path = paths.logs_dir() / "sync.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} "
                         f"{message}\n")
    except OSError:
        pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def next_backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter, capped at 15 minutes."""
    base = min(BASE_RETRY_SECONDS * (2 ** max(attempt - 1, 0)), MAX_RETRY_SECONDS)
    return base * random.uniform(0.8, 1.2)


class SyncEngine(QObject):
    """Single-flight background sync with retry scheduling."""

    state_changed = Signal(str)          # idle | syncing | offline | error
    progress = Signal(dict)              # {"pushed", "remaining", "retrying"}
    finished = Signal(int)               # pushed count of the last run

    def __init__(self, auth: AuthService, org_id: str,
                 interval_seconds: int = 60, parent=None):
        super().__init__(parent)
        self.auth = auth
        self.org_id = org_id
        self.interval_seconds = interval_seconds
        self._running = False
        self._timer = QTimer(self)
        self._timer.setInterval(interval_seconds * 1000)
        self._timer.timeout.connect(self.sync_now)
        self._pending_kick = QTimer(self)
        self._pending_kick.setSingleShot(True)
        self._pending_kick.setInterval(5000)
        self._pending_kick.timeout.connect(self.sync_now)

    # -------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self._timer.start()
        QTimer.singleShot(4000, self.sync_now)

    def stop(self) -> None:
        self._timer.stop()
        self._pending_kick.stop()

    def kick(self) -> None:
        """Debounced sync request (call from the UI thread after logging)."""
        self._pending_kick.start()

    # ------------------------------------------------------------------- runs

    def sync_now(self) -> None:
        if self._running:
            return
        self._running = True
        self.state_changed.emit("syncing")
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self._register_station()
            pushed = self._push_inspections()
            self._push_settings_and_models()
            self._upload_evidence()
            counts = db.count_by_sync_state()
            self.progress.emit({
                "pushed": pushed,
                "remaining": counts["pending"] + counts["retrying"],
                "retrying": counts["retrying"],
            })
            self.state_changed.emit("idle")
            self.finished.emit(pushed)
        except Exception as exc:  # noqa: BLE001
            _log_sync(f"sync run failed: {exc}")
            self.state_changed.emit("offline" if _looks_offline(exc) else "error")
            self.progress.emit({
                "pushed": 0,
                "remaining": db.count_unsynced_inspections(),
                "error": str(exc)[:200],
            })
        finally:
            self._running = False

    # -------------------------------------------------------------- internals

    def _register_station(self) -> None:
        from desktop import __version__

        station = db.get_station_id()
        self.auth.client.table("edge_stations").upsert(
            {
                "org_id": self.org_id,
                "id": station,
                "name": station,
                "app_version": __version__,
                "last_seen_at": _iso(_utc_now()),
            },
            on_conflict="org_id,id",
        ).execute()

    def _cloud_product_id(self) -> str | None:
        """Resolve the cloud product for this single-product desktop.

        The desktop currently runs one product ("default"). We map it to the
        organization's first cloud product and cache the mapping in app_meta.
        """
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'cloud_product_id'"
            ).fetchone()
            if row:
                return row[0]
        try:
            products = (
                self.auth.client.table("products")
                .select("id")
                .limit(1)
                .execute()
                .data
            )
        except Exception:  # noqa: BLE001
            return None
        if not products:
            return None
        product_id = products[0]["id"]
        with db.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
                ("cloud_product_id", product_id),
            )
        return product_id

    def _push_inspections(self) -> int:
        pushed = 0
        while True:
            rows = db.get_unsynced_inspections(BATCH_SIZE)
            if not rows:
                return pushed
            payload = [self._inspection_payload(row) for row in rows]
            try:
                self.auth.client.table("inspections").upsert(
                    payload,
                    on_conflict="org_id,uid",
                    ignore_duplicates=False,
                ).execute()
            except Exception as exc:  # noqa: BLE001
                attempt = max((row.get("sync_attempts") or 0) for row in rows) + 1
                retry_at = _iso(_utc_now() + timedelta(
                    seconds=next_backoff_seconds(attempt)
                ))
                for row in rows:
                    db.mark_inspection_sync_failed(row["id"], str(exc), retry_at)
                raise
            db.mark_inspections_synced([row["id"] for row in rows])
            pushed += len(rows)

    def _inspection_payload(self, row: dict) -> dict:
        return {
            "org_id": self.org_id,
            "uid": row["uid"],
            "station_id": row.get("station_id"),
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
        }

    def _push_settings_and_models(self) -> None:
        product_id = self._cloud_product_id()
        if not product_id:
            return
        settings = db.get_settings()
        self.auth.client.table("settings").upsert(
            {
                "org_id": self.org_id,
                "product_id": product_id,
                "threshold": settings.get("threshold", 0.46),
                "delta": settings.get("delta", 0.05),
                "active_model_version": settings.get("active_model_version"),
                "updated_at": _iso(_utc_now()),
            },
            on_conflict="org_id,product_id",
        ).execute()

        for model in db.get_all_models():
            self.auth.client.table("models").upsert(
                {
                    "org_id": self.org_id,
                    "product_id": product_id,
                    "version": model["version"],
                    "n_images": model.get("n_images", 0),
                    "ref_score": model.get("ref_score"),
                    "backbone": model.get("backbone", "WideResNet-50"),
                    "metrics": {},
                },
                on_conflict="product_id,version",
            ).execute()

    # ---------------------------------------------------------------- evidence

    def evidence_policy(self) -> str:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'evidence_policy'"
            ).fetchone()
        return row[0] if row else "none"

    def set_evidence_policy(self, policy: str) -> None:
        if policy not in ("none", "overlay", "original+overlay"):
            raise ValueError(f"Unknown evidence policy: {policy}")
        with db.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
                ("evidence_policy", policy),
            )

    def _upload_evidence(self) -> int:
        policy = self.evidence_policy()
        if policy == "none":
            return 0
        uploaded = 0
        with db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, uid, camera_id, image_path, overlay_path
                FROM inspections
                WHERE synced = 1 AND evidence_synced = 0
                ORDER BY timestamp
                LIMIT 50
                """
            ).fetchall()
        for row in rows:
            uid = row["uid"]
            camera = row["camera_id"] or "unassigned"
            files = []
            if policy == "original+overlay" and row["image_path"]:
                files.append((row["image_path"], f"{uid}_original.png"))
            if row["overlay_path"]:
                files.append((row["overlay_path"], f"{uid}_overlay.png"))

            uploaded_any = False
            try:
                for local_path, name in files:
                    path = Path(local_path)
                    if not path.exists():
                        continue
                    object_path = f"{self.org_id}/{camera}/{name}"
                    self.auth.client.storage.from_("evidence").upload(
                        object_path,
                        path.read_bytes(),
                        {"content-type": "image/png", "upsert": "true"},
                    )
                    uploaded_any = True
                # Nothing to send (or files already gone): mark handled so the
                # queue does not rescan it forever.
                with db.get_connection() as conn:
                    conn.execute(
                        "UPDATE inspections SET evidence_synced = 1 WHERE id = ?",
                        (row["id"],),
                    )
                if uploaded_any:
                    uploaded += 1
            except Exception as exc:  # noqa: BLE001 - evidence retries next run
                _log_sync(f"evidence upload failed for {uid}: {exc}")
                continue
        return uploaded


def _looks_offline(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = ("connection", "timeout", "timed out", "unreachable",
               "name or service", "network", "temporary failure")
    return any(marker in text for marker in markers)
