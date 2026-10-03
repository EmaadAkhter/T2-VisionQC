"""Relay server tests: registration, streaming, relay and assignments."""

from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault("VISIONQC_SERVER_DATA",
                      tempfile.mkdtemp(prefix="vqc-server-test-"))
os.environ.setdefault("VISIONQC_RELAY_FPS", "0")  # no throttling in tests

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from server.app import DASHBOARD_TOKEN, app  # noqa: E402

HEADERS = {"x-dashboard-token": DASHBOARD_TOKEN}


def _register(client: TestClient, name: str) -> dict:
    response = client.post("/cameras/register", json={"name": name},
                           headers=HEADERS)
    assert response.status_code == 200
    return response.json()


def test_register_and_list_cameras():
    with TestClient(app) as client:
        camera = _register(client, "Line A")
        assert camera["camera_id"].startswith("cam_")
        assert camera["api_key"]
        listing = client.get("/cameras", headers=HEADERS).json()["cameras"]
        assert any(item["id"] == camera["camera_id"] for item in listing)


def test_dashboard_requires_token():
    with TestClient(app) as client:
        assert client.get("/cameras").status_code == 401
        assert client.post("/cameras/register",
                           json={"name": "x"}).status_code == 401


def test_camera_rejects_bad_key():
    with TestClient(app) as client:
        camera = _register(client, "Locked")
        with client.websocket_connect(
            f"/ws/camera/{camera['camera_id']}"
        ) as camera_ws:
            camera_ws.send_text(json.dumps({"key": "wrong"}))
            with pytest.raises(WebSocketDisconnect):
                camera_ws.receive_json()


def test_camera_stream_relays_to_dashboard():
    with TestClient(app) as client:
        camera = _register(client, "Line B")
        with client.websocket_connect(
            f"/ws/camera/{camera['camera_id']}"
        ) as camera_ws:
            camera_ws.send_text(json.dumps({"key": camera["api_key"]}))
            assignment = camera_ws.receive_json()
            assert assignment["type"] == "assigned_model"
            assert assignment["model_version"] is None

            with client.websocket_connect(
                f"/ws/dashboard?token={DASHBOARD_TOKEN}"
            ) as dashboard:
                hello = dashboard.receive_json()
                assert hello["type"] == "hello"
                camera_ws.send_bytes(b"fake-jpeg-bytes")
                frame = dashboard.receive_json()
                assert frame["type"] == "frame"
                assert frame["camera_id"] == camera["camera_id"]
                assert frame["jpeg_b64"] == "ZmFrZS1qcGVnLWJ5dGVz"


def test_model_upload_assignment_and_camera_notification():
    with TestClient(app) as client:
        camera = _register(client, "Line C")
        response = client.post(
            "/models",
            files={"file": ("model.pt", b"weights-bytes")},
            data={"name": "Bottle line A", "backbone": "DINOv2 ViT-S/14"},
            headers=HEADERS,
        )
        assert response.status_code == 200
        version = response.json()["version"]

        with client.websocket_connect(
            f"/ws/camera/{camera['camera_id']}"
        ) as camera_ws:
            camera_ws.send_text(json.dumps({"key": camera["api_key"]}))
            assert camera_ws.receive_json()["model_version"] is None

            assigned = client.post(
                f"/cameras/{camera['camera_id']}/model",
                json={"model_version": version}, headers=HEADERS,
            )
            assert assigned.status_code == 200
            assert assigned.json()["model_version"] == version
            notification = camera_ws.receive_json()
            assert notification["model_version"] == version

        models = client.get("/models", headers=HEADERS).json()["models"]
        assert any(model["version"] == version for model in models)
        cameras = client.get("/cameras", headers=HEADERS).json()["cameras"]
        stored = next(c for c in cameras if c["id"] == camera["camera_id"])
        assert stored["model_version"] == version


def test_assigning_unknown_model_fails():
    with TestClient(app) as client:
        camera = _register(client, "Line D")
        response = client.post(
            f"/cameras/{camera['camera_id']}/model",
            json={"model_version": "v-does-not-exist"}, headers=HEADERS,
        )
        assert response.status_code == 404
