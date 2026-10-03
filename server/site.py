"""VisionQC public site: landing page, web console and client downloads.

Runs separately from the relay (`server/app.py`) so site rebuilds and
restarts can never interrupt camera streams. One hostname serves everything:

    /        -> web/            (static landing page)
    /admin/  -> admin-web/dist  (built React console)
    /images/ -> images/         (screenshots used by the landing page)
    /download/<file> -> server-data/downloads (phone APK)

Run:
    uvicorn server.site:app --host 127.0.0.1 --port 8081
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("visionqc.site")

ROOT_DIR = Path(__file__).resolve().parent.parent
SITE_DIR = Path(os.environ.get("VISIONQC_SITE_DIR", ROOT_DIR / "web"))
ADMIN_DIST_DIR = Path(
    os.environ.get("VISIONQC_ADMIN_DIST", ROOT_DIR / "admin-web" / "dist")
)
IMAGES_DIR = ROOT_DIR / "images"
DOWNLOADS_DIR = Path(
    os.environ.get("VISIONQC_SERVER_DATA", "server-data")
) / "downloads"

app = FastAPI(title="VisionQC Site", version="0.1.0")


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
