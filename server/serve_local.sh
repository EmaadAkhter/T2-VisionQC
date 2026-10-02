#!/usr/bin/env bash
# Run the VisionQC public site + relay + its Cloudflare tunnel on this Mac.
#
# Public site:  https://visionqc.tavesglobal.com/        (landing page)
#               https://visionqc.tavesglobal.com/admin/  (web console)
# Camera WS:    wss://visionqc.tavesglobal.com/ws/camera/<id>
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
    >> "$VISIONQC_SERVER_DATA/server.log" 2>&1 &
SERVER_PID=$!

cloudflared tunnel --config "$HOME/.cloudflared/config-visionqc.yml" run visionqc \
    >> "$VISIONQC_SERVER_DATA/tunnel.log" 2>&1 &
TUNNEL_PID=$!

trap 'kill "$SERVER_PID" "$TUNNEL_PID" 2>/dev/null || true' EXIT INT TERM
echo "relay on http://127.0.0.1:8000 (pid $SERVER_PID)"
echo "tunnel visionqc -> https://visionqc.tavesglobal.com (pid $TUNNEL_PID)"
echo "  /        landing page"
echo "  /admin/  web console"
wait
