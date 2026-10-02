"""Integration: desktop ServerClient against a live relay server.

Starts a real uvicorn instance on a free port, registers a camera over REST,
connects the dashboard over WebSocket, and pushes a frame from a fake camera.
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time

os.environ.setdefault("VISIONQC_SERVER_DATA",
                      tempfile.mkdtemp(prefix="vqc-client-test-"))
os.environ.setdefault("VISIONQC_RELAY_FPS", "0")

import pytest  # noqa: E402
import uvicorn  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

from server.app import DASHBOARD_TOKEN, app  # noqa: E402


@pytest.fixture(scope="module")
def server_url():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "relay server did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _wait(predicate, timeout_s: float) -> None:
    """Spin the Qt event loop until predicate() is true or time runs out."""
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(50)

    def check() -> None:
        if predicate():
            loop.quit()

    timer.timeout.connect(check)
    timer.start()
    QTimer.singleShot(int(timeout_s * 1000), loop.quit)
    loop.exec()


def test_dashboard_receives_camera_frames(server_url):
    QCoreApplication.instance() or QCoreApplication([])

    import asyncio
    import json

    import websockets

    from desktop.server_client import ServerClient

    client = ServerClient()
    client.configure(server_url, DASHBOARD_TOKEN)
    camera = client.register_camera("Test camera")
    assert camera["camera_id"].startswith("cam_")

    connected: list[bool] = []
    frames: list[tuple[str, bytes]] = []
    client.connected.connect(lambda: connected.append(True))
    client.frame_received.connect(
        lambda camera_id, jpeg: frames.append((camera_id, jpeg))
    )
    client.start()
    try:
        _wait(lambda: connected, 5)
        assert connected, "dashboard did not connect"

        async def push_frame() -> None:
            url = (server_url.replace("http://", "ws://")
                   + f"/ws/camera/{camera['camera_id']}")
            async with websockets.connect(url) as socket:
                await socket.send(json.dumps({"key": camera["api_key"]}))
                await socket.recv()  # assigned_model
                await socket.send(b"jpeg-bytes")

        asyncio.run(push_frame())
        _wait(lambda: frames, 5)
        assert frames, "dashboard received no frame"
        assert frames[0][0] == camera["camera_id"]
        assert frames[0][1] == b"jpeg-bytes"
    finally:
        client.stop()
