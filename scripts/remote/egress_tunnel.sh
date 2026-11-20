#!/bin/bash
# Give an offline host (g49: no outbound access) an exit through this Mac: a CONNECT proxy on
# 127.0.0.1:$PROXY_PORT here (connect_proxy.py), published on the host's
# 127.0.0.1:$PROXY_PORT by a reverse ssh tunnel that reconnects by itself.
# On the host, jobs that need the outside use HTTPS_PROXY=http://127.0.0.1:$PROXY_PORT
# (see `egress.env` written next to this script's state directory).
#
# Usage (run on the Mac):
#   egress_tunnel.sh start     start the proxy and the tunnel loop, detached
#   egress_tunnel.sh stop      stop both
#   egress_tunnel.sh status    pidfile liveness and a real request through the host
#
# Environment: TARGET_HOST (required: user@host of the offline host),
# PROXY_PORT (default 18118),
# STATE_DIR (default ~/.expedistill_egress), ALLOW (space-separated host
# suffixes; default is connect_proxy.py's own list).
#
# Liveness is judged by pidfiles (kill -0) and by a real request, never by
# pgrep -f.
set -euo pipefail

TARGET_HOST="${TARGET_HOST:?set TARGET_HOST (user@host of the host without outbound access)}"
PROXY_PORT="${PROXY_PORT:-18118}"
STATE_DIR="${STATE_DIR:-$HOME/.expedistill_egress}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$STATE_DIR"

alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

tunnel_loop() {
  # Reconnect forever; ExitOnForwardFailure makes a refused forward (port still
  # held by a dead session on the host) fail fast, so the loop retries.
  while true; do
    ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
        -o ConnectTimeout=20 -R "127.0.0.1:$PROXY_PORT:127.0.0.1:$PROXY_PORT" \
        "$TARGET_HOST" || true
    sleep 10
  done
}

case "${1:-}" in
  start)
    if alive "$STATE_DIR/proxy.pid" || alive "$STATE_DIR/tunnel.pid"; then
      echo "already running; stop first" >&2; exit 1
    fi
    allow=()
    for suffix in ${ALLOW:-}; do allow+=(--allow "$suffix"); done
    nohup python3 "$HERE/connect_proxy.py" --listen "127.0.0.1:$PROXY_PORT" \
      ${allow[@]+"${allow[@]}"} > "$STATE_DIR/proxy.log" 2>&1 < /dev/null &
    echo $! > "$STATE_DIR/proxy.pid"
    export TARGET_HOST PROXY_PORT
    (tunnel_loop > "$STATE_DIR/tunnel.log" 2>&1 < /dev/null) &
    echo $! > "$STATE_DIR/tunnel.pid"
    disown -a 2>/dev/null || true
    sleep 4
    alive "$STATE_DIR/proxy.pid" || { echo "proxy died; see $STATE_DIR/proxy.log" >&2; exit 1; }
    echo "started: proxy pid $(cat "$STATE_DIR/proxy.pid"), tunnel loop pid $(cat "$STATE_DIR/tunnel.pid")"
    ;;
  stop)
    for name in tunnel proxy; do
      if alive "$STATE_DIR/$name.pid"; then
        pid="$(cat "$STATE_DIR/$name.pid")"
        # the tunnel loop is a subshell: stop its ssh child with it
        pkill -P "$pid" 2>/dev/null || true
        kill "$pid" 2>/dev/null || true
      fi
      rm -f "$STATE_DIR/$name.pid"
    done
    echo stopped
    ;;
  status)
    alive "$STATE_DIR/proxy.pid" && echo "proxy: alive" || echo "proxy: down"
    alive "$STATE_DIR/tunnel.pid" && echo "tunnel loop: alive" || echo "tunnel loop: down"
    ssh -o BatchMode=yes -o ConnectTimeout=15 "$TARGET_HOST" \
      "curl -s -m 15 -o /dev/null -w 'host -> proxy -> E2B api: %{http_code}\n' \
       -x http://127.0.0.1:$PROXY_PORT https://api.ap-beijing.tencentags.com/health || echo 'host -> proxy: failed'"
    ;;
  *)
    echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
