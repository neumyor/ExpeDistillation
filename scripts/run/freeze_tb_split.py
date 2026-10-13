#!/usr/bin/env python
"""Generate and verify the TB frozen-split block of the reuse config.

Purpose: the tb21_split_reuse.yaml split lists must be BIT-EXACT copies of
the AgentBrew frozen split JSONs (evolve/test/validation = train/test/
reserved). The AgentBrew JSONs are the master copy (the frozen split was
produced there with seed 42 over terminal-bench 2.0 at revision
2fd12b88...; this repo only maps it, never re-derives it). This script
renders the ``split:`` block from those JSONs and verifies the config's
three lists against it element by element, so a hand edit or a data drift
fails loudly here instead of silently shrinking the experiment's task set.

Inputs
    --splits-dir DIR  directory holding evolve.json / test.json /
                      validation.json (the AgentBrew frozen splits).
    --config PATH     the experiment config YAML to verify.

Outputs
    stdout: the rendered split block, then the verification result.
    Exit 2 on ANY mismatch or malformed input -- tasks are never silently
    dropped from a config.

Example
    .venv/bin/python scripts/run/freeze_tb_split.py \
        --splits-dir /path/to/terminalbench-eval/data/splits \
        --config configs/experiments/tb21_split_reuse.yaml

No writes: the config is a handcrafted file; this script only verifies it
(the whole-config identity against the source run is the assemble-reuse
whitelist diff's job).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Frozen facts of the AgentBrew split (docs/decisions/
#: retrieval_delivery_tb_split_20261013.md section 1.1): the JSONs are the
#: master, these expected values are drift guards -- a JSON that no longer
#: carries them must stop the verification, not pass it silently.
EXPECTED = {
    "evolve": {"config_key": "train", "count": 41},
    "test": {"config_key": "test", "count": 28},
    "validation": {"config_key": "reserved", "count": 20},
}
EXPECTED_SEED = 42
EXPECTED_REVISION_PREFIX = "2fd12b8"
EXPECTED_UNION = 89


def _fail(message: str) -> None:
    print(f"FATAL: {message}", file=sys.stderr)
    sys.exit(2)


def _load_split(splits_dir: Path, name: str) -> dict:
    path = splits_dir / f"{name}.json"
    if not path.is_file():
        _fail(f"AgentBrew split file missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        _fail(f"{path} is not valid JSON: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        _fail(f"{path} must be an object with a tasks array")
    if data.get("seed") != EXPECTED_SEED:
        _fail(f"{path} carries seed {data.get('seed')!r}, expected "
              f"{EXPECTED_SEED} -- the frozen split is seed-42, do not "
              "re-derive it")
    revision = str(data.get("source_revision") or "")
    if not revision.startswith(EXPECTED_REVISION_PREFIX):
        _fail(f"{path} carries source_revision {revision!r}, expected the "
              f"frozen {EXPECTED_REVISION_PREFIX}... revision")
    expected = EXPECTED[name]["count"]
    tasks = data["tasks"]
    if len(tasks) != expected:
        _fail(f"{path} lists {len(tasks)} tasks, expected {expected}")
    if len(set(tasks)) != len(tasks):
        _fail(f"{path} lists duplicate task names")
    if not all(isinstance(task, str) and task for task in tasks):
        _fail(f"{path} lists non-string task names")
    return data


def render_block(lists: dict) -> str:
    """The ``split:`` YAML block, the exact shape the config carries."""
    lines = ["split:"]
    comments = {
        "train": "train = AgentBrew evolve (41 tasks, seed 42) -- "
                 "the distillation pool",
        "test": "test = AgentBrew test (28 tasks) -- both arms run "
                "fresh in stage 6",
        "reserved": "reserved = AgentBrew validation (20 tasks) -- held "
                    "out of both arms; dev-smoke only",
    }
    for key in ("train", "test", "reserved"):
        lines.append(f"  # {comments[key]}")
        lines.append(f"  {key}:")
        lines.extend(f"    - {name}" for name in lists[key])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and verify the TB frozen-split block of the "
                    "reuse config against the AgentBrew split JSONs.")
    parser.add_argument("--splits-dir", required=True, type=Path,
                        help="directory holding evolve.json / test.json / "
                             "validation.json (the AgentBrew frozen "
                             "splits)")
    parser.add_argument("--config", required=True, type=Path,
                        help="the experiment config YAML to verify")
    args = parser.parse_args()

    import yaml  # thin script: plain YAML parse, no expedistill import

    splits = {
        EXPECTED[name]["config_key"]: _load_split(args.splits_dir, name)
        for name in EXPECTED
    }
    lists = {key: data["tasks"] for key, data in splits.items()}

    overlap = sorted(
        (set(lists["train"]) & set(lists["test"]))
        | (set(lists["train"]) & set(lists["reserved"]))
        | (set(lists["test"]) & set(lists["reserved"])))
    if overlap:
        _fail(f"the AgentBrew lists overlap on: {overlap}")
    union = set().union(*lists.values())
    if len(union) != EXPECTED_UNION:
        _fail(f"the union of the three lists is {len(union)}, expected "
              f"{EXPECTED_UNION}")

    print(render_block(lists))
    print()

    try:
        config_text = args.config.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(f"cannot read config {args.config}: {exc}")
    try:
        config = yaml.safe_load(config_text)
    except ValueError as exc:
        _fail(f"{args.config} is not valid YAML: {exc}")
    if not isinstance(config, dict) or not isinstance(config.get("split"),
                                                      dict):
        _fail(f"{args.config} has no 'split:' mapping to verify")
    problems = []
    for key in ("train", "test", "reserved"):
        listed = config["split"].get(key)
        if listed != lists[key]:
            problems.append(
                f"split[{key!r}] does not match the AgentBrew JSON "
                f"element by element ({len(listed or [])} listed vs "
                f"{len(lists[key])} in the JSON); the JSONs are the "
                "master copy -- regenerate the block above and fix the "
                "config, never drop tasks silently")
    if problems:
        for problem in problems:
            print(f"  PROBLEM: {problem}", file=sys.stderr)
        sys.exit(2)

    print(f"OK: {args.config} split lists are bit-exact copies of the "
          f"AgentBrew frozen JSONs (train/test/reserved = "
          f"{len(lists['train'])}/{len(lists['test'])}/"
          f"{len(lists['reserved'])}, pairwise disjoint, union "
          f"{len(union)}, seed {EXPECTED_SEED}, revision "
          f"{splits['train']['source_revision']}).")


if __name__ == "__main__":
    main()
