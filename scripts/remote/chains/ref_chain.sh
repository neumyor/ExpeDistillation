#!/bin/bash
# Drive the ALFWorld reference run of the paper ablations in stages and start
# the arms that copy its phases the moment their prefix is complete.
#
# fork-run needs a finished, idle source, so the reference run is stopped
# twice on purpose with --until-phase and resumed:
#   stage 1  p01..p03  (baseline, learn 1, practice 1)   then fork
#                      written_once (p01-p02), written_once_nopb (p01-p02),
#                      body_only (p01-p03), trigger_only (p01-p03)
#   stage 2  p04..p06  (learn 2, practice 2, learn 3)     then fork
#                      ours_nopb (p01-p06)
#   stage 3  p07       (the test)
# Every forked arm replays its copied prefix against an unreachable endpoint
# first (zero model calls, or it stops), then starts with a supervisor.
#
# The reference run directory must already hold the imported baseline
# (expedistill import-baseline ... --config alfworld_paper_e1_ours.yaml).
#
# Environment (all required):
#   EXPERIMENT_HOME  root with expedistill.env (mode 600) and runs/
#   CODE_DIR         the deployed code directory
#   REF_NAME         run directory name of the reference run under runs/
#   SUFFIX           suffix of the arm run names (alfworld_paper_<arm>_<SUFFIX>)
# Optional: WORKERS (32), ROLLOUT_WORKERS (32).
#
#   nohup setsid ref_chain.sh > ref_chain.log 2>&1 < /dev/null &
set -u

: "${EXPERIMENT_HOME:?}" "${CODE_DIR:?}" "${REF_NAME:?}" "${SUFFIX:?}"
WORKERS="${WORKERS:-32}"; ROLLOUT_WORKERS="${ROLLOUT_WORKERS:-32}"
RUNS="$EXPERIMENT_HOME/runs"
log() { echo "$(date '+%F %T') $*"; }
die() { log "STOP: $*"; exit 1; }

start_run() {  # <run name> <config stem> [extra CLI args...]
  local name="$1" cfg="$2"; shift 2
  (cd "$CODE_DIR" && EXPEDISTILL_ENV_FILE="$EXPERIMENT_HOME/expedistill.env" RUNS_DIR="$RUNS" \
     WORKERS="$WORKERS" ROLLOUT_WORKERS="$ROLLOUT_WORKERS" \
     scripts/remote/start_run_bench_host.sh "configs/experiments/$cfg.yaml" "$name" \
       --llm-timeout 900 "$@" > "$RUNS/$name.start.log" 2>&1) || return 1
  (cd "$CODE_DIR" && .venv/bin/python scripts/remote/supervise_run.py "$RUNS/$name" \
     >> "$RUNS/$name.start.log" 2>&1) || return 1
}

wait_exit0() {  # <run name>: a driver exit code 0 (stop at --until-phase or done)
  local name="$1" code sup
  while true; do
    if [ -f "$RUNS/$name/driver.pid.exit" ]; then
      code="$(tr -d '[:space:]' < "$RUNS/$name/driver.pid.exit")"
      [ "$code" = "0" ] && return 0
      sup="$RUNS/$name/supervisor.pid"
      if [ -f "$sup" ] && kill -0 "$(cat "$sup")" 2>/dev/null; then sleep 60; continue; fi
      die "$name exited with code $code and has no live supervisor"
    fi
    sleep 60
  done
}

fork_arm() {  # <arm> <through phase> <until-phase index of it>
  local arm="$1" through="$2" until_idx="$3"
  local cfg="alfworld_paper_$arm" name="alfworld_paper_${arm}_$SUFFIX" rows
  set -a; . "$EXPERIMENT_HOME/expedistill.env"; set +a
  (cd "$CODE_DIR" && .venv/bin/expedistill fork-run --source-run "$RUNS/$REF_NAME" \
     --config "configs/experiments/$cfg.yaml" --run-dir "$RUNS/$name" --through "$through" \
     > "$RUNS/$name.fork.log" 2>&1) || die "fork-run $arm failed; see $RUNS/$name.fork.log"
  (cd "$CODE_DIR" && EXPEDISTILL_LLM_ENDPOINT=http://127.0.0.1:9/v1 \
     EXPEDISTILL_LLM_ENDPOINTS=qwen3.6-flash-distill=http://127.0.0.1:9/v1 \
     .venv/bin/python -m expedistill.cli run --config "configs/experiments/$cfg.yaml" \
       --run-dir "$RUNS/$name" --workers 32 --until-phase "$until_idx" \
       > "$RUNS/$name/dry.console.log" 2>&1) || die "prefix replay of $arm failed"
  rows="$(cat "$RUNS/$name"/llm_requests/* 2>/dev/null | wc -l)"
  [ "$rows" = "0" ] || die "prefix replay of $arm made $rows model calls"
  start_run "$name" "$cfg" || die "start of $arm failed"
  log "forked $arm through $through and started it"
}

log "stage 1: $REF_NAME through p03"
start_run "$REF_NAME" alfworld_paper_e1_ours --until-phase 2 || die "start of the reference run failed"
wait_exit0 "$REF_NAME"
log "stage 1 done"
fork_arm e2_written_once p02 1
fork_arm e8_written_once_nopb p02 1
fork_arm e3_body_only p03 2
fork_arm e4_trigger_only p03 2

log "stage 2: $REF_NAME through p06"
start_run "$REF_NAME" alfworld_paper_e1_ours --until-phase 5 || die "stage 2 start failed"
wait_exit0 "$REF_NAME"
log "stage 2 done"
fork_arm e8_ours_nopb p06 5

log "stage 3: $REF_NAME test"
start_run "$REF_NAME" alfworld_paper_e1_ours || die "stage 3 start failed"
wait_exit0 "$REF_NAME"
log "reference run finished"
