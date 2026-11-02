"""Build the frozen pooled bare-Q control for the four pending ALFWorld arms.

Reads the control arm of every completed run whose student is Q, pools all
scored trials per task (11-12 per task here), and writes the external-campaign
score file that the four pending e4 configs pin by sha256. The rule is fixed
before those arms' with-bank results exist: the control is the pooled bare-Q
rate, not a choice between runs.

Usage (repository root):
    python scripts/run/build_pooled_control.py

Output: configs/seed/alfworld_bare_q_pooled_20261102.json (+ printed sha256).
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from expedistill.core import ids  # noqa: E402
from expedistill.stages import paper_tables  # noqa: E402

BENCHMARK = "alfworld"
MODEL_KEY = "qwen3.6-flash-distill"
OUT = Path("configs/seed/alfworld_bare_q_pooled_20261102.json")
RUNS = (
    "runs/e1_ours_alfworld_20261030_2325",
    "runs/e2_d2q_alfworld_20261030_2350",
    "runs/e2_q2q_20261031_0621",
    "runs/e3_k1_20261101_1821",
)


def main() -> int:
    pooled = {}
    sources = []
    for run in RUNS:
        arm = paper_tables.load_arm(run)
        control = arm["control"]
        scored = [score for scores in control.values() for score in scores]
        sources.append({
            "run": run,
            "control_avg_at_n": (sum(scored) / len(scored)) if scored else None,
            "scored_trials": len(scored),
        })
        for task, scores in control.items():
            raw = ids.raw_task_id(task, BENCHMARK)
            pooled.setdefault(raw, []).extend(float(score) for score in scores)

    tasks = {}
    empty = []
    for raw, rewards in sorted(pooled.items()):
        if not rewards:
            empty.append(raw)
            continue
        tasks[f"{MODEL_KEY}:{raw}"] = {
            "n": len(rewards),
            "rewards": rewards,
            "success_rate": sum(rewards) / len(rewards),
        }
    if empty:
        raise SystemExit(f"tasks with no scored control trial in any run: "
                         f"{empty}")

    payload = {
        "description": (
            "Pooled bare Q control for ALFWorld: every scored trial of the "
            "control arm of e1_ours, e2_d2q, e2_q2q and e3_k1, pooled per "
            "task. Frozen before the four pending e4 arms ran; aligned to "
            "every archived trial, not to 3."),
        "model_key": MODEL_KEY,
        "aligned_attempts": "all",
        "sources": sources,
        "tasks": tasks,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    lengths = [entry["n"] for entry in tasks.values()]
    print(json.dumps({
        "path": str(OUT),
        "sha256": digest,
        "tasks": len(tasks),
        "min_trials": min(lengths),
        "max_trials": max(lengths),
        "pooled_avg": sum(entry["success_rate"] for entry in tasks.values())
        / len(tasks),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
