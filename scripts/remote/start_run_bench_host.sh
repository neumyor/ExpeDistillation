#!/usr/bin/env bash
# Start (or resume) one experiment run ON the benchmark host; returns at once.
#
# Usage (run on the benchmark host, from a deployed code directory):
#   scripts/remote/start_run_bench_host.sh <config name or path> [run name] [extra CLI args...]
#   bash <run dir>/launch.sh          # resume: repeats the last launch exactly
#
# Environment (all optional except RUNS_DIR):
#   RUNS_DIR  where run directories live (required)
#   WORKERS   parallel workers for steps that only call the LLM endpoint
#             (default 64; the team minimum is 32)
#   ROLLOUT_WORKERS  parallel benchmark rollouts (default: same as WORKERS)
#   EXPEDISTILL_ENV_FILE  the secrets file (default ~/.config/expedistill/expedistill.env)
#
# A run name that already exists is RESUMED; omit it to start a new run named
# <config stem>_<YYYYMMDD_HHMM>. Reads $EXPEDISTILL_ENV_FILE (secrets, mode 600,
# never in git), then $RUN_DIR/run.env if it exists: the settings that belong
# to ONE run (e.g. TBENCH_REASONING_EFFORT for a strong-model run), so every
# resume of that run starts with the same environment.
#
# Every launch writes $RUN_DIR/launch.sh: the same code directory, config,
# run name, extra CLI args and RUNS_DIR / WORKERS / ROLLOUT_WORKERS /
# EXPEDISTILL_ENV_FILE (never the secrets), so `bash $RUN_DIR/launch.sh`
# resumes with exactly the same command. scripts/remote/supervise_run.py
# re-runs it after an exit code 3.
#
# The driver runs in its own session under a detached waiter
# (scripts/smoke/launch_detached.py): $RUN_DIR/driver.pid holds the driver's
# PID, $RUN_DIR/driver.pid.exit its exit code once it ends, and its output
# goes to $RUN_DIR/driver.console.log. Liveness is the pidfile, never pgrep -f.
# Refuses to start while driver.pid names a live process.

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
CONFIG="$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")"

# The caller's choice of secrets file goes into launch.sh; its contents never do.
CALLER_ENV_FILE="${EXPEDISTILL_ENV_FILE:-}"
SECRETS="${EXPEDISTILL_ENV_FILE:-$HOME/.config/expedistill/expedistill.env}"
[[ -f "$SECRETS" ]] || { echo "missing $SECRETS (see scripts/remote/README.md)" >&2; exit 2; }
MODE="$(stat -c %a "$SECRETS" 2>/dev/null || stat -f %Lp "$SECRETS")"  # GNU || BSD stat
if [[ "$MODE" != "600" ]]; then
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
mkdir -p "$RUNS_DIR"
RUNS_DIR="$(cd "$RUNS_DIR" && pwd)"
RUN_DIR="$RUNS_DIR/$NAME"
WORKERS="${WORKERS:-64}"
ROLLOUT_WORKERS="${ROLLOUT_WORKERS:-}"
ROLLOUT=()
[[ -n "$ROLLOUT_WORKERS" ]] && ROLLOUT=(--rollout-workers "$ROLLOUT_WORKERS")
mkdir -p "$RUN_DIR"

OLD_PID=""
[[ -f "$RUN_DIR/driver.pid" ]] && OLD_PID="$(tr -d '[:space:]' < "$RUN_DIR/driver.pid")"
if [[ "$OLD_PID" =~ ^[0-9]+$ ]] && { kill -0 "$OLD_PID" 2>/dev/null || [[ -e "/proc/$OLD_PID" ]]; }; then
    echo "refusing to start: driver $OLD_PID of $RUN_DIR is still running" >&2
    exit 1
fi

[[ -f "$RUN_DIR/run.env" ]] && { set -a; . "$RUN_DIR/run.env"; set +a; }

LAUNCH="$RUN_DIR/launch.sh"
{
    printf '#!/usr/bin/env bash\n'
    printf '# Written by scripts/remote/start_run_bench_host.sh on every launch of this run.\n'
    printf '# Re-runs that launch exactly (resume); secrets come from the secrets file and run.env.\n'
    printf 'set -euo pipefail\n'
    printf 'export RUNS_DIR=%q\n' "$RUNS_DIR"
    printf 'export WORKERS=%q\n' "$WORKERS"
    if [[ -n "$ROLLOUT_WORKERS" ]]; then
        printf 'export ROLLOUT_WORKERS=%q\n' "$ROLLOUT_WORKERS"
    else
        printf 'unset ROLLOUT_WORKERS\n'
    fi
    if [[ -n "$CALLER_ENV_FILE" ]]; then
        printf 'export EXPEDISTILL_ENV_FILE=%q\n' "$CALLER_ENV_FILE"
    else
        printf 'unset EXPEDISTILL_ENV_FILE\n'
    fi
    printf 'exec bash %q %q %q' "$CODE/scripts/remote/start_run_bench_host.sh" "$CONFIG" "$NAME"
    for arg in "$@"; do printf ' %q' "$arg"; done
    printf '\n'
} > "$LAUNCH.tmp"
chmod 755 "$LAUNCH.tmp"
mv -f "$LAUNCH.tmp" "$LAUNCH"

PYTHON="$CODE/.venv/bin/python"
echo "run_dir: $RUN_DIR"
echo "pidfile: $RUN_DIR/driver.pid"
echo "console: $RUN_DIR/driver.console.log"
echo "resume:  bash $(printf '%q' "$LAUNCH")"

exec "$PYTHON" "$CODE/scripts/smoke/launch_detached.py" \
    --pidfile "$RUN_DIR/driver.pid" \
    --log "$RUN_DIR/driver.console.log" \
    -- "$PYTHON" -m expedistill.cli run \
        --config "$CONFIG" \
        --run-dir "$RUN_DIR" \
        --workers "$WORKERS" \
        ${ROLLOUT[@]+"${ROLLOUT[@]}"} \
        "$@"
