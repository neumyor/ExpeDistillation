"""Stage 0: load tasks, derive the split, freeze the run manifest.

Wiring only (layer 6): D1 loads the tasks, D2 divides them, B4 freezes the
result. The one derivation this stage owns is turning a ratio+seed split
config into explicit frozen lists before D2 sees it -- D2's contract is that
it receives frozen data, never a procedure (blocks/tasks.py docstring), and
that derivation belongs to the run start by design, not to a block.

Order of checks: the named rules are validated against the rule registries
first (``blocks/rules.py``; an unknown name is a ConfigError), before any
task is loaded or anything is written, so a typo in ``rules.segment_select``
stops the run here instead of after the baseline's executions. ``config.rules``
is complete by construction (core.config fills ``DEFAULT_RULES``), so the
names frozen below are the effective ones.

Task set: D1 loads exactly ``config.tasks`` (None = the plugin's full set,
which a plugin may reject -- TB does). With explicit split lists and no
``config.tasks``, the union of the four lists is loaded;
with both given, the union must equal the loaded set exactly (ConfigError
naming the ids on either side otherwise). A loaded set with a repeated id
(e.g. a task file naming one twice) is a ConfigError listing the ids,
checked before any split derivation. A ratio split is derived from the
loaded set.

The frozen split carries four lists: ``train``, ``val``, ``test`` and
``reserved``. ``val`` is what a practice phase may run instead of train;
``reserved`` is the partition held back from every phase (the TB
frozen-split reuse experiment): it is loaded -- the split lists must cover
the loaded task set exactly -- but never run; it exists so a dev smoke can
run held-out tasks without touching the measurement. A spec without
``val`` or ``reserved`` (and every ratio split) freezes them as ``[]``, so
every manifest and summary downstream can rely on all four keys being
present.

What the manifest freezes (B4 compares field by field on every start):
``benchmark``, the explicit ``split`` id lists (all four keys),
the whole config
(``config``: every ExperimentConfig field, including ``tasks``, the
completed ``rules`` and the prompt digests), ``prompt_digests``,
``prompt_pack`` (the benchmark pack's vocabulary, protocol, playbook seed
and overrides), ``rules``, ``model_roles``, ``tasks_digest`` and
``aux_metric``. ``aux_metric`` is the
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
``task_prompt_name``, its pack's ``executor_prompts`` and override targets,
never whatever other plugins a process happened to import. A task prompt that is not registered in B2 is frozen as ``None``;
real plugins register theirs at import, so only test fakes hit that case
(and a later registration still trips the comparison). Since schema v3
the freeze is FULL: every registered method prompt's digest is frozen on
every run, so the name set is the same for every run; a manifest listing
digests this run never calls is noise, not risk. Two static tests (tests/unit/prompts/
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
"val": [...ids], "test": [...ids], "reserved": [...ids]}`` -- task-id lists
only, never Task objects, so callers can JSON-serialise it (the pipeline
writes it straight into its ledger and hands each execute phase the list
its ``tasks`` names). A stage
that needs the Task objects rebuilds them from these ids through
``blocks.tasks.load_tasks``, which is deterministic from the frozen config.
"""

from __future__ import annotations

import dataclasses
import random
import string
from typing import Any, Dict, List, Optional, Sequence, Union

from expedistill import prompts
from expedistill.benchmarks.base import Task
from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks import rules as rules_block
from expedistill.blocks import tasks as tasks_block
from expedistill.blocks.tasks import tasks_digest
from expedistill.composites import external_control
from expedistill.composites.evolving_text import check_playbook_capability
from expedistill.composites.verdicts import get_verdict_rule
from expedistill.core.config import (
    SPLIT_LIST_KEYS,
    ExecutePhaseSpec,
    LearnPhaseSpec,
    split_lists,
)
from expedistill.methods.registry import resolve_params
from expedistill.infra.manifest import freeze
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import write_json
from expedistill.stages.context import StageContext

__all__ = ["run"]

_SUMMARY_NAME = "stage0_summary.json"

#: Rule key -> registry lookup that raises ConfigError for an unknown name.
_RULE_GETTERS = {
    "segment_select": rules_block.get_segment_select,
    "trajectory_select": rules_block.get_trajectory_select,
}

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
    """Sorted union of the four explicit split lists (``reserved``
    included: the held-back tasks are loaded even though no phase runs
    them), or None if there are no explicit lists."""
    if isinstance(config.split, dict) and "train" in config.split:
        listed: set = set()
        for ids in split_lists(config.split).values():
            listed.update(ids)
        return sorted(listed)
    return None


def tasks_spec(config: Any) -> Union[None, str, List[str]]:
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
    plugin-interpreted; a repeated id there would make a ratio split fail
    (or pass) depending on the seed.
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
            "the split lists must cover the configured tasks "
            f"exactly; listed but not in tasks: {not_in_set}; in tasks but "
            f"not listed: {unlisted}")


def _prompt_digests(benchmark: str) -> Dict[str, Optional[str]]:
    """EVERY registered method prompt plus exactly this benchmark's task
    prompt -- the v3 full freeze (the plan's section 5).

    Must run after D1 imported the plugin package (its prompt registers at
    import). An unregistered task prompt is frozen as None. Every method
    prompt registers at import of ``prompts/__init__``, and the static
    tests pin that every prompts/*.py module is in that import list --
    so the frozen surface can never miss a registered prompt. The cost is
    noise, not risk: a manifest lists digests this run may never call.

    A plugin that declares the skill-folder capability names its folder
    prompt through ``skill_folder_prompt_name`` (M3): that prompt is part
    of what a folder unit runs under, so its digest freezes too -- and
    an unregistered name is a ConfigError, not a None entry (a folder
    prompt is plugin code, not a test fake; the plugin's own task prompt
    keeps the None-on-unregistered contract for the fake-benchmarks).
    """
    plugin = get_plugin(benchmark)
    names = set(prompts.METHOD_PROMPT_NAMES)
    names.add(plugin.task_prompt_name)
    folder_prompt = plugin.skill_folder_prompt_name
    if folder_prompt is not None:
        if folder_prompt not in prompts.PROMPTS:
            raise ConfigError(
                f"benchmark {benchmark!r} declares skill_folder_prompt_name "
                f"{folder_prompt!r} but no prompt of that name is "
                "registered -- the folder prompt is part of the frozen "
                "protocol, so the plugin and its registration disagree")
        names.add(folder_prompt)
    pack = plugin.prompt_pack
    for extra in pack.executor_prompts:
        if extra not in prompts.PROMPTS:
            raise ConfigError(
                f"benchmark {benchmark!r} lists executor prompt {extra!r} "
                "in its prompt pack but no prompt of that name is "
                "registered -- the runner's prompts are part of the frozen "
                "protocol, so the pack and its registration disagree")
        names.add(extra)
    _check_overrides(benchmark, pack.overrides)
    names.update(pack.overrides.values())
    if pack.grader_report_prompt is not None:
        _check_grader_report_prompt(benchmark, pack.grader_report_prompt)
        names.add(pack.grader_report_prompt)
    return {
        name: (prompts.digest(name) if name in prompts.PROMPTS else None)
        for name in sorted(names)
    }


def _placeholders(name: str) -> set:
    return {field for _, field, _, _ in
            string.Formatter().parse(prompts.get(name))
            if field is not None}


def _check_overrides(benchmark: str, overrides: Any) -> None:
    """Every override target is registered and fills exactly the
    placeholders of the method prompt it replaces, so a caller's variables
    fit either template."""
    for method_name, target in overrides.items():
        for name in (method_name, target):
            if name not in prompts.PROMPTS:
                raise ConfigError(
                    f"benchmark {benchmark!r} overrides {method_name!r} "
                    f"with {target!r}, but no prompt named {name!r} is "
                    "registered")
        expected = _placeholders(method_name)
        actual = _placeholders(target)
        if actual != expected:
            raise ConfigError(
                f"benchmark {benchmark!r} overrides {method_name!r} with "
                f"{target!r}, whose placeholders differ: missing "
                f"{sorted(expected - actual)}, extra "
                f"{sorted(actual - expected)}")


def _check_grader_report_prompt(benchmark: str, name: str) -> None:
    """The grader report section is registered and fills exactly one
    ``{report}`` slot, the only variable the diagnosis hands it."""
    if name not in prompts.PROMPTS:
        raise ConfigError(
            f"benchmark {benchmark!r} declares grader_report_prompt "
            f"{name!r} but no prompt of that name is registered")
    if _placeholders(name) != {"report"}:
        raise ConfigError(
            f"benchmark {benchmark!r} declares grader_report_prompt "
            f"{name!r}, whose placeholders are "
            f"{sorted(_placeholders(name))}; it must have exactly "
            "{report}")


def build_manifest(config: Any, tasks: Sequence[Any], *,
                   split: Dict[str, Sequence[str]]) -> Dict[str, Any]:
    """The frozen manifest payload (B4 ``freeze``'s input), pure: the same
    config, tasks and split ids give the same manifest bytes. ``split``
    maps each of ``SPLIT_LIST_KEYS`` to its task ids."""
    prompt_digests = _prompt_digests(config.benchmark)
    frozen_config = dataclasses.replace(
        config, prompts_digests=prompt_digests)
    return {
        "benchmark": config.benchmark,
        # All four keys, always: downstream readers never have to branch
        # on which form the source config used.
        "split": {name: list(split[name]) for name in SPLIT_LIST_KEYS},
        "config": frozen_config.to_dict(),
        "prompt_digests": prompt_digests,
        # The benchmark's prompt pack is protocol like the prompt text
        # itself: one changed word is a new experiment.
        "prompt_pack": _frozen_pack(get_plugin(config.benchmark).prompt_pack),
        "rules": config.rules,
        "model_roles": config.models,
        "tasks_digest": tasks_digest(tasks),
        # The plugin-declared auxiliary metric's name (module docstring):
        # pre-registered here, before any result exists.
        "aux_metric": get_plugin(config.benchmark).aux_metric_name,
    }


def _frozen_pack(pack: Any) -> Dict[str, Any]:
    """The manifest form of a prompt pack: its texts. The named prompts
    freeze through ``prompt_digests``; the facts and checks are code."""
    frozen = {
        "vocabulary": pack.vocabulary.to_dict(),
        "protocol": pack.protocol,
        "playbook_seed": pack.playbook_seed,
        "overrides": dict(pack.overrides),
    }
    # Written only when declared, so a pack without one (ALFWorld,
    # SearchQA) freezes the same bytes it did before the field existed.
    if pack.grader_report_prompt is not None:
        frozen["grader_report_prompt"] = pack.grader_report_prompt
    return frozen


def _resolve_phase_methods(config: Any) -> None:
    """Resolve every phase family's params, fail-early (R9): a bad
    parameter, an unregistered method name or an unknown named rule
    stops the run before any task is loaded or executed.

    Per-phase: the config carries its method mappings inside the phase
    definitions (an execute phase resolves delivery + execution and checks
    that the benchmark can show its playbook, a learn phase resolves
    review + author),
    and the named verdict rule an execute phase carries (``acceptance``)
    validates against its registry here (core cannot: layer 0 must not
    import composites). An ABSENT acceptance name is the benchmark's
    default verdict rule, resolved by the phase driver at run time --
    validating it here would refuse every test-plugin benchmark the
    verdict table has not heard of, and the benchmark registry itself
    has already been checked by now (``get_plugin`` in _prompt_digests).

    An ``external_campaign`` control's score file is verified here too
    (existence + the pinned sha256): the file is part of the frozen
    method's identity, so a missing or drifted file must stop the run
    at the freeze step, not after phases have spent. The task-set
    alignment and the reward format stay with the loader the test phase
    calls (they need the loaded tasks, which are read right after).
    """
    for name in config.sequence:
        spec = config.phases[name]
        if isinstance(spec, ExecutePhaseSpec):
            resolve_params("delivery", spec.delivery)
            resolve_params("execution", spec.execution)
            check_playbook_capability(spec, config.benchmark)
            if spec.acceptance is not None:
                get_verdict_rule(spec.acceptance)
            if spec.compare is not None \
                    and spec.compare.external is not None:
                external_control.verify_external_control_file(
                    spec.compare.external.path,
                    spec.compare.external.sha256)
        elif isinstance(spec, LearnPhaseSpec):
            resolve_params("review", spec.review)
            resolve_params("author", spec.author)


def run(ctx: StageContext) -> Dict[str, Any]:
    """Validate rules, load and split the task set, freeze, write summary.

    Returns ``{"tasks": total, "train": [...ids], "val": [...ids],
    "test": [...ids], "reserved": [...ids]}`` (raw task ids, JSON-safe; an
    optional list the spec leaves out is ``[]``) and writes the same dict
    to ``stage0_summary.json`` next to the manifest.
    """
    config = ctx.config
    _validate_rules(config.rules)
    _resolve_phase_methods(config)
    tasks = tasks_block.load_tasks(config.benchmark, tasks_spec(config))
    _check_unique_ids(tasks)
    _check_split_covers_task_set(config, tasks)

    split_spec = config.split
    if isinstance(split_spec, dict) and "ratio" in split_spec:
        split_spec = _derive_split_spec(
            [task.task_id for task in tasks], split_spec)
    divided = tasks_block.split_tasks(tasks, split_spec)
    split = {name: [task.task_id for task in part]
             for name, part in zip(SPLIT_LIST_KEYS, divided)}
    freeze(ctx.run_dir, build_manifest(config, tasks, split=split))

    summary = {"tasks": len(tasks), **split}
    write_json(ctx.run_dir / _SUMMARY_NAME, summary, indent=2)
    return summary
