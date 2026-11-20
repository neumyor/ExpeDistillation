#!/bin/bash
# Stop what start_vllm_replicas.sh started: the load balancer first, then every
# replica's whole process group (a killed launcher alone leaves GPU workers
# behind), and report the GPU memory that is left.
#
# Required environment: VLLM_RUN_DIR. Optional: GPU_GROUPS (to report memory).
set -euo pipefail

: "${VLLM_RUN_DIR:?set VLLM_RUN_DIR}"
GPU_GROUPS="${GPU_GROUPS:-0,1 2,3 4,5}"

stop_group() {
  local pidfile="$1" pid
  [ -f "$pidfile" ] || return 0
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 60); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL -- "-$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pidfile"
  echo "stopped $(basename "$pidfile" .pid)"
}

stop_group "$VLLM_RUN_DIR/lb.pid"
for pidfile in "$VLLM_RUN_DIR"/replica*.pid; do
  stop_group "$pidfile"
done

sleep 5
for group in $GPU_GROUPS; do
  echo "GPUs $group MiB used: $(nvidia-smi --query-gpu=memory.used \
    --format=csv,noheader,nounits -i "$group" | tr '\n' ' ')"
done
