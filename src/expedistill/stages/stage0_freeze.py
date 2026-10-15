"""Stage 0: load tasks, derive the split, freeze the run manifest (README 3).

Wiring only (layer 6): D1 loads the tasks, D2 divides them, B4 freezes the
result. The one derivation this stage owns is turning a ratio+seed split
config into explicit frozen lists before D2 sees it -- D2's contract is that
it receives frozen data, never a procedure (blocks/tasks.py docstring), and
that derivation belongs to the run start by design, not to a block.

Order of checks: the named rules are validated against the rule registries
first (``blocks/rules.py``; an unknown name is a ConfigError), before any
task is loaded or anything is written, so a typo in ``rules.segment_select``
stops the run here instead of after stage 1's executions. ``config.rules``
is complete by construction (core.config fills ``DEFAULT_RULES``), so the
names frozen below are the effective ones.

Task set: D1 loads exactly ``config.tasks`` (None = the plugin's full set,
which a plugin may reject -- TB does). With explicit split lists and no
``config.tasks``, the union of the lists (``reserved`` included) is loaded;
with both given, the union must equal the loaded set exactly (ConfigError
naming the ids on either side otherwise). A loaded set with a repeated id
(e.g. a task file naming one twice) is a ConfigError listing the ids,
checked before any split derivation. A ratio split is derived from the
loaded set.

The frozen split carries three lists: ``train``, ``test`` and
``reserved``. ``reserved`` is the partition held back from BOTH arms
(protocol v2 / the TB frozen-split reuse experiment): it is loaded -- the
split lists must cover the loaded task set exactly -- but neither distilled
on nor tested on; it exists so a dev smoke can run held-out tasks without
touching the measurement. A two-key spec (``{"train", "test"}``) is still
accepted and freezes ``reserved: []``, so every manifest and summary
downstream can rely on all three keys being present (integration plan
ruling 7).

What the manifest freezes (B4 compares field by field on every start):
``benchmark``, ``mode``, the explicit ``split`` id lists (all three keys),
the whole config
(``config``: every ExperimentConfig field, including ``tasks``, the
completed ``rules`` and the prompt digests), ``prompt_digests``, ``rules``,
``model_roles``, ``tasks_digest`` and ``aux_metric``. ``aux_metric`` is the
plugin-declared auxiliary metric's NAME (``BenchmarkPlugin.aux_metric_name``,
None when the benchmark has none): the auxiliary metric is pre-registered --
part of the experiment identity, frozen before any result exists, never
picked between after seeing them. It is declared by the plugin, not a config
field, so it appears in the manifest but not in ``config``. Note the scorer
itself is CODE, not config: changing a plugin's scorer does not change any
prompt digest, so an old run directory will not refuse a resume across a
scorer change -- compare run directories produced by the same code, and
record scorer changes in the repo history.
``tasks_digest`` is a sha256 over the
loaded tasks sorted by id -- each task's ``task_id``, ``instruction`` and
``meta`` -- serialised as canonical JSON (see ``tasks_digest``), so a task
file whose text changed between resumes is refused even though the ids are
the same. It covers what the plugin puts in the Task: for TB the instruction
lives in the remote task directory and the Task carries only the name, so
remote-side edits are outside this digest.

Prompt digests are deterministic: the method prompts
(``prompts.METHOD_PROMPT_NAMES``) plus exactly the configured plugin's
``task_prompt_name``, never whatever other plugins a process happened to
import. A task prompt that is not registered in B2 is frozen as ``None``;
real plugins register theirs at import, so only test fakes hit that case
(and a later registration still trips the comparison). Since schema v3
the freeze is FULL: every registered method prompt's digest is frozen on
every run (the two G10/G11 prompts included -- they register at
``prompts/__init__`` import like every other), so the name set is the
same for every acceptance mode; a manifest listing digests this run never
calls is noise, not risk. Two static tests (tests/unit/prompts/
test_freeze.py) pin that nothing registers outside the frozen surface.

Resume: the manifest is the unit of this stage, and B4's ``freeze`` IS the
resume check -- a present manifest that matches is accepted (and nothing is
recomputed: every value below is a pure function of the frozen inputs), a
differing one raises ``FrozenProtocolChanged`` and names the changed keys.
Tasks are still loaded and split on every call because the returned id
lists and the content digest are derived from them; a task set that drifted
from the frozen split fails loudly inside D2 (unknown ids / uncovered tasks
are ConfigErrors), and drifted content fails on ``tasks_digest``.

The returned dict is exactly ``{"tasks": total, "train": [...ids],
"test": [...ids], "reserved": [...ids]}`` -- task-id lists only, never
Task objects, so callers can JSON-serialise it (the pipeline writes it
straight into its ledger; it reads only ``train`` and ``test``). A stage
that needs the Task objects rebuilds them from these ids through
``blocks.tasks.load_tasks``, which is deterministic from the frozen config.
"""

from __future__ import annotations

import dataclasses
import random
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from expedistill import prompts
from expedistill.benchmarks.base import Task
from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks import rules as rules_block
from expedistill.blocks import tasks as tasks_block
from expedistill.blocks.tasks import tasks_digest
from expedistill.core.config import METHOD_SLOTS
from expedistill.methods import resolve_params
from expedistill.infra.manifest import freeze
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import write_json
from expedistill.stages.context import StageContext

__all__ = ["run"]

_SUMMARY_NAME = "stage0_summary.json"

#: Rule key -> registry lookup that raises ConfigError for an unknown name.
_RULE_GETTERS = {
    "task_select": rules_block.get_task_select,
    "segment_select": rules_block.get_segment_select,
    "trajectory_select": rules_block.get_trajectory_select,
}

#: The memory address in a default ``object.__repr__`` ("<X object at
#: 0x10f...>"): stripped so an opaque meta value hashes the same per run.
_ADDRESS_RE = re.compile(r" at 0x[0-9a-fA-F]+")


def _two_way_train_count(n: int, ratio: float) -> int:
    """Largest-remainder split of ``n`` items into train (``ratio``) + test.

    Two buckets whose raw sizes sum to exactly ``n``, so at most one item is
    left after flooring; it goes to the bucket with the larger fractional
    part (ties to train). Same discipline as the old repo's
    ``evaluation/splits.py`` apportionment, collapsed to the two-bucket case
    this design needs.
    """
    train_count = int(n * ratio)
    if n * ratio - train_count >= 0.5:
        train_count += 1
    return train_count


def _derive_split_spec(task_ids: List[str],
                       ratio_spec: Dict[str, Any]) -> Dict[str, List[str]]:
    """Turn ``{"ratio": r, "seed": s}`` into explicit frozen lists.

    Deterministic by construction: sorted ids, one ``random.Random(seed)``
    shuffle, first ``train_count`` ids are train. Running the same config on
    the same task set always yields the same lists, which is what freezing
    them in the manifest promises.
    """
    ratio = ratio_spec["ratio"]
    seed = ratio_spec.get("seed", 0)
    ids = sorted(task_ids)
    random.Random(seed).shuffle(ids)
    train_count = _two_way_train_count(len(ids), ratio)
    return {
        "train": ids[:train_count],
        "test": ids[train_count:],
    }


def _explicit_split_ids(config: Any) -> Optional[List[str]]:
    """Sorted union of the explicit split lists (``reserved`` included:
    the held-back tasks are loaded even though neither arm runs them), or
    None if there are no explicit lists."""
    if (config.mode == "split" and isinstance(config.split, dict)
            and "train" in config.split):
        return sorted(set(config.split.get("train") or [])
                      | set(config.split.get("test") or [])
                      | set(config.split.get("reserved") or []))
    return None


def _tasks_spec(config: Any) -> Union[None, str, List[str]]:
    """What D1 should load: ``config.tasks`` if given, else the union of
    explicit split lists, else the plugin's full set (None)."""
    if config.tasks is not None:
        return config.tasks
    return _explicit_split_ids(config)


def _validate_rules(rule_names: Dict[str, str]) -> None:
    """Resolve every named rule now; an unknown name is a ConfigError."""
    for key, getter in _RULE_GETTERS.items():
        getter(rule_names[key])


def _check_unique_ids(tasks: Sequence[Task]) -> None:
    """Every loaded task id must be unique.

    The config rejects duplicates in a ``tasks`` list, but a task file is
    plugin-interpreted; a repeated id there would double-count the task in
    full mode and make a ratio split fail (or pass) depending on the seed.
    """
    seen: set = set()
    duplicates: set = set()
    for task in tasks:
        (duplicates if task.task_id in seen else seen).add(task.task_id)
    if duplicates:
        raise ConfigError(
            f"the loaded task set lists duplicate task ids: "
            f"{sorted(duplicates)}")


def _check_split_covers_task_set(config: Any, tasks: Sequence[Task]) -> None:
    """With both ``config.tasks`` and explicit lists, they must agree."""
    listed = _explicit_split_ids(config)
    if config.tasks is None or listed is None:
        return
    loaded = {task.task_id for task in tasks}
    not_in_set = sorted(set(listed) - loaded)
    unlisted = sorted(loaded - set(listed))
    if not_in_set or unlisted:
        raise ConfigError(
            "split train/test lists must cover the configured tasks "
            f"exactly; listed but not in tasks: {not_in_set}; in tasks but "
            f"not listed: {unlisted}")


def _prompt_digests(benchmark: str) -> Dict[str, Optional[str]]:
    """EVERY registered method prompt plus exactly this benchmark's task
    prompt -- the v3 full freeze (the plan's section 5).

    Must run after D1 imported the plugin package (its prompt registers at
    import). An unregistered task prompt is frozen as None. The two live
    prompts are always in the set now: they register at import of
    ``prompts/__init__`` like every other method prompt, and the static
    tests pin that every prompts/*.py module is in that import list --
    so the frozen surface can never miss a registered prompt. The cost is
    noise, not risk: a manifest lists digests this run may never call.
    """
    names = set(prompts.METHOD_PROMPT_NAMES)
    names.add(get_plugin(benchmark).task_prompt_name)
    return {
        name: (prompts.digest(name) if name in prompts.PROMPTS else None)
        for name in sorted(names)
    }


def build_manifest(config: Any, tasks: Sequence[Any], *,
                   train_ids: Sequence[str], test_ids: Sequence[str],
                   reserved_ids: Sequence[str]) -> Dict[str, Any]:
    """The frozen manifest payload (B4 ``freeze``'s input), pure: the same
    config, tasks and split ids give the same manifest bytes. Shared with
    the reuse assembler, which builds the PROSPECTIVE run's manifest to
    diff a source run against before assembling (ruling 4's whitelist
    walk) -- one assembly, so the two can never drift apart.
    """
    prompt_digests = _prompt_digests(config.benchmark)
    frozen_config = dataclasses.replace(
        config, prompts_digests=prompt_digests)
    return {
        "benchmark": config.benchmark,
        "mode": config.mode,
        # All three keys, always (ruling 7): downstream readers never have
        # to branch on which form the source config used.
        "split": {"train": list(train_ids), "test": list(test_ids),
                  "reserved": list(reserved_ids)},
        "config": frozen_config.to_dict(),
        "prompt_digests": prompt_digests,
        "rules": config.rules,
        "model_roles": config.models,
        "tasks_digest": tasks_digest(tasks),
        # The plugin-declared auxiliary metric's name (module docstring):
        # pre-registered here, before any result exists.
        "aux_metric": get_plugin(config.benchmark).aux_metric_name,
    }


def run(ctx: StageContext) -> Dict[str, Any]:
    """Validate rules, load and split the task set, freeze, write summary.

    Returns ``{"tasks": total, "train": [...ids], "test": [...ids],
    "reserved": [...ids]}`` (raw task ids, JSON-safe; ``reserved`` is
    ``[]`` for a two-key spec) and writes the same dict to
    ``stage0_summary.json`` next to the manifest.
    """
    config = ctx.config
    _validate_rules(config.rules)
    # Every method slot resolves here, fail-early: a bad parameter or an
    # unregistered method name stops the run before any task is loaded or
    # executed (the live method's check also re-validates its improvement
    # rule name -- the v2 spot check moved into its owning method).
    for slot in METHOD_SLOTS:
        resolve_params(slot, config)
    # A run frozen under schema v2 can never resume under v3 code: the
    # frozen key sets differ by design. Point the operator at the
    # maintenance branch instead of letting the generic manifest-diff
    # refusal bury the reason.
    manifest_path = Path(ctx.run_dir) / "manifest.json"
    if manifest_path.exists():
        try:
            frozen_version = json.loads(
                manifest_path.read_text(encoding="utf-8")
            ).get("config", {}).get("schema_version")
        except (OSError, ValueError):
            frozen_version = None
        if frozen_version == 2:
            raise ConfigError(
                "this run was frozen under config schema v2; schema v3 "
                "code cannot resume it -- continue it on its maintenance "
                "branch (the run's own checkout, AGENTS section 6), or "
                "assemble a new run from it")
    tasks = tasks_block.load_tasks(config.benchmark, _tasks_spec(config))
    _check_unique_ids(tasks)
    _check_split_covers_task_set(config, tasks)

    split_spec = config.split
    if (config.mode == "split" and isinstance(split_spec, dict)
            and "ratio" in split_spec):
        split_spec = _derive_split_spec(
            [task.task_id for task in tasks], split_spec)
    train_tasks, test_tasks, reserved_tasks = tasks_block.split_tasks(
        tasks, config.mode, split_spec)

    train_ids = [task.task_id for task in train_tasks]
    test_ids = [task.task_id for task in test_tasks]
    reserved_ids = [task.task_id for task in reserved_tasks]
    freeze(ctx.run_dir, build_manifest(
        config, tasks, train_ids=train_ids, test_ids=test_ids,
        reserved_ids=reserved_ids))

    summary = {"tasks": len(tasks), "train": train_ids, "test": test_ids,
               "reserved": reserved_ids}
    write_json(ctx.run_dir / _SUMMARY_NAME, summary, indent=2)
    return summary
