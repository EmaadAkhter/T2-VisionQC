"""Packaged-app entry point.

Usage (source or bundled binary):
    VisionQC                 # normal launch
    VisionQC --version       # print version
    VisionQC --smoke         # construct all windows offscreen and exit 0
    VisionQC --data-dir DIR  # override the data directory
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _apply_args(argv: list[str]) -> int | None:
    if "--version" in argv:
        from desktop import __version__

        print(__version__)
        return 0
    if "--data-dir" in argv:
        index = argv.index("--data-dir")
        if index + 1 < len(argv):
            os.environ["VISIONQC_DATA_DIR"] = argv[index + 1]
    return None


def run_smoke() -> int:
    """Construct the real windows without network or display; CI self-test."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import paths

    paths.ensure_torch_home()
    paths.ensure_model_home()

    from db import database as db

    db.init_db()

    from PySide6.QtWidgets import QApplication

    from desktop.auth import OrgContext
    from desktop.theme import STYLESHEET, apply_app_font
    from desktop.ui.window import MainWindow

    app = QApplication.instance() or QApplication([])
    apply_app_font(app)
    app.setStyleSheet(STYLESHEET)

    class StubAuth:
        user_email = "smoke@local"
        config: dict = {}

        def sign_out(self) -> None:
            pass

        def is_online(self) -> bool:
            return False

    org = OrgContext(
        org_id="00000000-0000-0000-0000-000000000000",
        org_name="Smoke Org",
        role="owner",
    )
    window = MainWindow(StubAuth(), [org], org, smoke=True)
    for key in ("inspect", "train", "cameras", "kpi", "settings", "profiles"):
        window._switch(key)  # noqa: SLF001 - self-test
    window.close()
    print("smoke ok")
    return 0


def run_selftest() -> int:
    """Exercise bundled weights + training + inference with synthetic images."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import tempfile

    import cv2
    import numpy as np

    import paths

    bundled = paths.ensure_torch_home()
    paths.ensure_model_home()
    from db import database as db

    db.init_db()
    from service.backbones import engine_display_name, resolve_default_engine
    from service.inference import PatchCoreModel

    with tempfile.TemporaryDirectory() as tmp:
        image_paths = []
        for index in range(6):
            rng = np.random.default_rng(index)
            image = rng.integers(80, 160, (320, 320, 3), dtype=np.uint8)
            path = Path(tmp) / f"selftest_{index}.png"
            cv2.imwrite(str(path), image)
            image_paths.append(str(path))
        engine, engine_kwargs = resolve_default_engine()
        model = PatchCoreModel(backbone=engine, backbone_kwargs=engine_kwargs)
        stats = model.fit(image_paths)
        prediction = model.predict(cv2.imread(image_paths[0]))

    print(
        f"selftest ok | weights={'bundled' if bundled else 'cache'} "
        f"| engine={engine_display_name(model.backbone.config()['name'])} "
        f"| bank={stats['memory_bank_size']} "
        f"| score={prediction['normalized_score']:.3f}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    early = _apply_args(argv)
    if early is not None:
        return early
    if "--smoke" in argv:
        return run_smoke()
    if "--selftest" in argv:
        return run_selftest()

    import paths

    paths.ensure_torch_home()
    paths.ensure_model_home()

    from desktop.main import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
