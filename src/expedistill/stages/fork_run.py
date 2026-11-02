"""Fork the finished phase prefix of one run into a fresh run directory.

A formal run is sometimes taken further than an earlier run went: one more
learn-and-check round before the final test, or the same bank handed to a
different executor. Redoing the early phases would repeat hours of rollouts
and model calls whose results are already on disk, so the new run starts
from a copy of them: the first K phases' directories and the run-level
object stores. Nothing else is copied -- the manifest is frozen by the new
run's own first start (against the NEW config), and the ledger, the usage
records, the lock and the workdirs belong to the run that produced them.

On that first start the copied phases take the ordinary resume path: a
finished execute phase re-derives its views from the stored trials and a
finished learn phase returns its stored summary, both without a model call,
and the first phase after the prefix runs fresh.

What makes a copy of the prefix faithful:

* The target config starts with the same K phases, defined the same way,
  and agrees with the source on everything else that acts on them (models,
  depths, rules, budgets, the train split). It may differ in the later
  phases, the test split and the task list.
* A learn phase decides differently at its last check, and the sequence's
  last learn phase also runs the configured curation. Both are derived
  from the sequence, so a copied learn phase must sit on the same side of
  both tests in the source and the target sequence; otherwise the copy
  would stand for decisions the new sequence would not have made.
* The skill store is run-level and every learn phase rewrites it in place
  (statuses, groups, merge products), so a finished source's store is the
  state after its LAST learn phase, not after the prefix. When a learn
  phase follows the prefix in the source, the skills are rebuilt from the
  bank artifact of the prefix's last learn phase (checked against the
  digest recorded beside it), and every other stored skill must be provably
  a later curation product. An execute phase after the prefix would have
  written exams, attempts or trajectories into the shared stores, which
  cannot be separated by copying, so that is refused; the test phase keeps
  its own stores and is not.
* The source must be finished and idle: its prefix phases carry their
  completion records and no run lock is left behind.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Set

from expedistill.composites import decision
from expedistill.composites.phase_bank import (
    load_phase_bank,
    load_phase_bank_summary,
    phase_bank_dir,
)
from expedistill.core.config import (
    CONFIG_SCHEMA_VERSION,
    PHASE_ROLE_TEST,
    ExecutePhaseSpec,
    ExperimentConfig,
    LearnPhaseSpec,
    load_config,
    phase_label,
)
from expedistill.core.ids import make_skill_id
from expedistill.core.models import Skill
from expedistill.infra.manifest import MANIFEST_NAME
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import LOCK_NAME, RunLock
from expedistill.infra.store import Store, object_id_of_file, read_json
from expedistill.inject import bank_reference

__all__ = ["fork_run"]

#: The run-level directories that hold the shared object stores and their
#: ledgers. The skill kind inside them is handled separately.
_STORE_DIRS = ("stage1_baseline", "stage2_segments", "stage3_generation",
               "stage4_acceptance")

#: Run-root files carried over as lineage records of the source's own
#: reuse (where its stage-1 trajectories came from).
_LINEAGE_FILES = ("ASSEMBLED_FROM.md",)

#: The completion records the phase drivers write last. The names are
#: those of phase_execute.phase_disk.SUMMARY_NAME and
#: phase_learn.SUMMARY_NAME; the drivers load lazily, so they are not
#: imported here (a test pins the equality).
_EXECUTE_SUMMARY_NAME = "phase_summary.json"
_LEARN_SUMMARY_NAME = "learn_summary.json"

_FORKED_FROM_NAME = "FORKED_FROM.md"
_THROUGH_RE = re.compile(r"p([0-9]{2,})")

#: Locks and torn-write sidecars are never part of a copy.
_IGNORE = shutil.ignore_patterns("*.lock")


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
    base_label = _plan_skills(source, count, labels)
    if run_dir.exists() and any(run_dir.iterdir()):
        raise ConfigError(
            f"{run_dir} is not empty; fork into a fresh run directory "
            "(the new run freezes its manifest on its first start)")

    lock = RunLock(run_dir)
    warning = lock.acquire()
    try:
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        summary = _copy_prefix(source_run, run_dir, source, labels,
                               base_label)
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


def _comparable(config: ExperimentConfig) -> Dict[str, Any]:
    """Everything of a config that acts on the prefix phases, apart from
    the phase definitions themselves: the later phases, the test split and
    the task list may differ."""
    payload = config.to_dict()
    for key in ("sequence", "phases", "tasks", "prompts_digests"):
        payload.pop(key, None)
    split = dict(payload.get("split") or {})
    for key in ("test", "reserved"):
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
    source_rest, target_rest = _comparable(source), _comparable(target)
    differing = sorted(key for key in set(source_rest) | set(target_rest)
                       if source_rest.get(key) != target_rest.get(key))
    if differing:
        raise ConfigError(
            f"the target config differs from the source in {differing}; "
            "those act on the copied phases")
    for index, name in enumerate(names):
        if not isinstance(source.phases[name], LearnPhaseSpec):
            continue
        if (_checks_left(source, index) > 0) != (_checks_left(target, index) > 0):
            raise ConfigError(
                f"learn phase {phase_label(index, name)} has a later check "
                "in one sequence and none in the other: it decides "
                "differently at its last check")
        if _is_last_learn(source, index) != _is_last_learn(target, index):
            raise ConfigError(
                f"learn phase {phase_label(index, name)} is the sequence's "
                "last learn phase in one sequence and not in the other: "
                "only the last runs the configured curation")


def _checks_left(config: ExperimentConfig, index: int) -> int:
    return decision.remaining_checks(
        [config.phases[name].role for name in config.sequence], index)


def _is_last_learn(config: ExperimentConfig, index: int) -> bool:
    return not any(isinstance(config.phases[name], LearnPhaseSpec)
                   for name in config.sequence[index + 1:])


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


def _plan_skills(source: ExperimentConfig, count: int,
                 labels: Sequence[str]) -> str:
    """Whether the skill store must be rebuilt, and from which phase's
    artifact: the label of the prefix's last learn phase, or ``""`` when
    the source store already is the state after the prefix."""
    later = [source.phases[name] for name in source.sequence[count:]]
    writers = [spec for spec in later if isinstance(spec, ExecutePhaseSpec)
               and spec.role != PHASE_ROLE_TEST]
    if writers:
        raise ConfigError(
            "a phase after the prefix of the source run writes exams, "
            "attempts or trajectories into the shared stores, which "
            "cannot be separated by copying; fork through a later phase")
    if not any(isinstance(spec, LearnPhaseSpec) for spec in later):
        return ""
    learn = [label for index, label in enumerate(labels)
             if isinstance(source.phases[source.sequence[index]],
                           LearnPhaseSpec)]
    if not learn:
        raise ConfigError(
            "a learn phase follows the prefix in the source run and the "
            "prefix has none: the skill store at the end of the prefix "
            "cannot be rebuilt from a bank artifact")
    return learn[-1]


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------

def _copy_prefix(source_run: Path, run_dir: Path, source: ExperimentConfig,
                 labels: Sequence[str], base_label: str) -> Dict[str, Any]:
    for label in labels:
        shutil.copytree(phase_bank_dir(source_run, label),
                        phase_bank_dir(run_dir, label), ignore=_IGNORE)
    for name in _STORE_DIRS:
        if (source_run / name).is_dir():
            shutil.copytree(source_run / name, run_dir / name,
                            ignore=_IGNORE)
    for name in _LINEAGE_FILES:
        if (source_run / name).is_file():
            shutil.copy2(source_run / name, run_dir / name)
    store = Store(run_dir)
    dropped: List[str] = []
    if base_label:
        dropped = _rebuild_skills(source_run, store, base_label)
    summary = {
        "phases": list(labels),
        "skills": {"mode": "rebuilt" if base_label else "verbatim",
                   "from_phase": base_label or None,
                   "count": len(store.ids("skills")),
                   "dropped_later_products": dropped},
        "stores": {kind: len(store.ids(kind))
                   for kind in ("trajectories", "subtasks", "exams",
                                "attempts")},
    }
    (run_dir / _FORKED_FROM_NAME).write_text(
        _render_forked_from(source_run, run_dir, source, summary),
        encoding="utf-8")
    return summary


def _rebuild_skills(source_run: Path, store: Store,
                    base_label: str) -> List[str]:
    """Replace the copied skills by the bank artifact of ``base_label``
    and return the ids dropped as later curation products."""
    rows = load_phase_bank(source_run, base_label)
    recorded = load_phase_bank_summary(source_run, base_label).get(
        "bank_reference")
    if bank_reference(rows) != recorded:
        raise ConfigError(
            f"the bank artifact of {base_label} no longer matches the "
            f"digest recorded beside it ({recorded}); the source run was "
            "modified after it finished")
    members = {row["skill_id"] for row in rows}
    directory = store.dir_for("skills")
    stored = {object_id_of_file(path): Skill.from_dict(read_json(path))
              for path in directory.glob("*.json")}
    missing = sorted(members - set(stored))
    if missing:
        raise ConfigError(
            f"the bank artifact of {base_label} names skill(s) {missing} "
            "that the source store does not hold")
    extra = _later_products(stored, members)
    _check_requirement_subtasks(store, members)
    for path in directory.glob("*.json"):
        path.unlink()
    for row in rows:
        skill = Skill.from_dict(row)
        if skill.to_dict() != row:
            raise ConfigError(
                f"skill {skill.skill_id} in the bank artifact of "
                f"{base_label} is not at the current schema; fork-run "
                "does not migrate skills")
        store.save(skill, "skills")
    return extra


def _later_products(stored: Mapping[str, Skill],
                    members: Set[str]) -> List[str]:
    """The stored skills outside the artifact, each proven to be a curation
    product of artifact members (directly or through earlier products)."""
    extra = {skill_id for skill_id in stored if skill_id not in members}
    produced: Set[str] = set()
    changed = True
    while changed:
        changed = False
        for skill_id in sorted(extra - produced):
            sources = stored[skill_id].merged_from
            if sources and all(source in members | produced
                               for source in sources):
                produced.add(skill_id)
                changed = True
    unexplained = sorted(extra - produced)
    if unexplained:
        raise ConfigError(
            f"stored skill(s) {unexplained} are neither in the bank "
            "artifact nor merges of its members; a later phase may have "
            "written them, or an earlier phase discarded them outside the "
            "artifact -- fork-run cannot tell which")
    return sorted(produced)


def _check_requirement_subtasks(store: Store, members: Set[str]) -> None:
    """A learn phase writes a requirement skill together with a subtask of
    its own (``req/...``); one whose skill is not a member was written
    after the prefix and would be carried into the copy."""
    foreign = sorted(
        subtask_id for subtask_id in store.ids("subtasks")
        if subtask_id.startswith("req/")
        and make_skill_id(subtask_id) not in members)
    if foreign:
        raise ConfigError(
            f"subtask(s) {foreign} belong to skills outside the bank "
            "artifact; they were written after the prefix")


def _render_forked_from(source_run: Path, run_dir: Path,
                        source: ExperimentConfig,
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
        f"- 运行级对象库目录 {', '.join(_STORE_DIRS)}（同样不含 `*.lock`）。",
    ]
    if skills["mode"] == "rebuilt":
        lines += [
            f"- 技能库按 `{skills['from_phase']}` 的库产物重建（源 run 在前缀之后"
            "还有 learn 相位，运行级技能库已被它改写）。重建前核对了库产物与旁边"
            f"记录的 bank_reference 一致；丢弃了 {len(skills['dropped_later_products'])}"
            " 条后续整理产物（均为前缀成员的合并产物）。",
        ]
    else:
        lines += ["- 技能库原样复制（源 run 在前缀之后没有 learn 相位）。"]
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
        "- 首次启动时，被复制的相位按普通续跑路径通过：已完成的 execute 相位由存档"
        "的 trial 重建视图，已完成的 learn 相位直接返回存档摘要，均不发模型请求；"
        "前缀之后的第一个相位全新执行。",
        "- 前缀 learn 相位记录的 `checks_left` 是源序列下的值；它在两条序列里都大于 0"
        "（或都等于 0），决策行为一致（分叉时已校验）。",
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
