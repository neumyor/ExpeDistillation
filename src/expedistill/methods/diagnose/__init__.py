"""Learn step 1: one diagnosis per failed trial (layer 6).

:func:`diagnose_trial` asks ``TRIAL_FAILURE_ANALYSIS`` about one failed
weak trial of a baseline or practice phase. The prompt shows what the
executor saw (the catalog and the playbook of a practice trial, the full
text of every card it opened; "(none)" for a bare baseline trial) and the
whole trial numbered from 0, read actions included. The diagnosis does not
read the profile, so the evidence stays independent of it.

A reply the diagnosis cannot use (a ``failing_step`` outside the trial's
steps, a ``scope`` outside ``subtask`` / ``general``, an empty ``reason``)
is sent back with the problem named (``DIAGNOSIS_CORRECTION``), at most
``REPAIRS`` times, then a ModelOutputError. A trial without a single step
has no step to blame: it is a ModelOutputError at once, with no call.
Nothing is written to disk here.
"""

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from expedistill import prompts
from expedistill.blocks.view import render_steps_view
from expedistill.composites.sheet_facts import SheetFacts
from expedistill.composites.trace import execution_instruction
from expedistill.core.answer_sheet import AnswerSheet
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.llm.call import call_model

__all__ = ["REPAIRS", "SCOPES", "Finding", "diagnose_trial"]

#: Business repairs of a reply the diagnosis cannot use, on top of
#: call_model's own JSON-format budget.
REPAIRS = 2

#: Where a failure belongs: one subtask, or no part in particular.
SCOPES = ("subtask", "general")

_PROMPT = "TRIAL_FAILURE_ANALYSIS"


@dataclass(frozen=True)
class Finding:
    """One diagnosed failed trial (one row of the findings ledger)."""

    sheet_id: str
    reason: str
    failing_step: int
    scope: str
    model_note: str = ""
    playbook_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Finding":
        return cls(**dict(data))


class _Rejected(Exception):
    """A parsed reply the diagnosis cannot use; its text completes the
    sentence "Your previous answer ..." of the repair note."""


def _text(parsed: Mapping[str, Any], key: str, *, required: bool) -> str:
    value = parsed.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise _Rejected(
            f"gave {key} {value!r}, which must be a "
            f"{'non-empty ' if required else ''}string")
    return value.strip()


def _read(parsed: Any, sheet_id: str, n_steps: int) -> Finding:
    if not isinstance(parsed, dict):
        raise _Rejected(f"was a JSON {type(parsed).__name__}, not an object")
    step = parsed.get("failing_step")
    if isinstance(step, bool) or not isinstance(step, int) \
            or not 0 <= step < n_steps:
        raise _Rejected(
            f"gave failing_step {step!r}; it must be an integer from 0 to "
            f"{n_steps - 1}, the number of a step of this trial")
    scope = parsed.get("scope")
    if scope not in SCOPES:
        raise _Rejected(
            f"gave scope {scope!r}; it must be \"subtask\" or \"general\"")
    return Finding(
        sheet_id=sheet_id,
        reason=_text(parsed, "reason", required=True),
        failing_step=step, scope=scope,
        model_note=_text(parsed, "model_note", required=False),
        playbook_note=_text(parsed, "playbook_note", required=False))


def _opened_text(facts: SheetFacts) -> str:
    return "\n\n".join(
        f"SKILL_ID: {skill_id} (first read at step {step})\n"
        f"{facts.opened_cards.get(skill_id, '(not in the store)')}"
        for skill_id, step in facts.opened.items()) or "(none)"


def diagnose_trial(
    sheet: AnswerSheet,
    facts: SheetFacts,
    *,
    handle: Any,
    instruction_for: Optional[Callable[[str], str]] = None,
    view_rules: Any = None,
    usage: Any = None,
    run_dir: Any = None,
) -> Finding:
    """Diagnose one failed trial; ``facts`` are the sheet's own
    (``composites.sheet_facts``)."""
    execution = sheet.execution
    if execution is None or facts.sheet_id != sheet.sheet_id:
        raise CodeBugError(
            f"sheet {sheet.sheet_id!r} has no execution or was handed the "
            f"facts of {facts.sheet_id!r}")
    steps = list(execution.steps or [])
    if not steps:
        raise ModelOutputError(
            f"{_PROMPT}: sheet {sheet.sheet_id!r} recorded no step, so no "
            "failing step can be named")
    variables = {
        "task_instruction": (execution_instruction(execution,
                                                   instruction_for)
                             or execution.task_ref),
        "trial_steps": render_steps_view(steps, view_rules),
        "last_step": str(len(steps) - 1),
        "skill_catalog": facts.catalog.strip() or "(none)",
        "playbook": facts.playbook.strip() or "(none)",
        "opened_skills": _opened_text(facts),
        "correction_note": "",
    }
    problem = "gave no usable reply"
    for _ in range(REPAIRS + 1):
        parsed = call_model(handle, _PROMPT, variables, output_json=True,
                            usage=usage, run_dir=run_dir)
        try:
            return _read(parsed, sheet.sheet_id, len(steps))
        except _Rejected as exc:
            problem = str(exc)
        variables["correction_note"] = prompts.get(
            "DIAGNOSIS_CORRECTION").format(problem=problem)
    raise ModelOutputError(
        f"{_PROMPT}: the answer still {problem} after {REPAIRS} repairs")
