"""Assemble a train-lineage reuse run from a finished source run.

The TB frozen-split reuse experiment (docs/decisions/
retrieval_delivery_tb_split_20261013.md) re-runs the pipeline over a NEW
frozen split (AgentBrew evolve/test/validation = train/test/reserved)
while reusing, with zero model calls, what the source run already
produced for the train side. This module copies an allowlist from the
source run into a fresh run directory, guarded by an identity check and
negative-list assertions, and writes an ASSEMBLED_FROM.md provenance
record at the run root.

Two levels (revision 3, the 2026-10-13 user ruling):

* ``bank`` (default): the baseline-phase trajectories, the segmentation,
  authoring and acceptance objects, and the curated bank filtered to
  train lineage. The new config may differ from the source run's frozen
  manifest ONLY in {mode, split, tasks, schema_version} and the
  final-test phase's delivery, compare and execution name (ruling 4);
  every other key must match, because the reused artifacts were produced
  under those exact inputs. The prompt digests are the declared
  exception: the new run freezes the current registry's digests without
  ever calling them, so their drift is disclosed, never refused
  (ruling 14).
* ``baseline``: the baseline phase's train-side trajectories and the accepted
  stage-1 infra shortfall ONLY; every downstream phase runs fresh. The
  identity check pins only what trajectory reproducibility needs:
  {benchmark, both executor models, both attempt counts, train within
  the source task set}.

Invariants:

* Allowlist copy (ruling 5): only the listed entries are copied, and a
  negative-list assertion verifies the assembled directory holds none of
  the source run's measurements or resume journals (manifest, ledgers,
  summaries, the curation journal, the final-test stores, phase
  records, ...).
* Full-chain lineage (ruling 1 / contract 10): a skill is train-side iff
  its own subtask and every original reached through ``merged_from`` are
  train (:func:`skill_lineage_tasks`). A bank row is kept iff its
  content-identical stored skill is kept, so the bank filter and the
  skill copy set cannot disagree.
* The store's accepted set equals the kept bank's accepted row ids. A v4
  source's final artifact may also carry pending members, which ride
  along disclosed by count. The segmentation audit is copied whole
  (ruling 5's lazy-audit exemption).

Schema worlds (R10): the source may be a v2/v3 stage-world run (bank at
the run root) or a v4 phase-world run (bank = the last learn phase's
artifact). An older source config is upgraded through the pure core
mappings, and both sides of the identity walk are normalised through
``ExperimentConfig.from_dict`` -> ``to_dict`` so they compare
isomorphically. Skills and exams land at the CURRENT object schema
(migrated on copy); the other kinds are byte-exact copies. The assembled
run is always v4: the filtered bank lands at the run root and is
pre-seeded as the last learn phase's bank artifact, so that phase's
curation short-circuits with zero model calls. The v3 stage-3
generation-failure ledger has no v4 image (a failed add is retryable in
v4): its train-side rows are counted and disclosed, not copied.

The assembled run is not a finished run: stage 0 freezes its own
manifest on the first pipeline start (the identity re-check of last
resort), the baseline phase skips the stored trajectories, and the test phase runs
fresh. The run directory is exclusively locked for the assembly; a
failed assembly is safe to re-run (object files are overwritten, the
ledgers are written atomically in full).
"""

from __future__ import annotations

import contextlib
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Set

from expedistill.composites import shortfall
from expedistill.composites.phase_bank import BANK_NAME, load_phase_bank, phase_bank_dir
from expedistill.composites.segment_checked import AUDIT_PATH
from expedistill.blocks import tasks as tasks_block
from expedistill.stages.stage0_freeze import build_manifest, tasks_spec
from expedistill.core.config import (
    ExecutePhaseSpec,
    LearnPhaseSpec,
    PHASE_ROLE_TEST,
    ExperimentConfig,
    load_config,
    phase_label,
    upgrade_v2_config,
    upgrade_v3_config,
    upgrade_v4_config,
    upgrade_v5_config,
)
from expedistill.core.ids import make_task_id, parse_trajectory_id
from expedistill.core.models import (
    Attempt,
    Exam,
    Skill,
    Subtask,
    Trajectory,
    migrate_skill_record,
    migrate_skill_rows,
)
from expedistill.infra.manifest import MANIFEST_NAME, differing_keys
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import RunLock
from expedistill.infra.store import (
    KIND_DIRS,
    object_filename,
    object_id_of_file,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)

__all__ = ["LEVELS", "assemble_reuse", "skill_lineage_tasks"]

#: The two reuse levels (module docstring).
LEVELS = ("bank", "baseline")

#: Ruling 4: the manifest/config keys a bank-level reuse config may change.
#: The final-test phase's keys are added per target config by
#: :func:`_allowed_diff_prefixes`, because the phase's name is the
#: config's own choice.
_BASE_ALLOWED_DIFF_PREFIXES = (
    "mode", "config.mode",
    "split", "config.split",
    "tasks", "config.tasks",
    "schema_version", "config.schema_version",
)

#: The prompt-digest snapshots stay out of the identity walk; their drift
#: is disclosed per prompt name instead (module docstring, ruling 14).
_EXCLUDED_DIFF_KEYS = frozenset({"prompt_digests"})
_EXCLUDED_CONFIG_KEYS = frozenset({"prompts_digests"})

#: Ruling 5: none of these may exist in the assembled run. Each is either
#: a measurement of the source run or a resume journal whose serials do
#: not survive the train-side filter. The phase world kept these run-root
#: and store paths, so the stage-world names are still the ones to forbid;
#: the phases/ tree has its own exact-shape assertion.
_FORBIDDEN_PATHS = (
    MANIFEST_NAME,
    "pipeline_log.jsonl",
    "failed_units.jsonl",
    "skip_audit.jsonl",
    "stage0_summary.json",
    "stage1_summary.json",
    "stage2_summary.json",
    "stage3_generation/stage3_summary.json",
    "stage4_acceptance/stage4_summary.json",
    "stage5_curation/stage5_summary.json",
    "stage5_curation/curation_log.json",
    "stage4_acceptance/leak_verdicts.jsonl",
    "stage4_acceptance/rewrite_failures.jsonl",
)
_FORBIDDEN_DIRS = ("llm_requests", "stage6_test", "work")

#: Baseline only: the bank level writes these on purpose, the baseline
#: level copies no downstream product, so they must be absent and the
#: downstream store kinds must hold no objects.
_BASELINE_FORBIDDEN_PATHS = (
    "skill_bank.jsonl",
    "stage2_segments/segmentation_audit.jsonl",
    "stage3_generation/generation_failures.jsonl",
)
_BASELINE_FORBIDDEN_KINDS = ("subtasks", "skills", "exams", "attempts")

_ASSEMBLED_FROM_NAME = "ASSEMBLED_FROM.md"
_SKILL_SUFFIX = "/skill"


# ---------------------------------------------------------------------------
# Lineage (contract 10): the single source of "which tasks is this skill from"
# ---------------------------------------------------------------------------

def skill_lineage_tasks(
    skill: Skill,
    subtasks_by_id: Mapping[str, Subtask],
    *,
    skills_by_id: Optional[Mapping[str, Skill]] = None,
) -> Set[str]:
    """Full-chain lineage task ids of ``skill`` (contract 10).

    The lineage is the task set of every original the skill stands for:
    the task of its own ``subtask_id`` plus, recursively, the lineage of
    every skill named in ``merged_from`` (a merge carries only its
    anchor's subtask id, so the anchor-only view of a merge would hide a
    validation-sourced member -- ruling 1's two leaked merges). Task ids
    are the STORED form (what the source run's Subtask records carry, e.g.
    ``"tb:dna-insert"``), so callers compare against the stored form of
    their split lists, not the raw config names.

    ``subtasks_by_id`` must cover the anchor subtask of every skill passed
    in (a missing anchor is source corruption and raises ConfigError).
    ``merged_from`` entries resolve through ``skills_by_id`` when given;
    without it, an ORIGINAL skill id (``<subtask_id>/skill``, the
    core.ids rule) resolves through ``subtasks_by_id`` directly, which is
    enough for any bank without nested merges (a merge consuming another
    merge). Anything else without ``skills_by_id`` is a loud ConfigError,
    never a silent empty lineage.
    """
    tasks: Set[str] = set()
    seen: Set[str] = set()
    stack: List[Skill] = [skill]
    while stack:
        current = stack.pop()
        if current.skill_id in seen:
            continue
        seen.add(current.skill_id)
        subtask = subtasks_by_id.get(current.subtask_id)
        if subtask is None:
            raise ConfigError(
                f"skill {current.skill_id} names anchor subtask "
                f"{current.subtask_id}, which the source run does not "
                "store; the lineage is unverifiable and the source is "
                "corrupt")
        tasks.add(subtask.task_id)
        for source_id in current.merged_from:
            source = None if skills_by_id is None \
                else skills_by_id.get(source_id)
            if source is not None:
                stack.append(source)
            elif source_id.endswith(_SKILL_SUFFIX):
                original = subtasks_by_id.get(
                    source_id[:-len(_SKILL_SUFFIX)])
                if original is None:
                    raise ConfigError(
                        f"skill {current.skill_id} names merged source "
                        f"{source_id}, whose subtask the source run does "
                        "not store; the lineage is unverifiable and the "
                        "source is corrupt")
                tasks.add(original.task_id)
            else:
                raise ConfigError(
                    f"cannot resolve merged source {source_id!r} of skill "
                    f"{current.skill_id}: it is a merged-skill id, so pass "
                    "skills_by_id covering it (the assembly always does)")
    return tasks


# ---------------------------------------------------------------------------
# Bank-level identity walk (ruling 4)
# ---------------------------------------------------------------------------

def _allowed_diff_prefixes(config: ExperimentConfig) -> tuple:
    """Ruling 4's whitelist for one target config: the shape-independent
    keys plus the final-test phase's delivery, compare and execution name.

    The execution name is the delivery's forced twin (core's load-time
    pairing check: ``folder`` pairs with ``real_folder``, every other
    delivery with ``real``), so it moves whenever the delivery does. Only
    the name leaf is whitelisted: the other execution leaves change what
    is measured.
    """
    prefixes = list(_BASE_ALLOWED_DIFF_PREFIXES)
    for name in config.sequence:
        spec = config.phases[name]
        if isinstance(spec, ExecutePhaseSpec) and spec.role == PHASE_ROLE_TEST:
            prefixes.append(f"config.phases.{name}.delivery")
            prefixes.append(f"config.phases.{name}.compare")
            prefixes.append(f"config.phases.{name}.execution.name")
    return tuple(prefixes)


def _path_allowed(path: str, prefixes: Sequence[str]) -> bool:
    return any(path == prefix or path.startswith(prefix + ".")
               for prefix in prefixes)


def _section(path: str, prefixes: Sequence[str]) -> str:
    """Collapse one allowed diff path for the summary: a final-test phase
    key keeps its full prefix (it names which knob drifted), everything
    else collapses to its first key under config, or its top key."""
    for prefix in prefixes:
        if prefix.startswith("config.phases.") and _path_allowed(
                path, [prefix]):
            return prefix
    parts = path.split(".")
    return "config." + parts[1] if parts[0] == "config" else parts[0]


def _last_learn_label(config: ExperimentConfig) -> Optional[str]:
    """Label of the sequence's last learn phase, or None without one."""
    positions = [index for index, name in enumerate(config.sequence)
                 if isinstance(config.phases[name], LearnPhaseSpec)]
    if not positions:
        return None
    return phase_label(positions[-1], config.sequence[positions[-1]])


def _source_config(source_manifest: Mapping[str, Any],
                   source_run: Path) -> "tuple[ExperimentConfig, int]":
    """The source run's frozen config parsed as v5 (upgraded through the
    pure core mappings when frozen under an older schema), plus the
    original schema version."""
    raw = source_manifest.get("config")
    if not isinstance(raw, dict):
        raise ConfigError(
            f"the source run {source_run} has a manifest without a "
            "frozen config; only a run frozen by stage 0 can be reused")
    version = raw.get("schema_version", 3)
    if not isinstance(version, int) or isinstance(version, bool) \
            or version not in (2, 3, 4, 5, 6):
        raise ConfigError(
            f"the source run {source_run} froze config schema v{version!r}; "
            "only schema v2 to v6 source runs can be reused (the older "
            "ones chain through upgrade_v2_config, upgrade_v3_config, "
            "upgrade_v4_config and upgrade_v5_config)")
    try:
        if version == 2:
            raw = upgrade_v2_config(raw)
        if version in (2, 3):
            raw = upgrade_v3_config(raw)
        if version in (2, 3, 4):
            raw = upgrade_v4_config(raw)
        if version in (2, 3, 4, 5):
            raw = upgrade_v5_config(raw)
        return ExperimentConfig.from_dict(raw), version
    except ValueError as exc:
        raise ConfigError(
            f"the source run {source_run} has a config that cannot be "
            f"upgraded to schema v6 for the identity walk: {exc}; the "
            "source is corrupt or of an unsupported shape") from exc


def _source_bank_rows(source_run: Path, source_config: ExperimentConfig,
                      source_version: int) -> List[Dict[str, Any]]:
    """The source run's curated bank rows: the last learn phase's
    artifact for a v4 source, the run-root file for an older one. A
    missing bank is a refusal, never an empty read."""
    if source_version >= 4:
        label = _last_learn_label(source_config)
        if label is None:
            raise ConfigError(
                f"the source run {source_run} has no learn phase, so it "
                "never produced a bank artifact; bank-level reuse has "
                "nothing to reuse")
        return load_phase_bank(source_run, label)
    path = source_run / "skill_bank.jsonl"
    if not path.is_file():
        raise ConfigError(
            f"the source run {source_run} has no run-level "
            "skill_bank.jsonl; only a finished stage-world run can be "
            "reused at the bank level")
    return read_jsonl(path)


def _identity_view(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """The manifest without the prompt-digest snapshots."""
    view = {key: value for key, value in manifest.items()
            if key not in _EXCLUDED_DIFF_KEYS}
    config = view.get("config")
    if isinstance(config, dict):
        view["config"] = {key: value for key, value in config.items()
                          if key not in _EXCLUDED_CONFIG_KEYS}
    return view


# ---------------------------------------------------------------------------
# Store reading and copying (the source is read through plain files: a Store
# instance would create directory skeletons in it)
# ---------------------------------------------------------------------------

def _source_objects(source_run: Path,
                    kind: str) -> Dict[str, Dict[str, Any]]:
    """Every stored object of ``kind`` as raw parsed JSON, by decoded id."""
    directory = source_run / KIND_DIRS[kind]
    if not directory.is_dir():
        return {}
    return {object_id_of_file(path): read_json(path)
            for path in sorted(directory.glob("*.json"))}


def _copy_kind(source_run: Path, run_dir: Path, kind: str,
               kept: Mapping[str, Any]) -> int:
    """Byte-exact copy of every kept object of a kind whose schema did not
    move (trajectories, subtasks, attempts); returns the count."""
    directory = run_dir / KIND_DIRS[kind]
    directory.mkdir(parents=True, exist_ok=True)
    for obj_id in kept:
        shutil.copy2(source_run / KIND_DIRS[kind] / object_filename(obj_id),
                     directory / object_filename(obj_id))
    return len(kept)


def _write_migrated_kind(run_dir: Path, kind: str,
                         kept: Mapping[str, Any]) -> int:
    """Write every kept (already migrated) skill or exam at the current
    object schema, exactly as the store writes a record; returns the
    count."""
    directory = run_dir / KIND_DIRS[kind]
    directory.mkdir(parents=True, exist_ok=True)
    for obj_id, obj in kept.items():
        write_json(directory / object_filename(obj_id), obj.to_dict(),
                   indent=2)
    return len(kept)


def _load_shortfall_units(source_run: Path,
                          train_ids: Set[str]) -> List[str]:
    """Stage 1's accepted infra holes, train side only: a hole on a task
    the new run never dispatches is inert, and copying it would carry
    test/reserved lineage. A missing record means no accepted holes."""
    units: List[str] = []
    for unit_id in shortfall.stage1_accepted_unit_ids(source_run):
        try:
            unit_task = parse_trajectory_id(unit_id)[0]
        except ValueError:
            raise ConfigError(
                f"accepted_infra_shortfall names a non-trajectory unit "
                f"id {unit_id!r}; the source is corrupt") from None
        if unit_task in train_ids:
            units.append(unit_id)
    return units


def _write_shortfall(run_dir: Path, units: Sequence[str]) -> int:
    if units:
        shortfall.write_unit_ids(shortfall.stage1_shortfall_path(run_dir),
                                 units)
    return len(units)


def _campaign_seeded(kept_trajectories: Mapping[str, Trajectory]
                     ) -> Dict[str, Any]:
    """Campaign-seeding provenance of the copied trajectories."""
    seeded = {"count": 0, "origin_runs": {}}
    for obj in kept_trajectories.values():
        meta = obj.meta or {}
        if "campaign_origin_run" in meta:
            seeded["count"] += 1
            origin = str(meta["campaign_origin_run"])
            seeded["origin_runs"][origin] = \
                seeded["origin_runs"].get(origin, 0) + 1
    return seeded


@contextlib.contextmanager
def _locked_fresh_run_dir(run_dir: Path) -> Iterator[None]:
    """Hold the exclusive run lock on a run directory without a manifest.

    The manifest check runs again under the lock: a concurrent process may
    have frozen one between the first check and the acquisition.
    """
    message = (f"{run_dir} already contains {MANIFEST_NAME}; assemble into "
               "a fresh run directory (the pipeline freezes the manifest on "
               "its first start -- never before)")
    if (run_dir / MANIFEST_NAME).exists():
        raise ConfigError(message)
    lock = RunLock(run_dir)
    warning = lock.acquire()
    try:
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        if (run_dir / MANIFEST_NAME).exists():
            raise ConfigError(message)
        yield
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Assertions over the assembled directory
# ---------------------------------------------------------------------------

def _assert_negative_list(run_dir: Path, *, baseline: bool) -> None:
    """Ruling 5's negative list, plus the baseline-only entries."""
    present = [name for name in _FORBIDDEN_PATHS
               if (run_dir / name).exists()]
    present += [name + "/" for name in _FORBIDDEN_DIRS
                if (run_dir / name).is_dir()]
    if baseline:
        present += [name for name in _BASELINE_FORBIDDEN_PATHS
                    if (run_dir / name).exists()]
        for kind in _BASELINE_FORBIDDEN_KINDS:
            directory = run_dir / KIND_DIRS[kind]
            if directory.is_dir() and any(directory.iterdir()):
                present.append(KIND_DIRS[kind] + "/")
    if present:
        raise ConfigError(
            f"the assembled run contains file(s) the assembly must "
            f"never create: {present}; either the run_dir was not "
            "fresh or the assembly copied out of its allowlist -- a "
            "human must look at this directory")


def _assert_phase_tree(run_dir: Path,
                       preseed_label: Optional[str]) -> None:
    """The phases/ tree holds exactly the pre-seeded bank artifact, or
    does not exist when nothing is pre-seeded. Every other phases/ entry
    of a v4 source is a measurement or resume journal of that source."""
    phases_dir = run_dir / "phases"
    if preseed_label is None:
        if phases_dir.exists():
            raise ConfigError(
                f"the assembled run contains a phases/ tree the assembly "
                f"must never create (the target sequence has no learn "
                f"phase, so nothing is pre-seeded): "
                f"{sorted(p.as_posix() for p in phases_dir.rglob('*'))}; "
                "the run_dir was not fresh -- a human must look at this "
                "directory")
        return
    expected = phase_bank_dir(run_dir, preseed_label) / BANK_NAME
    expected_rel = expected.relative_to(run_dir).as_posix()
    actual = sorted(
        path.relative_to(run_dir).as_posix()
        for path in phases_dir.rglob("*") if path.is_file())
    if actual != [expected_rel]:
        raise ConfigError(
            f"the assembled run's phases/ tree must hold exactly "
            f"{[expected_rel]}, found {actual}; either the run_dir was "
            "not fresh or the assembly copied a source phase record -- "
            "a human must look at this directory")


# ---------------------------------------------------------------------------
# ASSEMBLED_FROM.md
# ---------------------------------------------------------------------------

def _merged_original_groups(
        source_skills: Mapping[str, Skill],
        copied_skill_ids: Set[str],
        kept_bank_ids: Set[str]) -> Dict[str, Any]:
    """Train-side merged originals, split by whether a kept merge still
    consumes them (ruling 1). An original whose every consuming merge was
    filtered out dangles in the new store: status "merged" with nothing
    left that carries its content on."""
    consumed_by_kept: Set[str] = set()
    for skill_id in kept_bank_ids:
        # Every original reachable through the kept rows' merged_from
        # chains (a kept row's whole chain is train-side by construction).
        stack = list(source_skills[skill_id].merged_from)
        seen: Set[str] = set()
        while stack:
            source_id = stack.pop()
            if source_id in seen:
                continue
            seen.add(source_id)
            source = source_skills.get(source_id)
            if source is None:
                continue
            if source.merged_from:
                stack.extend(source.merged_from)
            else:
                consumed_by_kept.add(source_id)
    merged_originals = sorted(
        skill_id for skill_id, skill in source_skills.items()
        if skill.status == "merged" and skill_id in copied_skill_ids)
    dangling = sorted(
        skill_id for skill_id in merged_originals
        if skill_id not in consumed_by_kept)
    return {
        "train_merged_originals": len(merged_originals),
        "consumed_by_kept_merges": len(merged_originals) - len(dangling),
        "dangling": dangling,
    }


def _header_lines(source_run: Path, source_manifest: Mapping[str, Any],
                  config_path: Path, config: ExperimentConfig,
                  run_dir: Path, split_sizes: Mapping[str, int]) -> List[str]:
    return [
        f"- 源 run：`{source_run}`（benchmark "
        f"{source_manifest['benchmark']!r}，mode "
        f"{source_manifest['mode']!r}，tasks_digest "
        f"{str(source_manifest.get('tasks_digest'))[:12]}…）",
        f"- 复用配置：`{config_path}`（mode {config.mode}；"
        f"train/test/reserved = {split_sizes['train']}/"
        f"{split_sizes['test']}/{split_sizes['reserved']}）",
        f"- 装配目标：`{run_dir}`",
        "- 知情声明：tasks_digest 只覆盖任务的 id、instruction 与 meta，"
        "**远端任务目录的内容漂移不在其覆盖范围**；复用假设远端环境与"
        "源 run 运行时一致。",
    ]


def _campaign_lines(campaign_seeded: Mapping[str, Any],
                    total: int) -> List[str]:
    seeded = campaign_seeded["count"]
    if seeded:
        lines = [
            f"- 拷贝的 {total} 条 train 双臂轨迹中 {seeded} 条携带 campaign "
            "播种溯源 meta（campaign 归档经 `scripts/run/seed_tb_stage1.py` "
            "播种）；拷贝逐字节保留各轨迹 meta 的 campaign_* 字段。"]
    else:
        lines = [
            f"- 拷贝的 {total} 条 train 双臂轨迹均无 campaign 播种溯源 "
            "meta（源 run 的 baseline 相位为直接执行）。"]
    origins = campaign_seeded.get("origin_runs") or {}
    if origins:
        lines.append("- 溯源 origin run 分布：" + "、".join(
            f"{name}（{count} 条）" for name, count
            in sorted(origins.items())) + "。")
    return lines


def _render_assembled_from(
        *,
        header: Sequence[str],
        allowed_diffs: Sequence[str],
        digest_drift: Sequence[str],
        counts: Mapping[str, Any],
        merged: Mapping[str, Any],
        campaign_seeded: Mapping[str, Any],
        source_version: int,
        preseed_label: Optional[str],
) -> str:
    """The bank-level ASSEMBLED_FROM.md text."""
    lines: List[str] = ["# 装配溯源（ASSEMBLED_FROM）", ""]
    add = lines.append
    lines.extend(header)
    add(f"- 源 run 的配置 schema：v{source_version}")
    add("- 方案与契约：`docs/decisions/retrieval_delivery_tb_split_20261013.md`"
        "（§0 裁决 1/4/5/6、§1.1–§1.4、§3.9 契约 6/7/10）。")
    add("")
    add("## 同一性校验（白名单 diff，裁决 4）")
    add("")
    add("- 源 manifest 的配置先经纯映射升到 v4 形状，两侧再经 from_dict → "
        "to_dict 规整为同一形状后逐键比对；仅允许 {mode, split, tasks, "
        "schema_version, 终测相位的 delivery、compare 与 execution.name} "
        "不同。其余任一键（benchmark、双 executor 模型、双 n_attempts、"
        "三条规则名、整个 sequence 与全部相位定义、"
        "invalid_redispatch_limit、token_budgets、model_options、"
        "weak_profile、tasks_digest）不一致即拒绝装配。")
    add(f"- 本次实际发生的允许差异：{', '.join(allowed_diffs) or '（无）'}。")
    add("- 混装声明：prompt digest 不在同一性校验内。新 run 按当前 prompt "
        "注册表冻结 digest，但复用部分零模型调用、这些 prompt 不会被执行；"
        "被拷贝的产物由源 run 在其冻结的 prompt 下产生。与源 run 相比 "
        f"digest 发生变化的 prompt：{', '.join(digest_drift) or '（无）'}。")
    add("")
    add("## 装配产物与续跑行为")
    add("")
    if preseed_label is not None:
        add(f"- 过滤后的库同时写入 run 根（skill_bank.jsonl）与末个 learn "
            f"相位的产物（`phases/{preseed_label.replace(':', '_')}/"
            "skill_bank.jsonl`）：末个 learn 相位的整理在非空产物上直接"
            "返回，零模型调用；phases/ 下没有其他文件（装配时断言）。")
        add("- 披露：末相位产物 = 源 run 的整理结果（合并后的行原样保留）"
            "+ 本 run 在末相位之前新接受的成员按其存储记录原样追加，"
            "后者**未经整理**。")
    else:
        add("- 目标 sequence 不含 learn 相位，库只写入 run 根"
            "（skill_bank.jsonl），不预置相位产物。")
    add("- 首次管线启动：p00 冻结 manifest（即同一性终检），baseline 相位跳过"
        "已存储的轨迹，learn 相位在拷贝的记录上重新决策、整理直接返回，"
        "test 相位全新执行。")
    if counts["generation_failure_rows"]:
        add(f"- 披露：源 run 的阶段 3 generation_failures 有 "
            f"{counts['generation_failure_rows']} 条 train 溯源行。v4 学习"
            "相位把失败的新增视为可重试，没有对应账本，这些单元将由新 "
            "run 首个 learn 相位重新尝试（真实模型调用），不拷贝。")
    if counts["pending_bank_rows"]:
        add(f"- 披露：源 run 的末相位库产物携带 "
            f"{counts['pending_bank_rows']} 条 train 溯源的待定成员"
            "（源 run 未完成检验的材料），随库与 store 原样带入，"
            "新 run 的 exam/live 相位将真实测量它们。")
    add("")
    add("## 全链口径（契约 10）")
    add("")
    add("- skill 的 train 判定 = `skill_lineage_tasks` 全链口径：自身 "
        "subtask 的任务 + `merged_from` 递归到底的每个原件的任务，"
        "全部落在 train 才保留。库行与同 id 的存储 skill 内容一致，"
        "按同一判定保留。")
    add("- segmentation_audit.jsonl 整文件拷贝（含 validation 侧的惰性"
        "审计记录，裁决 5 豁免）。")
    add("- skills / exams 的存储记录与 skill_bank 行迁移到当前对象 schema "
        "后写入；trajectories / subtasks / attempts 逐字节拷贝。")
    add("")
    add("## 逐类计数")
    add("")
    add("| 类别 | 计数 |")
    add("|---|---|")
    for name in ("trajectories", "subtasks", "skills", "exams", "attempts"):
        add(f"| {name} | {counts[name]} |")
    add(f"| skill_bank 行 | {counts['bank_rows']} |")
    add(f"| 阶段 3 generation_failures 行（train 溯源，不拷贝） | "
        f"{counts['generation_failure_rows']} |")
    add(f"| segmentation_audit 行 | {counts['segmentation_audit_rows']} |")
    add(f"| accepted_infra_shortfall 单元 | "
        f"{counts['accepted_infra_shortfall_units']} |")
    add("")
    add("## train 侧 merged 原件（裁决 1）")
    add("")
    add(f"- 共 {merged['train_merged_originals']} 条 train 溯源的 merged "
        f"原件：{merged['consumed_by_kept_merges']} 条被过滤后库内的 merge "
        f"消费；{len(merged['dangling'])} 条悬空（消费它的 merge 因含 "
        "test/reserved 溯源被过滤，原件以 status=merged 留在新 store）。")
    if merged["dangling"]:
        add("- 悬空清单：")
        for skill_id in merged["dangling"]:
            add(f"  - `{skill_id}`")
    add("")
    add("## baseline 相位播种溯源")
    add("")
    lines.extend(_campaign_lines(campaign_seeded, counts["trajectories"]))
    add("")
    add("## 未拷贝清单（“未列即不拷”，裁决 5）")
    add("")
    add("以下文件和目录不进入新 run（装配时逐项断言不存在）："
        "manifest.json、pipeline_log.jsonl、llm_requests/、"
        "failed_units.jsonl、skip_audit.jsonl、各 stage summary、"
        "stage5_curation/curation_log.json、stage6_test/、work/、"
        "stage4 的 leak_verdicts.jsonl 与 rewrite_failures.jsonl；"
        "v4 源的整棵 phases/ 树（相位记录、units、报告、账本、摘要），"
        "唯一例外是上面预置的相位库产物。"
        "新 run 的 manifest 由首次管线启动时 p00 冻结。")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The baseline level (revision 3): stage-1-only reuse
# ---------------------------------------------------------------------------

def _source_task_ids(source_manifest: Mapping[str, Any],
                     source_run: Path) -> Set[str]:
    """Raw task ids of the source run's task set: stage 0 freezes every
    loaded task into the split lists, so their union is the task set and
    the subset check needs no task loading."""
    split = source_manifest.get("split")
    if not isinstance(split, dict):
        raise ConfigError(
            "the source run's manifest records no split mapping; only a "
            "run frozen by stage 0 can be reused")
    ids: Set[str] = set()
    for value in split.values():
        if isinstance(value, list):
            ids.update(item for item in value if isinstance(item, str))
    if not ids:
        raise ConfigError(
            "the source run's manifest split lists are empty; the source "
            "task set is unverifiable and the source is corrupt")
    return ids


def _baseline_identity_check(
        source_manifest: Mapping[str, Any],
        config: ExperimentConfig,
        train_tasks: Sequence[Any],
        source_run: Path,
        config_path: Path) -> Dict[str, Any]:
    """Revision 3's pinned minimum: both executor models, both attempt
    counts and train within the source task set (the benchmark is checked
    before the plugin import). Every key that only acts downstream may
    differ. Returns the pinned values for the provenance record.
    """
    model_roles = source_manifest.get("model_roles")
    if not isinstance(model_roles, dict):
        raise ConfigError(
            f"the source run {source_run} has a manifest without "
            "model_roles; only a run frozen by stage 0 can be reused")
    source_config = source_manifest.get("config")
    if not isinstance(source_config, dict):
        raise ConfigError(
            f"the source run {source_run} has a manifest without a "
            "frozen config; only a run frozen by stage 0 can be reused")
    for role in ("executor_strong", "executor_weak"):
        source_model = model_roles.get(role)
        new_model = config.models.get(role)
        if source_model is None or source_model != new_model:
            raise ConfigError(
                f"the reuse config {config_path} names executor model "
                f"{role}={new_model!r} but the source run {source_run} "
                f"ran {source_model!r}; the copied stage-1 trajectories "
                "were produced by the source run's executors, so the "
                "executor models are pinned at the baseline level "
                "(revision 3)")
    for key in ("n_attempts_strong", "n_attempts_weak"):
        source_value = source_config.get(key)
        new_value = getattr(config, key)
        if source_value is None or source_value != new_value:
            raise ConfigError(
                f"the reuse config {config_path} sets {key}={new_value!r} "
                f"but the source run {source_run} ran {source_value!r}; "
                "the attempt counts decide how many stage-1 trajectories "
                "exist per task and arm, so they are pinned at the "
                "baseline level (revision 3)")
    source_ids = _source_task_ids(source_manifest, source_run)
    train_raw = {task.task_id for task in train_tasks}
    extra = sorted(train_raw - source_ids)
    if extra:
        raise ConfigError(
            f"the reuse config {config_path} puts task(s) {extra} in "
            "train, but the source run's task set does not contain them; "
            "the baseline level copies the source run's stage-1 "
            "trajectories, so train must be a subset of the source task "
            "set (revision 3)")
    return {
        "executor_strong": model_roles["executor_strong"],
        "executor_weak": model_roles["executor_weak"],
        "n_attempts_strong": source_config["n_attempts_strong"],
        "n_attempts_weak": source_config["n_attempts_weak"],
        "train_count": len(train_raw),
        "source_task_count": len(source_ids),
    }


def _render_assembled_from_baseline(
        *,
        header: Sequence[str],
        pinned: Mapping[str, Any],
        counts: Mapping[str, Any],
        campaign_seeded: Mapping[str, Any]) -> str:
    """The baseline-level ASSEMBLED_FROM.md text."""
    lines: List[str] = ["# 装配溯源（ASSEMBLED_FROM，baseline 档位）", ""]
    add = lines.append
    lines.extend(header)
    add("- 方案：`docs/decisions/retrieval_delivery_tb_split_20261013.md`"
        " 修订三（2026-10-13 用户裁决：baseline 相位轨迹复用 + 下游相位全新"
        "蒸馏）。")
    add("")
    add("## 同一性校验与 train ⊆ 源任务集（修订三）")
    add("")
    add("- baseline 档位只固定 {benchmark、双 executor 模型名、双 "
        "n_attempts、train ⊆ 源任务集}，即 baseline 相位轨迹可复现所需的最小集；"
        "规则名、prompt digest、相位定义、预算等可以不同（它们只作用于"
        "全部重新运行的下游相位）。")
    add(f"- 核对通过：executor_strong {pinned['executor_strong']!r}、"
        f"executor_weak {pinned['executor_weak']!r}；n_attempts_strong/weak "
        f"= {pinned['n_attempts_strong']}/{pinned['n_attempts_weak']}；"
        f"train 的 {pinned['train_count']} 个任务全部在源任务集的 "
        f"{pinned['source_task_count']} 个任务内。")
    add("")
    add("## 逐类计数")
    add("")
    add("| 类别 | 计数 |")
    add("|---|---|")
    add(f"| trajectories | {counts['trajectories']} |")
    add(f"| accepted_infra_shortfall 单元 | "
        f"{counts['accepted_infra_shortfall_units']} |")
    add("")
    add("## baseline 相位播种溯源")
    add("")
    lines.extend(_campaign_lines(campaign_seeded, counts["trajectories"]))
    add("")
    add("## 未拷贝清单（baseline 相位轨迹之外零复用）")
    add("")
    add("- 不拷贝任何下游产物：subtask / skill / exam / attempt、"
        "skill_bank.jsonl、stage2_segments/segmentation_audit.jsonl、"
        "stage3_generation/generation_failures.jsonl、v4 源的整棵 "
        "phases/ 树，以及负清单的全部条目（manifest.json、"
        "pipeline_log.jsonl、llm_requests/、failed_units.jsonl、"
        "skip_audit.jsonl、各 stage summary、curation_log.json、"
        "stage6_test/、work/、leak_verdicts.jsonl、rewrite_failures.jsonl）。")
    add("- **baseline 相位之外的全部相位将按当前协议全新执行**：切段、写作、"
        "验收、整理与终测全部按本配置的相位定义与当前 prompt 注册表"
        "真实调用；新 run 的 manifest 由首次管线启动时 p00 冻结。")
    return "\n".join(lines) + "\n"


def _assemble_baseline(*,
                       source_run: Path,
                       source_manifest: Mapping[str, Any],
                       config: ExperimentConfig,
                       config_path: Path,
                       run_dir: Path,
                       split_sizes: Dict[str, int],
                       train_tasks: Sequence[Any],
                       train_ids: Set[str]) -> Dict[str, Any]:
    """The baseline level: train-side trajectories and shortfall only."""
    pinned = _baseline_identity_check(
        source_manifest, config, train_tasks, source_run, config_path)
    trajectories = {
        obj_id: Trajectory.from_dict(record) for obj_id, record
        in _source_objects(source_run, "trajectories").items()}
    kept_trajectories = {
        obj_id: obj for obj_id, obj in trajectories.items()
        if obj.task_id in train_ids}
    shortfall_units = _load_shortfall_units(source_run, train_ids)

    with _locked_fresh_run_dir(run_dir):
        counts = {
            "trajectories": _copy_kind(source_run, run_dir, "trajectories",
                                       kept_trajectories),
            "accepted_infra_shortfall_units": _write_shortfall(
                run_dir, shortfall_units),
        }
        _assert_negative_list(run_dir, baseline=True)
        _assert_phase_tree(run_dir, None)
        (run_dir / _ASSEMBLED_FROM_NAME).write_text(
            _render_assembled_from_baseline(
                header=_header_lines(source_run, source_manifest,
                                     config_path, config, run_dir,
                                     split_sizes),
                pinned=pinned,
                counts=counts,
                campaign_seeded=_campaign_seeded(kept_trajectories)),
            encoding="utf-8")

    return {
        "source_run": str(source_run),
        "config": str(config_path),
        "run_dir": str(run_dir),
        "level": "baseline",
        "split_sizes": split_sizes,
        "counts": counts,
        "train_subset_of_source_tasks": {
            "train": pinned["train_count"],
            "source_tasks": pinned["source_task_count"],
        },
        "assembled_from": _ASSEMBLED_FROM_NAME,
    }


# ---------------------------------------------------------------------------
# The bank level
# ---------------------------------------------------------------------------

def _load_source_store(source_run: Path) -> Dict[str, Dict[str, Any]]:
    """The source run's five object kinds, parsed. Skills go through the
    old-schema migration first (a pre-v2 merge product needs its sibling
    records); a row that cannot migrate is a corrupt source."""
    skill_rows = _source_objects(source_run, "skills")
    try:
        migrated = migrate_skill_rows(list(skill_rows.values()))
        skills = {obj_id: Skill.from_dict(row)
                  for obj_id, row in zip(skill_rows, migrated)}
        exams = {obj_id: Exam.from_dict(record) for obj_id, record
                 in _source_objects(source_run, "exams").items()}
    except ValueError as exc:
        raise ConfigError(
            f"an object row of the source run {source_run} cannot "
            f"migrate to the current skill schema: {exc}; the source is "
            "corrupt") from exc
    return {
        "trajectories": {
            obj_id: Trajectory.from_dict(record) for obj_id, record
            in _source_objects(source_run, "trajectories").items()},
        "subtasks": {
            obj_id: Subtask.from_dict(record) for obj_id, record
            in _source_objects(source_run, "subtasks").items()},
        "skills": skills,
        "skill_rows": skill_rows,
        "exams": exams,
        "attempts": {
            obj_id: Attempt.from_dict(record) for obj_id, record
            in _source_objects(source_run, "attempts").items()},
    }


def _keep_sets(store: Mapping[str, Dict[str, Any]],
               train_ids: Set[str]) -> Dict[str, Dict[str, Any]]:
    """The train-lineage subset of every object kind. Exams follow their
    subtask, attempts their exam and skill, so every kept object is
    train-side by construction."""
    subtasks = store["subtasks"]
    skills = store["skills"]
    kept_subtasks = {obj_id: obj for obj_id, obj in subtasks.items()
                     if obj.task_id in train_ids}
    kept_skills = {
        obj_id: skill for obj_id, skill in skills.items()
        if skill_lineage_tasks(skill, subtasks, skills_by_id=skills)
        <= train_ids}
    kept_exams = {obj_id: obj for obj_id, obj in store["exams"].items()
                  if obj.subtask_id in kept_subtasks}
    return {
        "trajectories": {
            obj_id: obj for obj_id, obj in store["trajectories"].items()
            if obj.task_id in train_ids},
        "subtasks": kept_subtasks,
        "skills": kept_skills,
        "exams": kept_exams,
        "attempts": {
            obj_id: obj for obj_id, obj in store["attempts"].items()
            if obj.exam_id in kept_exams
            and (obj.skill_id is None or obj.skill_id in kept_skills)},
    }


def _filter_bank(bank_rows: Sequence[Mapping[str, Any]],
                 store: Mapping[str, Dict[str, Any]],
                 kept_skills: Mapping[str, Skill],
                 source_run: Path) -> List[Dict[str, Any]]:
    """The bank rows whose skill is kept, at the current object schema.

    Every row must load and match the stored skill of the same id after
    migration (both sides through the object roundtrip, so a consistent
    v1 source compares equal); the kept row is then the content of a
    copied skill record, which keeps bank and store identical.
    """
    kept_rows: List[Dict[str, Any]] = []
    for row in bank_rows:
        skill_id = str(row.get("skill_id"))
        stored = store["skills"].get(skill_id)
        if stored is None:
            raise ConfigError(
                f"bank row {skill_id!r} names a skill the source run "
                "does not store; the source is corrupt")
        try:
            row_obj = Skill.from_dict(
                migrate_skill_record(row, store["skill_rows"]))
        except ValueError as exc:
            raise ConfigError(
                f"bank row {skill_id!r} of the source run {source_run} "
                f"cannot migrate to the current skill schema: {exc}; the "
                "source is corrupt") from exc
        if row_obj.to_dict() != stored.to_dict():
            raise ConfigError(
                f"bank row {skill_id!r} differs from the stored skill "
                "of the same id; the source is corrupt")
        if skill_id in kept_skills:
            kept_rows.append(row_obj.to_dict())
    return kept_rows


def _kept_generation_failures(source_run: Path, source_version: int,
                              subtasks: Mapping[str, Subtask],
                              train_ids: Set[str]) -> List[Dict[str, Any]]:
    """Train-side rows of the stage-world generation-failure ledger
    (counted and disclosed, never copied: module docstring). A v4 source
    has no such ledger."""
    if source_version >= 4:
        return []
    kept: List[Dict[str, Any]] = []
    for row in read_jsonl(source_run / "stage3_generation"
                          / "generation_failures.jsonl"):
        subtask_id = str(row.get("subtask_id"))
        subtask = subtasks.get(subtask_id)
        if subtask is None:
            raise ConfigError(
                f"generation_failures row names subtask {subtask_id!r}, "
                "which the source run does not store; the source is "
                "corrupt")
        if subtask.task_id in train_ids:
            kept.append(row)
    return kept


def _identity_walk(source_manifest: Mapping[str, Any],
                   source_config: ExperimentConfig,
                   prospective: Mapping[str, Any],
                   config: ExperimentConfig,
                   source_run: Path,
                   config_path: Path) -> "tuple[List[str], List[str]]":
    """Ruling 4's whitelist diff of the source manifest against the
    manifest the new run's stage 0 will freeze. Returns the allowed
    diffs (collapsed to section level) and the prompt-digest drift; any
    other diff is a refusal naming every offending leaf."""
    canonical_source = {**source_manifest,
                        "config": source_config.to_dict()}
    allowed_prefixes = _allowed_diff_prefixes(config)
    diff_paths = differing_keys(
        _identity_view(canonical_source), _identity_view(prospective))
    unexpected = [path for path in diff_paths
                  if not _path_allowed(path, allowed_prefixes)]
    if unexpected:
        raise ConfigError(
            f"the reuse config {config_path} differs from the source run "
            f"{source_run} beyond the allowed keys "
            f"{list(allowed_prefixes)}: {unexpected}; the reused "
            "artifacts were produced under the source run's frozen inputs, "
            "so align the config with the source run (or use a fresh "
            "source) instead of mixing protocols")
    allowed_diffs = sorted(
        {_section(path, allowed_prefixes) for path in diff_paths})
    source_digests = source_manifest.get("prompt_digests") or {}
    new_digests = prospective["prompt_digests"]
    digest_drift = sorted(
        name for name in set(source_digests) | set(new_digests)
        if source_digests.get(name) != new_digests.get(name))
    return allowed_diffs, digest_drift


def _assemble_bank(*,
                   source_run: Path,
                   source_manifest: Mapping[str, Any],
                   config: ExperimentConfig,
                   config_path: Path,
                   run_dir: Path,
                   split_sizes: Dict[str, int],
                   tasks: Sequence[Any],
                   split_lists: Mapping[str, Sequence[Any]],
                   train_ids: Set[str]) -> Dict[str, Any]:
    """The bank level: the train-lineage store, the filtered bank and the
    pre-seeded last-learn-phase artifact. Every refusal happens before
    anything is written into ``run_dir``."""
    source_config, source_version = _source_config(
        source_manifest, source_run)
    prospective = build_manifest(
        config, tasks,
        train_ids=[task.task_id for task in split_lists["train"]],
        test_ids=[task.task_id for task in split_lists["test"]],
        reserved_ids=[task.task_id for task in split_lists["reserved"]])
    allowed_diffs, digest_drift = _identity_walk(
        source_manifest, source_config, prospective, config, source_run,
        config_path)

    store = _load_source_store(source_run)
    kept = _keep_sets(store, train_ids)
    kept_bank_rows = _filter_bank(
        _source_bank_rows(source_run, source_config, source_version),
        store, kept["skills"], source_run)
    kept_bank_ids = {str(row["skill_id"]) for row in kept_bank_rows}
    kept_accepted_bank_ids = {str(row["skill_id"]) for row in kept_bank_rows
                              if row.get("status") == "accepted"}
    # Ruling 5: the new store's accepted set must equal the kept bank's
    # accepted rows (pending bank members ride along, counted).
    new_accepted = {obj_id for obj_id, skill in kept["skills"].items()
                    if skill.status == "accepted"}
    if new_accepted != kept_accepted_bank_ids:
        raise ConfigError(
            "the assembled store's accepted skills would not equal the "
            "filtered bank's accepted rows (accepted-but-uncopied: "
            f"{sorted(new_accepted - kept_accepted_bank_ids)}; "
            "bank-but-not-accepted: "
            f"{sorted(kept_accepted_bank_ids - new_accepted)}); the source "
            "run's accepted set and bank disagree -- a human must look at "
            "this run")
    kept_failure_rows = _kept_generation_failures(
        source_run, source_version, store["subtasks"], train_ids)
    shortfall_units = _load_shortfall_units(source_run, train_ids)
    audit_path = source_run / AUDIT_PATH
    audit_rows = read_jsonl(audit_path)
    preseed_label = _last_learn_label(config)

    with _locked_fresh_run_dir(run_dir):
        counts: Dict[str, int] = {}
        for kind in ("trajectories", "subtasks", "attempts"):
            counts[kind] = _copy_kind(source_run, run_dir, kind, kept[kind])
        for kind in ("skills", "exams"):
            counts[kind] = _write_migrated_kind(run_dir, kind, kept[kind])

        # The filtered bank at the run root (read by the audit tooling and
        # the pre-learn bank fallback) and as the last learn phase's
        # artifact, on which that phase's curation returns without any
        # model call.
        write_jsonl(run_dir / "skill_bank.jsonl", kept_bank_rows)
        if preseed_label is not None:
            write_jsonl(phase_bank_dir(run_dir, preseed_label) / BANK_NAME,
                        kept_bank_rows)
        counts["bank_rows"] = len(kept_bank_rows)
        counts["pending_bank_rows"] = (
            len(kept_bank_rows) - len(kept_accepted_bank_ids))
        counts["generation_failure_rows"] = len(kept_failure_rows)
        # The segmentation audit, whole file and byte-exact (ruling 5's
        # lazy-audit exemption).
        counts["segmentation_audit_rows"] = len(audit_rows)
        if audit_path.is_file():
            target_audit = run_dir / AUDIT_PATH
            target_audit.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(audit_path, target_audit)
        counts["accepted_infra_shortfall_units"] = _write_shortfall(
            run_dir, shortfall_units)

        _assert_negative_list(run_dir, baseline=False)
        _assert_phase_tree(run_dir, preseed_label)

        merged = _merged_original_groups(
            store["skills"], set(kept["skills"]), kept_bank_ids)
        (run_dir / _ASSEMBLED_FROM_NAME).write_text(_render_assembled_from(
            header=_header_lines(source_run, source_manifest, config_path,
                                 config, run_dir, split_sizes),
            allowed_diffs=allowed_diffs,
            digest_drift=digest_drift,
            counts=counts,
            merged=merged,
            campaign_seeded=_campaign_seeded(kept["trajectories"]),
            source_version=source_version,
            preseed_label=preseed_label), encoding="utf-8")

    return {
        "source_run": str(source_run),
        "config": str(config_path),
        "run_dir": str(run_dir),
        "level": "bank",
        "source_schema": source_version,
        "preseeded_phase_bank": preseed_label,
        "split_sizes": split_sizes,
        "counts": counts,
        "allowed_identity_diffs": allowed_diffs,
        "prompt_digest_drift": digest_drift,
        "merged_originals": merged,
        "assembled_from": _ASSEMBLED_FROM_NAME,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def assemble_reuse(source_run: Path, config_path: Path, run_dir: Path,
                   level: str = "bank") -> Dict[str, Any]:
    """Assemble ``run_dir`` from ``source_run`` per the reuse config.

    Takes the exclusive run lock, refuses a ``run_dir`` that already has
    a manifest, validates the level's identity rule, copies the level's
    allowlist, asserts the negative list and writes ``ASSEMBLED_FROM.md``.
    Returns a JSON-safe summary (the level, per-kind counts and the
    level's identity record) for the CLI to print.
    """
    if level not in LEVELS:
        raise ConfigError(
            f"level must be one of {LEVELS}, got {level!r}")
    source_run = Path(source_run)
    config_path = Path(config_path)
    run_dir = Path(run_dir)
    if not source_run.is_dir():
        raise ConfigError(f"source run {source_run} does not exist")
    source_manifest_path = source_run / MANIFEST_NAME
    if not source_manifest_path.is_file():
        raise ConfigError(
            f"source run {source_run} has no {MANIFEST_NAME}; only a "
            "finished frozen run can be reused")
    if source_run.resolve() == run_dir.resolve():
        raise ConfigError(
            f"run_dir must differ from the source run (both are "
            f"{run_dir})")
    source_manifest = read_json(source_manifest_path)

    try:
        config = load_config(str(config_path))
    except (ValueError, OSError) as exc:
        raise ConfigError(
            f"invalid reuse config {config_path}: {exc}") from exc
    if config.mode == "split" and isinstance(config.split, dict) \
            and "ratio" in config.split:
        # The copy is keyed by the frozen lists; a ratio+seed split would
        # have to be re-derived exactly the way stage 0 derives it.
        raise ConfigError(
            "the reuse config must carry explicit frozen split lists; a "
            "ratio+seed spec would have to be re-derived identically by "
            "stage 0 -- write the lists out (see scripts/run/"
            "freeze_tb_split.py) instead")
    # Checked before the plugin import, so a mismatch surfaces as an
    # identity error instead of a missing-plugin import error.
    if (level == "baseline"
            and config.benchmark != source_manifest.get("benchmark")):
        raise ConfigError(
            f"the reuse config {config_path} names benchmark "
            f"{config.benchmark!r} but the source run {source_run} ran "
            f"{source_manifest.get('benchmark')!r}; the benchmark is "
            "pinned at both levels (revision 3)")

    tasks = tasks_block.load_tasks(config.benchmark, tasks_spec(config))
    train_tasks, test_tasks, reserved_tasks = tasks_block.split_tasks(
        tasks, config.mode, config.split)
    split_sizes = {"train": len(train_tasks), "test": len(test_tasks),
                   "reserved": len(reserved_tasks)}
    # Stored-form task ids ("{benchmark}:{raw}"), the form every source
    # object's task_id carries.
    train_ids = {make_task_id(config.benchmark, task.task_id)
                 for task in train_tasks}
    if level == "baseline":
        return _assemble_baseline(
            source_run=source_run,
            source_manifest=source_manifest,
            config=config,
            config_path=config_path,
            run_dir=run_dir,
            split_sizes=split_sizes,
            train_tasks=train_tasks,
            train_ids=train_ids)
    return _assemble_bank(
        source_run=source_run,
        source_manifest=source_manifest,
        config=config,
        config_path=config_path,
        run_dir=run_dir,
        split_sizes=split_sizes,
        tasks=tasks,
        split_lists={"train": train_tasks, "test": test_tasks,
                     "reserved": reserved_tasks},
        train_ids=train_ids)
