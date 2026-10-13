"""Smoke test: stage 0 + stage 1 on SearchQA with real model endpoints.

Runs the real stage0_freeze.run / stage1_baseline.run against a real
OpenAI-compatible endpoint and the real SearchQA plugin (outer
benchmark/searchqa-eval checkout via EXPEDISTILL_SEARCHQA_EVAL_DIR).

Usage (from the repo root, with .env loaded into the environment):
    set -a; . ./.env; set +a
    .venv/bin/python scripts/smoke/smoke_stage01_searchqa.py            # new run dir
    .venv/bin/python scripts/smoke/smoke_stage01_searchqa.py --run-dir runs/smoke_searchqa_real_XXXX  # resume/rerun

Cost controls (task brief, run on 2026-10-11): every handle carries
max_tokens=1024 -- today src/ defaults to 5120 (llm/call.py
ModelHandle.max_tokens) and model_options can override per model, so a fresh
smoke can simply rely on the default. The SearchQA episode budget is
EXPEDISTILL_SEARCHQA_MAX_STEPS (default 6); this run used the default.
2 tasks x 1 attempt x 2 arms = 4 episodes.

No file under src/, tests/, README or AGENTS.md is modified; the script
only writes the run directory under runs/.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from expedistill.core.config import MODEL_ROLES, ExperimentConfig
from expedistill.infra.runlock import RunLock
from expedistill.infra.store import Store
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.call import make_handle
from expedistill.llm.client import OpenAICompatClient
from expedistill.stages import stage0_freeze, stage1_baseline
from expedistill.stages.context import StageContext

#: Two items of the searchqa-eval TEST split (data/searchqa_split/test/items.json).
#: Raw task ids are 32-hex strings (md5 of the original SearchQA record).
TASK_IDS = [
    "5093b6d997674d25be29a4c94fcd5185",  # "Risky Business" movie question
    "99dc9de2fd094b8e9de41a9040081c1d",  # Descartes died in ... (Sweden)
]

STRONG_MODEL = "DEEPSEEK_up5zdj"
WEAK_MODEL = "qwen3.6-flash-distill"

MAX_TOKENS = 1024


def usage_path(run_dir: Path) -> Path:
    return run_dir / "llm_requests" / "usage.jsonl"


def count_usage_lines(run_dir: Path) -> int:
    path = usage_path(run_dir)
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run-dir", type=Path, default=None,
        help="existing run dir to rerun (resume verification); "
             "default: runs/smoke_searchqa_real_<timestamp>")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    if args.run_dir is not None:
        run_dir = Path(args.run_dir)
        if not run_dir.is_absolute():
            run_dir = repo_root / run_dir
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = repo_root / "runs" / f"smoke_searchqa_real_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"run_dir: {run_dir}")

    config = ExperimentConfig(
        benchmark="searchqa",
        mode="full",
        models={"executor_strong": STRONG_MODEL, "executor_weak": WEAK_MODEL},
        n_attempts_strong=1,
        n_attempts_weak=1,
        tasks=list(TASK_IDS),
        model_options={STRONG_MODEL: {"reasoning_effort": "max"}},
    )

    usage_before = count_usage_lines(run_dir)

    store = Store(run_dir)
    lock = RunLock(run_dir)
    warning = lock.acquire()
    try:
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        usage = UsageRecorder(usage_path(run_dir))
        client = OpenAICompatClient.from_env()

        # One handle per role (the config's role table is completed by
        # __post_init__), one shared client, max_tokens capped per the brief.
        handles = {}
        for role in MODEL_ROLES:
            model_name = config.models[role]
            handles[role] = make_handle(
                role, model_name, client,
                max_tokens=MAX_TOKENS,
                extra_body=dict(config.model_options.get(model_name, {})),
                infra_retries=3,
                infra_backoff_seconds=5.0,
            )

        ctx = StageContext(
            run_dir=run_dir,
            store=store,
            config=config,
            usage=usage,
            handles=handles,
            workdir_root=run_dir / "work",
        )
        ctx.workers = 1
        ctx.allow_infra_shortfall = False

        t0 = time.time()
        s0 = stage0_freeze.run(ctx)
        stage0_seconds = time.time() - t0
        print(f"[stage0] {stage0_seconds:.1f}s summary: {json.dumps(s0, ensure_ascii=False)}")

        t1 = time.time()
        s1 = stage1_baseline.run(
            ctx, train_tasks=s0["train"], test_tasks=s0["test"])
        stage1_seconds = time.time() - t1
        n_episodes = len(TASK_IDS) * (
            config.n_attempts_strong + config.n_attempts_weak)
        print(f"[stage1] {stage1_seconds:.1f}s "
              f"({stage1_seconds / max(n_episodes, 1):.1f}s/episode)")
        print(f"[stage1] strong: {json.dumps(s1['strong']['summary'])}")
        print(f"[stage1] weak:   {json.dumps(s1['weak']['summary'])}")
        print(f"[stage1] F: {s1['F']}  complete={s1['complete']}  "
              f"infra_missing={s1['infra_missing']}")

        totals = usage.totals()
        print(f"[usage] overall: {json.dumps(totals['overall'])}")
        for role in ("executor_strong", "executor_weak"):
            print(f"[usage] {role}: {json.dumps(totals['by_role'].get(role))}")

        usage_after = count_usage_lines(run_dir)
        print(f"[usage] usage.jsonl lines: before={usage_before} "
              f"after={usage_after} new={usage_after - usage_before}")
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
