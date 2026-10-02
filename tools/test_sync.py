"""Automated sync tests against the local Supabase stack.

Usage:
    python3 tools/test_sync.py

Covers: duplicate pushes, disposition update propagation, retry/backoff,
offline queue drain, evidence policy, settings push, two-station UID safety.
Uses a temporary SQLite database and cleans its cloud rows afterwards.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Isolate local state before importing anything that reads paths.
TMP_DATA = tempfile.mkdtemp(prefix="visionqc_sync_")
os.environ["VISIONQC_DATA_DIR"] = TMP_DATA

from PySide6.QtCore import QCoreApplication  # noqa: E402

from db import database as db  # noqa: E402
from desktop.auth import AuthService  # noqa: E402
from desktop.config import load_config  # noqa: E402
from desktop.sync import SyncEngine  # noqa: E402

CONFIG = json.loads((ROOT / "desktop" / "config.local.json").read_text())
RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def service_key() -> str:
    result = subprocess.run(
        ["supabase", "status", "-o", "json"],
        capture_output=True, text=True, cwd=ROOT,
    )
    return json.loads(result.stdout)["SERVICE_ROLE_KEY"]


class FlakyTable:
    def __init__(self, real, fail_first: bool):
        self.real = real
        self.fail_first = fail_first

    def upsert(self, *args, **kwargs):
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("connection reset by peer")
        return self.real.upsert(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self.real, item)


class FlakyClient:
    """Fails the first inspections upsert, then behaves normally."""

    def __init__(self, real, fail_inspections_once: bool = True):
        self.real = real
        self.fail_inspections_once = fail_inspections_once

    def table(self, name: str):
        if name == "inspections" and self.fail_inspections_once:
            self.fail_inspections_once = False
            return FlakyTable(self.real.table(name), True)
        return self.real.table(name)

    def __getattr__(self, item):
        return getattr(self.real, item)


def log_sample(uid: str, verdict: str = "PASS", score: float = 0.2) -> int:
    return db.log_inspection(
        uid=uid,
        timestamp="2026-10-03T10:00:00",
        product_id="default",
        model_version="sync-test",
        raw_score=score * 2,
        score=score,
        threshold=0.46,
        delta=0.05,
        verdict=verdict,
        certainty="High",
        explanation="sync test row",
        region_label=None,
        area_pct=0.0,
        setup_status="OK",
        image_path="",
        overlay_path="",
        latency_ms=10,
        demo=0,
    )


def main() -> int:
    app = QCoreApplication.instance() or QCoreApplication([])
    db.init_db()

    auth = AuthService(load_config())
    auth.sign_in("owner@visionqc.local", "visionqc123")
    org_id = auth.refresh_orgs()[0].org_id

    engine = SyncEngine(auth, org_id)
    cloud = auth.client
    prefix = "SYNCTEST-"

    # Cleanup from any previous run.
    cloud.table("inspections").delete().like("uid", f"{prefix}%").execute()

    # ---------------------------------------------------------------- push ---
    log_sample(f"{prefix}0001", "PASS", 0.2)
    log_sample(f"{prefix}0002", "FAIL", 0.7)
    engine._run()  # noqa: SLF001 - synchronous test run
    rows = (
        cloud.table("inspections").select("uid,station_id")
        .like("uid", f"{prefix}%").execute().data
    )
    check("push: rows reach cloud", len(rows) == 2, str(len(rows)))
    check("push: station attributed",
          all(r.get("station_id") for r in rows))
    check("push: local queue drained",
          db.count_unsynced_inspections() == 0)

    # ------------------------------------------------------- duplicate push ---
    engine._run()  # noqa: SLF001
    rows2 = (
        cloud.table("inspections").select("uid")
        .like("uid", f"{prefix}%").execute().data
    )
    check("duplicate push: still 2 cloud rows", len(rows2) == 2, str(len(rows2)))

    # ------------------------------------- disposition update propagation ----
    local = db.get_inspections(limit=100)
    target = next(r for r in local if r["uid"] == f"{prefix}0001")
    db.update_disposition(target["id"], "FAIL", override=True, note="operator")
    check("disposition edit marks row pending",
          db.count_unsynced_inspections() == 1)
    engine._run()  # noqa: SLF001
    cloud_row = (
        cloud.table("inspections").select("disposition,disposition_by_override")
        .eq("uid", f"{prefix}0001").execute().data[0]
    )
    check("disposition update reaches cloud",
          cloud_row["disposition"] == "FAIL"
          and cloud_row["disposition_by_override"] is True,
          str(cloud_row))

    # ------------------------------------------------- retry / backoff -------
    log_sample(f"{prefix}0003", "PASS", 0.3)
    engine.auth.client = FlakyClient(auth.client, fail_inspections_once=True)
    engine._run()  # noqa: SLF001 - first inspections upsert fails
    states = db.count_by_sync_state()
    check("failure: row scheduled for retry", states["retrying"] >= 1,
          str(states))
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE inspections SET next_retry_at = '2000-01-01T00:00:00' "
            "WHERE uid = ?", (f"{prefix}0003",)
        )
    engine.auth.client = auth.client
    engine._run()  # noqa: SLF001 - retry succeeds
    check("retry: row arrives exactly once",
          len(cloud.table("inspections").select("uid")
              .eq("uid", f"{prefix}0003").execute().data) == 1)

    # --------------------------------------------------- offline drain -------
    for index in range(4, 14):
        log_sample(f"{prefix}{index:04d}", "PASS", 0.2)
    engine._run()  # noqa: SLF001 - "network back"
    drained = (
        cloud.table("inspections").select("uid")
        .like("uid", f"{prefix}%").execute().data
    )
    check("offline drain: all rows exactly once", len(drained) == 13,
          str(len(drained)))

    # ------------------------------------------------------- evidence policy --
    images = Path(TMP_DATA) / "images"
    images.mkdir(parents=True, exist_ok=True)
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE inspections SET camera_id = NULL, synced = 1, evidence_synced = 0"
        )
        for row in conn.execute(
            "SELECT id, uid FROM inspections WHERE uid LIKE ?", (f"{prefix}%",)
        ).fetchall():
            overlay = images / f"{row['uid']}_overlay.png"
            original = images / f"{row['uid']}_original.png"
            overlay.write_bytes(b"png-overlay")
            original.write_bytes(b"png-original")
            conn.execute(
                "UPDATE inspections SET image_path = ?, overlay_path = ? WHERE id = ?",
                (str(original), str(overlay), row["id"]),
            )

    engine.set_evidence_policy("none")
    uploaded = engine._upload_evidence()  # noqa: SLF001
    check("evidence none: nothing uploaded", uploaded == 0)

    engine.set_evidence_policy("overlay")
    uploaded = engine._upload_evidence()  # noqa: SLF001
    listing = cloud.storage.from_("evidence").list(
        f"{org_id}/unassigned", {"limit": 100}
    )
    check("evidence overlay: objects uploaded",
          uploaded == 13 and len(listing) >= 13,
          f"uploaded={uploaded} objects={len(listing)}")

    # ------------------------------------------------------ settings push -----
    engine._run()  # noqa: SLF001
    product_id = engine._cloud_product_id()  # noqa: SLF001
    cloud_settings = (
        cloud.table("settings").select("threshold,active_model_version")
        .eq("org_id", org_id).eq("product_id", product_id).execute().data
    )
    check("settings pushed to cloud", len(cloud_settings) == 1, str(cloud_settings))

    # ------------------------------------------------ two-station UID safety --
    station_uid = {}
    for label in ("aaaa", "bbbb"):
        with db.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('station_id', ?)",
                (f"sync-test-{label}",),
            )
        station_uid[label] = db.generate_inspection_uid()
    check("two stations: UIDs differ",
          station_uid["aaaa"] != station_uid["bbbb"], str(station_uid))
    check("two stations: codes present",
          station_uid["aaaa"].endswith("AAAA")
          and station_uid["bbbb"].endswith("BBBB"))

    # ------------------------------------------------------------- cleanup ----
    cloud.table("inspections").delete().like("uid", f"{prefix}%").execute()
    try:
        cloud.storage.from_("evidence").remove(
            [f"{org_id}/unassigned/{name}" for name in
             [o["name"] for o in listing]]
        )
    except Exception:  # noqa: BLE001
        pass

    failed = [name for name, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
