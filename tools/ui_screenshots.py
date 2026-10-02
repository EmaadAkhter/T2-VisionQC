"""Render every desktop page offscreen to PNG for review.

Usage:
    QT_QPA_PLATFORM=offscreen python3 tools/ui_screenshots.py

Writes data/screenshots/ui_<page>.png. No network calls, no visible window.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("VISIONQC_DATA_DIR", str(ROOT / "data"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from db import database as db  # noqa: E402
from desktop.auth import OrgContext  # noqa: E402
from desktop.theme import STYLESHEET, apply_app_font  # noqa: E402
from desktop.ui.login import LoginWindow, NoAccessDialog  # noqa: E402
from desktop.ui.window import MainWindow  # noqa: E402

OUT = ROOT / "data" / "screenshots"


class StubAuth:
    user_email = "owner@visionqc.local"
    config: dict = {}

    def sign_out(self) -> None:
        pass

    def is_online(self) -> bool:
        return True

    def remember_org(self, org_id: str) -> None:
        pass


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    apply_app_font(app)
    app.setStyleSheet(STYLESHEET)
    db.init_db()

    org = OrgContext(
        org_id="00000000-0000-0000-0000-000000000000",
        org_name="Demo Works",
        role="owner",
    )

    # Login window
    login = LoginWindow(StubAuth())
    login.resize(460, 520)
    login.show()
    app.processEvents()
    login.grab().save(str(OUT / "ui_login.png"))
    login.close()

    # No-access window
    no_access = NoAccessDialog(StubAuth())
    no_access.show()
    app.processEvents()
    no_access.grab().save(str(OUT / "ui_no_access.png"))
    no_access.close()

    # Main window pages
    window = MainWindow(StubAuth(), [org], org, smoke=True)
    window.resize(1280, 820)
    window.show()
    app.processEvents()
    for key in ("inspect", "train", "profiles", "cameras", "multi_camera",
                "kpi", "settings"):
        window._switch(key)  # noqa: SLF001 - screenshot tool
        app.processEvents()
        window.grab().save(str(OUT / f"ui_{key}.png"))
        print("saved", f"ui_{key}.png")
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
