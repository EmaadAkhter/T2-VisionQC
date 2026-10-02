"""Pairing endpoints: mint a QR token, claim it once, reject the rest."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("VISIONQC_SERVER_DATA",
                      tempfile.mkdtemp(prefix="vqc-pair-test-"))

from fastapi.testclient import TestClient  # noqa: E402

from server.app import (  # noqa: E402
    DASHBOARD_TOKEN,
    app,
    create_pairing,
    init_db,
    register_camera,
)

init_db()
client = TestClient(app)


def _start(name: str) -> dict:
    response = client.post(
        "/pairing/start",
        json={"name": name},
        headers={"x-dashboard-token": DASHBOARD_TOKEN},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_pairing_round_trip_and_single_use():
    started = _start("Pairing phone")
    assert started["token"].startswith("pair_")
    assert started["expires_in"] > 0

    claimed = client.post("/pairing/claim", json={"token": started["token"]})
    assert claimed.status_code == 200, claimed.text
    credentials = claimed.json()
    assert credentials["camera_id"] == started["camera_id"]
    assert credentials["api_key"]
    assert credentials["name"] == "Pairing phone"

    # The same code cannot be claimed twice.
    again = client.post("/pairing/claim", json={"token": started["token"]})
    assert again.status_code == 404


def test_pairing_start_requires_dashboard_token():
    response = client.post("/pairing/start", json={"name": "Nope"})
    assert response.status_code == 401


def test_pairing_start_requires_name():
    response = client.post("/pairing/start", json={},
                           headers={"x-dashboard-token": DASHBOARD_TOKEN})
    assert response.status_code == 400


def test_expired_pairing_code_is_rejected():
    camera = register_camera("Expired phone")
    pairing = create_pairing(camera["camera_id"], ttl_s=-5)
    response = client.post("/pairing/claim", json={"token": pairing["token"]})
    assert response.status_code == 404


def test_unknown_pairing_code_is_rejected():
    response = client.post("/pairing/claim", json={"token": "pair_not-real"})
    assert response.status_code == 404
