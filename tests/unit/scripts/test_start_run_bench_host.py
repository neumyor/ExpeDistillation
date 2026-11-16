"""The benchmark-host start script writes <run dir>/launch.sh, and running
that file repeats the launch exactly (same config, run name, extra
arguments and run environment). Runs the real script in a stand-in code
tree whose launcher only records what it was asked to start."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "remote" / "start_run_bench_host.sh"

FAKE_LAUNCHER = textwrap.dedent("""\
    import json, os, sys
    keys = ("RUNS_DIR", "WORKERS", "ROLLOUT_WORKERS", "EXPEDISTILL_ENV_FILE",
            "FROM_SECRETS", "FROM_RUN_ENV")
    with open(os.environ["RECORD_FILE"], "a") as handle:
        handle.write(json.dumps({"argv": sys.argv[1:],
                                 "env": {k: os.environ.get(k) for k in keys}}) + "\\n")
    """)


def _code_tree(tmp_path):
    code = tmp_path / "code"
    (code / "scripts" / "remote").mkdir(parents=True)
    (code / "scripts" / "smoke").mkdir(parents=True)
    (code / "configs" / "experiments").mkdir(parents=True)
    (code / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(SCRIPT, code / "scripts" / "remote" / SCRIPT.name)
    (code / "scripts" / "smoke" / "launch_detached.py").write_text(FAKE_LAUNCHER)
    python = code / ".venv" / "bin" / "python"
    python.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    (code / "configs" / "experiments" / "demo.yaml").write_text("x: 1\n")
    secrets = tmp_path / "secrets.env"
    secrets.write_text("EXPEDISTILL_TB_HOST=local\nFROM_SECRETS=yes\n")
    secrets.chmod(0o600)
    return code, secrets


def _env(tmp_path, **extra):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
           "RECORD_FILE": str(tmp_path / "record.jsonl")}
    env.update(extra)
    return env


def _records(tmp_path):
    return [json.loads(line) for line in
            (tmp_path / "record.jsonl").read_text().splitlines()]


def test_launch_sh_repeats_the_launch_exactly(tmp_path):
    code, secrets = _code_tree(tmp_path)
    runs = tmp_path / "runs"
    extra = ["--llm-timeout", "1800", "--note", "a b'c $HOME"]
    first = subprocess.run(
        ["bash", str(code / "scripts" / "remote" / SCRIPT.name), "demo",
         "myrun", *extra],
        env=_env(tmp_path, RUNS_DIR=str(runs), WORKERS="128",
                 ROLLOUT_WORKERS="48", EXPEDISTILL_ENV_FILE=str(secrets)),
        capture_output=True, text=True, cwd=tmp_path)
    assert first.returncode == 0, first.stderr
    run_dir = (runs / "myrun").resolve()
    launch = run_dir / "launch.sh"
    assert os.access(launch, os.X_OK)
    assert f"resume:  bash {launch}" in first.stdout
    assert "FROM_SECRETS" not in launch.read_text()

    (run_dir / "run.env").write_text("FROM_RUN_ENV=1\n")
    resumed = subprocess.run(["bash", str(launch)], env=_env(tmp_path),
                             capture_output=True, text=True, cwd=tmp_path)
    assert resumed.returncode == 0, resumed.stderr

    one, two = _records(tmp_path)
    assert one["argv"] == two["argv"]
    argv = one["argv"]
    assert argv[argv.index("--config") + 1] == str(
        (code / "configs" / "experiments" / "demo.yaml").resolve())
    assert argv[argv.index("--run-dir") + 1] == str(run_dir)
    assert argv[argv.index("--workers") + 1] == "128"
    assert argv[argv.index("--rollout-workers") + 1] == "48"
    assert argv[-len(extra):] == extra
    for key in ("RUNS_DIR", "WORKERS", "ROLLOUT_WORKERS",
                "EXPEDISTILL_ENV_FILE", "FROM_SECRETS"):
        assert one["env"][key] == two["env"][key]
    assert two["env"]["FROM_RUN_ENV"] == "1"


def test_refuses_while_the_driver_is_alive(tmp_path):
    code, secrets = _code_tree(tmp_path)
    run_dir = tmp_path / "runs" / "myrun"
    run_dir.mkdir(parents=True)
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (run_dir / "driver.pid").write_text(f"{holder.pid}\n")
        done = subprocess.run(
            ["bash", str(code / "scripts" / "remote" / SCRIPT.name), "demo",
             "myrun"],
            env=_env(tmp_path, RUNS_DIR=str(tmp_path / "runs"),
                     EXPEDISTILL_ENV_FILE=str(secrets)),
            capture_output=True, text=True, cwd=tmp_path)
    finally:
        holder.kill()
        holder.wait()
    assert done.returncode == 1
    assert "still running" in done.stderr
    assert not (run_dir / "launch.sh").exists()
    assert not (tmp_path / "record.jsonl").exists()
