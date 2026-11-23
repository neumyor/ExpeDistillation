#!/usr/bin/env python3
"""Run a queue of forked experiment runs on the benchmark host, at most
``--slots`` at a time.

Every job names the run it forks from (``source``), how many phases it copies
(``through``, the last copied phase label, and ``until``, that phase's 0-based
index in the sequence) and the config stem. A job starts when

* its source run has finished (``driver.pid.exit`` holding 0),
* fewer than ``--slots`` of the queue's runs are still running.

Starting a job is the checked recipe of the reference runs: ``fork-run``, a
replay of the copied prefix against an unreachable model endpoint (it must
make zero model calls), then ``start_run_bench_host.sh`` and a supervisor. A
failed step is logged as ``STOP`` for that job only; the queue goes on with
the others. A run counts as running until its driver ends with exit code 0,
or ends otherwise with no live supervisor (then it counts as failed and is
logged).

usage:
    run_queue.py --jobs jobs.json --runs-dir RUNS --code-dir CODE \
        --env-file ENV --slots 4 --workers 32 --rollout-workers 12 \
        [--dead-endpoint-env KEY=VALUE ...]

jobs.json: a list, in priority order, of
    {"name": "tb21_paper_e1_no_skills_j40", "config": "tb21_paper_e1_no_skills",
     "source": "tb21_paper_e1_ours_20261116_1612", "through": "p01", "until": 0}
Every job's config file is configs/experiments/<config>.yaml in the code dir.
Progress is printed with a timestamp; the process exits when every job is
done or failed.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def log(message):
    print(time.strftime("%F %T"), message, flush=True)


def read_exit(run_dir):
    path = run_dir / "driver.pid.exit"
    if not path.is_file():
        return None
    try:
        return int(path.read_text().strip())
    except ValueError:
        return -1


def pid_alive(path):
    try:
        pid = int(Path(path).read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def run_state(run_dir):
    """finished / failed / running / absent."""
    if not run_dir.is_dir():
        return "absent"
    code = read_exit(run_dir)
    if code is None:
        return "running"
    if code == 0:
        return "finished"
    if pid_alive(run_dir / "supervisor.pid"):
        return "running"
    return "failed"


def load_env(path):
    env = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def start(job, args, base_env):
    runs = Path(args.runs_dir)
    code = Path(args.code_dir)
    name, config = job["name"], f"configs/experiments/{job['config']}.yaml"
    run_dir = runs / name
    env = dict(base_env)
    # 1. fork the finished prefix
    done = subprocess.run(
        [str(code / ".venv/bin/expedistill"), "fork-run", "--source-run",
         str(runs / job["source"]), "--config", config, "--run-dir",
         str(run_dir), "--through", job["through"]],
        cwd=code, env=env, capture_output=True, text=True)
    (runs / f"{name}.fork.log").write_text(done.stdout + done.stderr)
    if done.returncode != 0:
        return f"fork-run failed ({done.returncode}); see {name}.fork.log"
    # 2. replay the copied prefix with no model reachable: zero calls
    dead = dict(env)
    dead["EXPEDISTILL_LLM_ENDPOINT"] = "http://127.0.0.1:9/v1"
    dead["EXPEDISTILL_LLM_ENDPOINTS"] = "qwen3.6-flash-distill=http://127.0.0.1:9/v1"
    with open(run_dir / "dry.console.log", "w") as handle:
        done = subprocess.run(
            [str(code / ".venv/bin/python"), "-m", "expedistill.cli", "run",
             "--config", config, "--run-dir", str(run_dir), "--workers", "8",
             "--until-phase", str(job["until"])],
            cwd=code, env=dead, stdout=handle, stderr=subprocess.STDOUT)
    if done.returncode != 0:
        return "prefix replay failed; see dry.console.log"
    rows = 0
    for path in (run_dir / "llm_requests").glob("*") \
            if (run_dir / "llm_requests").is_dir() else []:
        try:
            rows += sum(1 for _ in open(path, "rb"))
        except OSError:
            pass
    if rows:
        return f"prefix replay made {rows} model-call records; stopping"
    # 3. start the run and its supervisor
    start_env = dict(env)
    start_env.update({"EXPEDISTILL_ENV_FILE": args.env_file,
                      "RUNS_DIR": str(runs), "WORKERS": str(args.workers),
                      "ROLLOUT_WORKERS": str(args.rollout_workers)})
    done = subprocess.run(
        ["bash", str(code / "scripts/remote/start_run_bench_host.sh"),
         config, name, "--llm-timeout", str(args.llm_timeout)],
        cwd=code, env=start_env, capture_output=True, text=True)
    (runs / f"{name}.start.log").write_text(done.stdout + done.stderr)
    if done.returncode != 0:
        return f"start failed ({done.returncode}); see {name}.start.log"
    done = subprocess.run(
        [str(code / ".venv/bin/python"),
         str(code / "scripts/remote/supervise_run.py"), str(run_dir)],
        cwd=code, env=start_env, capture_output=True, text=True)
    if done.returncode != 0:
        return "supervisor failed to start"
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", required=True)
    parser.add_argument("--runs-dir", required=True)
    parser.add_argument("--code-dir", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--slots", type=int, required=True)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--rollout-workers", type=int, required=True)
    parser.add_argument("--llm-timeout", type=int, default=1800)
    parser.add_argument("--poll", type=int, default=60)
    args = parser.parse_args()
    jobs = json.loads(Path(args.jobs).read_text())
    runs = Path(args.runs_dir)
    base_env = dict(os.environ)
    base_env.update(load_env(args.env_file))
    state = {job["name"]: "pending" for job in jobs}
    log(f"queue of {len(jobs)} jobs, {args.slots} slots, "
        f"rollout workers {args.rollout_workers}")
    while True:
        running = 0
        for job in jobs:
            name = job["name"]
            if state[name] == "started":
                now = run_state(runs / name)
                if now == "finished":
                    state[name] = "done"
                    log(f"{name}: finished")
                elif now == "failed":
                    state[name] = "failed"
                    log(f"STOP {name}: the driver ended with an error and has "
                        "no live supervisor")
                else:
                    running += 1
        for job in jobs:
            name = job["name"]
            if state[name] != "pending":
                continue
            source_state = run_state(runs / job["source"])
            if source_state == "failed":
                state[name] = "failed"
                log(f"STOP {name}: its source {job['source']} failed")
                continue
            if source_state != "finished" or running >= args.slots:
                continue
            log(f"{name}: starting (fork {job['source']} through "
                f"{job['through']})")
            problem = start(job, args, base_env)
            if problem:
                state[name] = "failed"
                log(f"STOP {name}: {problem}")
            else:
                state[name] = "started"
                running += 1
                log(f"{name}: started")
        left = [n for n, s in state.items() if s in ("pending", "started")]
        if not left:
            log("queue finished: " + json.dumps(
                {s: sum(1 for v in state.values() if v == s)
                 for s in ("done", "failed")}))
            return 0 if all(v == "done" for v in state.values()) else 1
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
