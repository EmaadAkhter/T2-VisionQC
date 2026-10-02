"""Offscreen smoke test for the desktop app.

Constructs the real windows and every page against the running local Supabase
without showing UI:

    QT_QPA_PLATFORM=offscreen python3 -m desktop.smoke_test
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from db import database as db  # noqa: E402
from desktop.auth import AuthService  # noqa: E402
from desktop.config import load_config  # noqa: E402
from desktop.theme import STYLESHEET, apply_app_font  # noqa: E402
from desktop.ui.login import LoginWindow  # noqa: E402
from desktop.ui.window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    apply_app_font(app)
    app.setStyleSheet(STYLESHEET)
    db.init_db()

    auth = AuthService(load_config())
    auth.sign_in("owner@visionqc.local", "visionqc123")
    orgs = auth.refresh_orgs()
    assert orgs, "seeded owner must belong to the demo org"
    org = orgs[0]
    print(f"signed in as {auth.user_email}; org={org.org_name} role={org.role}")

    login = LoginWindow(auth)
    print("login window constructed")

    window = MainWindow(auth, [org], org)
    for key in ("inspect", "train", "cameras", "multi_camera", "kpi",
                "settings"):
        window._switch(key)  # noqa: SLF001 - smoke test
        print(f"page constructed: {key}")
    window.close()
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
