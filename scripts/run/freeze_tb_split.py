#!/usr/bin/env python
"""Generate and verify the TB frozen-split block of an experiment config.

The AgentBrew split JSONs are the master copy: this script only maps them
onto the config's ``train`` / ``test`` / ``reserved`` lists and verifies
the mapping element by element, so a hand edit or a data drift fails
loudly instead of silently shrinking the experiment's task set.

Two protocols are supported because old run directories remain frozen
historical records:

``agentbrew25`` (active since 2026-10-26)
    ``data/splits/tb21_agentbrew25/{evolve,test}.json``: 25 evolve
    (config ``train``), 64 test, no validation/reserved.
``legacy``
    ``data/splits/tb21/{evolve,test,validation}.json``: 41 / 28 / 20
    mapped to train / test / reserved.

Inputs
    --splits-dir DIR  directory holding the protocol's JSON files.
    --config PATH     the experiment config YAML to verify.
    --protocol NAME   ``agentbrew25`` (default) or ``legacy``.

Outputs
    stdout: the rendered split block, then the verification result.
    Exit 2 on ANY mismatch or malformed input -- tasks are never silently
    dropped from a config.

Examples
    .venv/bin/python scripts/run/freeze_tb_split.py \
        --splits-dir ../benchmark/terminalbench-eval/data/splits/tb21_agentbrew25 \
        --config configs/experiments/<new config>.yaml

    .venv/bin/python scripts/run/freeze_tb_split.py --protocol legacy \
        --splits-dir ../benchmark/terminalbench-eval/data/splits/tb21 \
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

#: Protocol -> the three frozen facts (file stem, config key, count).
PROTOCOLS = {
    "agentbrew25": {
        "splits": {
            "evolve": {"config_key": "train", "count": 25},
            "test": {"config_key": "test", "count": 64},
        },
        "union": 89,
        "seed": 42,
        # The active JSON deliberately carries source_revision: null.
        "revision_prefix": None,
    },
    "legacy": {
        "splits": {
            "evolve": {"config_key": "train", "count": 41},
            "test": {"config_key": "test", "count": 28},
            "validation": {"config_key": "reserved", "count": 20},
        },
        "union": 89,
        "seed": 42,
        # The checked-in legacy JSONs do not carry source_revision; the
        # count/seed checks are the drift guard here.
        "revision_prefix": None,
    },
}


def _fail(message: str) -> None:
    print(f"FATAL: {message}", file=sys.stderr)
    sys.exit(2)


def _load_split(splits_dir: Path, name: str, protocol: str) -> dict:
    spec = PROTOCOLS[protocol]["splits"][name]
    path = splits_dir / f"{name}.json"
    if not path.is_file():
        _fail(f"{protocol} split file missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        _fail(f"{path} is not valid JSON: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        _fail(f"{path} must be an object with a tasks array")
    seed = PROTOCOLS[protocol]["seed"]
    if data.get("seed") != seed:
        _fail(f"{path} carries seed {data.get('seed')!r}, expected "
              f"{seed} -- the frozen split is seed-{seed}, do not "
              "re-derive it")
    revision_prefix = PROTOCOLS[protocol]["revision_prefix"]
    if revision_prefix is not None:
        revision = str(data.get("source_revision") or "")
        if not revision.startswith(revision_prefix):
            _fail(f"{path} carries source_revision {revision!r}, expected "
                  f"the frozen {revision_prefix}... revision")
    expected = spec["count"]
    tasks = data["tasks"]
    if len(tasks) != expected:
        _fail(f"{path} lists {len(tasks)} tasks, expected {expected}")
    if len(set(tasks)) != len(tasks):
        _fail(f"{path} lists duplicate task names")
    if not all(isinstance(task, str) and task for task in tasks):
        _fail(f"{path} lists non-string task names")
    declared = data.get("task_count")
    if declared is not None and declared != len(tasks):
        _fail(f"{path} declares task_count={declared!r} but lists "
              f"{len(tasks)} tasks")
    return data


def render_block(lists: dict, protocol: str) -> str:
    """The ``split:`` YAML block, the exact shape the config carries."""
    lines = ["split:"]
    comments = {
        "train": "train = AgentBrew evolve",
        "test": "test = AgentBrew test",
        "reserved": "reserved = AgentBrew validation (legacy only)",
    }
    for key in ("train", "test", "reserved"):
        if key not in lists:
            continue
        lines.append(f"  # {comments[key]} ({len(lists[key])} tasks) -- "
                     f"{protocol}")
        lines.append(f"  {key}:")
        lines.extend(f"    - {name}" for name in lists[key])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and verify the TB frozen-split block of an "
                    "experiment config against the AgentBrew split JSONs.")
    parser.add_argument("--splits-dir", required=True, type=Path,
                        help="directory holding the protocol's JSON files")
    parser.add_argument("--config", required=True, type=Path,
                        help="the experiment config YAML to verify")
    parser.add_argument("--protocol", choices=sorted(PROTOCOLS),
                        default="agentbrew25",
                        help="split protocol (default: agentbrew25)")
    args = parser.parse_args()

    import yaml  # thin script: plain YAML parse, no expedistill import

    protocol_spec = PROTOCOLS[args.protocol]
    splits = {
        name: _load_split(args.splits_dir, name, args.protocol)
        for name in protocol_spec["splits"]
    }
    lists = {spec["config_key"]: data["tasks"]
             for name, spec in protocol_spec["splits"].items()
             for data in [splits[name]]}

    overlap = sorted(
        (set(lists.get("train", [])) & set(lists.get("test", [])))
        | (set(lists.get("train", [])) & set(lists.get("reserved", [])))
        | (set(lists.get("test", [])) & set(lists.get("reserved", []))))
    if overlap:
        _fail(f"the split lists overlap on: {overlap}")
    union = set().union(*(set(items) for items in lists.values()))
    if len(union) != protocol_spec["union"]:
        _fail(f"the union of the split lists is {len(union)}, expected "
              f"{protocol_spec['union']}")

    print(render_block(lists, args.protocol))
    print()

    try:
        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"cannot read config {args.config}: {exc}")
    if not isinstance(config, dict) or not isinstance(config.get("split"),
                                                      dict):
        _fail(f"{args.config} has no 'split:' mapping to verify")

    expected_keys = set(lists)
    listed_keys = set(config["split"])
    if listed_keys != expected_keys:
        _fail(f"{args.config} split keys are {sorted(listed_keys)}, "
              f"expected exactly {sorted(expected_keys)} for protocol "
              f"{args.protocol}")
    problems = []
    for key in expected_keys:
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
          f"AgentBrew frozen JSONs ({args.protocol}: "
          f"{ '/'.join(str(len(lists[key])) for key in lists) }, "
          f"pairwise disjoint, union {len(union)}, seed "
          f"{protocol_spec['seed']}).")


if __name__ == "__main__":
    main()
