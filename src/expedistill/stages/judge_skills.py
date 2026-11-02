"""Judge how a finished test phase used its delivered skill cards (layer 7).

Read-only on the run. For every with-bank trial of the phase:

1. one applicability call: of the cards the agent was shown (the delivery
   manifest, described exactly as the agent's card list described them),
   which had a trigger that actually arose in the trajectory;
2. for every card the agent opened (``skill_read_events``, the same read
   facts the rest of the system uses), one adherence call over the trajectory
   from the card's first read onward.

One JSON line per trial goes to ``<out_dir>/judgments.jsonl``, written only
when ALL the trial's calls succeeded, so a resume re-judges exactly the
trials that have no line yet. The aggregates are
``composites.trigger_metrics.trigger_metrics`` over those lines.

Where the inputs come from: the trial sheets are the phase's
``answer_sheets/``; the delivery manifest is the sheet's own
``artifacts["skill_folder"]`` when it carries one (live phases) and the
phase's ``units.jsonl`` row of the same unit otherwise (the test phase does
not copy the manifest onto its sheets); the card texts are the bank
artifact the phase received -- the latest learn artifact before the phase,
or ``bank_phase_label``.

Cost: one applicability call plus one adherence call per opened card, per
trial. A trial whose reply broke the output contract is retried once, then
reported in ``failed_trials`` and left unwritten; infrastructure errors do
not stop the other trials and make the call raise InfrastructureError at
the end, so a resume finishes them. A provider refusal (typically a
trajectory longer than the judge's window) fails only its trial, the same way.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Union

from expedistill.benchmarks.registry import resolve_plugin
from expedistill.blocks.adherence import (
    card_steps,
    judge_adherence,
    judge_applicability,
)
from expedistill.blocks.view import render_steps_view
from expedistill.composites import phase_bank, phase_report
from expedistill.composites.skill_use import skill_read_events
from expedistill.composites.trace import instruction_with_initial
from expedistill.composites.trigger_metrics import trigger_metrics
from expedistill.core import vocabulary
from expedistill.core.answer_sheet import AnswerSheet
from expedistill.core.models import Skill
from expedistill.infra.manifest import MANIFEST_NAME
from expedistill.infra.parallel import run_units
from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
)
from expedistill.infra.store import (
    append_jsonl,
    read_json,
    read_jsonl,
    write_json,
)
from expedistill.inject.skill_text import (
    DEFAULT_DESCRIPTION_MODE,
    description_text,
)
from expedistill.llm.call import ModelHandle
from expedistill.methods.review import attribution
from expedistill.stages.analysis_common import (
    bank_phase_labels,
    phase_number,
    prepare_out_dir,
)

__all__ = ["JUDGMENTS_NAME", "SUMMARY_NAME", "judge_phase"]

JUDGMENTS_NAME = "judgments.jsonl"
SUMMARY_NAME = "trigger_metrics.json"
UNITS_NAME = "units.jsonl"

#: Calls per judge reply that breaks the output contract: the first ask and
#: one resample (the exam blocks' repair budget).
_BLOCK_ATTEMPTS = 2


def judge_phase(
    run_dir: Union[str, Path],
    phase_label: str,
    out_dir: Union[str, Path],
    judge_handle: ModelHandle,
    *,
    arm: Optional[str] = "with_bank",
    workers: int = 1,
    usage: Any = None,
    bank_phase_label: Optional[str] = None,
    view_rules: Any = None,
    instruction_for: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Judge every ``arm`` trial of ``phase_label`` and aggregate.

    ``arm`` None takes every trajectory trial of the phase (a live phase has
    no arm). ``view_rules`` and ``instruction_for`` default to the run's
    benchmark plugin (read from the manifest); they are parameters so a
    caller can supply them without the benchmark installed. Returns
    ``{"phase_label", "arm", "bank_phase_label", "judge_model",
    "expected_trials", "judged_trials", "failed_trials", "metrics"}``; the
    same dict is written to ``<out_dir>/trigger_metrics.json``.
    """
    run_dir = Path(run_dir)
    phase_dir = phase_bank.phase_bank_dir(run_dir, phase_label)
    bank_label = bank_phase_label or _bank_label_before(run_dir, phase_label)
    out_dir = prepare_out_dir(run_dir, out_dir, {
        "run_dir": str(run_dir.resolve()), "phase_label": phase_label,
        "arm": arm, "bank_phase_label": bank_label,
        "judge_model": judge_handle.name})
    view_rules, instruction_for = _benchmark_hooks(
        run_dir, view_rules, instruction_for)

    bank = {row["skill_id"]: Skill.from_dict(row)
            for row in phase_bank.load_phase_bank(run_dir, bank_label)}
    unit_manifests = _unit_manifests(phase_dir)
    sheets = _judgeable_sheets(phase_dir, phase_label, arm, unit_manifests)

    judgments_path = out_dir / JUDGMENTS_NAME
    done = {row["sheet_id"] for row in read_jsonl(judgments_path)}
    infra_failed: List[str] = []
    failed: List[Dict[str, str]] = []

    def judge(item: Any) -> None:
        sheet, manifest = item
        try:
            row = _judge_trial(
                sheet, manifest, bank, view_rules=view_rules,
                instruction_for=instruction_for, handle=judge_handle,
                usage=usage, out_dir=out_dir)
        except InfrastructureError:
            infra_failed.append(sheet.sheet_id)
            return
        except (ModelOutputError, ProviderRefusalError) as exc:
            # A refusal here is almost always one trajectory too long for
            # the judge's window: a property of that trial, not of the run.
            failed.append({"sheet_id": sheet.sheet_id,
                           "error_class": type(exc).__name__,
                           "error": " ".join(str(exc).split())[:500]})
            return
        append_jsonl(judgments_path, row)

    run_units(judge, [(sheet, manifest) for sheet, manifest in sheets
                      if sheet.sheet_id not in done], workers)

    expected = {sheet.sheet_id for sheet, _ in sheets}
    rows = sorted((row for row in read_jsonl(judgments_path)
                   if row["sheet_id"] in expected),
                  key=lambda row: row["sheet_id"])
    summary = {
        "phase_label": phase_label,
        "arm": arm,
        "bank_phase_label": bank_label,
        "judge_model": judge_handle.name,
        "expected_trials": len(expected),
        "judged_trials": len(rows),
        "failed_trials": sorted(failed, key=lambda item: item["sheet_id"]),
        "metrics": trigger_metrics(rows),
    }
    write_json(out_dir / SUMMARY_NAME, summary, indent=2)
    if infra_failed:
        raise InfrastructureError(
            f"{len(infra_failed)} trial(s) of {phase_label!r} are pending "
            "on infrastructure errors; resume to judge them")
    return summary


def _bank_label_before(run_dir: Path, phase_label: str) -> str:
    """The bank the phase received: the latest artifact of an earlier phase."""
    number = phase_number(phase_label)
    earlier = [label for label in bank_phase_labels(run_dir)
               if phase_number(label) < number]
    if not earlier:
        raise ConfigError(
            f"no phase before {phase_label!r} left a skill bank artifact "
            f"in {run_dir}")
    return earlier[-1]


def _benchmark_hooks(run_dir: Path, view_rules: Any,
                     instruction_for: Optional[Callable[[str], str]]):
    """The run's observation truncation rules and task-instruction lookup,
    from its benchmark plugin unless the caller supplied them."""
    if view_rules is not None and instruction_for is not None:
        return view_rules, instruction_for
    path = run_dir / MANIFEST_NAME
    if not path.is_file():
        raise ConfigError(
            f"{run_dir} has no {MANIFEST_NAME}; pass view_rules and "
            "instruction_for explicitly")
    benchmark = read_json(path)["config"]["benchmark"]
    plugin = resolve_plugin(benchmark)
    if instruction_for is None:
        @lru_cache(maxsize=None)
        def instruction_for(task_ref: str) -> str:
            return attribution.task_instruction_for(benchmark, task_ref)
    return (plugin.view_rules if view_rules is None else view_rules,
            instruction_for)


def _unit_manifests(phase_dir: Path) -> Dict[str, Mapping[str, Any]]:
    """``{unit_id: folder manifest}`` from the phase's resolved unit rows."""
    manifests: Dict[str, Mapping[str, Any]] = {}
    for row in read_jsonl(phase_dir / UNITS_NAME):
        manifest = (row.get("aux") or {}).get("skill_folder")
        if manifest:
            manifests[row["unit_id"]] = manifest
    return manifests


def _judgeable_sheets(phase_dir: Path, phase_label: str, arm: Optional[str],
                      unit_manifests: Mapping[str, Mapping[str, Any]]):
    """``[(sheet, manifest)]``: the arm's present trajectory trials that were
    delivered at least one card, in sheet order."""
    report = phase_report.read_phase_report(
        phase_dir, phase_kind="test", learning_usable=False)
    if report is None:
        raise ConfigError(
            f"phase {phase_label!r} ({phase_dir}) has no trial-level "
            "answer sheets; it did not finish or is not an execute phase")
    out = []
    for sheet in report.sheets:
        execution = sheet.execution
        if execution is None or execution.kind != "trajectory":
            continue
        if arm is not None and execution.arm != arm:
            continue
        manifest = (execution.artifacts.get("skill_folder")
                    or unit_manifests.get(execution.unit_id))
        if manifest and manifest.get("files"):
            out.append((sheet, manifest))
    if not out:
        raise ConfigError(
            f"phase {phase_label!r} has no {arm!r} trial that was "
            "delivered a skill folder; there is nothing to judge")
    return out


def _cards(manifest: Mapping[str, Any],
           bank: Mapping[str, Skill]) -> List[Dict[str, str]]:
    """The card list the agent saw: each delivered skill with the
    description the folder's SKILL.md header carried."""
    mode = manifest.get("description_mode") or DEFAULT_DESCRIPTION_MODE
    cards = []
    for entry in manifest["files"]:
        skill = bank.get(entry["skill_id"])
        if skill is None or skill.version != entry["version"]:
            raise ConfigError(
                f"the delivery manifest lists {entry['skill_id']!r} at "
                f"version {entry['version']}, which the bank artifact does "
                "not carry; pass the bank_phase_label the phase received")
        cards.append({"skill_id": skill.skill_id,
                      "description": description_text(skill, mode)})
    return cards


def _repaired(call: Callable[[], Any]) -> Any:
    """``call()``, resampled once when its reply broke the output contract."""
    for attempt in range(_BLOCK_ATTEMPTS):
        try:
            return call()
        except ModelOutputError:
            if attempt == _BLOCK_ATTEMPTS - 1:
                raise


def _outcome(sheet: AnswerSheet) -> Optional[bool]:
    verdict = sheet.grading.verdict if sheet.grading is not None else None
    return {vocabulary.RUN_SUCCESS: True,
            vocabulary.RUN_FAILURE: False}.get(verdict)


def _judge_trial(sheet: AnswerSheet, manifest: Mapping[str, Any],
                 bank: Mapping[str, Skill], *, view_rules: Any,
                 instruction_for: Callable[[str], str],
                 handle: ModelHandle, usage: Any,
                 out_dir: Path) -> Dict[str, Any]:
    """One trial's judgment row; every model call must succeed."""
    execution = sheet.execution
    steps = list(execution.steps or [])
    cards = _cards(manifest, bank)
    metrics = execution.metrics or {}
    instruction = instruction_with_initial(
        metrics,
        instruction_for(execution.task_ref) or str(
            metrics.get("initial_prompt") or ""))

    applicability = _repaired(lambda: judge_applicability(
        instruction, cards, render_steps_view(steps, view_rules),
        len(steps), handle, usage=usage, run_dir=out_dir))

    opened: List[str] = []
    adherence: Dict[str, List[str]] = {}
    detail: Dict[str, List[Dict[str, Any]]] = {}
    for event in skill_read_events(execution, manifest):
        skill_id, first = event["skill_id"], event["step_index"]
        opened.append(skill_id)
        content = bank[skill_id].content
        if not card_steps(content):
            adherence[skill_id], detail[skill_id] = [], []
            continue
        window = render_steps_view(
            steps[first:], view_rules, start_index=first)
        items = _repaired(lambda: judge_adherence(
            content, window, handle, usage=usage, run_dir=out_dir))
        detail[skill_id] = items
        adherence[skill_id] = [item["status"] for item in items]

    return {
        "sheet_id": sheet.sheet_id,
        "task": execution.task_ref,
        "attempt": execution.sample_index,
        "delivered": [card["skill_id"] for card in cards],
        "opened": opened,
        "applicable": {skill_id: item["applicable"]
                       for skill_id, item in applicability.items()},
        "applicability": applicability,
        "adherence": adherence,
        "adherence_detail": detail,
        "correct": _outcome(sheet),
    }
