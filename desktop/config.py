"""Desktop configuration loader.

Resolution order (later sources fill in missing keys only):
1. environment variables (VISIONQC_SUPABASE_URL / VISIONQC_SUPABASE_ANON_KEY)
2. user config file in the app data directory (written by the first-run dialog)
3. developer config ``desktop/config.local.json`` (repo, git-ignored)
4. bundled ``config.default.json`` next to the executable

The anon key is a public client key; the service-role key must never appear
here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import paths

_KEYS = ("supabase_url", "supabase_anon_key", "studio_url")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _dev_config_path() -> Path:
    return Path(__file__).resolve().parent / "config.local.json"


def _bundled_config_path() -> Path | None:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "config.default.json"
    return None


def load_config() -> dict:
    config: dict = {
        "supabase_url": "",
        "supabase_anon_key": "",
        "studio_url": "",
    }
    sources: list[dict] = []
    import os

    sources.append({
        "supabase_url": os.environ.get("VISIONQC_SUPABASE_URL", ""),
        "supabase_anon_key": os.environ.get("VISIONQC_SUPABASE_ANON_KEY", ""),
        "studio_url": os.environ.get("VISIONQC_STUDIO_URL", ""),
    })
    sources.append(_read_json(paths.config_path()))
    sources.append(_read_json(_dev_config_path()))
    bundled = _bundled_config_path()
    if bundled is not None:
        sources.append(_read_json(bundled))

    for source in sources:
        for key in _KEYS:
            if not config.get(key) and source.get(key):
                config[key] = source[key]
    return config


def is_configured(config: dict | None = None) -> bool:
    config = config or load_config()
    return bool(config.get("supabase_url") and config.get("supabase_anon_key"))


def save_config(supabase_url: str, supabase_anon_key: str,
                studio_url: str = "") -> None:
    """Persist the first-run configuration to the app data directory."""
    payload = {
        "supabase_url": supabase_url.strip().rstrip("/"),
        "supabase_anon_key": supabase_anon_key.strip(),
        "studio_url": studio_url.strip(),
        "saved_by": "first-run dialog",
    }
    path = paths.config_path()
    path.write_text(json.dumps(payload, indent=2) + "\n")
