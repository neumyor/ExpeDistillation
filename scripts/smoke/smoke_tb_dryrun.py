"""TB smoke step 2: DRY_RUN submission against the real runner on the benchmark host.

Validates the submission contract end to end without touching any sandbox:
the exact environment the plugin would put in front of the tb21 runner
(``runner_env``, the same function ``TBPlugin._submit`` uses) plus
``DRY_RUN=1``, executed synchronously over ssh. The runner must generate
its Harbor job YAML, validate it with ``JobConfig``, print the summary
lines, and exit 0 -- no E2B sandbox, no model call, no trial directory.

Also performs the one-time remote setup the plugin expects: create the
root and its ``env.sh`` (venv + credential wiring, mirroring the campaign's
``the campaign launcher's env.sh``); the plugin's submit sources it
before every launch.

Usage:
    set -a; . ./.env; set +a
    .venv/bin/python scripts/smoke/smoke_tb_dryrun.py [--task git-leak-recovery]

Options:
    --task    TB task name for the YAML (default git-leak-recovery)
    --model   MODEL_NAME to send (default qwen3.6-flash-distill)
    --skills  also DRY_RUN TBENCH_EVAL_MODE=skills (uploads a dummy SKILL.md)

Output: stdout report; remote artifacts under $EXPEDISTILL_TB_ROOT
(configs/<run_id>.yaml, logs/<run_id>.dryrun.log).
"""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from expedistill.benchmarks.tb.plugin import runner_env  # noqa: E402
from expedistill.benchmarks.tb.remote import RemoteLink  # noqa: E402
from expedistill.infra.reliability import ConfigError  # noqa: E402

#: One-time remote setup: the plugin's submit sources <root>/env.sh before
#: launching the runner. Mirrors the campaign's env.sh for the parts the
#: bundle does not default correctly on the benchmark host: the Harbor venv lives in
#: ~/agentbrew-tb21 (not <scripts>/.venv) and OPENAI_API_KEY is derived from
#: TBENCH_LLM_KEY (the runner checks the former, secrets files export the
#: latter). TBENCH_TENCENT_ENV_FILE points at nothing so the runner does not
#: also source the bundle's secrets.env (same credentials, campaign setup).
ROOT_ENV_SH = """\
# Sourced by the expedistill TB plugin before each runner launch.
# Mirrors the campaign launcher's env.sh (the tb21 campaign launcher).
set -a
. "$HOME/agentbrew-tb21/.env"
set +a
export OPENAI_API_KEY="${OPENAI_API_KEY_OVERRIDE:-$TBENCH_LLM_KEY}"
export TBENCH_TENCENT_ENV_FILE=/nonexistent/.env
export PYTHON_BIN="$HOME/agentbrew-tb21/.venv/bin/python"
export HARBOR_BIN="$HOME/agentbrew-tb21/.venv/bin/harbor"
export PYTHONDONTWRITEBYTECODE=1
"""

RUNNER_DEFAULT = ""


def _ssh_stdin(link: RemoteLink, remote_path: str, content: str) -> None:
    """Write ``content`` to ``remote_path`` via ssh stdin (no temp files)."""
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o",
         "StrictHostKeyChecking=accept-new", "-i", link.key_path, link.host,
         "cat > " + shlex.quote(remote_path)],
        input=content, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"writing {remote_path} failed: {proc.stderr}")


def _resolve_root(link: RemoteLink) -> str:
    root = link.root
    if root.startswith("~"):
        home = link.run('printf %s "$HOME"')[0].strip()
        root = home + root[1:]
    return root.rstrip("/")


def _setup_remote(link: RemoteLink, root: str) -> None:
    out, err, rc = link.run("mkdir -p " + shlex.quote(root + "/logs"))
    if rc != 0:
        raise SystemExit(f"remote mkdir {root} failed (rc={rc}): {err}")
    existing = link.run("cat " + shlex.quote(root + "/env.sh"))[0]
    if existing.strip() == ROOT_ENV_SH.strip():
        print(f"[setup] {root}/env.sh already in place")
        return
    if existing.strip():
        print(f"[setup] {root}/env.sh exists with different content:")
        print(existing)
        raise SystemExit("refusing to overwrite a modified remote env.sh")
    _ssh_stdin(link, root + "/env.sh", ROOT_ENV_SH)
    print(f"[setup] wrote {root}/env.sh (venv + credential wiring)")


def dry_run(link: RemoteLink, root: str, runner: str, env: dict,
            label: str) -> int:
    """One synchronous DRY_RUN invocation; returns the runner's exit code."""
    exports = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items())
    log_path = f"{root}/logs/{env['RUN_ID']}.dryrun.log"
    # Source env.sh exactly as a live launch would, so the DRY_RUN resolves
    # the same PYTHON_BIN the real run will use.
    cmd = (f"cd {shlex.quote(root)} && "
           f"if [ -f {shlex.quote(root + '/env.sh')} ];"
           f" then . {shlex.quote(root + '/env.sh')}; fi; "
           f"env DRY_RUN=1 {exports} bash {shlex.quote(runner)} "
           f"2>&1 | tee {shlex.quote(log_path)}; exit ${{PIPESTATUS[0]}}")
    out, err, rc = link.run(cmd, timeout=300.0)
    print(f"--- {label} (run_id={env['RUN_ID']}, rc={rc}) ---")
    print(out.rstrip())
    if err.strip():
        print("stderr:", err.strip()[:500])
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default="git-leak-recovery")
    parser.add_argument("--model", default="qwen3.6-flash-distill")
    parser.add_argument("--skills", action="store_true",
                        help="also validate TBENCH_EVAL_MODE=skills")
    args = parser.parse_args()

    link = RemoteLink()
    runner = os.environ.get("EXPEDISTILL_TB_RUNNER", RUNNER_DEFAULT)
    if not runner:
        raise SystemExit("EXPEDISTILL_TB_RUNNER is unset (see .env.example)")
    root = _resolve_root(link)
    _setup_remote(link, root)

    failures = []
    env = runner_env(f"ed_dryrun_{args.task}_bare", f"{root}/jobs",
                     f"{root}/retry_archives", args.task, args.model,
                     "bare", None)
    if dry_run(link, root, runner, env, "bare") != 0:
        failures.append("bare")

    if args.skills:
        skill_root = f"{root}/skills/ed_dryrun_{args.task}_skills"
        link.run("mkdir -p " + shlex.quote(f"{skill_root}/{args.task}"))
        _ssh_stdin(link, f"{skill_root}/{args.task}/SKILL.md",
                   "# Dummy skill for the DRY_RUN smoke\n"
                   "- check the mirror before installing packages\n")
        env = runner_env(f"ed_dryrun_{args.task}_skills", f"{root}/jobs",
                         f"{root}/retry_archives", args.task, args.model,
                         "skills", skill_root)
        if dry_run(link, root, runner, env, "skills") != 0:
            failures.append("skills")

    if failures:
        print(f"FAILED: {', '.join(failures)} DRY_RUN did not exit 0")
        return 1
    print("DRY_RUN OK: runner generated and validated the job YAML "
          "for every requested mode")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigError as exc:
        print(f"config error: {exc}")
        raise SystemExit(2)
