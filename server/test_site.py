"""Public site tests: landing page, admin console and client downloads."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("VISIONQC_SERVER_DATA",
                      tempfile.mkdtemp(prefix="vqc-site-test-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.site import ADMIN_DIST_DIR, DOWNLOADS_DIR, app  # noqa: E402


def test_landing_page_served_at_root():
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "VisionQC" in response.text
        assert "/admin/" in response.text


def test_admin_console_served_under_admin():
    if not (ADMIN_DIST_DIR / "index.html").is_file():
        pytest.skip("admin-web/dist not built")
    with TestClient(app) as client:
        response = client.get("/admin/")
        assert response.status_code == 200
        assert 'id="root"' in response.text


def test_download_serves_client_builds():
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOADS_DIR / "VisionQC-phone-test.apk"
    target.write_bytes(b"fake-apk")
    try:
        with TestClient(app) as client:
            response = client.get("/download/VisionQC-phone-test.apk")
            assert response.status_code == 200
            assert response.content == b"fake-apk"
    finally:
        target.unlink(missing_ok=True)
