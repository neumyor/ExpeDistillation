#!/usr/bin/env bash
# Launch one experiment run detached and print its pidfile and resume command.
#
# Usage:
#   scripts/run/run_experiment.sh <config name or path> [extra CLI args...]
#
# Environment:
#   WORKERS    parallel workers (default 64)
#   RUN_DIR    explicit run directory (default runs/<config stem>_<timestamp>)
#   PYTHON     interpreter (default .venv/bin/python)
#
# Sources .env (never committed) before starting. The driver is detached with
# start_new_session (macOS has no setsid command) and the script returns at
# once; its pidfile is the only liveness source -- never use pgrep -f.
#
# Example:
#   scripts/run/run_experiment.sh configs/experiments/searchqa_paper_e1_ours.yaml

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <config name or path> [extra CLI args...]" >&2
    exit 2
fi

CONFIG="$1"
shift
if [[ ! -f "$CONFIG" ]]; then
    if [[ -f "configs/experiments/$CONFIG" ]]; then
        CONFIG="configs/experiments/$CONFIG"
    elif [[ -f "configs/experiments/$CONFIG.yaml" ]]; then
        CONFIG="configs/experiments/$CONFIG.yaml"
    else
        echo "config not found: $1" >&2
        exit 2
    fi
fi

if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
fi

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
WORKERS="${WORKERS:-64}"
STEM="$(basename "$CONFIG" .yaml)"
RUN_DIR="${RUN_DIR:-$ROOT/runs/${STEM}_$(date +%Y%m%d_%H%M)}"
mkdir -p "$RUN_DIR"

echo "run_dir: $RUN_DIR"
echo "pidfile: $RUN_DIR/driver.pid"
echo "console: $RUN_DIR/driver.console.log"
echo "resume:  $PYTHON -m expedistill.cli run --config $CONFIG --run-dir $RUN_DIR --workers $WORKERS $*"

# The launcher returns once the driver runs; a detached waiter records the
# driver's exit code in driver.pid.exit when it ends.
exec "$PYTHON" "$ROOT/scripts/smoke/launch_detached.py" \
    --pidfile "$RUN_DIR/driver.pid" \
    --log "$RUN_DIR/driver.console.log" \
    -- "$PYTHON" -m expedistill.cli run \
        --config "$CONFIG" \
        --run-dir "$RUN_DIR" \
        --workers "$WORKERS" \
        "$@"
