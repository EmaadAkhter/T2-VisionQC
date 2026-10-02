"""SQLite database layer for VisionQC.

Handles inspections, models, settings, and settings history.
Uses per-operation connections to survive Streamlit reruns.
"""

import os
import sqlite3
import time
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from contextlib import contextmanager

import paths


def get_db_path() -> str:
    """Get the database path (packaging-aware), creating parents if needed."""
    path = str(paths.db_path())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


@contextmanager
def get_connection():
    """Context manager for database connections.

    Yields a connection that is committed on success and closed on exit.
    """
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize the database schema."""
    with get_connection() as conn:
        # Inspections table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inspections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT UNIQUE NOT NULL,
                timestamp TEXT NOT NULL,
                product_id TEXT DEFAULT 'default',
                model_version TEXT,
                raw_score REAL,
                score REAL,
                threshold REAL,
                delta REAL,
                verdict TEXT,
                certainty TEXT,
                disposition TEXT DEFAULT 'PENDING',
                disposition_by_override INTEGER DEFAULT 0,
                operator_note TEXT,
                explanation TEXT,
                region_label TEXT,
                area_pct REAL,
                setup_status TEXT,
                image_path TEXT,
                overlay_path TEXT,
                latency_ms INTEGER,
                demo INTEGER DEFAULT 0
            )
        """)

        # Models table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS models (
                version TEXT PRIMARY KEY,
                product_id TEXT DEFAULT 'default',
                created_at TEXT,
                n_images INTEGER,
                backbone TEXT,
                coreset_ratio REAL,
                ref_score REAL,
                baseline_brightness REAL,
                baseline_blur REAL,
                parent_version TEXT,
                model_path TEXT
            )
        """)
        _add_column(conn, "models", "name", "TEXT")

        # Per-camera model assignment (local station scope). camera_key is
        # either a cloud camera id or "usb:<index>" for the built-in camera.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS camera_models (
                camera_key TEXT PRIMARY KEY,
                model_version TEXT,
                threshold REAL,
                delta REAL,
                updated_at TEXT
            )
        """)

        # Settings table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                product_id TEXT PRIMARY KEY,
                threshold REAL DEFAULT 0.46,
                delta REAL DEFAULT 0.05,
                active_model_version TEXT,
                pin_hash TEXT
            )
        """)

        # Settings history table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT
            )
        """)

        # Create indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inspections_timestamp ON inspections(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inspections_verdict ON inspections(verdict)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inspections_model_version ON inspections(model_version)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inspections_demo ON inspections(demo)")

        # Cloud sync columns (added after v1; safe to re-run)
        _add_column(conn, "inspections", "synced", "INTEGER DEFAULT 0")
        _add_column(conn, "inspections", "station_id", "TEXT")
        _add_column(conn, "inspections", "camera_id", "TEXT")
        _add_column(conn, "inspections", "local_revision", "INTEGER DEFAULT 0")
        _add_column(conn, "inspections", "synced_revision", "INTEGER DEFAULT -1")
        _add_column(conn, "inspections", "synced_at", "TEXT")
        _add_column(conn, "inspections", "sync_attempts", "INTEGER DEFAULT 0")
        _add_column(conn, "inspections", "last_sync_error", "TEXT")
        _add_column(conn, "inspections", "next_retry_at", "TEXT")
        _add_column(conn, "inspections", "evidence_synced", "INTEGER DEFAULT 0")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Product profiles: everything learned per product/camera view during
        # onboarding (canonical mask, background model, presence regions).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS product_profiles (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL DEFAULT 'default',
                camera_id TEXT,
                name TEXT NOT NULL,
                canonical_mask_path TEXT,
                background_model_path TEXT,
                presence_regions_path TEXT,
                coverage_floor REAL DEFAULT 0.9,
                threshold REAL DEFAULT 0.46,
                delta REAL DEFAULT 0.05,
                model_version TEXT,
                ref_score REAL,
                status TEXT NOT NULL DEFAULT 'draft',
                metrics_json TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_product "
                     "ON product_profiles(product_id, status)")
        conn.execute("UPDATE inspections SET station_id = 'legacy' WHERE station_id IS NULL")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_inspections_synced ON inspections(synced)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inspections_retry ON inspections(next_retry_at)")


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add a column if it does not exist (SQLite has no IF NOT EXISTS here)."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError:
        pass


# ---------------------------------------------------------------------------
# Station identity
# ---------------------------------------------------------------------------

def get_station_id() -> str:
    """Stable per-install station id: <host>-<4 hex>. Generated once."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'station_id'"
        ).fetchone()
        if row:
            return row[0]

    import re
    import socket
    import uuid

    host = re.sub(r"[^a-z0-9]+", "-", socket.gethostname().lower()).strip("-")
    host = host[:24] or "station"
    station = f"{host}-{uuid.uuid4().hex[:4]}"
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('station_id', ?)",
            (station,),
        )
    return station


def get_station_code() -> str:
    """Short station code used in inspection UIDs (4 chars, upper-case)."""
    return get_station_id().rsplit("-", 1)[-1].upper()


# ---------------------------------------------------------------------------
# Product profiles
# ---------------------------------------------------------------------------

def save_profile(profile: Dict[str, Any]) -> str:
    """Insert or update a product profile. Returns the profile id."""
    import json as _json
    import uuid

    profile_id = profile.get("id") or uuid.uuid4().hex
    metrics = profile.get("metrics")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO product_profiles (
                id, product_id, camera_id, name, canonical_mask_path,
                background_model_path, presence_regions_path, coverage_floor,
                threshold, delta, model_version, ref_score, status,
                metrics_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                profile.get("product_id", "default"),
                profile.get("camera_id"),
                profile.get("name", "Profile"),
                profile.get("canonical_mask_path"),
                profile.get("background_model_path"),
                profile.get("presence_regions_path"),
                profile.get("coverage_floor", 0.9),
                profile.get("threshold", 0.46),
                profile.get("delta", 0.05),
                profile.get("model_version"),
                profile.get("ref_score"),
                profile.get("status", "draft"),
                _json.dumps(metrics) if metrics else None,
                profile.get("created_at")
                or time.strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )
    return profile_id


def get_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM product_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return dict(row) if row else None


def list_profiles(product_id: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        if product_id:
            cursor = conn.execute(
                "SELECT * FROM product_profiles WHERE product_id = ? "
                "ORDER BY created_at DESC",
                (product_id,),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM product_profiles ORDER BY created_at DESC"
            )
        return [dict(row) for row in cursor.fetchall()]


def get_active_profile(product_id: str = "default") -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM product_profiles WHERE product_id = ? "
            "AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (product_id,),
        ).fetchone()
        return dict(row) if row else None


def activate_profile(profile_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE product_profiles SET status = 'draft' WHERE product_id = "
            "(SELECT product_id FROM product_profiles WHERE id = ?)",
            (profile_id,),
        )
        conn.execute(
            "UPDATE product_profiles SET status = 'active' WHERE id = ?",
            (profile_id,),
        )


def delete_profile(profile_id: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM product_profiles WHERE id = ?", (profile_id,))


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def get_unsynced_inspections(limit: int = 500) -> List[Dict[str, Any]]:
    """Inspections that are new or changed since the last successful sync.

    Rows waiting on a retry backoff are skipped until `next_retry_at` passes.
    """
    now = _utc_now_iso()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT * FROM inspections
            WHERE (synced = 0 OR local_revision > COALESCE(synced_revision, -1))
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY timestamp
            LIMIT ?
            """,
            (now, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def mark_inspections_synced(row_ids: List[int]) -> None:
    """Mark local inspections as successfully pushed to the cloud."""
    if not row_ids:
        return
    placeholders = ",".join("?" for _ in row_ids)
    with get_connection() as conn:
        conn.execute(
            f"""
            UPDATE inspections
            SET synced = 1,
                synced_at = ?,
                synced_revision = local_revision,
                sync_attempts = 0,
                last_sync_error = NULL,
                next_retry_at = NULL
            WHERE id IN ({placeholders})
            """,
            [_utc_now_iso(), *row_ids],
        )


def mark_inspection_sync_failed(row_id: int, error: str,
                                next_retry_at: str) -> None:
    """Record a failed push attempt and schedule the retry."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE inspections
            SET sync_attempts = sync_attempts + 1,
                last_sync_error = ?,
                next_retry_at = ?
            WHERE id = ?
            """,
            (error[:500], next_retry_at, row_id),
        )


def count_unsynced_inspections() -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT COUNT(*) FROM inspections
            WHERE synced = 0 OR local_revision > COALESCE(synced_revision, -1)
            """
        )
        return cursor.fetchone()[0]


def count_by_sync_state() -> Dict[str, int]:
    """Counts for the sync status UI."""
    with get_connection() as conn:
        pending = conn.execute(
            """
            SELECT COUNT(*) FROM inspections
            WHERE (synced = 0 OR local_revision > COALESCE(synced_revision, -1))
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            """,
            (_utc_now_iso(),),
        ).fetchone()[0]
        retrying = conn.execute(
            """
            SELECT COUNT(*) FROM inspections
            WHERE synced = 0 AND next_retry_at IS NOT NULL AND next_retry_at > ?
            """,
            (_utc_now_iso(),),
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM inspections").fetchone()[0]
    return {
        "pending": pending,
        "retrying": retrying,
        "synced": total - pending - retrying,
        "total": total,
    }


def generate_inspection_uid() -> str:
    """Human-readable inspection ID: INS-YYYYMMDD-NNNN-<station code>."""
    from datetime import datetime

    date_str = datetime.now().strftime("%Y%m%d")
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM inspections WHERE uid LIKE ?",
            (f"INS-{date_str}-%",),
        )
        count = cursor.fetchone()[0]
    return f"INS-{date_str}-{count + 1:04d}-{get_station_code()}"


def log_inspection(
    uid: str,
    timestamp: str,
    product_id: str,
    model_version: str,
    raw_score: float,
    score: float,
    threshold: float,
    delta: float,
    verdict: str,
    certainty: str,
    explanation: str,
    region_label: Optional[str],
    area_pct: float,
    setup_status: str,
    image_path: str,
    overlay_path: str,
    latency_ms: int,
    demo: int = 0,
    operator_note: Optional[str] = None,
    camera_id: Optional[str] = None,
) -> int:
    """Log a single inspection. Returns the row ID."""
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO inspections (
                uid, timestamp, product_id, model_version, raw_score, score,
                threshold, delta, verdict, certainty, disposition, explanation,
                region_label, area_pct, setup_status, image_path, overlay_path,
                latency_ms, demo, operator_note, station_id, camera_id,
                local_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            uid, timestamp, product_id, model_version, raw_score, score,
            threshold, delta, verdict, certainty, explanation,
            region_label, area_pct, setup_status, image_path, overlay_path,
            latency_ms, demo, operator_note, get_station_id(), camera_id
        ))
        return cursor.lastrowid


def update_disposition(
    inspection_id: int,
    disposition: str,
    override: bool = False,
    note: Optional[str] = None,
):
    """Update the final disposition; bumps local_revision so sync re-pushes it."""
    with get_connection() as conn:
        conn.execute("""
            UPDATE inspections
            SET disposition = ?,
                disposition_by_override = ?,
                operator_note = COALESCE(?, operator_note),
                local_revision = local_revision + 1
            WHERE id = ?
        """, (disposition, 1 if override else 0, note, inspection_id))


def get_inspections(
    date: Optional[str] = None,
    verdict: Optional[str] = None,
    demo: Optional[int] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Get inspections with optional filters."""
    query = "SELECT * FROM inspections WHERE 1=1"
    params = []

    if date:
        query += " AND date(timestamp) = date(?)"
        params.append(date)
    if verdict:
        query += " AND verdict = ?"
        params.append(verdict)
    if demo is not None:
        query += " AND demo = ?"
        params.append(demo)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_dashboard_stats(date: Optional[str] = None, demo: int = 0) -> Dict[str, Any]:
    """Get dashboard statistics for a given date."""
    if date is None:
        from datetime import datetime
        date = datetime.now().strftime("%Y-%m-%d")

    with get_connection() as conn:
        # Total inspected
        cursor = conn.execute(
            "SELECT COUNT(*) FROM inspections WHERE date(timestamp) = date(?) AND demo = ?",
            (date, demo)
        )
        total = cursor.fetchone()[0]

        if total == 0:
            return {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "pending_review": 0,
                "rejection_rate": None,
            }

        # Passed (final disposition PASS)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM inspections WHERE date(timestamp) = date(?) AND demo = ? AND disposition = 'PASS'",
            (date, demo)
        )
        passed = cursor.fetchone()[0]

        # Failed (final disposition FAIL)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM inspections WHERE date(timestamp) = date(?) AND demo = ? AND disposition = 'FAIL'",
            (date, demo)
        )
        failed = cursor.fetchone()[0]

        # Pending review
        cursor = conn.execute(
            "SELECT COUNT(*) FROM inspections WHERE date(timestamp) = date(?) AND demo = ? AND disposition = 'PENDING'",
            (date, demo)
        )
        pending = cursor.fetchone()[0]

        rejection_rate = failed / total if total > 0 else 0

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pending_review": pending,
            "rejection_rate": rejection_rate,
        }


def save_model_metadata(
    version: str,
    product_id: str,
    created_at: str,
    n_images: int,
    backbone: str,
    coreset_ratio: float,
    ref_score: float,
    baseline_brightness: float,
    baseline_blur: float,
    parent_version: Optional[str],
    model_path: str,
    name: Optional[str] = None,
):
    """Save model metadata to the database."""
    with get_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO models (
                version, product_id, created_at, n_images, backbone, coreset_ratio,
                ref_score, baseline_brightness, baseline_blur, parent_version,
                model_path, name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            version, product_id, created_at, n_images, backbone, coreset_ratio,
            ref_score, baseline_brightness, baseline_blur, parent_version,
            model_path, name
        ))


def rename_model(version: str, name: str) -> None:
    """Set the human label of a trained model."""
    with get_connection() as conn:
        conn.execute("UPDATE models SET name = ? WHERE version = ?",
                     (name, version))


def set_camera_model(camera_key: str, model_version: Optional[str],
                     threshold: Optional[float] = None,
                     delta: Optional[float] = None) -> None:
    """Assign a model (or None = active model) to a camera / source key."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO camera_models (camera_key, model_version, threshold,
                                       delta, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(camera_key) DO UPDATE SET
                model_version = excluded.model_version,
                threshold = excluded.threshold,
                delta = excluded.delta,
                updated_at = excluded.updated_at
        """, (camera_key, model_version, threshold, delta,
              time.strftime("%Y-%m-%dT%H:%M:%S")))


def get_camera_model(camera_key: str) -> Optional[Dict[str, Any]]:
    """Model assignment for a camera / source key, if any."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM camera_models WHERE camera_key = ?", (camera_key,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_camera_models() -> List[Dict[str, Any]]:
    """All camera-to-model assignments on this station."""
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM camera_models ORDER BY camera_key")
        return [dict(row) for row in cursor.fetchall()]


def get_model(version: str) -> Optional[Dict[str, Any]]:
    """Get model metadata by version."""
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM models WHERE version = ?", (version,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_models(product_id: str = "default") -> List[Dict[str, Any]]:
    """Get all models for a product."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM models WHERE product_id = ? ORDER BY created_at DESC",
            (product_id,)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_settings(product_id: str = "default") -> Dict[str, Any]:
    """Get settings for a product."""
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM settings WHERE product_id = ?", (product_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        # Create default settings
        conn.execute("""
            INSERT INTO settings (product_id, threshold, delta, active_model_version, pin_hash)
            VALUES (?, 0.46, 0.05, NULL, NULL)
        """, (product_id,))
        return {
            "product_id": product_id,
            "threshold": 0.46,
            "delta": 0.05,
            "active_model_version": None,
            "pin_hash": None,
        }


def update_settings(product_id: str, field: str, new_value: Any):
    """Update a setting and log the change."""
    # Get old value
    settings = get_settings(product_id)
    old_value = settings.get(field)

    with get_connection() as conn:
        conn.execute(
            f"UPDATE settings SET {field} = ? WHERE product_id = ?",
            (new_value, product_id)
        )
        conn.execute("""
            INSERT INTO settings_history (timestamp, field, old_value, new_value)
            VALUES (?, ?, ?, ?)
        """, (time.strftime("%Y-%m-%dT%H:%M:%S"), field, str(old_value), str(new_value)))


def get_settings_history(limit: int = 100) -> List[Dict[str, Any]]:
    """Get settings change history."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM settings_history ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_recent_scores(model_version: str, limit: int = 200) -> List[float]:
    """Get recent normalized scores for a model version (for impact preview)."""
    with get_connection() as conn:
        cursor = conn.execute("""
            SELECT score FROM inspections
            WHERE model_version = ? AND demo = 0
            ORDER BY timestamp DESC
            LIMIT ?
        """, (model_version, limit))
        rows = cursor.fetchall()
        return [row[0] for row in rows]


def export_csv(date: str, demo: int = 0) -> str:
    """Export inspections for a date as CSV string."""
    import csv
    import io

    inspections = get_inspections(date=date, demo=demo, limit=10000)

    if not inspections:
        return ""

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=inspections[0].keys())
    writer.writeheader()
    writer.writerows(inspections)

    return output.getvalue()
