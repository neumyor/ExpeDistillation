"""Experiment 3's segmentation metrics over finished runs (read-only).

Table 3 of the research plan compares K=1 (one segment per trajectory) with
K=5 (the default) and reports four things: test avg@3, the skill bank size,
the share of skills that do NOT apply at task start, and the share of test
trials that opened a skill after the first environment action.

The first two come from artifacts that already exist (test answer sheets via
``paper_tables`` and each learn phase's ``skill_bank.jsonl``). The bank size
counts the ``active`` cards (the ones delivery hands out); the artifact also
carries the ``discarded`` ones, reported beside it. The other two are
computed here:

* **anchor** -- every active card names the segments its material came
  from (``source_subtasks``, the strong success and weak failure segments
  of its subtask type). Each segment records ``step_range`` in the shared
  Subtask store, and a segment's start is where the subtask begins in its
  trajectory. A card's anchor is the minimum start over its sources;
  anchor 0 means it applies at task start. An imported card has no
  sources and stays unknown.
* **mid-task open** -- a folder trial's stored steps include one step per
  ``ReadSkill[<location>]`` reply. The trial counts as a mid-task open when
  at least one read follows the first environment action.

Both are program facts (no judge model), fixed before the K=1 run starts.
"""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from expedistill.benchmarks.skill_folder import read_target
from expedistill.composites import paired_stats, phase_bank
from expedistill.core.models import Subtask
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import Store, read_json, read_jsonl
from expedistill.stages import paper_tables
from expedistill.stages.analysis_common import bank_phase_labels

__all__ = [
    "anchor_distribution",
    "mid_task_open_metrics",
    "read_facts",
    "render",
    "run",
    "run_metrics",
    "skill_anchor",
]

_TEST_ROLE = "test"
_ACTIVE = "active"


def _segment_start(store: Store, subtask_id: str) -> Optional[int]:
    if not store.exists("subtasks", subtask_id):
        return None
    subtask = store.load("subtasks", subtask_id, Subtask)
    return int(subtask.step_range[0])


def skill_anchor(skill: Mapping[str, Any], store: Store) -> Optional[int]:
    """The earliest segment start behind one card, or None when unresolved.

    The minimum over the sources answers "can this card apply at task
    start?": any source at step 0 makes the answer yes.
    """
    starts = [start for start in (
        _segment_start(store, source)
        for source in skill.get("source_subtasks") or [])
        if start is not None]
    return min(starts) if starts else None


def anchor_distribution(skills: Iterable[Mapping[str, Any]],
                        store: Store) -> Dict[str, Any]:
    """How many active cards of a bank artifact apply at task start,
    mid-task, or unknown; the discarded members are only counted."""
    rows = list(skills)
    active = [skill for skill in rows if skill.get("status") == _ACTIVE]
    anchors: List[Optional[int]] = [skill_anchor(skill, store)
                                    for skill in active]
    known = [anchor for anchor in anchors if anchor is not None]
    at_start = sum(1 for anchor in known if anchor == 0)
    mid_task = sum(1 for anchor in known if anchor > 0)
    return {
        "skills": len(anchors),
        "discarded": len(rows) - len(active),
        "known": len(known),
        "unknown": len(anchors) - len(known),
        "at_task_start": at_start,
        "mid_task": mid_task,
        "mid_task_share": (mid_task / len(known)) if known else None,
        "anchor_steps": sorted(anchor for anchor in known),
    }


def read_facts(steps: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """One trial's read facts: did it open a card, and when relative to play?"""
    reads = []
    first_env: Optional[int] = None
    for index, step in enumerate(steps):
        action = str(step.get("action", ""))
        if read_target(action, ignore_case=True) is not None:
            reads.append(index)
        elif first_env is None:
            first_env = index
    mid_task = first_env is not None and any(
        index > first_env for index in reads)
    return {
        "opened": bool(reads),
        "reads": len(reads),
        "mid_task_open": bool(mid_task),
        "first_env_step": first_env,
    }


def mid_task_open_metrics(trials: Iterable[Sequence[Mapping[str, Any]]]
                          ) -> Dict[str, Any]:
    """The trial-level rates: any open, and open after the first action."""
    total = opened = mid_task = 0
    for steps in trials:
        facts = read_facts(steps)
        total += 1
        opened += facts["opened"]
        mid_task += facts["mid_task_open"]
    return {
        "trials": total,
        "opened": opened,
        "mid_task_open": mid_task,
        "open_rate": (opened / total) if total else None,
        "mid_task_open_rate": (mid_task / total) if total else None,
    }


def _bank(run_dir: Path, label: str) -> List[Dict[str, Any]]:
    path = phase_bank.phase_bank_path(run_dir, label)
    if not path.exists():
        raise ConfigError(f"{run_dir}: bank phase {label} has no {path.name}")
    return read_jsonl(path)


def _test_phase(run_dir: Path) -> Tuple[str, Path]:
    phases_dir = run_dir / phase_bank.PHASES_DIR
    for entry in sorted(phases_dir.iterdir(), reverse=True):
        summary = entry / "phase_summary.json"
        if summary.is_file() and read_json(summary).get("role") == _TEST_ROLE:
            return f"{entry.name.split('_', 1)[0]}:{entry.name.split('_', 1)[1]}", entry
    raise ConfigError(f"{run_dir}: no test phase summary found")


def _trial_steps(phase_dir: Path) -> List[List[Dict[str, Any]]]:
    """The with-bank trials' stored steps, in answer-sheet order."""
    trials = []
    for path in sorted((phase_dir / "answer_sheets").glob("*.json")):
        sheet = read_json(path)
        execution = sheet.get("execution") or {}
        if execution.get("arm") != "with_bank":
            continue
        trials.append(list(execution.get("steps") or []))
    return trials


def run_metrics(label: str, run_dir: Any) -> Dict[str, Any]:
    """One run's Table-3 metrics (read-only; never writes the run)."""
    run = Path(run_dir).resolve()
    arm = paper_tables.load_arm(run)
    summary = arm["meta"]
    with_bank = arm["with_bank"]
    trials = sum(len(scores) for scores in with_bank.values())
    store = Store(run)
    bank_labels = bank_phase_labels(run)
    if not bank_labels:
        raise ConfigError(f"{run}: no learn phase left a skill bank")
    initial = _bank(run, bank_labels[0])
    final = _bank(run, bank_labels[-1])
    label_name, phase_dir = _test_phase(run)
    metrics = paired_stats.arm_metrics(with_bank)
    return {
        "label": label,
        "run_dir": str(run),
        "test_phase": label_name,
        "control_source": summary.get("control_source"),
        "tasks": len(with_bank),
        "trials": trials,
        "avg_at_n": metrics["avg_at_n"],
        "pass_at_n": metrics["pass_at_n"],
        "infra_missing": summary.get("infra_missing"),
        "bank_phases": bank_labels,
        "initial_bank": {"phase": bank_labels[0],
                         **anchor_distribution(initial, store)},
        "final_bank": {"phase": bank_labels[-1],
                       **anchor_distribution(final, store)},
        "reads": mid_task_open_metrics(_trial_steps(phase_dir)),
    }


def _percent(numerator: Optional[int], denominator: Optional[int]) -> str:
    if numerator is None or not denominator:
        return "—"
    return f"{numerator / denominator:.1%}"


def render(arms: Sequence[Mapping[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """The Table-3 markdown and its JSON payload for the given runs."""
    labels = [str(arm["label"]) for arm in arms]
    lines = [
        "| 指标 | " + " | ".join(labels) + " |",
        "|---|" + "|".join("---" for _ in labels) + "|",
    ]

    def row(name: str, values: Sequence[str]) -> None:
        lines.append(f"| {name} | " + " | ".join(values) + " |")

    row("测试任务数", [str(arm["tasks"]) for arm in arms])
    row("测试 trial 数", [str(arm["trials"]) for arm in arms])
    row("avg@3", [_percent_value(arm["avg_at_n"]) for arm in arms])
    row("pass@3", [_percent_value(arm["pass_at_n"]) for arm in arms])
    row("初始技能库大小（在用）",
        [str(arm["initial_bank"]["skills"]) for arm in arms])
    row("最终技能库大小（在用）",
        [str(arm["final_bank"]["skills"]) for arm in arms])
    row("最终库·已删除", [str(arm["final_bank"]["discarded"]) for arm in arms])
    row("初始库·不在任务开始适用",
        [_percent(arm["initial_bank"]["mid_task"],
                  arm["initial_bank"]["known"]) for arm in arms])
    row("最终库·不在任务开始适用",
        [_percent(arm["final_bank"]["mid_task"],
                  arm["final_bank"]["known"]) for arm in arms])
    row("最终库·anchor 无法解析",
        [str(arm["final_bank"]["unknown"]) for arm in arms])
    row("任务中途打开率",
        [_percent(arm["reads"]["mid_task_open"], arm["reads"]["trials"])
         for arm in arms])
    row("任意打开率",
        [_percent(arm["reads"]["opened"], arm["reads"]["trials"])
         for arm in arms])
    row("对照来源", [str(arm["control_source"] or "—") for arm in arms])
    row("run", [str(arm["run_dir"]) for arm in arms])
    payload = {"arms": list(arms)}
    return "\n".join(lines), payload


def _percent_value(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1%}"


def _parse_arm(value: str) -> Tuple[str, Path]:
    # The label may itself contain '=' ("K=5=..."), so split at the LAST one.
    label, separator, run_dir = value.rpartition("=")
    if not separator or not label.strip() or not run_dir.strip():
        raise ConfigError(
            f"--arm expects LABEL=RUN_DIR, got {value!r}")
    return label.strip(), Path(run_dir.strip())


def run(arm_specs: Sequence[str], out_md: Optional[Path] = None,
        out_json: Optional[Path] = None) -> str:
    """Read the named runs, render the table, optionally write it out."""
    if not arm_specs:
        raise ConfigError("at least one --arm LABEL=RUN_DIR is required")
    parsed = [_parse_arm(spec) for spec in arm_specs]
    for _, run_dir in parsed:
        resolved = run_dir.resolve()
        for out in (out_md, out_json):
            if out is None:
                continue
            out = Path(out).resolve()
            if out == resolved or resolved in out.parents:
                raise ConfigError(
                    f"the output {out} is inside the run {resolved}; an "
                    "analysis never writes into the run it reads")
    arms = [run_metrics(label, run_dir) for label, run_dir in parsed]
    table, payload = render(arms)
    if out_md is not None:
        Path(out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(out_md).write_text(table + "\n", encoding="utf-8")
    if out_json is not None:
        Path(out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(out_json).write_text(
            json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8")
    return table
