#!/bin/bash
set -euo pipefail
export HTTPS_PROXY=http://127.0.0.1:18118 HTTP_PROXY=http://127.0.0.1:18118 NO_PROXY=127.0.0.1,localhost
export PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
TB="${EXPEDISTILL_HOME:?set EXPEDISTILL_HOME}/tb"
"$TB/venv/bin/pip" install -r "$TB/scripts/vendor/requirements.txt"
"$TB/venv/bin/pip" install "$TB/scripts/vendor/claw-harbor"
"$TB/venv/bin/harbor" --version
echo harbor-ready
