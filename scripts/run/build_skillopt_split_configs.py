#!/usr/bin/env python
"""Build and verify the SkillOpt-official split-mode experiment configs
(stage 2/3 protocol v2).

Extracts the task-id lists from the SkillOpt split manifests, writes the two
campaign configs, and verifies that every id resolves through OUR benchmark
plugins exactly the way stage 0 will (blocks.tasks.load_tasks on the sorted
union of the split lists, then blocks.tasks.split_tasks on the explicit
lists). The SkillOpt val splits (ALFWorld 18, SearchQA 200) are intentionally
not used; the campaign runs on train + test only.

Protocol v2 (2026-10-13): the generated configs carry the v2 rule names
(any_weak_failed / strong_success_weak_failed) and the skill_delivery block
(retrieval delivery with a not-yet-frozen min_score placeholder). The v1
config paths are RETIRED from this script and never regenerated: they are
frozen records of the v1 experiments, and a regeneration that silently
wrote v2 content over a frozen v1 file would corrupt history. The
alfworld v2 config also exists as a hand-copied file -- render_config must
produce it BYTE-IDENTICALLY (integration plan ruling 12), or --check fails
and the default mode would overwrite a hand edit; the searchqa v2 config
has no handwritten copy, so the default mode generating it IS its source
of truth.

Id conventions (why they need no translation):
- ALFWorld: the SkillOpt alfworld_path_split items carry ``gamefile`` paths
  relative to $ALFWORLD_DATA, which is exactly the id convention of our
  ALFWorld plugin (benchmarks/alfworld/plugin.py, resolve_gamefile).
- SearchQA: the SkillOpt searchqa_id_split items carry the 32-hex ``id``
  shared by the outer searchqa-eval repo's items.json, which is exactly the
  id convention of our SearchQA plugin (benchmarks/searchqa/plugin.py).

Inputs
    --skillopt-data DIR  SkillOpt data root (default: the outer workspace
                         checkout, repos/pulled/SkillOpt/data).
    .env, sourced by the caller (example command below), must provide
    ALFWORLD_DATA and EXPEDISTILL_SEARCHQA_EVAL_DIR.

Outputs
    configs/experiments/alfworld_skillopt_split_v2.yaml
    configs/experiments/searchqa_skillopt_split_v2.yaml
    stdout: per-split counts, resolution results, any misses. Exit 2 on any
    miss or mismatch -- tasks are never silently dropped from a config.

Modes
    default: write both v2 configs (deterministic, byte-identical on
             rerun), then verify them end to end. This also materialises
             searchqa_skillopt_split_v2.yaml, which no one hand-writes.
    --check: write nothing; verify the configs already on disk byte-match
             this generator's render_config output, against the SkillOpt
             extraction, and through the plugins.

Example
    cd <repo root>
    set -a; . ./.env; set +a
    .venv/bin/python scripts/run/build_skillopt_split_configs.py --check

No model calls: config parsing and task loading only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]

#: SkillOpt split manifests (outer workspace checkout); overridable.
DEFAULT_SKILL_OPT_DATA = Path(
    "/Users/yimingniu/Code/SkillEvolvePaperList/repos/pulled/SkillOpt/data")

SPLIT_DIRS = {
    "alfworld": "alfworld_path_split",
    "searchqa": "searchqa_id_split",
}
ID_FIELDS = {
    "alfworld": "gamefile",
    "searchqa": "id",
}
#: The exact counts the SkillOpt manifests carry; a drift is a loud stop,
#: never a silent shrink of the task set.
EXPECTED_COUNTS = {
    "alfworld": {"train": 39, "test": 134},
    "searchqa": {"train": 400, "test": 1400},
}
#: v2 output paths. The v1 files are retired from this script (frozen
#: records of finished v1 experiments, never regenerated or overwritten).
CONFIG_PATHS = {
    "alfworld": REPO_ROOT
    / "configs/experiments/alfworld_skillopt_split_v2.yaml",
    "searchqa": REPO_ROOT
    / "configs/experiments/searchqa_skillopt_split_v2.yaml",
}

#: Weak-model profiles, carried over from the pilot configs verbatim: the
#: campaign runs the same weak model through the same runner and prompts, so
#: the pilot texts apply unchanged.
WEAK_PROFILES = {
    "alfworld": (
        "The weak model is qwen3.6-flash-distill, a small distilled model. "
        "It follows short, concrete instructions well but misses abstract "
        "goals, gets confused when several conditions must be checked in "
        "order, sometimes invents object ids it has not observed, and can "
        "loop on a failing action. Write skills as short ordered steps with "
        "explicit triggers and exact ALFWorld command forms."
    ),
    "searchqa": (
        "The weak model is qwen3.6-flash-distill, a small distilled model. "
        "It follows short, concrete instructions well but is weak on obscure "
        "trivia, gets distracted by long clue preambles, often gives up "
        "after one search instead of reformulating the query, and tends to "
        "answer with a sentence instead of the short answer phrase. Write "
        "skills as short ordered steps with explicit triggers, e.g. when to "
        "reformulate a Search query and how to phrase the final Finish[...] "
        "answer as a concise noun phrase."
    ),
}

#: The token_budgets section, identical in both generated configs (and in
#: tb21_full_v2.yaml): the reviewed, behavior-preserving 2026-10-12
#: migration table. Output budgets by role -- strong-model roles carry
#: 65536, the weak/qwen roles keep requesting the 5120 they always did;
#: input is measure-only in v1 (no thresholds).
TOKEN_BUDGETS_LINES = [
    "token_budgets:",
    "  # Output budgets by role. Behavior-preserving migration (2026-10-12):",
    "  # strong-model roles carried 65536 (pre-registered 2026-10-12 after G7",
    "  # burned 32768 twice); qwen roles requested 5120 today (the endpoint",
    "  # ignores it -- usage meta cap_violation will keep flagging, which is",
    "  # the evidence we want). Input: measure-only in v1 (no thresholds).",
    "  default_max_output_tokens: 65536",
    "  max_input_tokens: null",
    "  roles:",
    "    executor_strong: {max_output_tokens: 65536}",
    "    segmenter:       {max_output_tokens: 65536}",
    "    author:          {max_output_tokens: 65536}",
    "    examiner:        {max_output_tokens: 65536}",
    "    judge:           {max_output_tokens: 65536}",
    "    rewriter:        {max_output_tokens: 65536}",
    "    curator:         {max_output_tokens: 65536}",
    "    auditor:         {max_output_tokens: 65536}",
    "    executor_weak:   {max_output_tokens: 5120}",
    "    answerer:        {max_output_tokens: 5120}",
]


def _fail(message: str) -> None:
    print(f"FATAL: {message}", file=sys.stderr)
    sys.exit(2)


def extract_ids(skillopt_data: Path, benchmark: str) -> Dict[str, List[str]]:
    """Train/test id lists from the SkillOpt manifest, count- and
    duplicate-checked against the known split sizes."""
    split_dir = skillopt_data / SPLIT_DIRS[benchmark]
    field = ID_FIELDS[benchmark]
    out: Dict[str, List[str]] = {}
    for split in ("train", "test"):
        path = split_dir / split / "items.json"
        if not path.is_file():
            _fail(f"SkillOpt split file missing: {path}")
        items = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            _fail(f"{path} is not a JSON array")
        ids = [str(item[field]) for item in items]
        expected = EXPECTED_COUNTS[benchmark][split]
        if len(ids) != expected:
            _fail(f"{path} carries {len(ids)} items, expected {expected}")
        duplicates = sorted(t for t, c in Counter(ids).items() if c > 1)
        if duplicates:
            _fail(f"{path} carries duplicate ids: {duplicates}")
        out[split] = ids
    return out


#: The skill_delivery section, identical in both generated configs (protocol
#: v2 / retrieval split). min_score is a pre-registered PLACEHOLDER: it is
#: frozen per campaign only after the calibration protocol runs on that
#: campaign's own development slice (integration plan ruling 9), never
#: inherited from another bank.
SKILL_DELIVERY_LINES = [
    "skill_delivery:",
    "  # Retrieval-based delivery at test time (protocol v2). min_score",
    "  # 0.05 is a PLACEHOLDER, not frozen: before this campaign's full",
    "  # launch it must be calibrated on this benchmark's own development",
    "  # slice with the same protocol (integration plan, ruling 9 in",
    "  # docs/decisions/retrieval_delivery_tb_split_20261013.md) -- a",
    "  # value calibrated on another bank does not transfer (the IDF scale",
    "  # follows the bank).",
    "  mode: retrieval",
    "  top_k: 5",
    "  max_categories: 3",
    "  min_score: 0.05",
]


def render_config(benchmark: str, ids: Dict[str, List[str]]) -> str:
    """The full config text; deterministic (same input, same bytes).

    Byte-identical to the hand-copied alfworld v2 config (integration plan
    ruling 12): any divergence would make --check fatal or let the default
    mode silently overwrite a hand edit."""
    if benchmark == "alfworld":
        header = (
            "# ALFWorld campaign: the SkillOpt official split, split mode,\n"
            "# protocol v2 (stage 2/3 protocol v2, 2026-10-13; see\n"
            "# docs/decisions/stage23_protocol_v2_20261013.md and\n"
            "# docs/decisions/retrieval_delivery_tb_split_20261013.md).\n"
            "# train = SkillOpt alfworld_path_split/train (39 games under\n"
            "# json_2.1.1/train), test = SkillOpt .../test (134 games under\n"
            "# json_2.1.1/valid_unseen); SkillOpt's val split (18 valid_seen\n"
            "# games) is not used. Ids are gamefile paths relative to\n"
            "# $ALFWORLD_DATA (the plugin's convention), extracted from the\n"
            "# SkillOpt manifest by scripts/run/build_skillopt_split_configs.py.\n"
        )
    else:
        header = (
            "# SearchQA campaign: the SkillOpt official split, split mode,\n"
            "# protocol v2 (stage 2/3 protocol v2, 2026-10-13; see\n"
            "# docs/decisions/stage23_protocol_v2_20261013.md and\n"
            "# docs/decisions/retrieval_delivery_tb_split_20261013.md).\n"
            "# train = SkillOpt searchqa_id_split/train (400 items), test =\n"
            "# SkillOpt .../test (1400 items); SkillOpt's val split (200\n"
            "# items) is not used. Ids are the 32-hex item ids of the outer\n"
            "# searchqa-eval repo (the plugin's convention), extracted from\n"
            "# the SkillOpt manifest by scripts/run/build_skillopt_split_configs.py.\n"
        )
    lines: List[str] = [header]
    lines.append(f"benchmark: {benchmark}")
    lines.append("mode: split")
    lines.append("split:")
    for split in ("train", "test"):
        lines.append(f"  {split}:")
        lines.extend(f"    - {task_id}" for task_id in ids[split])
    lines.append("models:")
    lines.append("  executor_strong: deepseek-v4-flash-0731--ali")
    lines.append("  executor_weak: qwen3.6-flash-distill")
    lines.append("n_attempts_strong: 3")
    lines.append("n_attempts_weak: 3")
    lines.append("K: 5")
    lines.append("segment_retry_limit: 2")
    lines.append("retry_limit: 2")
    lines.append("# Pre-registered before this experiment started (2026-10-12): the budget is a")
    lines.append("# context-overflow safety valve, not a compression target; 4096 was a pilot")
    lines.append("# placeholder that a 100+ member bank mathematically cannot meet (merges")
    lines.append("# compress ~0.98). See docs/decisions/bank_token_budget_20261012.md.")
    lines.append("bank_token_budget: 96000")
    lines.append("curation_rounds: 3")
    lines.append("rules:")
    lines.append("  task_select: any_weak_failed")
    lines.append("  segment_select: strong_success_weak_failed")
    lines.append("  # Quota rule: up to 2 strong + up to 2 weak trajectories per")
    lines.append("  # task, successes first within the strong arm, failures first")
    lines.append("  # within the weak arm (the v2 material order); must be")
    lines.append("  # registered under this name in blocks/rules.py (stage 0")
    lines.append("  # validates every rule name).")
    lines.append("  trajectory_select: strong2_weak2")
    lines.extend(SKILL_DELIVERY_LINES)
    lines.append("model_options:")
    lines.append("  deepseek-v4-flash-0731--ali:")
    lines.append("    # Output budgets moved to token_budgets below (2026-10-12")
    lines.append("    # behavior-preserving migration); only generation-behavior")
    lines.append("    # parameters stay here.")
    lines.append("    reasoning_effort: max")
    lines.extend(TOKEN_BUDGETS_LINES)
    lines.append("weak_profile: >-")
    # Single-paragraph profile text: fold-clean under >-, two-space indent.
    lines.extend(f"  {part}" for part in WEAK_PROFILES[benchmark].split("\n"))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Verification: config parse + the exact task pipeline stage 0 will run
# ---------------------------------------------------------------------------

def _alfworld_misses(union: List[str]) -> List[str]:
    """Every id that would not load (gamefile or traj_data.json missing);
    a complete miss list, because the plugin raises on the first one."""
    raw = os.environ.get("ALFWORLD_DATA", "").strip()
    if not raw:
        _fail("ALFWORLD_DATA is not set; source .env before running (see "
              "the module docstring example)")
    root = Path(raw).expanduser()
    misses = []
    for task_id in union:
        gamefile = root / task_id
        if not gamefile.is_file() or not (gamefile.parent / "traj_data.json").is_file():
            misses.append(task_id)
    return misses


def _searchqa_misses(union: List[str]) -> List[str]:
    """Ids absent from the eval repo's materialized items (plus the
    per-split provenance cross-check against the SkillOpt extraction)."""
    raw = os.environ.get("EXPEDISTILL_SEARCHQA_EVAL_DIR", "").strip()
    if not raw:
        _fail("EXPEDISTILL_SEARCHQA_EVAL_DIR is not set; source .env before "
              "running (see the module docstring example)")
    data_dir = Path(raw).expanduser() / "data" / "searchqa_split"
    eval_ids: Dict[str, set] = {}
    for split in ("train", "val", "test"):
        path = data_dir / split / "items.json"
        if not path.is_file():
            _fail(f"eval repo split file missing: {path}")
        eval_ids[split] = {str(item["id"]) for item in
                           json.loads(path.read_text(encoding="utf-8"))}
    available = eval_ids["train"] | eval_ids["val"] | eval_ids["test"]
    return [task_id for task_id in union if task_id not in available]


def verify_config(benchmark: str, cfg_path: Path,
                  ids: Dict[str, List[str]]) -> Dict[str, Any]:
    """Parse the config and resolve its split through the plugins, exactly
    the way stage 0 does (explicit lists, no ``tasks`` field: D1 loads the
    sorted union, D2 checks unknown/overlap/coverage). Returns a summary
    dict; exits 2 on any problem."""
    from expedistill.core.config import load_config
    from expedistill.blocks import tasks as tasks_block

    print(f"[{benchmark}] config: {cfg_path}")
    if not cfg_path.is_file():
        _fail(f"config not found: {cfg_path}")
    # The configs are generated files, so the on-disk bytes must be exactly
    # what render_config produces: a hand edit the generator cannot
    # reproduce is drift, and --check must fail on it loudly rather than
    # bless a config that a rerun would silently overwrite.
    if render_config(benchmark, ids) != cfg_path.read_text(encoding="utf-8"):
        _fail(f"{cfg_path} does not byte-match render_config output; "
              "regenerate it (default mode) or update the generator")
    config = load_config(str(cfg_path))
    problems: List[str] = []
    if config.benchmark != benchmark:
        problems.append(f"benchmark is {config.benchmark!r}, expected {benchmark!r}")
    if config.mode != "split":
        problems.append(f"mode is {config.mode!r}, expected 'split'")
    if config.tasks is not None:
        problems.append(
            f"'tasks' must be absent in split mode with explicit lists "
            f"(stage 0 loads the split union), got {config.tasks!r}")
    for split in ("train", "test"):
        listed = list(config.split.get(split) or [])
        if listed != ids[split]:
            problems.append(
                f"split[{split!r}] does not match the SkillOpt extraction "
                f"({len(listed)} listed vs {len(ids[split])} extracted); "
                "regenerate the config")
    overlap = sorted(set(config.split["train"]) & set(config.split["test"]))
    if overlap:
        problems.append(f"train/test overlap on: {overlap[:10]}")
    if problems:
        for problem in problems:
            print(f"  PROBLEM: {problem}")
        sys.exit(2)

    # The exact spec stage 0 hands to D1: the sorted union of the lists
    # (stages/stage0_freeze.py _explicit_split_ids / _tasks_spec).
    union = sorted(set(config.split["train"]) | set(config.split["test"]))
    misses = (_alfworld_misses(union) if benchmark == "alfworld"
              else _searchqa_misses(union))
    if misses:
        print(f"  MISSING ({len(misses)} of {len(union)} ids do not resolve "
              f"against the benchmark data):")
        for task_id in misses:
            print(f"    {task_id}")
        _fail(f"{benchmark}: {len(misses)} task id(s) do not resolve; fix the "
              "data or the split, never drop tasks silently")

    tasks = tasks_block.load_tasks(benchmark, union)
    # Contract 3: split_tasks returns (train, test, reserved); a two-key
    # split spec (what these configs carry) must yield no reserved tasks.
    train_tasks, test_tasks, reserved_tasks = tasks_block.split_tasks(
        tasks, config.mode, config.split)
    if reserved_tasks:
        _fail(f"{benchmark}: split_tasks returned {len(reserved_tasks)} "
              "reserved tasks for a two-key split spec; expected none")
    expected = EXPECTED_COUNTS[benchmark]
    if len(train_tasks) != expected["train"] or len(test_tasks) != expected["test"]:
        _fail(f"{benchmark}: loaded {len(train_tasks)}/{len(test_tasks)} "
              f"train/test tasks, expected {expected['train']}/{expected['test']}")
    if len(tasks) != expected["train"] + expected["test"]:
        _fail(f"{benchmark}: loaded {len(tasks)} tasks, expected "
              f"{expected['train'] + expected['test']}")

    # Split provenance, for the record.
    if benchmark == "alfworld":
        for split, task_list in (("train", train_tasks), ("test", test_tasks)):
            parts = Counter((task.meta.get("split") or Path(
                task.task_id).parts[1]) for task in task_list)
            print(f"  {split}: {len(task_list)} tasks, "
                  f"engine split dirs {dict(parts)}")
    else:
        raw = os.environ.get("EXPEDISTILL_SEARCHQA_EVAL_DIR", "").strip()
        data_dir = Path(raw).expanduser() / "data" / "searchqa_split"
        eval_ids = {
            split: {str(item["id"]) for item in json.loads(
                (data_dir / split / "items.json").read_text(encoding="utf-8"))}
            for split in ("train", "val", "test")}
        provenance = {
            split: Counter(
                next(name for name in ("train", "val", "test")
                     if task.task_id in eval_ids[name])
                for task in task_list)
            for split, task_list in (("train", train_tasks), ("test", test_tasks))}
        for split, counts in provenance.items():
            print(f"  {split}: {sum(counts.values())} tasks, "
                  f"eval-repo splits {dict(counts)}")

    print(f"  OK: {len(train_tasks)} train + {len(test_tasks)} test ids all "
          f"resolve through the {benchmark} plugin "
          f"({len(union)} union ids loaded)")
    return {
        "config": config,
        "union": len(union),
        "train": len(train_tasks),
        "test": len(test_tasks),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build/verify the SkillOpt split-mode experiment configs.")
    parser.add_argument(
        "--skillopt-data", type=Path, default=DEFAULT_SKILL_OPT_DATA,
        help=f"SkillOpt data root (default: {DEFAULT_SKILL_OPT_DATA})")
    parser.add_argument(
        "--check", action="store_true",
        help="verify the configs on disk instead of writing them")
    args = parser.parse_args()

    extracted = {
        benchmark: extract_ids(args.skillopt_data, benchmark)
        for benchmark in ("alfworld", "searchqa")
    }
    for benchmark, ids in extracted.items():
        print(f"[{benchmark}] extracted from SkillOpt: "
              f"train {len(ids['train'])}, test {len(ids['test'])} "
              f"(counts match {EXPECTED_COUNTS[benchmark]})")

    if not args.check:
        for benchmark, ids in extracted.items():
            text = render_config(benchmark, ids)
            path = CONFIG_PATHS[benchmark]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"[{benchmark}] wrote {path} ({len(text.splitlines())} lines)")

    for benchmark in ("alfworld", "searchqa"):
        verify_config(benchmark, CONFIG_PATHS[benchmark], extracted[benchmark])

    print("ALL OK: both configs load, match the SkillOpt splits exactly, and "
          "every id resolves through our plugins.")


if __name__ == "__main__":
    main()
