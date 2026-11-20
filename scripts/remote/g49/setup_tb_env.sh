#!/bin/bash
# Build the Harbor environment for TB 2.1 on g49 (CentOS 7, glibc 2.17): a conda
# python 3.12 whose interpreter is re-pointed at a glibc 2.28 sysroot, so the
# manylinux_2_28 wheels in vendor/requirements.txt load. Same recipe as the vLLM env.
set -euo pipefail
export HTTPS_PROXY=http://127.0.0.1:18118 HTTP_PROXY=http://127.0.0.1:18118 NO_PROXY=127.0.0.1,localhost
export PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
TB="${EXPEDISTILL_HOME:?set EXPEDISTILL_HOME}/tb"
ENV="$TB/env"; CONDA="${CONDA:-$HOME/miniconda3/bin/conda}"
CONDA_OVERRIDE_GLIBC=2.28 "$CONDA" create -y -p "$ENV" \
  --override-channels -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge \
  python=3.12 sysroot_linux-64=2.28 patchelf
SYS="$ENV/x86_64-conda-linux-gnu/sysroot"
"$ENV/bin/patchelf" --set-interpreter "$SYS/lib64/ld-linux-x86-64.so.2" \
  --force-rpath --set-rpath "$SYS/lib64:$ENV/lib" "$ENV/bin/python3.12"
"$ENV/bin/python" -c "import platform; print(platform.libc_ver())"
"$ENV/bin/python" -m venv "$TB/venv"
echo env-ready
