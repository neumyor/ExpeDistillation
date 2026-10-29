#!/usr/bin/env bash
# Start (or resume) one experiment run ON the benchmark host, detached from any ssh session.
#
# Usage (run on the benchmark host, from a deployed code directory):
#   scripts/remote/start_run_bench_host.sh <config name or path> [run name] [extra CLI args...]
#
# Environment (all optional):
#   WORKERS   parallel workers (default 64; the team minimum is 32)
#   RUNS_DIR  where run directories live (required)
#   EXPEDISTILL_ENV_FILE  the secrets file (default ~/.config/expedistill/expedistill.env)
#
# A run name that already exists is RESUMED; omit it to start a new run named
# <config stem>_<YYYYMMDD_HHMM>. Reads $EXPEDISTILL_ENV_FILE (default
# ~/.config/expedistill/expedistill.env; secrets, mode 600, never in git). The driver is a new session with a pidfile; its
# liveness is the pidfile, never pgrep -f.
#
# Prints: run_dir, pidfile, console log, and the command that resumes it.

set -euo pipefail

CODE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$CODE"

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <config name or path> [run name] [extra CLI args...]" >&2
    exit 2
fi
CONFIG="$1"; shift
if [[ ! -f "$CONFIG" ]]; then
    for candidate in "configs/experiments/$CONFIG" "configs/experiments/$CONFIG.yaml"; do
        [[ -f "$candidate" ]] && { CONFIG="$candidate"; break; }
    done
fi
[[ -f "$CONFIG" ]] || { echo "config not found: $CONFIG" >&2; exit 2; }

SECRETS="${EXPEDISTILL_ENV_FILE:-$HOME/.config/expedistill/expedistill.env}"
[[ -f "$SECRETS" ]] || { echo "missing $SECRETS (see scripts/remote/README.md)" >&2; exit 2; }
if [[ "$(stat -c %a "$SECRETS")" != "600" ]]; then
    echo "$SECRETS must be mode 600" >&2; exit 2
fi
set -a; . "$SECRETS"; set +a
: "${EXPEDISTILL_TB_HOST:?}"
[[ "$EXPEDISTILL_TB_HOST" == "local" ]] || {
    echo "EXPEDISTILL_TB_HOST must be 'local' on the benchmark host, got '$EXPEDISTILL_TB_HOST'" >&2; exit 2; }

STEM="$(basename "$CONFIG" .yaml)"
NAME="${1:-}"
if [[ -n "$NAME" && "$NAME" != --* ]]; then shift; else NAME="${STEM}_$(date +%Y%m%d_%H%M)"; fi
RUNS_DIR="${RUNS_DIR:?RUNS_DIR is not set: the directory that holds run directories}"
RUN_DIR="$RUNS_DIR/$NAME"
WORKERS="${WORKERS:-64}"
mkdir -p "$RUN_DIR"

PYTHON="$CODE/.venv/bin/python"
echo "run_dir: $RUN_DIR"
echo "pidfile: $RUN_DIR/driver.pid"
echo "console: $RUN_DIR/driver.console.log"
echo "resume:  $0 $CONFIG $NAME"

PYTHONUNBUFFERED=1 exec "$PYTHON" "$CODE/scripts/smoke/launch_detached.py" \
    --pidfile "$RUN_DIR/driver.pid" \
    --log "$RUN_DIR/driver.console.log" \
    -- "$PYTHON" -m expedistill.cli run \
        --config "$CONFIG" \
        --run-dir "$RUN_DIR" \
        --workers "$WORKERS" \
        "$@"
