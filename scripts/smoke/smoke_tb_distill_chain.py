#!/usr/bin/env python3
"""Live smoke: seed a run dir with two real TB2.1 archive trials, then run
stages 0-4 for real (real models, real prompts) through the CLI.

Chain under test: stage 0 freeze -> seeded stage 1 (both units pre-stored, so
K1 must skip them with ZERO model calls) -> stage 2 G1 segmentation of both
trajectories -> stage 3 G2/G3 per selected subtask -> stage 4 K4 acceptance
(bare answer -> with-skill answer -> G5 judging -> G6 rewrite loop).

Task: adaptive-rejection-sampler (TB2.1 campaign: qwen 0/5 failed, dpsk 3/5
passed -- a genuine distillation gap). The strong arm is seeded from the
smallest successful dpsk trial, the weak arm from a valid (pytest ran) failed
qwen trial; both are rebuilt with benchmarks.tb.trajectory.reconstruct and
stored under the pipeline's own ID rules, with model_name /
skills_injected exactly what K1's stored-unit check expects.

Usage (env from .env; run from the repo root):
    .venv/bin/python scripts/smoke/smoke_tb_distill_chain.py
Output: a new runs/smoke_tb_distill_<ts>/ directory, then the CLI's stage
lines. The CLI run afterwards (same args) is the resume smoke: zero model
calls, same summaries.
"""

import datetime
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from expedistill.benchmarks.tb.trajectory import reconstruct  # noqa: E402
from expedistill.core.ids import make_task_id, make_trajectory_id  # noqa: E402
from expedistill.core.models import Step, Trajectory  # noqa: E402
from expedistill.infra.store import Store  # noqa: E402
from expedistill.stages.pipeline import run_pipeline  # noqa: E402

TASK = "adaptive-rejection-sampler"
SAMPLE_ROOT = (REPO / "tmp" / "tbval" / "sample" / "data2" / "niuyiming"
               / "e1_final_archive")
STRONG_TRIAL = SAMPLE_ROOT / "dpsk" / TASK / "04_adaptive-rejection-sampler__pmF3Ubs"
WEAK_TRIAL = SAMPLE_ROOT / "qwen" / TASK / "03_adaptive-rejection-sampler__KkpFXma"
STRONG_MODEL = "DEEPSEEK_up5zdj"
WEAK_MODEL = "qwen3.6-flash-distill"

#: A real description of the weak model, from the TB2.1 campaign evidence
#: (avg@5 0.47 vs the strong arm's 0.63; the sampled trials show unparseable
#: JSON turns, terminal hangs, AgentTimeout).
WEAK_PROFILE = """qwen3.6-flash-distill is a small, fast, distilled model. In the
TerminalBench 2.1 campaign it solved 47% of tasks per attempt on average while the
strong reference model solved 63%. Its recurring failure modes: it writes
plausible-looking but subtly wrong numerical or statistical code and declares
success without validating it against tests; on long rollouts it loses track of
the original goal and repeats earlier work; its structured responses
intermittently fail to parse, wasting turns; it sometimes launches interactive
programs that hang the terminal and it cannot recover, so trials end in
timeouts."""

CONFIG_YAML = f"""benchmark: tb
mode: full
models:
  executor_strong: {STRONG_MODEL}
  executor_weak: {WEAK_MODEL}
weak_profile: |
{"".join("  " + line + chr(10) for line in WEAK_PROFILE.splitlines())}n_attempts_strong: 1
n_attempts_weak: 1
schema_version: 3
methods:
  segmentation: {{K: 5}}
tasks: [{TASK}]
model_options:
  {STRONG_MODEL}:
    reasoning_effort: max
token_budgets:
  # Output budgets by role; same reviewed table as the campaign configs
  # (behavior-preserving migration 2026-10-12).
  default_max_output_tokens: 65536
  max_input_tokens: null
  roles:
    executor_strong: {{max_output_tokens: 65536}}
    segmenter:       {{max_output_tokens: 65536}}
    author:          {{max_output_tokens: 65536}}
    examiner:        {{max_output_tokens: 65536}}
    judge:           {{max_output_tokens: 65536}}
    rewriter:        {{max_output_tokens: 65536}}
    curator:         {{max_output_tokens: 65536}}
    auditor:         {{max_output_tokens: 65536}}
    executor_weak:   {{max_output_tokens: 5120}}
    answerer:        {{max_output_tokens: 5120}}
"""


def seed(store: Store, role: str, model: str, trial: Path) -> Trajectory:
    """Rebuild one archived trial as the stored stage-1 unit of ``role``."""
    traj_json = json.loads((trial / "agent" / "trajectory.json").read_text())
    result = json.loads((trial / "result.json").read_text())
    reward = (result.get("verifier_result") or {}).get("rewards", {}).get("reward")
    steps, skipped = reconstruct(traj_json)
    if not steps:
        raise SystemExit(f"no reconstructable steps in {trial}")
    expected_status = "success" if (reward or 0) >= 1 else "failure"
    task_id = make_task_id("tb", TASK)
    trajectory = Trajectory(
        trajectory_id=make_trajectory_id(task_id, role, 0),
        task_id=task_id,
        benchmark="tb",
        model_role=role,
        model_name=model,
        attempt_index=0,
        skills_injected=None,
        steps=[Step(**step) for step in steps],
        status=expected_status,
        meta={
            "tb_task": TASK,
            "reward": reward,
            "skipped_steps": skipped,
            "campaign_trial": str(trial),
            "campaign_source": "e1_final_archive (tb21 campaign, 2026-10-11)",
            "note": "seeded from the campaign archive; steps rebuilt via "
                    "benchmarks.tb.trajectory.reconstruct",
        })
    store.save(trajectory, "trajectories")
    print(f"seeded {trajectory.trajectory_id}: status={expected_status} "
          f"reward={reward} steps={len(steps)} skipped={skipped}")
    return trajectory


def main() -> None:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = REPO / "runs" / f"smoke_tb_distill_{stamp}"
    run_dir.mkdir(parents=True)
    config_path = run_dir / "config.yaml"
    config_path.write_text(CONFIG_YAML, encoding="utf-8")
    print(f"run dir: {run_dir}")

    # Real stage 0: load the task set, freeze the manifest.
    print("\n== stage 0 (in-process run_pipeline, until_stage=0) ==")
    summary = run_pipeline(config_path=config_path, run_dir=run_dir,
                           until_stage=0)
    print(f"stage0: {json.dumps(summary, ensure_ascii=False)}")

    # Seed both stage-1 units. This must cover the FULL expected unit set
    # (n_attempts_strong = n_attempts_weak = 1), or K1 would really launch
    # remote Harbor rollouts.
    store = Store(run_dir)
    print("\n== seeding stage-1 units from the archive ==")
    seed(store, "strong", STRONG_MODEL, STRONG_TRIAL)
    seed(store, "weak", WEAK_MODEL, WEAK_TRIAL)

    # The real chain, through the CLI (so the resume command below is the
    # documented interface). Stage 1 must find both units and skip.
    argv = [str(REPO / ".venv" / "bin" / "expedistill"), "run",
            "--config", str(config_path),
            "--run-dir", str(run_dir),
            "--until-stage", "4", "--workers", "2"]
    print("\n== stages 1-4 via the CLI ==")
    print("$ " + " ".join(argv))
    completed = subprocess.run(argv)
    if completed.returncode != 0:
        print(f"\nCLI exited {completed.returncode}; resume with the same "
              "command (the chain resumes by stored unit).")
        sys.exit(completed.returncode)
    print("\n== done ==")
    print(f"run dir: {run_dir}")
    print("resume smoke (should make zero model calls):")
    print("  " + " ".join(argv[:1] + ["run"] + argv[2:]))


if __name__ == "__main__":
    main()
