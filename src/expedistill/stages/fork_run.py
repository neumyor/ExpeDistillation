"""Fork the finished phase prefix of one run into a fresh run directory.

A formal run is sometimes taken further than an earlier run went: one more
practice-and-learn round before the final test, the practice on another
list, or the same bank handed to a different final test. Redoing the early
phases would repeat hours of rollouts and model calls whose results are
already on disk, so the new run starts from a copy of them: the first K
phases' directories and the part of the run-level object stores those
phases produced. The manifest is frozen by the new run's own first start
(against the NEW config), and the ledger, the usage records, the lock and
the workdirs belong to the run that produced them.

On that first start the copied phases take the ordinary resume path: a
finished execute phase returns its stored summary and a finished learn
phase returns its stored summary, both without a model call, and the
first phase after the prefix runs fresh.

What makes a copy of the prefix faithful:

* The target config starts with the same K phases, defined the same way,
  and agrees with the source on everything else that acts on them (models,
  depths, rules, budgets, the train split, and the val split when a copied
  phase practises on it). It may differ in the later phases, the test
  split and the task list.
* The run-level objects are filtered by the phase that created them: a
  trajectory by the phase label in its id; a segment, a subtask type and a
  segment's type assignment by the ``phase_label`` they record; a
  segmentation audit record by the learn phase that cut its trajectory
  (the first learn phase after the trajectory's execute phase); a
  ``failed_units.jsonl`` row by its ``stage``. Only what the first K
  phases created is copied.
* The skill store is run-level and every learn phase rewrites it in place
  (versions, statuses), so a finished source's store is the state after
  its LAST learn phase. It is rebuilt from the bank artifact of the
  prefix's last learn phase (checked against the digest recorded beside
  it); without a learn phase in the prefix only the run-level imported
  skills (those without provenance) are kept. Every stored skill left out
  must have been created after the prefix: its version-1 provenance row
  belongs to a later phase.
* The source must be finished and idle: its prefix phases carry their
  completion records and no run lock is left behind.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from expedistill.composites.locate import (
    SEGMENT_TYPES_PATH,
    SUBTASK_TYPES_PATH,
)
from expedistill.composites.phase_bank import (
    load_phase_bank,
    load_phase_bank_summary,
    phase_bank_dir,
)
from expedistill.composites.provenance import find_provenance
from expedistill.composites.segment_checked import AUDIT_PATH
from expedistill.composites.shortfall import stage1_shortfall_path
from expedistill.core.config import (
    CONFIG_SCHEMA_VERSION,
    ExecutePhaseSpec,
    ExperimentConfig,
    LearnPhaseSpec,
    load_config,
    phase_label,
)
from expedistill.core.ids import parse_trajectory_id
from expedistill.core.models import Skill, Subtask
from expedistill.infra.manifest import MANIFEST_NAME
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import LOCK_NAME, RunLock
from expedistill.infra.store import (
    Store,
    object_id_of_file,
    read_json,
    read_jsonl,
    write_jsonl,
)
from expedistill.inject import bank_reference

__all__ = ["fork_run"]

#: Run-root files carried over as they are: the run-level bank an import
#: wrote and the records of where the source's inputs came from.
_ROOT_FILES = ("skill_bank.jsonl", "BANK_ORIGIN.md", "BASELINE_ORIGIN.md")

#: The run-root unit-failure ledger (infra.reliability.UnitFailureRecorder).
_FAILED_UNITS_NAME = "failed_units.jsonl"

#: The completion records the phase drivers write last. The names are
#: those of phase_execute.phase_disk.SUMMARY_NAME and
#: phase_learn.SUMMARY_NAME; the drivers load lazily, so they are not
#: imported here (a test pins the equality).
_EXECUTE_SUMMARY_NAME = "phase_summary.json"
_LEARN_SUMMARY_NAME = "learn_summary.json"

_FORKED_FROM_NAME = "FORKED_FROM.md"
_THROUGH_RE = re.compile(r"p([0-9]{2,})")

#: Locks and torn-write sidecars are never part of a copy.
_IGNORE = shutil.ignore_patterns("*.lock", "*.interrupted-tail")


def fork_run(*, source_run: Path, config_path: Path, run_dir: Path,
             through: str) -> Dict[str, Any]:
    """Copy phases ``p01`` through ``through`` of ``source_run`` into the
    fresh ``run_dir`` and return a JSON-safe summary of what landed.

    ``through`` is the last copied phase's label head (``"p05"``). The new
    run is started afterwards with the ordinary ``run`` command and
    ``config_path``; its first start freezes its own manifest. A fork that
    fails while copying leaves a partial directory: delete it and fork
    again.
    """
    source_run, run_dir = Path(source_run), Path(run_dir)
    if source_run.resolve() == run_dir.resolve():
        raise ConfigError("fork the prefix into a different run directory")
    manifest = _read_source_manifest(source_run)
    source = _source_config(manifest, source_run)
    try:
        target = load_config(str(config_path))
    except (ValueError, OSError) as exc:
        raise ConfigError(f"invalid config {config_path}: {exc}") from exc
    count = _prefix_length(through, source)
    labels = [phase_label(index, name)
              for index, name in enumerate(source.sequence[:count])]
    _check_same_prefix(source, target, count)
    _require_finished(source_run, source, labels)
    skills = _prefix_skills(source_run, source, labels)
    if run_dir.exists() and any(run_dir.iterdir()):
        raise ConfigError(
            f"{run_dir} is not empty; fork into a fresh run directory "
            "(the new run freezes its manifest on its first start)")

    lock = RunLock(run_dir)
    warning = lock.acquire()
    try:
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        summary = _copy_prefix(source_run, run_dir, source, labels, skills)
    finally:
        lock.release()
    summary.update(source_run=str(source_run), config=str(config_path),
                   run_dir=str(run_dir), through=through)
    return summary


# ---------------------------------------------------------------------------
# The source and the identity of the prefix
# ---------------------------------------------------------------------------

def _read_source_manifest(source_run: Path) -> Mapping[str, Any]:
    if (source_run / LOCK_NAME).exists():
        raise ConfigError(
            f"{source_run} holds {LOCK_NAME}: it is running, or a driver "
            "died without releasing it; fork a finished, idle run")
    path = source_run / MANIFEST_NAME
    if not path.is_file():
        raise ConfigError(
            f"the source run {source_run} has no {MANIFEST_NAME}; only a "
            "run frozen by stage 0 can be forked")
    return read_json(path)


def _source_config(manifest: Mapping[str, Any],
                   source_run: Path) -> ExperimentConfig:
    raw = manifest.get("config")
    if not isinstance(raw, dict) \
            or raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"the source run {source_run} froze a config that is not "
            f"schema v{CONFIG_SCHEMA_VERSION}; fork-run copies phase "
            "results only between runs of the current schema")
    try:
        return ExperimentConfig.from_dict(raw)
    except ValueError as exc:
        raise ConfigError(
            f"the source run {source_run} froze a config that no longer "
            f"loads: {exc}") from exc


def _prefix_length(through: str, source: ExperimentConfig) -> int:
    match = _THROUGH_RE.fullmatch(through)
    if match is None:
        raise ConfigError(
            f"--through must be a phase label head such as 'p05', "
            f"got {through!r}")
    count = int(match.group(1))
    if not 1 <= count <= len(source.sequence):
        raise ConfigError(
            f"--through {through} is outside the source sequence of "
            f"{len(source.sequence)} phase(s)")
    return count


def _comparable(config: ExperimentConfig, names: Sequence[str]
                ) -> Dict[str, Any]:
    """Everything of a config that acts on the prefix phases ``names``,
    apart from the phase definitions themselves: the later phases, the
    test split, the reserved list, the task list and -- unless a prefix
    phase practises on it -- the val list may differ."""
    payload = config.to_dict()
    for key in ("sequence", "phases", "tasks", "prompts_digests"):
        payload.pop(key, None)
    split = dict(payload.get("split") or {})
    dropped = ["test", "reserved"]
    if not any(isinstance(config.phases[name], ExecutePhaseSpec)
               and config.phases[name].tasks == "val" for name in names):
        dropped.append("val")
    for key in dropped:
        split.pop(key, None)
    payload["split"] = split
    return payload


def _check_same_prefix(source: ExperimentConfig, target: ExperimentConfig,
                       count: int) -> None:
    names = source.sequence[:count]
    if target.sequence[:count] != names:
        raise ConfigError(
            f"the target sequence must start with the source's first "
            f"{count} phase(s) {names}, got {target.sequence[:count]}")
    source_raw, target_raw = source.to_dict(), target.to_dict()
    for name in sorted(set(names)):
        if source_raw["phases"][name] != target_raw["phases"][name]:
            raise ConfigError(
                f"phase {name!r} is defined differently in the target "
                "config; a copied phase must stand for exactly what the "
                "new run would have measured")
    source_rest = _comparable(source, names)
    target_rest = _comparable(target, names)
    differing = sorted(key for key in set(source_rest) | set(target_rest)
                       if source_rest.get(key) != target_rest.get(key))
    if differing:
        raise ConfigError(
            f"the target config differs from the source in {differing}; "
            "those act on the copied phases")


def _require_finished(source_run: Path, source: ExperimentConfig,
                      labels: Sequence[str]) -> None:
    for index, label in enumerate(labels):
        spec = source.phases[source.sequence[index]]
        directory = phase_bank_dir(source_run, label)
        if isinstance(spec, LearnPhaseSpec):
            path = directory / _LEARN_SUMMARY_NAME
            finished = path.is_file() and "bank" in read_json(path)
        else:
            path = directory / _EXECUTE_SUMMARY_NAME
            finished = path.is_file() and read_json(path).get(
                "complete") is True
        if not finished:
            raise ConfigError(
                f"phase {label} of the source run is not finished "
                f"({path.name} is missing or says incomplete); only "
                "finished phases can be forked")


# ---------------------------------------------------------------------------
# The skill store at the end of the prefix
# ---------------------------------------------------------------------------

def _prefix_skills(source_run: Path, source: ExperimentConfig,
                   labels: Sequence[str]) -> Dict[str, Any]:
    """The skills the store holds at the end of the prefix, and the stored
    ones left out (each proven to be created after the prefix)."""
    learn = [label for index, label in enumerate(labels)
             if isinstance(source.phases[source.sequence[index]],
                           LearnPhaseSpec)]
    directory = Store(source_run).dir_for("skills")
    stored = {object_id_of_file(path): Skill.from_dict(read_json(path))
              for path in sorted(directory.glob("*.json"))}
    if learn:
        base_label: Optional[str] = learn[-1]
        rows = load_phase_bank(source_run, base_label)
        recorded = load_phase_bank_summary(source_run, base_label).get(
            "bank_reference")
        if bank_reference(rows) != recorded:
            raise ConfigError(
                f"the bank artifact of {base_label} no longer matches the "
                f"digest recorded beside it ({recorded}); the source run "
                "was modified after it finished")
        kept: List[Skill] = []
        for row in rows:
            skill = Skill.from_dict(row)
            if skill.to_dict() != row:
                raise ConfigError(
                    f"skill {skill.skill_id} in the bank artifact of "
                    f"{base_label} is not at the current schema; fork-run "
                    "does not migrate skills")
            kept.append(skill)
        missing = sorted({skill.skill_id for skill in kept} - set(stored))
        if missing:
            raise ConfigError(
                f"the bank artifact of {base_label} names skill(s) "
                f"{missing} that the source store does not hold")
    else:
        base_label = None
        kept = [skill for skill_id, skill in stored.items()
                if find_provenance(source_run, skill_id, 1) is None]
    kept_ids = {skill.skill_id for skill in kept}
    dropped = sorted(set(stored) - kept_ids)
    prefix = set(labels)
    unexplained = []
    for skill_id in dropped:
        first = find_provenance(source_run, skill_id, 1)
        if first is None or first.phase_label in prefix:
            unexplained.append(skill_id)
    if unexplained:
        raise ConfigError(
            f"stored skill(s) {unexplained} are not in the skill store at "
            "the end of the prefix, but their first version was not "
            "written after it; fork-run cannot tell where they belong")
    return {"from_phase": base_label, "skills": kept, "dropped": dropped}


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------

def _cutting_phase(source: ExperimentConfig) -> Dict[str, Optional[str]]:
    """``{execute phase label: label of the first learn phase after it}``
    -- the learn phase that cut that phase's trajectories (None when no
    learn phase follows)."""
    labels = [phase_label(index, name)
              for index, name in enumerate(source.sequence)]
    out: Dict[str, Optional[str]] = {}
    for index, name in enumerate(source.sequence):
        if not isinstance(source.phases[name], ExecutePhaseSpec):
            continue
        out[labels[index]] = next(
            (labels[later] for later in range(index + 1, len(labels))
             if isinstance(source.phases[source.sequence[later]],
                           LearnPhaseSpec)), None)
    return out


def _copy_trajectories(source_run: Path, run_dir: Path,
                       prefix: Set[str]) -> int:
    source_store, store = Store(source_run), Store(run_dir)
    count = 0
    for path in sorted(source_store.dir_for("trajectories").glob("*.json")):
        trajectory_id = object_id_of_file(path)
        try:
            phase = parse_trajectory_id(trajectory_id).phase_label
        except ValueError as exc:
            raise ConfigError(
                f"the source run stores a trajectory with an id this "
                f"schema does not read: {exc}") from exc
        if phase in prefix:
            shutil.copy2(path, store.dir_for("trajectories") / path.name)
            count += 1
    return count


def _copy_subtasks(source_run: Path, run_dir: Path,
                   prefix: Set[str]) -> int:
    source_store, store = Store(source_run), Store(run_dir)
    count = 0
    for path in sorted(source_store.dir_for("subtasks").glob("*.json")):
        if Subtask.from_dict(read_json(path)).phase_label in prefix:
            shutil.copy2(path, store.dir_for("subtasks") / path.name)
            count += 1
    return count


def _copy_rows(source: Path, target: Path, keep: Any) -> int:
    """Copy the JSONL rows ``keep(row)`` accepts; no file when none is."""
    rows = [row for row in read_jsonl(source) if keep(row)]
    if rows:
        write_jsonl(target, rows)
    return len(rows)


def _copy_prefix(source_run: Path, run_dir: Path, source: ExperimentConfig,
                 labels: Sequence[str],
                 skills: Mapping[str, Any]) -> Dict[str, Any]:
    prefix = set(labels)
    for label in labels:
        shutil.copytree(phase_bank_dir(source_run, label),
                        phase_bank_dir(run_dir, label), ignore=_IGNORE)
    store = Store(run_dir)
    cutter = _cutting_phase(source)

    def cut_in_prefix(row: Mapping[str, Any]) -> bool:
        phase = parse_trajectory_id(row["trajectory_id"]).phase_label
        return cutter.get(phase) in prefix

    stores = {
        "trajectories": _copy_trajectories(source_run, run_dir, prefix),
        "subtasks": _copy_subtasks(source_run, run_dir, prefix),
        "subtask_types": _copy_rows(
            source_run / SUBTASK_TYPES_PATH, run_dir / SUBTASK_TYPES_PATH,
            lambda row: row["phase_label"] in prefix),
        "segment_types": _copy_rows(
            source_run / SEGMENT_TYPES_PATH, run_dir / SEGMENT_TYPES_PATH,
            lambda row: row["phase_label"] in prefix),
        "segmentation_audit": _copy_rows(
            source_run / AUDIT_PATH, run_dir / AUDIT_PATH, cut_in_prefix),
        "failed_units": _copy_rows(
            source_run / _FAILED_UNITS_NAME, run_dir / _FAILED_UNITS_NAME,
            lambda row: row.get("stage") in prefix),
    }
    # The baseline's accepted holes: the baseline is always the first
    # phase, so a prefix holds it whenever the source ran one.
    shortfall = stage1_shortfall_path(source_run)
    if shortfall.is_file():
        shutil.copy2(shortfall, stage1_shortfall_path(run_dir))
    for skill in skills["skills"]:
        store.save(skill, "skills")
    for name in _ROOT_FILES:
        if (source_run / name).is_file():
            shutil.copy2(source_run / name, run_dir / name)
    summary = {
        "phases": list(labels),
        "skills": {"from_phase": skills["from_phase"],
                   "count": len(skills["skills"]),
                   "dropped_later_products": list(skills["dropped"])},
        "stores": stores,
    }
    (run_dir / _FORKED_FROM_NAME).write_text(
        _render_forked_from(source_run, run_dir, summary), encoding="utf-8")
    return summary


def _render_forked_from(source_run: Path, run_dir: Path,
                        summary: Mapping[str, Any]) -> str:
    skills = summary["skills"]
    lines = [
        "# 前缀分叉（FORKED_FROM）",
        "",
        f"- 源 run：`{source_run}`",
        f"- 分叉目标：`{run_dir}`",
        f"- 复制的相位：{', '.join(summary['phases'])}",
        "",
        "## 复制了什么",
        "",
        "- 上述相位的整个相位目录（不含 `*.lock`）。",
        "- 运行级对象中由这些相位产生的部分：轨迹按编号里的相位标签，段、子任务"
        "类型与段归属按各自记录的 `phase_label`，切段审计按切它的学习相位，"
        "`failed_units.jsonl` 按 `stage`。",
    ]
    if skills["from_phase"]:
        lines += [
            f"- 技能库按 `{skills['from_phase']}` 的库工件重建（核对了工件与旁边"
            f"记录的 bank_reference 一致）；源 run 里另有 "
            f"{len(skills['dropped_later_products'])} 条技能在前缀之后才写出，"
            "没有复制。",
        ]
    else:
        lines += [
            "- 前缀里没有学习相位：技能库只保留 run 级导入的技能（没有来源记录的"
            f"技能）；源 run 里另有 {len(skills['dropped_later_products'])} "
            "条技能在前缀之后才写出，没有复制。",
        ]
    lines += [
        "",
        "## 没有复制什么",
        "",
        "- `manifest.json`：新 run 首次启动时按新配置冻结。",
        "- `pipeline_log.jsonl`、`stage0_summary.json`：新 run 自己写。",
        "- `llm_requests/`（用量账本和原始请求）：前缀的用量留在源 run，新 run 的"
        "用量只计它自己发出的调用。",
        "- `work/`、`driver.*`、`run.lock`：属于产生它们的那次运行。",
        "",
        "## 续跑行为",
        "",
        "- 首次启动时，被复制的相位按普通续跑路径通过：已完成的执行相位与学习相位"
        "直接返回存档摘要，均不发模型请求；前缀之后的第一个相位全新执行。",
        "",
        "## 对象计数",
        "",
        "| 类别 | 计数 |",
        "|---|---|",
        f"| skills | {skills['count']} |",
    ]
    for kind, number in summary["stores"].items():
        lines.append(f"| {kind} | {number} |")
    return "\n".join(lines) + "\n"
