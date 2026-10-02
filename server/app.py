"""VisionQC relay server: cameras in, dashboards out.

POC scope (deployable, no cloud dependency):

- Cameras (Android app or edge clients) register, then stream JPEG frames
  over a WebSocket.
- Dashboards (the desktop app) subscribe and receive the latest frame per
  camera, throttled to a configurable relay rate.
- Models trained on a desktop are uploaded and assigned per camera; camera
  clients are told which model to use.

State lives in a small SQLite file next to the data directory; uploaded
model files are stored on disk. Everything runs in one process, which is
plenty for a pilot line with a handful of cameras.

Run:
    uvicorn server.app:app --host 0.0.0.0 --port 8000

Environment:
    VISIONQC_SERVER_DATA          data directory (default ./server-data)
    VISIONQC_DASHBOARD_TOKEN      dashboard API token (default dev-token)
    VISIONQC_RELAY_FPS            max frames/s relayed per camera (default 5)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("visionqc.relay")

DATA_DIR = Path(os.environ.get("VISIONQC_SERVER_DATA", "server-data"))
MODELS_DIR = DATA_DIR / "models"
DOWNLOADS_DIR = DATA_DIR / "downloads"
DB_PATH = DATA_DIR / "server.db"
DASHBOARD_TOKEN = os.environ.get("VISIONQC_DASHBOARD_TOKEN", "dev-token")
RELAY_FPS = float(os.environ.get("VISIONQC_RELAY_FPS", "5"))
MAX_FRAME_BYTES = int(os.environ.get("VISIONQC_MAX_FRAME_BYTES", 2_000_000))

# Public site served on the same hostname: / is the landing page, /admin the
# built web console (admin-web/dist).
ROOT_DIR = Path(__file__).resolve().parent.parent
SITE_DIR = Path(os.environ.get("VISIONQC_SITE_DIR", ROOT_DIR / "web"))
ADMIN_DIST_DIR = Path(
    os.environ.get("VISIONQC_ADMIN_DIST", ROOT_DIR / "admin-web" / "dist")
)
IMAGES_DIR = ROOT_DIR / "images"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cameras (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                api_key TEXT NOT NULL,
                model_version TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS models (
                version TEXT PRIMARY KEY,
                name TEXT,
                backbone TEXT,
                path TEXT NOT NULL,
                n_images INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pairing_tokens (
                token TEXT PRIMARY KEY,
                camera_id TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def register_camera(name: str) -> dict[str, Any]:
    camera_id = f"cam_{secrets.token_hex(4)}"
    api_key = secrets.token_urlsafe(24)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO cameras (id, name, api_key, created_at) VALUES (?, ?, ?, ?)",
            (camera_id, name[:80] or "camera", api_key, _now()),
        )
    return {"camera_id": camera_id, "api_key": api_key, "name": name}


PAIRING_TTL_S = float(os.environ.get("VISIONQC_PAIRING_TTL", "600"))


def create_pairing(camera_id: str, ttl_s: float | None = None) -> dict[str, Any]:
    """Mint a short-lived, single-use pairing token for a camera."""
    token = f"pair_{secrets.token_urlsafe(18)}"
    expires_at = time.time() + (PAIRING_TTL_S if ttl_s is None else ttl_s)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO pairing_tokens (token, camera_id, expires_at, created_at) "
            "VALUES (?, ?, ?, ?)",
            (token, camera_id, expires_at, _now()),
        )
    return {"token": token, "expires_at": expires_at}


def claim_pairing(token: str) -> Optional[dict[str, Any]]:
    """Consume a pairing token and return the camera, or None if unusable."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM pairing_tokens WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM pairing_tokens WHERE token = ?", (token,))
        if row["expires_at"] < time.time():
            return None
        camera = conn.execute(
            "SELECT * FROM cameras WHERE id = ?", (row["camera_id"],)
        ).fetchone()
        return dict(camera) if camera else None


def get_camera(camera_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        return dict(row) if row else None


def list_cameras() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM cameras ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]


def set_camera_model(camera_id: str, model_version: Optional[str]) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE cameras SET model_version = ? WHERE id = ?",
            (model_version, camera_id),
        )
        return cursor.rowcount > 0


def save_model(version: str, name: str, backbone: str,
               path: str, n_images: int = 0) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO models "
            "(version, name, backbone, path, n_images, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (version, name, backbone, path, n_images, _now()),
        )


def list_models() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM models ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]


def get_model(version: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM models WHERE version = ?", (version,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Realtime hub
# ---------------------------------------------------------------------------

class Hub:
    """Connected cameras, dashboards and the latest frame per camera."""

    def __init__(self) -> None:
        self.camera_sockets: dict[str, WebSocket] = {}
        self.dashboards: set[WebSocket] = set()
        self.latest: dict[str, dict[str, Any]] = {}
        self._last_relay: dict[str, float] = {}

    # -- camera side --------------------------------------------------------

    async def connect_camera(self, camera_id: str, socket: WebSocket) -> None:
        self.camera_sockets[camera_id] = socket
        camera = get_camera(camera_id) or {}
        await socket.send_json({
            "type": "assigned_model",
            "model_version": camera.get("model_version"),
        })

    def disconnect_camera(self, camera_id: str) -> None:
        self.camera_sockets.pop(camera_id, None)

    async def relay_frame(self, camera_id: str, jpeg: bytes) -> None:
        if not jpeg or len(jpeg) > MAX_FRAME_BYTES:
            return
        now = time.monotonic()
        min_interval = 1.0 / RELAY_FPS if RELAY_FPS > 0 else 0.0
        if now - self._last_relay.get(camera_id, 0.0) < min_interval:
            return
        self._last_relay[camera_id] = now
        frame = base64.b64encode(jpeg).decode("ascii")
        self.latest[camera_id] = {"jpeg_b64": frame, "ts": time.time()}
        await self.broadcast({
            "type": "frame",
            "camera_id": camera_id,
            "ts": self.latest[camera_id]["ts"],
            "jpeg_b64": frame,
        })

    # -- dashboard side -----------------------------------------------------

    async def connect_dashboard(self, socket: WebSocket) -> None:
        self.dashboards.add(socket)
        cameras = [
            {
                "id": camera["id"],
                "name": camera["name"],
                "model_version": camera["model_version"],
                "online": camera["id"] in self.camera_sockets,
            }
            for camera in list_cameras()
        ]
        await socket.send_json({"type": "hello", "cameras": cameras})
        for camera_id, snapshot in self.latest.items():
            await socket.send_json({
                "type": "frame",
                "camera_id": camera_id,
                "ts": snapshot["ts"],
                "jpeg_b64": snapshot["jpeg_b64"],
            })

    def disconnect_dashboard(self, socket: WebSocket) -> None:
        self.dashboards.discard(socket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for socket in self.dashboards:
            try:
                await socket.send_json(payload)
            except Exception:  # noqa: BLE001 - a broken dashboard must not stall cameras
                dead.append(socket)
        for socket in dead:
            self.dashboards.discard(socket)

    async def notify_assignment(self, camera_id: str) -> None:
        socket = self.camera_sockets.get(camera_id)
        if socket is None:
            return
        camera = get_camera(camera_id) or {}
        try:
            await socket.send_json({
                "type": "assigned_model",
                "model_version": camera.get("model_version"),
            })
        except Exception:  # noqa: BLE001
            pass


hub = Hub()


def _require_dashboard(token: Optional[str]) -> None:
    if token != DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid dashboard token")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="VisionQC Relay", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/download/{filename}")
async def download(filename: str) -> FileResponse:
    """Serve client builds; no-cache so a new APK is never stale at the edge."""
    target = DOWNLOADS_DIR / Path(filename).name
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        target,
        media_type="application/vnd.android.package-archive",
        filename=target.name,
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "cameras": len(list_cameras()),
        "online_cameras": list(hub.camera_sockets),
        "dashboards": len(hub.dashboards),
        "models": len(list_models()),
    }


# -- cameras ----------------------------------------------------------------

@app.post("/cameras/register")
async def cameras_register(body: dict[str, Any],
                           x_dashboard_token: str = Header(default="")) -> dict[str, Any]:
    _require_dashboard(x_dashboard_token)
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Camera name required")
    return register_camera(name)


@app.get("/cameras")
def cameras_list(x_dashboard_token: str = Header(default="")) -> dict[str, Any]:
    _require_dashboard(x_dashboard_token)
    return {"cameras": list_cameras()}


# -- phone pairing ----------------------------------------------------------

@app.post("/pairing/start")
async def pairing_start(body: dict[str, Any],
                        x_dashboard_token: str = Header(default="")) -> dict[str, Any]:
    """Register a camera and mint a single-use pairing token for its QR code.

    The QR only ever carries this token, never the camera API key.
    """
    _require_dashboard(x_dashboard_token)
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Camera name required")
    camera = register_camera(name)
    pairing = create_pairing(camera["camera_id"])
    return {
        "camera_id": camera["camera_id"],
        "name": camera["name"],
        "token": pairing["token"],
        "expires_in": max(0, int(pairing["expires_at"] - time.time())),
    }


@app.post("/pairing/claim")
async def pairing_claim(body: dict[str, Any]) -> dict[str, Any]:
    """Exchange a pairing token for the camera credentials (single use)."""
    token = str(body.get("token", "")).strip()
    if not token:
        raise HTTPException(status_code=400, detail="Pairing token required")
    camera = claim_pairing(token)
    if camera is None:
        raise HTTPException(status_code=404,
                            detail="Invalid or expired pairing code")
    return {
        "camera_id": camera["id"],
        "api_key": camera["api_key"],
        "name": camera["name"],
    }


@app.post("/cameras/{camera_id}/model")
async def cameras_set_model(camera_id: str, body: dict[str, Any],
                            x_dashboard_token: str = Header(default="")) -> dict[str, Any]:
    _require_dashboard(x_dashboard_token)
    version = body.get("model_version") or None
    if version is not None and get_model(version) is None:
        raise HTTPException(status_code=404, detail="Unknown model version")
    if not set_camera_model(camera_id, version):
        raise HTTPException(status_code=404, detail="Unknown camera")
    await hub.notify_assignment(camera_id)
    return {"camera_id": camera_id, "model_version": version}


# -- models -----------------------------------------------------------------

@app.post("/models")
async def models_upload(
    file: UploadFile = File(...),
    name: str = Form(default=""),
    backbone: str = Form(default=""),
    n_images: int = Form(default=0),
    x_dashboard_token: str = Header(default=""),
) -> dict[str, Any]:
    _require_dashboard(x_dashboard_token)
    version = f"v{time.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    target = MODELS_DIR / f"{version}.pt"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty model file")
    target.write_bytes(content)
    save_model(version, name or version, backbone, str(target), n_images)
    return {"version": version, "name": name or version, "backbone": backbone}


@app.get("/models")
def models_list(x_dashboard_token: str = Header(default="")) -> dict[str, Any]:
    _require_dashboard(x_dashboard_token)
    return {"models": list_models()}


# -- websockets -------------------------------------------------------------

@app.websocket("/ws/camera/{camera_id}")
async def ws_camera(socket: WebSocket, camera_id: str) -> None:
    await socket.accept()
    camera = get_camera(camera_id)
    if camera is None:
        await socket.close(code=4404)
        return
    # First message must authenticate the camera.
    try:
        hello = json.loads(await socket.receive_text())
    except Exception:  # noqa: BLE001
        await socket.close(code=4400)
        return
    if hello.get("key") != camera["api_key"]:
        await socket.close(code=4403)
        return

    await hub.connect_camera(camera_id, socket)
    try:
        while True:
            message = await socket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data:
                await hub.relay_frame(camera_id, data)
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect_camera(camera_id)


@app.websocket("/ws/dashboard")
async def ws_dashboard(socket: WebSocket) -> None:
    if socket.query_params.get("token") != DASHBOARD_TOKEN:
        await socket.close(code=4403)
        return
    await socket.accept()
    await hub.connect_dashboard(socket)
    try:
        while True:
            # Dashboards are subscribers; ignore any inbound traffic but keep
            # the connection open.
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect_dashboard(socket)


# ---------------------------------------------------------------------------
# Public site (single hostname)
# ---------------------------------------------------------------------------
# Mounted after every API/WebSocket route, so the relay keeps priority and the
# static mounts only catch what the API does not claim:
#   /        -> web/            (landing page)
#   /admin   -> admin-web/dist  (built React console)
#   /images  -> images/         (screenshots used by the landing page)

if IMAGES_DIR.is_dir():
    app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

if ADMIN_DIST_DIR.is_dir():
    app.mount("/admin", StaticFiles(directory=ADMIN_DIST_DIR, html=True),
              name="admin")
else:
    logger.warning(
        "admin console not built: %s (run 'cd admin-web && npm run build')",
        ADMIN_DIST_DIR,
    )

if SITE_DIR.is_dir():
    app.mount("/", StaticFiles(directory=SITE_DIR, html=True), name="site")
else:
    logger.warning("landing page directory missing: %s", SITE_DIR)
