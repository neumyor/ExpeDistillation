#!/usr/bin/env bash
# Deploy the current checkout to the benchmark host, where the driver runs next to Harbor.
#
# Usage:
#   scripts/remote/deploy_bench_host.sh            # deploy HEAD (must be clean)
#   scripts/remote/deploy_bench_host.sh --force    # deploy a dirty tree; the code
#                                              # directory is named <commit>-dirty
#
# Input : .env (EXPEDISTILL_TB_HOST / EXPEDISTILL_TB_KEY of this machine) and
#         EXPEDISTILL_DEPLOY_BASE, the absolute directory on the benchmark host
#         that holds one code directory per commit.
# Output: $EXPEDISTILL_DEPLOY_BASE/<commit>/  holding src/, configs/,
#         scripts/, pyproject.toml, README.md, a venv, and DEPLOYED_FROM.
#         It prints the remote directory on the last line.
#
# One directory per commit and none is ever overwritten: a formal run stays on
# the checkout it was started from (AGENTS.md section 6). GitHub is not
# reachable from the benchmark host in some deployments, so the files are
# pushed with rsync. The remote secrets file is not touched here; see
# scripts/remote/README.md.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && { set -a; . ./.env; set +a; }
: "${EXPEDISTILL_TB_HOST:?EXPEDISTILL_TB_HOST is not set (.env)}"
: "${EXPEDISTILL_TB_KEY:?EXPEDISTILL_TB_KEY is not set (.env)}"
: "${EXPEDISTILL_DEPLOY_BASE:?EXPEDISTILL_DEPLOY_BASE is not set (.env)}"

COMMIT="$(git rev-parse --short=12 HEAD)"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    if [[ "${1:-}" != "--force" ]]; then
        echo "working tree has uncommitted changes; commit them or pass --force" >&2
        exit 2
    fi
    COMMIT="${COMMIT}-dirty"
fi

REMOTE_BASE="${EXPEDISTILL_DEPLOY_BASE%/}"
REMOTE_DIR="$REMOTE_BASE/$COMMIT"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=25 -o ServerAliveInterval=10
     -o ServerAliveCountMax=3 -i "$EXPEDISTILL_TB_KEY")

"${SSH[@]}" "$EXPEDISTILL_TB_HOST" "mkdir -p '$REMOTE_DIR'"
rsync -az --delete \
    -e "ssh -o BatchMode=yes -o ConnectTimeout=25 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i $EXPEDISTILL_TB_KEY" \
    --exclude='__pycache__' --exclude='*.pyc' \
    src configs scripts pyproject.toml README.md AGENTS.md \
    "$EXPEDISTILL_TB_HOST:$REMOTE_DIR/"

"${SSH[@]}" "$EXPEDISTILL_TB_HOST" bash -s "$REMOTE_DIR" "$COMMIT" <<'REMOTE'
set -euo pipefail
dir="$1"; commit="$2"
cd "$dir"
if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .
.venv/bin/python -c "import expedistill, sys; print('import ok', sys.version.split()[0])"
printf 'commit %s\ndeployed %s\n' "$commit" "$(date '+%F %T')" > DEPLOYED_FROM
REMOTE
echo "$REMOTE_DIR"
