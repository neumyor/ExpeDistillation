# Sourced by the expedistill TB plugin before each runner launch (g49).
# Credentials come from the bundle's secrets.env; the runner checks OPENAI_API_KEY,
# which secrets.env carries as an alias of TBENCH_LLM_KEY.
# Needs EXPEDISTILL_HOME in the driver's environment (expedistill.env).
set -a
. "$EXPEDISTILL_HOME/tb/scripts/secrets.env"
set +a
export TBENCH_TENCENT_ENV_FILE=/nonexistent/.env
export TBENCH_VENV_DIR="$EXPEDISTILL_HOME/tb/venv"
export PYTHON_BIN="$TBENCH_VENV_DIR/bin/python"
export HARBOR_BIN="$TBENCH_VENV_DIR/bin/harbor"
export PYTHONDONTWRITEBYTECODE=1
