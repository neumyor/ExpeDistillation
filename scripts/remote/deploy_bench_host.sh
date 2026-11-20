#!/usr/bin/env bash
# Deploy the code to the benchmark host, where the driver runs next to Harbor.
#
# Usage:
#   scripts/remote/deploy_bench_host.sh            # deploy the committed HEAD
#                                              # (tracked changes must be committed)
#   scripts/remote/deploy_bench_host.sh --force    # deploy the working tree as it
#                                              # is; the code directory is named
#                                              # <commit>-dirty-<timestamp>
#
# Input : .env (EXPEDISTILL_TB_HOST / EXPEDISTILL_TB_KEY of this machine) and
#         EXPEDISTILL_DEPLOY_BASE, the absolute directory on the benchmark host
#         that holds one code directory per commit. EXPEDISTILL_DEPLOY_ENV
#         names another env file to read instead of .env (one file per host,
#         kept outside the repository); EXPEDISTILL_REMOTE_PYTHON is the python
#         that creates the remote venv (default python3; the package needs
#         3.9 to 3.11); EXPEDISTILL_REMOTE_PROXY, when set, is an http proxy
#         URL the remote pip uses (a host without outbound access reaches
#         the package mirror through scripts/remote/egress_tunnel.sh).
# Output: $EXPEDISTILL_DEPLOY_BASE/<commit>/  holding src/, configs/,
#         scripts/, pyproject.toml, README.md, AGENTS.md, a venv, and
#         DEPLOYED_FROM. It prints the remote directory on the last line.
#
# A clean deploy ships exactly the committed tree: HEAD is exported with
# `git archive` into a temporary directory and rsync'd from there, so
# untracked files under src/, configs/ or scripts/ never ship. --force
# rsyncs those paths straight from the working tree, untracked files
# included.
#
# One directory per commit (per dirty deploy) and none is ever overwritten: a
# formal run stays on the checkout it was started from (AGENTS.md section 6).
# A dirty deploy records `git status` and the sha256 of `git diff HEAD` in
# DEPLOYED_FROM, so the snapshot can be matched to the commit made from it
# later. GitHub is not reachable from the benchmark host in some
# deployments, so the files are pushed with rsync. The remote secrets file
# is not touched here; see scripts/remote/README.md.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
DEPLOY_ENV="${EXPEDISTILL_DEPLOY_ENV:-.env}"
if [[ -n "${EXPEDISTILL_DEPLOY_ENV:-}" && ! -f "$DEPLOY_ENV" ]]; then
    echo "EXPEDISTILL_DEPLOY_ENV $DEPLOY_ENV does not exist" >&2; exit 2
fi
[[ -f "$DEPLOY_ENV" ]] && { set -a; . "$DEPLOY_ENV"; set +a; }
REMOTE_PYTHON="${EXPEDISTILL_REMOTE_PYTHON:-python3}"
REMOTE_PROXY="${EXPEDISTILL_REMOTE_PROXY:-}"
: "${EXPEDISTILL_TB_HOST:?EXPEDISTILL_TB_HOST is not set (.env)}"
: "${EXPEDISTILL_TB_KEY:?EXPEDISTILL_TB_KEY is not set (.env)}"
: "${EXPEDISTILL_DEPLOY_BASE:?EXPEDISTILL_DEPLOY_BASE is not set (.env)}"

FORCE=0
case "${1:-}" in
    "") ;;
    --force) FORCE=1 ;;
    *) echo "usage: $0 [--force]" >&2; exit 2 ;;
esac

SHIPPED=(src configs scripts pyproject.toml README.md AGENTS.md)
COMMIT="$(git rev-parse --short=12 HEAD)"
DIRTY_NOTE=""
if [[ "$FORCE" == 0 && -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "working tree has uncommitted changes; commit them or pass --force" >&2
    exit 2
fi
# A --force deploy ships the working tree; it is named as a commit only when
# the shipped paths hold nothing but the commit (untracked files included).
if [[ "$FORCE" == 1 && -n "$(git status --porcelain -- "${SHIPPED[@]}")" ]]; then
    COMMIT="${COMMIT}-dirty-$(date +%Y%m%d%H%M%S)"
    DIRTY_NOTE="$(git status --porcelain)
diff-sha256 $(git diff HEAD | shasum -a 256 | cut -d' ' -f1)"
fi
# ssh re-parses its arguments on the far side, so the multi-line note travels
# base64-encoded.
DIRTY_B64="$(printf '%s' "$DIRTY_NOTE" | base64 | tr -d '\n')"

REMOTE_BASE="${EXPEDISTILL_DEPLOY_BASE%/}"
REMOTE_DIR="$REMOTE_BASE/$COMMIT"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=25 -o ServerAliveInterval=10
     -o ServerAliveCountMax=3 -i "$EXPEDISTILL_TB_KEY")

SOURCE="$ROOT"
if [[ "$FORCE" == 0 ]]; then
    SOURCE="$(mktemp -d)"
    trap 'rm -rf "$SOURCE"' EXIT
    git archive --format=tar HEAD -- "${SHIPPED[@]}" | tar -x -C "$SOURCE"
fi

"${SSH[@]}" "$EXPEDISTILL_TB_HOST" "mkdir -p '$REMOTE_DIR'"
(cd "$SOURCE" && rsync -az --delete \
    -e "ssh -o BatchMode=yes -o ConnectTimeout=25 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i $EXPEDISTILL_TB_KEY" \
    --exclude='__pycache__' --exclude='*.pyc' \
    "${SHIPPED[@]}" \
    "$EXPEDISTILL_TB_HOST:$REMOTE_DIR/")

"${SSH[@]}" "$EXPEDISTILL_TB_HOST" bash -s "$REMOTE_DIR" "$COMMIT" "$DIRTY_B64" "$REMOTE_PYTHON" "$REMOTE_PROXY" <<'REMOTE'
set -euo pipefail
dir="$1"; commit="$2"; dirty_note="$(printf '%s' "${3:-}" | base64 -d)"; py="$4"; proxy="${5:-}"
if [[ -n "$proxy" ]]; then
    export HTTPS_PROXY="$proxy" HTTP_PROXY="$proxy" NO_PROXY=127.0.0.1,localhost
fi
cd "$dir"
if [[ ! -x .venv/bin/python ]]; then
    "$py" -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .
.venv/bin/python -c "import expedistill, sys; print('import ok', sys.version.split()[0])"
printf 'commit %s\ndeployed %s\n' "$commit" "$(date '+%F %T')" > DEPLOYED_FROM
[[ -z "$dirty_note" ]] || printf 'uncommitted changes at deploy:\n%s\n' "$dirty_note" >> DEPLOYED_FROM
REMOTE
echo "$REMOTE_DIR"
