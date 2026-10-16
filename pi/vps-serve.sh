#!/bin/sh
# Runs on the VPS (copied there by `obd vps sync`): start the read-only web
# pages from the synced data folder, unless they are already running.
# Usage: sh vps-serve.sh [port]
BASE="$HOME/deepal-obd"
PORT="${1:-8080}"
PIDFILE="$BASE/serve.pid"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    exit 0
fi
cd "$BASE" || exit 1
env OBD_WEB_PASSWORD="$(cat "$BASE/web_password")" PYTHONPATH="$BASE/app" \
    nohup python3 -m deepal_s05 serve --data-dir "$BASE/data" \
    --host 0.0.0.0 --http-port "$PORT" > "$BASE/serve.log" 2>&1 &
echo $! > "$PIDFILE"
