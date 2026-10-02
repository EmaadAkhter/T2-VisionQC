"""Local edge server: receives phone frames over the LAN, runs inference.

The server binds to the factory LAN only, requires a pairing code during
setup and a device token afterwards, and keeps all image processing local.

Endpoints:
    GET  /health                     -> server + model status
    POST /pair   {code, name}        -> {token, org_id}
    POST /inspect  (JPEG body)       -> verdict JSON + heatmap PNG (base64)
    GET  /devices                    -> paired devices (for the desktop UI)

Run standalone for testing:
    python3 -m desktop.edge_server
"""

from __future__ import annotations

import base64
import io
import json
import secrets
import socket
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from db import database as db
from desktop.model_store import ModelStore, log_inspection, run_inspection

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8765


def local_ip() -> str:
    """Best-effort LAN IP of this machine."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class EdgeServer:
    """Threaded FastAPI server with pairing and token auth."""

    def __init__(self, port: int = DEFAULT_PORT, org_id: str = ""):
        self.port = port
        self.org_id = org_id
        self.pairing_code = f"{secrets.randbelow(1_000_000):06d}"
        self.tokens: dict[str, dict] = {}
        self.model_store = ModelStore()
        self._infer_lock = threading.Lock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.app = self._build_app()

    # ------------------------------------------------------------------ app

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="VisionQC Edge", docs_url=None, redoc_url=None)

        @app.get("/health")
        def health():
            version = self.model_store.active_version()
            return {
                "status": "ok",
                "model_version": version,
                "org_id": self.org_id,
                "paired_devices": len(self.tokens),
            }

        @app.post("/pair")
        async def pair(request: Request):
            body = await request.json()
            code = str(body.get("code", "")).strip()
            name = str(body.get("name", "phone")).strip()[:60] or "phone"
            if code != self.pairing_code:
                raise HTTPException(status_code=403, detail="Invalid pairing code")
            token = secrets.token_urlsafe(24)
            self.tokens[token] = {"name": name, "paired_at": time.time()}
            return {
                "token": token,
                "org_id": self.org_id,
                "edge_name": socket.gethostname(),
            }

        @app.post("/inspect")
        async def inspect(request: Request,
                          x_device_token: str = Header(default="")):
            device = self.tokens.get(x_device_token)
            if device is None:
                raise HTTPException(status_code=401, detail="Device not paired")

            body = await request.body()
            if not body:
                raise HTTPException(status_code=400, detail="Empty frame")
            image = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise HTTPException(status_code=400, detail="Undecodable frame")

            model = self.model_store.get()
            if model is None:
                raise HTTPException(status_code=409, detail="No active model")

            settings = db.get_settings()
            artifacts = self.model_store.artifacts()
            with self._infer_lock:
                result = run_inspection(image, model, settings, artifacts)

            if result.get("no_product"):
                return JSONResponse({
                    "uid": "",
                    "device": device["name"],
                    "verdict": "NO_PRODUCT",
                    "score": result["score"],
                    "certainty": result["certainty"],
                    "explanation": (
                        "No product detected in the expected region. "
                        "Adjust the unit or camera and try again."
                    ),
                    "setup_status": result["setup_status"],
                    "latency_ms": result["latency_ms"],
                    "heatmap_jpeg_b64": "",
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                })

            uid = log_inspection(result, image, model.model_version, source="edge")

            heatmap_ok, heatmap_buf = cv2.imencode(
                ".jpg", cv2.cvtColor(result["overlay"], cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 80],
            )
            heatmap_b64 = (
                base64.b64encode(heatmap_buf.tobytes()).decode("ascii")
                if heatmap_ok else ""
            )

            return JSONResponse({
                "uid": uid,
                "device": device["name"],
                "verdict": result["verdict"],
                "score": result["score"],
                "certainty": result["certainty"],
                "explanation": result["explanation"],
                "setup_status": result["setup_status"],
                "latency_ms": result["latency_ms"],
                "heatmap_jpeg_b64": heatmap_b64,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            })

        @app.get("/devices")
        def devices():
            return {
                "pairing_code": self.pairing_code,
                "devices": [
                    {"name": info["name"], "paired_at": info["paired_at"]}
                    for info in self.tokens.values()
                ],
            }

        return app

    # -------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        config = uvicorn.Config(
            self.app, host="0.0.0.0", port=self.port, log_level="warning"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        self._thread = None

    @property
    def base_url(self) -> str:
        return f"http://{local_ip()}:{self.port}"


def main() -> int:
    from desktop.auth import AuthService
    from desktop.config import load_config

    db.init_db()
    org_id = ""
    try:
        auth = AuthService(load_config())
        auth.sign_in("owner@visionqc.local", "visionqc123")
        org_id = auth.refresh_orgs()[0].org_id
    except Exception as exc:  # noqa: BLE001
        print(f"warning: no org context ({exc})")

    server = EdgeServer(org_id=org_id)
    server.start()
    print(f"Edge server on {server.base_url}")
    print(f"Pairing code: {server.pairing_code}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
