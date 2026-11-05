"""Freeze one bare ALFWorld run as the control file of its student model.

Every with-bank ALFWorld arm pins its student's frozen bare file
(``BARE_CONTROL_FILES`` in build_paper_configs.py). This script reads a
finished ``e1_bare_<student>`` run's WITH-BANK arm -- the empty bank makes
that arm the bare measurement -- and writes the external-campaign score
file the other configs pin by sha256.

The bare run's own control arm is a zero-execution placeholder and is
deliberately ignored: the bare number is the with-bank arm.

Usage (repository root):
    python scripts/run/build_bare_control.py --run-dir runs/e1_bare_q_... \
        --model-key qwen3.6-flash-distill \
        --out configs/seed/alfworld_bare_q_20261102.json

Output: the score file and a JSON summary (path, sha256, tasks, avg).
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from expedistill.core import ids  # noqa: E402
from expedistill.stages import paper_tables  # noqa: E402

BENCHMARK = "alfworld"


def build(run_dir: str, model_key: str, out: Path) -> dict:
    arm = paper_tables.load_arm(run_dir)
    meta = arm["meta"]
    if not meta.get("complete"):
        raise SystemExit(f"{run_dir}: the test phase is not complete")
    unscored = (meta.get("unscored") or {}).get("with_bank") or []
    if unscored:
        raise SystemExit(
            f"{run_dir}: {len(unscored)} unscored trial(s) in the bare arm; "
            "the frozen control must be scored completely")
    scores = arm["with_bank"]
    if not scores:
        raise SystemExit(f"{run_dir}: the with-bank arm has no tasks")

    tasks = {}
    empty = []
    for task, values in scores.items():
        raw = ids.raw_task_id(task, BENCHMARK)
        rewards = [float(value) for value in values]
        if not rewards:
            empty.append(raw)
            continue
        tasks[f"{model_key}:{raw}"] = {
            "n": len(rewards),
            "rewards": rewards,
            "success_rate": sum(rewards) / len(rewards),
        }
    if empty:
        raise SystemExit(f"tasks with no scored bare trial: {empty}")
    missing = 134 - len(tasks)
    if missing:
        raise SystemExit(
            f"{run_dir}: {len(tasks)} tasks, expected 134; the bare run "
            "did not cover the full test split")

    payload = {
        "description": (
            f"Bare {model_key} on ALFWorld's test split under the unified "
            "protocol (2026-11-02): the with-bank arm of the empty-bank "
            f"run {Path(run_dir).name}, every scored trial per task. "
            "Frozen before any with-bank arm of this protocol ran; every "
            "with-bank arm of this student model pins this file."),
        "model_key": model_key,
        "aligned_attempts": "all",
        "sources": [{
            "run": str(run_dir),
            "arm": "with_bank",
            "tasks": len(tasks),
            "scored_trials": sum(entry["n"] for entry in tasks.values()),
            "avg_at_n": sum(entry["success_rate"]
                            for entry in tasks.values()) / len(tasks),
        }],
        "tasks": tasks,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    return {
        "path": str(out),
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "tasks": len(tasks),
        "scored_trials": sum(entry["n"] for entry in tasks.values()),
        "avg_at_n": sum(entry["success_rate"]
                        for entry in tasks.values()) / len(tasks),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True,
                        help="finished e1_bare_<student> run directory")
    parser.add_argument("--model-key", required=True,
                        help="the student's logical model name (the key "
                             "prefix in the score file)")
    parser.add_argument("--out", required=True,
                        help="score file to write (configs/seed/...)")
    args = parser.parse_args(argv)
    summary = build(args.run_dir, args.model_key, Path(args.out))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
