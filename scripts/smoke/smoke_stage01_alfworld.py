"""Smoke driver: real LLM endpoint + real ALFWorld plugin, stage 0 + stage 1.

Runs the real wiring (load_config -> freeze -> K1 dual arms) against the
outer benchmark/alfworld-eval stack with a tiny 2-task set, then audits the
run directory and prints a JSON report. Re-run with ``--run-dir <same>`` to
verify resume: stage 0 re-freezes (manifest compare) and stage 1 must make
ZERO model requests and leave results unchanged.

Cost controls (this smoke only; run on 2026-10-11 before src/ gained the
5120 default in llm/call.py ModelHandle.max_tokens, overridable per model via
model_options -- a fresh smoke can rely on the default instead):
  * every model handle carries max_tokens=1024;
  * EXPEDISTILL_ALFWORLD_MAX_STEPS=5 caps episodes at 5 actions.

Usage:
    set -a; . ./.env; set +a
    .venv/bin/python scripts/smoke/smoke_stage01_alfworld.py            # new run
    .venv/bin/python scripts/smoke/smoke_stage01_alfworld.py --run-dir \
        runs/smoke_alfworld_real_<ts>                                   # resume

The five ALFWorld variables default to the outer workspace layout if unset
(outer README.md "Official ALFWorld environment"); nothing is written
outside the run directory.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from expedistill.core.config import load_config  # noqa: E402
from expedistill.infra.runlock import RunLock  # noqa: E402
from expedistill.infra.store import Store, read_json, read_jsonl  # noqa: E402
from expedistill.infra.usage import UsageRecorder  # noqa: E402
from expedistill.llm.call import make_handle  # noqa: E402
from expedistill.llm.client import OpenAICompatClient  # noqa: E402
from expedistill.stages import stage0_freeze, stage1_baseline  # noqa: E402
from expedistill.stages.context import StageContext  # noqa: E402

OUTER = REPO.parent.parent.parent  # SkillEvolvePaperList
EVAL_DIR = OUTER / "benchmark" / "alfworld-eval"

# Defaults from the outer workspace (outer README.md:144 and BUILD.md:96);
# every one is overridable through the environment.
ENV_DEFAULTS = {
    "EXPEDISTILL_ALFWORLD_EVAL_DIR": str(EVAL_DIR),
    "EXPEDISTILL_ALFWORLD_VENV": str(EVAL_DIR / ".venv" / "bin" / "python"),
    "ALFWORLD_DATA": str(EVAL_DIR / ".data" / "alfworld"),
    "ALFWORLD_CONFIG": str(EVAL_DIR / "configs" / "textworld.yaml"),
    "ALFWORLD_BENCH_SRC": str(EVAL_DIR / "src"),
    "EXPEDISTILL_ALFWORLD_MAX_STEPS": "5",
}

TASKS = [
    "json_2.1.1/valid_seen/pick_and_place_simple-ToiletPaper-None-"
    "ToiletPaperHanger-406/trial_T20190908_122858_883968/game.tw-pddl",
    "json_2.1.1/valid_seen/pick_and_place_simple-SprayBottle-None-"
    "GarbageCan-409/trial_T20190908_054742_818364/game.tw-pddl",
]

STRONG = "DEEPSEEK_up5zdj"
WEAK = "qwen3.6-flash-distill"
MAX_TOKENS = 1024


def config_yaml() -> str:
    return f"""\
benchmark: alfworld
mode: full
schema_version: 3
models:
  executor_strong: {STRONG}
  executor_weak: {WEAK}
n_attempts_strong: 1
n_attempts_weak: 1
model_options:
  {STRONG}:
    reasoning_effort: max
tasks:
{chr(10).join('  - ' + repr(t) for t in TASKS)}
"""


def adapter_pids() -> list:
    """Live 'expedistill-alfworld-adapter' processes, via ps (never pgrep -f)."""
    out = subprocess.run(
        ["ps", "-axo", "pid,command"], capture_output=True, text=True,
        check=True).stdout
    return [line for line in out.splitlines()
            if "expedistill-alfworld-adapter" in line]


def leak_scan(run_dir: Path) -> dict:
    """Check no file under run_dir contains the API key or endpoint creds."""
    secret = os.environ.get("EXPEDISTILL_LLM_API_KEY", "").strip()
    hits = []
    if secret:
        for path in run_dir.rglob("*"):
            if path.is_file() and path.suffix in (".json", ".jsonl", ".yaml",
                                                  ".yml", ".md", ".txt", ""):
                try:
                    if secret in path.read_text(encoding="utf-8",
                                                errors="ignore"):
                        hits.append(str(path.relative_to(run_dir)))
                except OSError:
                    pass
    return {"api_key_leaked": hits}


def tree_hash(run_dir: Path) -> dict:
    """sha256 per file (relative path -> hash), for resume equality checks."""
    out = {}
    for path in sorted(run_dir.rglob("*")):
        if path.is_file() and path.name != "run.lock":
            out[str(path.relative_to(run_dir))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return out


def per_unit_timing(run_dir: Path) -> list:
    """Rough per-episode wall time and game-load gap from the ledgers.

    Serial (workers=1), so consecutive request rows in usage.jsonl joined
    with llm_requests duration_ms give: per-unit execution span, and the
    gap between units as the game-load + store overhead estimate.
    """
    rows = read_jsonl(run_dir / "llm_requests" / "usage.jsonl")
    dumps = {}
    for path in (run_dir / "llm_requests").glob("*.json"):
        dump = read_json(path)
        dumps[dump.get("request_id")] = dump
    units = []
    current = None
    prev_end = None
    for row in rows:
        if row.get("status") == "error" and row.get("request_id") not in dumps:
            continue  # an errored call has no dump; keep timing simple
        dump = dumps.get(row.get("request_id"), {})
        duration = (dump.get("duration_ms") or 0) / 1000.0
        start = float(row.get("time") or 0) - duration
        role, model = row.get("role"), row.get("model")
        # One episode = a contiguous run of rows for one (role, instruction):
        # the task prompt embeds "Your task:\n<instruction>\n\n<room
        # description>" before "Interaction so far:", which is a stable
        # per-game key.
        text = str(dump.get("request_text") or "")
        head = text.split("Interaction so far:", 1)[0]
        marker = "Your task:\n"
        instruction = (head.split(marker, 1)[1].strip()
                       if marker in head else head.strip())[:80]
        key = (role, model, instruction)
        if current is None or current["key"] != key:
            if current is not None:
                units.append(current)
            current = {"key": key, "first": start, "last": start + duration,
                       "requests": 1,
                       "load_gap_s": (round(max(0.0, start - prev_end), 1)
                                      if prev_end is not None else None)}
        else:
            current["last"] = start + duration
            current["requests"] += 1
        prev_end = start + duration
    if current is not None:
        units.append(current)
    return [
        {"role": u["key"][0], "model": u["key"][1],
         "instruction": u["key"][2][:60],
         "requests": u["requests"],
         "exec_span_s": round(u["last"] - u["first"], 1),
         "load_gap_s": u.get("load_gap_s")}
        for u in units
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default=None,
                        help="reuse a run directory (resume); default: new "
                             "runs/smoke_alfworld_real_<ts>")
    args = parser.parse_args()

    for name, value in ENV_DEFAULTS.items():
        os.environ.setdefault(name, value)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.run_dir) if args.run_dir else (
        REPO / "runs" / f"smoke_alfworld_real_{timestamp}")
    resume = bool(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    config_path = run_dir / "smoke_config.yaml"
    if not resume:
        config_path.write_text(config_yaml(), encoding="utf-8")

    report = {"run_dir": str(run_dir), "resume": resume,
              "env": {k: os.environ.get(k) for k in ENV_DEFAULTS}}

    # -- wiring identical to pipeline.run_pipeline, stages 0-1 only ----------
    cfg = load_config(str(config_path))
    store = Store(run_dir)
    lock = RunLock(run_dir)
    warning = lock.acquire()
    try:
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        usage = UsageRecorder(run_dir / "llm_requests" / "usage.jsonl")
        client = OpenAICompatClient.from_env()

        def handle_for(role: str):
            model = cfg.models[role]
            return make_handle(
                role, model, client,
                max_tokens=MAX_TOKENS,
                extra_body=dict(cfg.model_options.get(model, {})),
                infra_retries=3,
                infra_backoff_seconds=5.0,
            )

        ctx = StageContext(
            run_dir=run_dir, store=store, config=cfg, usage=usage,
            handles={role: handle_for(role) for role in
                     ("executor_strong", "executor_weak")},
            workdir_root=run_dir / "work",
        )
        ctx.workers = 1
        ctx.allow_infra_shortfall = False

        usage_before = len(read_jsonl(usage.path))
        leaks_before = len(adapter_pids())

        t0 = time.monotonic()
        s0 = stage0_freeze.run(ctx)
        report["stage0_seconds"] = round(time.monotonic() - t0, 1)
        report["stage0"] = s0

        t1 = time.monotonic()
        s1 = stage1_baseline.run(ctx, train_tasks=s0["train"],
                                 test_tasks=s0["test"])
        report["stage1_seconds"] = round(time.monotonic() - t1, 1)
        report["stage1"] = {
            "strong": s1["strong"]["summary"],
            "weak": s1["weak"]["summary"],
            "F": s1["F"],
            "infra_missing": s1["infra_missing"],
            "complete": s1["complete"],
        }

        usage_after = len(read_jsonl(usage.path))
        report["model_requests"] = usage_after - usage_before
        report["usage"] = usage.totals()
        report["orphan_adapters_before"] = leaks_before
        report["orphan_adapters_after"] = len(adapter_pids())
        report["per_unit"] = per_unit_timing(run_dir)
        report["leak_scan"] = leak_scan(run_dir)
    finally:
        lock.release()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
