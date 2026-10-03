#!/usr/bin/env bash
# Run the VisionQC public site, relay and Cloudflare tunnel on this Mac.
#
# Public site:  https://visionqc.tavesglobal.com/        (landing page)
#               https://visionqc.tavesglobal.com/admin/  (web console)
# Relay:        https://relay.tavesglobal.com            (API + camera WS)
# Camera WS:    wss://relay.tavesglobal.com/ws/camera/<id>
# Config:       server/.env.local (dashboard token, data dir)
# Tunnel:       ~/.cloudflared/config-visionqc.yml
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
. server/.env.local
set +a
mkdir -p "$VISIONQC_SERVER_DATA"

if [ ! -f admin-web/dist/index.html ]; then
    echo "building admin console (admin-web/dist missing)…"
    (cd admin-web && npm run build)
fi

python3 -m uvicorn server.app:app --host 127.0.0.1 --port 8000 \
    >> "$VISIONQC_SERVER_DATA/relay.log" 2>&1 &
RELAY_PID=$!

python3 -m uvicorn server.site:app --host 127.0.0.1 --port 8081 \
    >> "$VISIONQC_SERVER_DATA/site.log" 2>&1 &
SITE_PID=$!

cloudflared tunnel --config "$HOME/.cloudflared/config-visionqc.yml" run visionqc \
    >> "$VISIONQC_SERVER_DATA/tunnel.log" 2>&1 &
TUNNEL_PID=$!

trap 'kill "$RELAY_PID" "$SITE_PID" "$TUNNEL_PID" 2>/dev/null || true' EXIT INT TERM
echo "site  http://127.0.0.1:8081 (pid $SITE_PID)  -> https://visionqc.tavesglobal.com"
echo "relay http://127.0.0.1:8000 (pid $RELAY_PID) -> https://relay.tavesglobal.com"
echo "tunnel visionqc (pid $TUNNEL_PID)"
echo "  /        landing page   |  /admin/  web console"
echo "  relay:   /cameras /pairing /models /health /ws/... /download/..."
wait
