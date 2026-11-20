#!/bin/bash
# Start several vLLM replicas of one model, one tensor-parallel group of GPUs
# per replica, and put vllm_lb.py in front of them as ONE OpenAI-compatible
# endpoint. Replicas listen on 127.0.0.1 only; the load balancer is the only
# listener that may be reachable from other machines.
#
# Required environment:
#   VLLM_ENV         conda env (or venv) that has the `vllm` command
#   VLLM_MODEL_DIR   the checkpoint directory
#   VLLM_RUN_DIR     where logs and pidfiles go (created)
# Optional:
#   GPU_GROUPS       space-separated groups, e.g. "0,1 2,3 4,5" (default)
#   SERVED_NAME      model name on the wire (default qwen3.6-35b-a3b)
#   REPLICA_BASE_PORT  first replica port (default 8110)
#   LB_LISTEN        host:port of the endpoint (default 127.0.0.1:8010)
#   MAX_MODEL_LEN, MAX_NUM_SEQS, GPU_UTIL   (262144, 64, 0.90)
#   WAIT_SECONDS     how long to wait for every replica (default 2400)
#   VLLM_API_KEY     when set, every replica requires it as a bearer key
#   CUDA_HOME        default /usr/local/cuda
#
# Usage: start_vllm_replicas.sh      (stop with stop_vllm_replicas.sh)
set -euo pipefail

: "${VLLM_ENV:?set VLLM_ENV}" "${VLLM_MODEL_DIR:?set VLLM_MODEL_DIR}"
: "${VLLM_RUN_DIR:?set VLLM_RUN_DIR}"
GPU_GROUPS="${GPU_GROUPS:-0,1 2,3 4,5}"
SERVED_NAME="${SERVED_NAME:-qwen3.6-35b-a3b}"
REPLICA_BASE_PORT="${REPLICA_BASE_PORT:-8110}"
LB_LISTEN="${LB_LISTEN:-127.0.0.1:8010}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-262144}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-64}"
GPU_UTIL="${GPU_UTIL:-0.90}"
WAIT_SECONDS="${WAIT_SECONDS:-2400}"
CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
HERE="$(cd "$(dirname "$0")" && pwd)"
LB_PORT="${LB_LISTEN##*:}"

mkdir -p "$VLLM_RUN_DIR"

alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

for pidfile in "$VLLM_RUN_DIR"/replica*.pid "$VLLM_RUN_DIR"/lb.pid; do
  if alive "$pidfile"; then
    echo "refusing to start: $pidfile points at a live process" >&2
    exit 1
  fi
done

# Every target GPU must be empty: a replica that cannot get its memory fails
# late and leaves orphans behind.
for group in $GPU_GROUPS; do
  used="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits \
          -i "$group")"
  for mib in $used; do
    if [ "$mib" -gt 2000 ]; then
      echo "refusing to start: a GPU of group $group holds $mib MiB" >&2
      exit 1
    fi
  done
done

export CUDA_HOME PATH="$VLLM_ENV/bin:$CUDA_HOME/bin:$PATH"
export VLLM_LOGGING_LEVEL=INFO VLLM_USE_FLASHINFER_SAMPLER=0

backends=()
index=0
for group in $GPU_GROUPS; do
  port=$((REPLICA_BASE_PORT + index))
  tp="$(echo "$group" | tr ',' '\n' | wc -l | tr -d ' ')"
  CUDA_VISIBLE_DEVICES="$group" nohup setsid "$VLLM_ENV/bin/vllm" serve \
    "$VLLM_MODEL_DIR" \
    --served-model-name "$SERVED_NAME" \
    --tensor-parallel-size "$tp" \
    --max-model-len "$MAX_MODEL_LEN" \
    --gpu-memory-utilization "$GPU_UTIL" \
    --max-num-seqs "$MAX_NUM_SEQS" \
    --reasoning-parser qwen3 \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    --host 127.0.0.1 --port "$port" \
    > "$VLLM_RUN_DIR/replica$index.log" 2>&1 < /dev/null &
  echo $! > "$VLLM_RUN_DIR/replica$index.pid"
  backends+=(--backend "http://127.0.0.1:$port")
  echo "replica $index: GPUs $group, port $port, pid $(cat "$VLLM_RUN_DIR/replica$index.pid")"
  index=$((index + 1))
done

# Wait until every replica answers /health; give up early when one dies.
deadline=$((SECONDS + WAIT_SECONDS))
for ((i = 0; i < index; i++)); do
  port=$((REPLICA_BASE_PORT + i))
  until curl -sf -o /dev/null -m 5 "http://127.0.0.1:$port/health"; do
    if ! alive "$VLLM_RUN_DIR/replica$i.pid"; then
      echo "replica $i died; see $VLLM_RUN_DIR/replica$i.log" >&2
      exit 1
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "replica $i not healthy after ${WAIT_SECONDS}s" >&2
      exit 1
    fi
    sleep 10
  done
  echo "replica $i healthy"
done

nohup setsid "$VLLM_ENV/bin/python" "$HERE/vllm_lb.py" --listen "$LB_LISTEN" \
  "${backends[@]}" > "$VLLM_RUN_DIR/lb.log" 2>&1 < /dev/null &
echo $! > "$VLLM_RUN_DIR/lb.pid"
sleep 3
alive "$VLLM_RUN_DIR/lb.pid" || { echo "load balancer died" >&2; exit 1; }

# A real generation, not just /health: the endpoint must return content.
auth=()
[ -n "${VLLM_API_KEY:-}" ] && auth=(-H "Authorization: Bearer $VLLM_API_KEY")
reply="$(curl -sf -m 120 "http://127.0.0.1:$LB_PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' "${auth[@]}" \
  -d "{\"model\":\"$SERVED_NAME\",\"messages\":[{\"role\":\"user\",\"content\":\"What is 6*21? Answer with the number only.\"}],\"max_tokens\":16,\"temperature\":0,\"chat_template_kwargs\":{\"enable_thinking\":false}}")"
echo "$reply" | grep -q '126' || { echo "generation check failed: $reply" >&2; exit 1; }
echo "endpoint $LB_LISTEN ready: $index replicas, model $SERVED_NAME"
