#!/usr/bin/env python
"""Build a schema-v5 live-run experiment config for a local benchmark.

Purpose: render the same method configuration as the TB21 live run
(baseline -> learn -> live -> learn -> live -> learn -> test) for
SearchQA or ALFWorld, with the SkillOpt frozen split as the task set.

Differences from the TB21 live-run config that this script encodes:

- The test phase uses the ``whole_bank`` prompt injection instead of
  ``folder``. SearchQA and ALFWorld expose no file-system action, so a
  skill folder cannot reach their agents. Curation keeps the accepted
  bank at ten skills or fewer, so injecting the whole bank stays within
  the weak model's context.
- The test phase compares against ``fresh_bare``. The external campaign
  control file exists only for TB21.
- ``use_weak_profile`` is false by default: the profile text is optional
  and the caller asked for no profile.

Inputs
    --benchmark {searchqa,alfworld}
    --skillopt-data DIR   SkillOpt data root (default: the outer workspace
                          checkout, repos/pulled/SkillOpt/data).
    --out PATH            output config path.
    --check               verify the existing --out file byte-matches the
                          render output; write nothing.
    --no-verify           skip resolving the split through the plugin (the
                          unit tests use tiny synthetic manifests; production
                          generation must leave verification on).

Outputs
    the config file, plus a one-line count report. Exit 2 on any count,
    duplicate, resolution or load failure.

Example
    set -a; . ./.env; set +a
    .venv/bin/python scripts/run/build_local_live_config.py \
        --benchmark searchqa \
        --out configs/experiments/searchqa_split_live_run_20261020.yaml

No model calls.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLOPT_DATA = Path(
    "/Users/yimingniu/Code/SkillEvolvePaperList/repos/pulled/SkillOpt/data")

SPLIT_DIRS = {
    "searchqa": "searchqa_id_split",
    "alfworld": "alfworld_path_split",
}
ID_FIELDS = {
    "searchqa": "id",
    "alfworld": "gamefile",
}

MODELS = {
    "answerer": "qwen3.6-flash-distill",
    "auditor": "deepseek-v4-flash-0731-tencent",
    "author": "deepseek-v4-flash-0731-tencent",
    "curator": "deepseek-v4-flash-0731-tencent",
    "examiner": "deepseek-v4-flash-0731-tencent",
    "executor_strong": "deepseek-v4-flash-0731-tencent",
    "executor_weak": "qwen3.6-flash-distill",
    "judge": "deepseek-v4-flash-0731-tencent",
    "rewriter": "deepseek-v4-flash-0731-tencent",
    "segmenter": "deepseek-v4-flash-0731-tencent",
}

TOKEN_BUDGETS = {
    "default_max_output_tokens": 65536,
    "max_input_tokens": None,
    "roles": {role: {"max_output_tokens": 65536} for role in MODELS},
}

RULES = {
    "segment_select": "strong_success_weak_failed",
    "task_select": "any_weak_failed",
    "trajectory_select": "strong1_weak1",
}

SEQUENCE = ["baseline", "learn", "live", "learn", "live", "learn", "test"]


def _phases() -> Dict[str, Any]:
    """The TB21 live-run method, with the local-benchmark test phase."""
    return {
        "baseline": {
            "kind": "execute",
            "role": "baseline",
            "tasks": "train",
            "delivery": {"name": "none"},
            "execution": {
                "name": "real",
                "arms": ["executor_strong", "executor_weak"],
            },
            "acceptance": None,
            "compare": None,
        },
        "learn": {
            "kind": "learn",
            "role": "learn",
            "evidence": {
                "name": "segment",
                "K": 5,
                "retry_limit": 2,
                "write_lessons": True,
            },
            "author": {"name": "distill_diagnose"},
            "curate": {
                "name": "agentbrew",
                "merge_check": "none",
                "rounds": 3,
                "token_budget": None,
                "similarity_input": "summary",
                "grouping_input": "full",
                "drop_low_value": False,
            },
        },
        "live": {
            "kind": "execute",
            "role": "live",
            "tasks": "train",
            "delivery": {"name": "group_by_task"},
            "execution": {
                "name": "real",
                "n_attempts": 3,
                "max_injection_tokens": None,
            },
            "acceptance": None,
            "compare": {"against": "baseline", "rule": "strict_rate_gain"},
        },
        "test": {
            "kind": "execute",
            "role": "test",
            "tasks": "test",
            "delivery": {"name": "whole_bank"},
            "execution": {"name": "real"},
            "acceptance": None,
            "compare": {"against": "fresh_bare", "rule": None},
        },
    }


def extract_ids(skillopt_data: Path, benchmark: str) -> Dict[str, List[str]]:
    """Train/test ids from the SkillOpt manifest, checked against its counts."""
    split_dir = skillopt_data / SPLIT_DIRS[benchmark]
    field = ID_FIELDS[benchmark]
    manifest_path = split_dir / "split_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"FATAL: cannot read {manifest_path}: {exc}")
    counts = manifest.get("counts") or {}
    out: Dict[str, List[str]] = {}
    for split in ("train", "test"):
        path = split_dir / split / "items.json"
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SystemExit(f"FATAL: cannot read {path}: {exc}")
        if not isinstance(items, list):
            raise SystemExit(f"FATAL: {path} is not a JSON array")
        ids = [str(item[field]) for item in items]
        expected = counts.get(split)
        if expected is not None and len(ids) != expected:
            raise SystemExit(
                f"FATAL: {path} carries {len(ids)} items, manifest says "
                f"{expected}")
        duplicates = sorted(t for t, c in Counter(ids).items() if c > 1)
        if duplicates:
            raise SystemExit(f"FATAL: {path} carries duplicate ids: {duplicates}")
        out[split] = ids
    return out


def build_config(benchmark: str, ids: Dict[str, List[str]], *,
                 use_weak_profile: bool = False) -> Dict[str, Any]:
    """The complete config mapping, in a stable key order."""
    return {
        "benchmark": benchmark,
        "invalid_redispatch_limit": 1,
        "mode": "split",
        "model_options": {
            "deepseek-v4-flash-0731-tencent": {"reasoning_effort": "max"},
        },
        "models": dict(MODELS),
        "n_attempts_strong": 3,
        "n_attempts_weak": 3,
        "phases": _phases(),
        "rules": dict(RULES),
        "schema_version": 5,
        "sequence": list(SEQUENCE),
        "split": {
            "train": list(ids["train"]),
            "test": list(ids["test"]),
        },
        "token_budgets": {
            "default_max_output_tokens": TOKEN_BUDGETS[
                "default_max_output_tokens"],
            "max_input_tokens": TOKEN_BUDGETS["max_input_tokens"],
            "roles": {
                role: dict(budget)
                for role, budget in TOKEN_BUDGETS["roles"].items()
            },
        },
        "use_weak_profile": use_weak_profile,
    }


def render_config(benchmark: str, ids: Dict[str, List[str]], *,
                  use_weak_profile: bool = False) -> str:
    """Deterministic YAML text for the config."""
    config = build_config(benchmark, ids, use_weak_profile=use_weak_profile)
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True,
                          default_flow_style=False)


def verify_config(benchmark: str, path: Path, ids: Dict[str, List[str]]) -> None:
    """Load the file and resolve its split through the benchmark plugin."""
    from expedistill.blocks import tasks as tasks_block
    from expedistill.core.config import load_config

    config = load_config(str(path))
    if config.benchmark != benchmark:
        raise SystemExit(
            f"FATAL: {path} declares benchmark {config.benchmark!r}, "
            f"expected {benchmark!r}")
    if config.mode != "split":
        raise SystemExit(f"FATAL: {path} declares mode {config.mode!r}")
    for split in ("train", "test"):
        listed = list(config.split.get(split) or [])
        if listed != ids[split]:
            raise SystemExit(
                f"FATAL: split[{split!r}] in {path} does not match the "
                f"SkillOpt extraction ({len(listed)} vs {len(ids[split])})")

    union = sorted(set(config.split["train"]) | set(config.split["test"]))
    tasks = tasks_block.load_tasks(benchmark, union)
    train_tasks, test_tasks, reserved = tasks_block.split_tasks(
        tasks, config.mode, config.split)
    if reserved:
        raise SystemExit(
            f"FATAL: a two-key split produced {len(reserved)} reserved tasks")
    if (len(train_tasks), len(test_tasks)) != (len(ids["train"]),
                                               len(ids["test"])):
        raise SystemExit(
            f"FATAL: resolved {len(train_tasks)}/{len(test_tasks)} train/test "
            f"tasks, expected {len(ids['train'])}/{len(ids['test'])}")
    print(f"  OK: {len(train_tasks)} train + {len(test_tasks)} test ids "
          f"resolve through the {benchmark} plugin")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a schema-v5 live-run config for a local benchmark.")
    parser.add_argument("--benchmark", required=True,
                        choices=sorted(SPLIT_DIRS))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--skillopt-data", type=Path,
                        default=DEFAULT_SKILLOPT_DATA)
    parser.add_argument("--check", action="store_true",
                        help="verify --out byte-matches the render output")
    parser.add_argument("--no-verify", action="store_true",
                        help="skip loading the split through the plugin "
                             "(unit tests with synthetic manifests only)")
    parser.add_argument("--use-weak-profile", action="store_true",
                        help="enable the profile switch (default off)")
    args = parser.parse_args()

    ids = extract_ids(args.skillopt_data, args.benchmark)
    print(f"[{args.benchmark}] SkillOpt split: train {len(ids['train'])}, "
          f"test {len(ids['test'])}")
    text = render_config(args.benchmark, ids,
                         use_weak_profile=args.use_weak_profile)

    if args.check:
        if not args.out.is_file():
            raise SystemExit(f"FATAL: {args.out} does not exist")
        if args.out.read_text(encoding="utf-8") != text:
            raise SystemExit(
                f"FATAL: {args.out} does not byte-match the render output; "
                "regenerate it")
        print(f"  {args.out} matches the generated content")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"  wrote {args.out}")

    if not args.no_verify:
        verify_config(args.benchmark, args.out, ids)


if __name__ == "__main__":
    main()
