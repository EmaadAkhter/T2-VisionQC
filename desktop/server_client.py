"""Client for the VisionQC relay server.

Runs the dashboard WebSocket on a background thread and exposes Qt signals so
the multi-camera page never blocks. REST calls (camera registry, model upload
and assignment) are synchronous helpers meant to be called from a worker
thread; see ``desktop/ui/multi_camera.py``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx
from PySide6.QtCore import QObject, Signal


def _ws_url(base_url: str, path: str, token: str | None = None) -> str:
    base = base_url.rstrip("/")
    base = base.replace("https://", "wss://").replace("http://", "ws://")
    url = f"{base}{path}"
    if token:
        url += f"?token={quote(token)}"
    return url


class ServerClient(QObject):
    """Dashboard connection to the relay server."""

    connected = Signal()
    disconnected = Signal(str)
    cameras_changed = Signal(object)      # list[dict]
    frame_received = Signal(str, object)  # camera_id, jpeg bytes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.base_url = ""
        self.token = ""
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._running = False

    # ------------------------------------------------------------- lifecycle

    def configure(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        if not self.base_url or not self.token:
            raise ValueError("Server URL and token are required")
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="visionqc-server-client", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._loop is not None and self._stop_event is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    # ---------------------------------------------------------------- thread

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()
        try:
            self._loop.run_until_complete(self._dashboard_loop())
        except Exception as exc:  # noqa: BLE001 - report, never crash the app
            if self._running:
                self.disconnected.emit(str(exc))
        finally:
            try:
                self._loop.close()
            finally:
                self._loop = None
                self._stop_event = None
                self._running = False

    async def _dashboard_loop(self) -> None:
        import websockets

        url = _ws_url(self.base_url, "/ws/dashboard", self.token)
        while self._running:
            try:
                async with websockets.connect(url, max_size=8 * 1024 * 1024,
                                              open_timeout=5) as socket:
                    self.connected.emit()
                    async for message in socket:
                        if not self._running:
                            break
                        self._handle_message(message)
            except Exception as exc:  # noqa: BLE001 - retry loop
                if self._running:
                    self.disconnected.emit(str(exc))
            if not self._running:
                break
            await asyncio.sleep(2)

    def _handle_message(self, message: str) -> None:
        try:
            data = json.loads(message)
        except (TypeError, ValueError):
            return
        kind = data.get("type")
        if kind == "hello":
            self.cameras_changed.emit(data.get("cameras", []))
        elif kind == "frame":
            jpeg = base64.b64decode(data.get("jpeg_b64", ""))
            self.frame_received.emit(str(data.get("camera_id", "")), jpeg)
        elif kind == "cameras":
            self.cameras_changed.emit(data.get("cameras", []))

    # ------------------------------------------------------------------ rest

    def _headers(self) -> dict[str, str]:
        return {"x-dashboard-token": self.token}

    def list_cameras(self) -> list[dict[str, Any]]:
        response = httpx.get(f"{self.base_url}/cameras", headers=self._headers(),
                             timeout=10)
        response.raise_for_status()
        return response.json().get("cameras", [])

    def register_camera(self, name: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/cameras/register",
            json={"name": name}, headers=self._headers(), timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def start_pairing(self, name: str) -> dict[str, Any]:
        """Register a camera and get a single-use token for its QR code."""
        response = httpx.post(
            f"{self.base_url}/pairing/start",
            json={"name": name}, headers=self._headers(), timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def list_models(self) -> list[dict[str, Any]]:
        response = httpx.get(f"{self.base_url}/models", headers=self._headers(),
                             timeout=10)
        response.raise_for_status()
        return response.json().get("models", [])

    def assign_model(self, camera_id: str, version: str | None) -> None:
        response = httpx.post(
            f"{self.base_url}/cameras/{camera_id}/model",
            json={"model_version": version}, headers=self._headers(), timeout=10,
        )
        response.raise_for_status()

    def upload_model(self, model_path: str, name: str, backbone: str,
                     n_images: int = 0) -> dict[str, Any]:
        path = Path(model_path)
        with path.open("rb") as handle:
            response = httpx.post(
                f"{self.base_url}/models",
                headers=self._headers(),
                files={"file": (path.name, handle, "application/octet-stream")},
                data={
                    "name": name or path.stem,
                    "backbone": backbone,
                    "n_images": str(int(n_images)),
                },
                timeout=60,
            )
        response.raise_for_status()
        return response.json()

    def health(self) -> dict[str, Any]:
        response = httpx.get(f"{self.base_url}/health", timeout=5)
        response.raise_for_status()
        return response.json()
