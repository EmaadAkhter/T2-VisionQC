"""Packaging-aware filesystem locations.

Resolution order for the data directory:
1. ``VISIONQC_DATA_DIR`` environment variable (explicit override)
2. the repository ``data/`` folder when running from source (dev mode)
3. the platform user-data directory (packaged builds, read-only bundles)

The bundled torch weights are exposed through ``ensure_torch_home()`` so the
packaged app never tries to download model weights at the factory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "VisionQC"


def _repo_root() -> Path | None:
    """Repository root when running from source; None when frozen."""
    if getattr(sys, "frozen", False):
        return None
    return Path(__file__).resolve().parent


def _user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    return (
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        / "visionqc"
    )


def data_dir() -> Path:
    override = os.environ.get("VISIONQC_DATA_DIR")
    if override:
        path = Path(override).expanduser()
    else:
        repo = _repo_root()
        legacy = repo / "data" if repo else None
        path = legacy if legacy is not None and legacy.exists() else _user_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def images_dir() -> Path:
    path = data_dir() / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "visionqc.db"


def session_path() -> Path:
    return data_dir() / "desktop_session.json"


def config_path() -> Path:
    return data_dir() / "config.json"


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_weights_dir() -> Path | None:
    """Directory that contains ``hub/checkpoints/wide_resnet50_2-*.pth``."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates += [base / "torch_home", base / "weights"]
    repo = _repo_root()
    if repo is not None:
        candidates += [repo / "packaging" / "weights" / "torch_home"]
    for candidate in candidates:
        checkpoint_dir = candidate / "hub" / "checkpoints"
        if checkpoint_dir.exists() and any(checkpoint_dir.glob("wide_resnet50_2-*.pth")):
            return candidate
    return None


def ensure_torch_home() -> Path | None:
    """Point torch at bundled weights when available (no runtime downloads)."""
    bundled = bundled_weights_dir()
    if bundled is not None:
        os.environ.setdefault("TORCH_HOME", str(bundled))
    return bundled
