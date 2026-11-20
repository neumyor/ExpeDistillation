#!/bin/bash
# Wait for a source run to finish, fork a follow-up run from it, replay the
# copied prefix with no model reachable (must make zero calls), then start the
# follow-up and its supervisor. Run it on the benchmark host, detached:
#
#   nohup setsid chain_fork.sh <source run name> <config stem> <through phase> \
#       <until-phase index> <workers> <rollout workers> [<new run name>] \
#       > chain.log 2>&1 < /dev/null &
#
# <through phase> is the last copied phase (p06), <until-phase index> its
# 0-based position (5). The new run name defaults to <config stem>_<date>.
# Environment: EXPERIMENT_HOME (the experiment root), CODE_DIR (deployed code
# directory). Gives up, and says why in its log, when the source ends with a
# nonzero exit code or when any step fails; it never retries.
set -u

SRC="${1:?source run name}"; STEM="${2:?config stem}"; THROUGH="${3:?through phase}"
UNTIL="${4:?until-phase index}"; WORKERS="${5:?workers}"; ROLLOUT="${6:?rollout workers}"
NAME="${7:-${STEM}_$(date +%Y%m%d)}"
: "${EXPERIMENT_HOME:?}" "${CODE_DIR:?}"
RUNS="$EXPERIMENT_HOME/runs"
log() { echo "$(date '+%F %T') [$NAME] $*"; }

log "waiting for $SRC"
while true; do
  if [ -f "$RUNS/$SRC/driver.pid.exit" ]; then
    code="$(tr -d '[:space:]' < "$RUNS/$SRC/driver.pid.exit")"
    if [ "$code" = "0" ]; then break; fi
    # a nonzero exit may be resumed by the source's supervisor; wait while it lives
    sup="$RUNS/$SRC/supervisor.pid"
    if [ -f "$sup" ] && kill -0 "$(cat "$sup")" 2>/dev/null; then sleep 120; continue; fi
    log "source exited with code $code and has no live supervisor; giving up"; exit 1
  fi
  sleep 120
done
log "source finished"

set -a; . "$EXPERIMENT_HOME/expedistill.env"; set +a
cd "$CODE_DIR" || exit 1
CONFIG="configs/experiments/$STEM.yaml"

.venv/bin/expedistill fork-run --source-run "$RUNS/$SRC" --config "$CONFIG" \
    --run-dir "$RUNS/$NAME" --through "$THROUGH" > "$RUNS/$NAME.fork.log" 2>&1 \
  || { log "fork-run failed; see $RUNS/$NAME.fork.log"; exit 1; }
log "forked through $THROUGH"

# Replay of the copied prefix with a dead endpoint: no model call may happen.
(EXPEDISTILL_LLM_ENDPOINT=http://127.0.0.1:9/v1 \
 EXPEDISTILL_LLM_ENDPOINTS=qwen3.6-flash-distill=http://127.0.0.1:9/v1 \
 .venv/bin/python -m expedistill.cli run --config "$CONFIG" --run-dir "$RUNS/$NAME" \
    --workers 32 --until-phase "$UNTIL" > "$RUNS/$NAME/dry.console.log" 2>&1) \
  || { log "prefix replay failed; see $RUNS/$NAME/dry.console.log"; exit 1; }
rows="$(cat "$RUNS/$NAME"/llm_requests/* 2>/dev/null | wc -l)"
[ "$rows" = "0" ] || { log "prefix replay made $rows model calls; stopping"; exit 1; }
log "prefix replay clean"

EXPEDISTILL_ENV_FILE="$EXPERIMENT_HOME/expedistill.env" RUNS_DIR="$RUNS" \
  WORKERS="$WORKERS" ROLLOUT_WORKERS="$ROLLOUT" \
  scripts/remote/start_run_bench_host.sh "$CONFIG" "$NAME" --llm-timeout 900 \
  || { log "start failed"; exit 1; }
.venv/bin/python scripts/remote/supervise_run.py "$RUNS/$NAME" || { log "supervisor start failed"; exit 1; }
log "started"
