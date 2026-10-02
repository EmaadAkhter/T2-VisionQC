# VisionQC Relay Server

One small service that lets phones run VisionQC cameras and lets the desktop
app watch every camera in one dashboard. Deployable anywhere Docker runs.

## Public site (single host)

The relay serves the public pages on the same hostname as the camera API:

| Path | Source | Purpose |
| --- | --- | --- |
| `/` | `web/` | Landing page |
| `/admin/` | `admin-web/dist` | Built React console |
| `/images/` | `images/` | Screenshots used by the landing page |

Build the console once with `cd admin-web && npm run build` (or let
`serve_local.sh` build it when `dist/` is missing). The public endpoint is
`https://visionqc.tavesglobal.com` — `/` is the landing page and `/admin/`
the web console, both behind the existing `visionqc` Cloudflare tunnel.

## Run locally

```bash
pip install -r server/requirements.txt
VISIONQC_DASHBOARD_TOKEN=dev-token \
  uvicorn server.app:app --host 0.0.0.0 --port 8000
```

## Run with Docker

```bash
cd server
VISIONQC_DASHBOARD_TOKEN=change-me docker compose up --build
```

The service listens on `:8000` and stores its registry and uploaded models in
the `visionqc-data` volume (`/data` inside the container).

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `VISIONQC_SERVER_DATA` | `server-data` | SQLite registry + uploaded model files |
| `VISIONQC_DASHBOARD_TOKEN` | `dev-token` | Token for dashboard REST/WebSocket access |
| `VISIONQC_RELAY_FPS` | `5` | Max frames per second relayed per camera |
| `VISIONQC_MAX_FRAME_BYTES` | `2000000` | Drop frames larger than this |

## HTTP API

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | none | Service status |
| `POST` | `/cameras/register` | dashboard token | Create a camera, returns `camera_id` + `api_key` |
| `GET` | `/cameras` | dashboard token | List cameras |
| `POST` | `/cameras/{id}/model` | dashboard token | Assign a model (or `null`) to a camera |
| `POST` | `/models` | dashboard token | Upload a trained `.pt` + metadata (multipart) |
| `GET` | `/models` | dashboard token | List uploaded models |

Dashboard token is sent as the `x-dashboard-token` header.

## WebSocket protocol

### Camera

```
WS /ws/camera/{camera_id}
-> {"key": "<api_key>"}          # first message, authenticates
<- {"type": "assigned_model", "model_version": "v..."}
-> <binary JPEG frames>          # throttled/relayed to dashboards
<- {"type": "assigned_model", ...}   # sent again when the model changes
```

### Dashboard

```
WS /ws/dashboard?token=<dashboard_token>
<- {"type": "hello", "cameras": [{id, name, model_version, online}]}
<- {"type": "frame", "camera_id": "...", "ts": 1.23, "jpeg_b64": "..."}
```

Frames are relayed at most `VISIONQC_RELAY_FPS` per camera; a dashboard that
connects late immediately receives the latest snapshot it missed.

## Android client

The Android camera app (`mobile/android/`) registers its camera through this
API, then streams frames to `/ws/camera/{camera_id}`. The desktop dashboard
(`desktop/ui/multi_camera.py`) subscribes to `/ws/dashboard`.
