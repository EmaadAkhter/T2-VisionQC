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


DATABASE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "visionqc.db")


def get_db_path() -> str:
    """Get the database path, creating parent directories if needed."""
    path = os.path.abspath(DATABASE_PATH)
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

        # Cloud sync flag (added after v1; safe to re-run)
        try:
            conn.execute("ALTER TABLE inspections ADD COLUMN synced INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inspections_synced ON inspections(synced)")


def get_unsynced_inspections(limit: int = 500) -> List[Dict[str, Any]]:
    """Inspections not yet pushed to the cloud."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM inspections WHERE synced = 0 ORDER BY timestamp LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


def mark_inspections_synced(row_ids: List[int]) -> None:
    """Mark local inspections as successfully pushed to the cloud."""
    if not row_ids:
        return
    placeholders = ",".join("?" for _ in row_ids)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE inspections SET synced = 1 WHERE id IN ({placeholders})",
            row_ids,
        )


def count_unsynced_inspections() -> int:
    with get_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM inspections WHERE synced = 0")
        return cursor.fetchone()[0]


def generate_inspection_uid() -> str:
    """Generate a human-readable inspection ID: INS-YYYYMMDD-NNNN."""
    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d")

    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM inspections WHERE uid LIKE ?",
            (f"INS-{date_str}-%",)
        )
        count = cursor.fetchone()[0]

    return f"INS-{date_str}-{count + 1:04d}"


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
) -> int:
    """Log a single inspection. Returns the row ID."""
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO inspections (
                uid, timestamp, product_id, model_version, raw_score, score,
                threshold, delta, verdict, certainty, disposition, explanation,
                region_label, area_pct, setup_status, image_path, overlay_path,
                latency_ms, demo, operator_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            uid, timestamp, product_id, model_version, raw_score, score,
            threshold, delta, verdict, certainty, explanation,
            region_label, area_pct, setup_status, image_path, overlay_path,
            latency_ms, demo, operator_note
        ))
        return cursor.lastrowid


def update_disposition(
    inspection_id: int,
    disposition: str,
    override: bool = False,
    note: Optional[str] = None,
):
    """Update the final disposition of an inspection."""
    with get_connection() as conn:
        conn.execute("""
            UPDATE inspections
            SET disposition = ?, disposition_by_override = ?, operator_note = COALESCE(?, operator_note)
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
):
    """Save model metadata to the database."""
    with get_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO models (
                version, product_id, created_at, n_images, backbone, coreset_ratio,
                ref_score, baseline_brightness, baseline_blur, parent_version, model_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            version, product_id, created_at, n_images, backbone, coreset_ratio,
            ref_score, baseline_brightness, baseline_blur, parent_version, model_path
        ))


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
