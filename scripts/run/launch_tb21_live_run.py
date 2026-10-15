"""Detached driver launcher for the TB21 split live-run experiment
(AGENTS rules: start_new_session + pidfile + console log; liveness by
pidfile, never pgrep).

Usage: launch_tb21_live_run.py <run_dir> [--until-stage N]
Launches stage 0..5 by default; stage 6 launches only after the
retrieval preflight gate (runbook section 1 / decision doc
retrieval_delivery_tb_split_20261013.md) has re-probed min_score on the
new bank. Source .env in the calling shell first -- the driver inherits
this process's environment (the CLI loads nothing itself).

Ops parameters only (workers / stagger / timeouts); every method
parameter lives in configs/experiments/tb21_split_live_run.yaml.
"""
import os
import subprocess
import sys

cmd = [
    ".venv/bin/expedistill", "run",
    "--config", "configs/experiments/tb21_split_live_run.yaml",
    "--run-dir", sys.argv[1],
    "--workers", "64", "--k1-stagger", "20",
    "--llm-timeout", "1500",
    "--llm-infra-retries", "5", "--llm-infra-backoff", "10",
]
if "--until-stage" in sys.argv:
    cmd += ["--until-stage", sys.argv[sys.argv.index("--until-stage") + 1]]

console = open(os.path.join(sys.argv[1], "console.log"), "ab")
proc = subprocess.Popen(cmd, stdout=console, stderr=console,
                        start_new_session=True)
with open(os.path.join(sys.argv[1], "driver.pids"), "w") as stream:
    stream.write(str(proc.pid) + "\n")
print("driver pid:", proc.pid)
