"""Learn step 5: rewrite one side of a card (layer 6).

* :func:`revise_trigger` (``REVISE_TRIGGER``) returns new ``summary`` and
  ``when_to_use`` from trigger evidence; :func:`revise_content`
  (``REVISE_CONTENT``) returns new ``steps`` and ``not_applicable_when``
  from content evidence. Each touches only its own side.
* :func:`diagnosis_text` renders the evidence's diagnoses for either
  prompt, one ``REVISION_DIAGNOSIS`` entry per failed trial.
* :func:`apply_revision` lands one operation with the version guard: the
  revision plan (the version the rewrite starts from) is written before
  the model call, the new version is saved with its provenance, and the
  receipt follows. A resumed phase skips a receipted operation, and a card
  already one version past its plan gets its receipt without a new call.

A rewrite whose fields equal the old ones is no change (receipt
``unchanged_after_revision``); a rewrite whose output stays malformed after
``REVISION_ATTEMPTS`` leaves the card as it is (receipt
``revision_failed``). Either way only the receipt is written.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Sequence, Tuple

from expedistill import prompts
from expedistill.blocks.author import (
    parse_content_fields,
    parse_trigger_fields,
)
from expedistill.composites import learn_ledger as ledger
from expedistill.composites.provenance import record_version
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import SKILL_SIDES, Skill
from expedistill.core.trace import Provenance, TraceRef
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.inject.skill_text import render_card
from expedistill.llm.call import call_model
from expedistill.prompts.profile import profile_section

__all__ = [
    "OUTCOME_OF_SIDE",
    "REVISION_ATTEMPTS",
    "Revision",
    "apply_revision",
    "diagnosis_text",
    "revise_content",
    "revise_trigger",
]

#: Attempts of one rewrite whose output is malformed (each on top of
#: call_model's own JSON-format budget).
REVISION_ATTEMPTS = 2

#: The receipt of a rewrite that produced a new version, per side.
OUTCOME_OF_SIDE = {"content": "content_revised",
                   "trigger": "trigger_revised"}


@dataclass(frozen=True)
class Revision:
    """One rewrite's result: the side, its new field values and why."""

    side: str
    fields: Dict[str, Any]
    reason: str

    def unchanged(self, skill: Skill) -> bool:
        return all(getattr(skill, name) == value
                   for name, value in self.fields.items())


def diagnosis_text(items: Sequence[Tuple[int, str]]) -> str:
    """The rewrite prompts' diagnosis section: one entry per failed trial,
    ``(failing_step, reason)`` in evidence order."""
    template = prompts.get("REVISION_DIAGNOSIS")
    return "\n\n".join(
        template.format(number=number, failing_step=step, reason=reason)
        for number, (step, reason) in enumerate(items, start=1))


def _reason(prompt: str, parsed: Any) -> str:
    value = parsed.get("revision_reason") if isinstance(parsed, dict) \
        else None
    if value is not None and not isinstance(value, str):
        raise ModelOutputError(
            f"{prompt}: revision_reason must be a string, got {value!r}")
    return (value or "").strip()


def _with_attempts(prompt: str, ask: Callable[[], Revision]) -> Revision:
    last: Any = None
    for _ in range(REVISION_ATTEMPTS):
        try:
            return ask()
        except ModelOutputError as exc:
            last = exc
    raise ModelOutputError(
        f"{prompt}: no valid rewrite after {REVISION_ATTEMPTS} attempts: "
        f"{last}")


def _profile(handle: Any, profile: str) -> str:
    return profile_section("skill_reader", profile,
                           handle.prompt_defaults.get("agent_kind"))


def revise_trigger(skill: Skill, *, catalog: str, evidence_steps: str,
                   diagnosis: str, playbook: str, profile: str, handle: Any,
                   usage: Any = None, run_dir: Any = None) -> Revision:
    """New ``summary`` and ``when_to_use`` for ``skill``."""
    prompt = "REVISE_TRIGGER"
    variables = {
        "current_summary": skill.summary,
        "current_when_to_use": skill.when_to_use,
        "skill_catalog": catalog.strip() or "(none)",
        "playbook": playbook.strip() or "(none)",
        "evidence_steps": evidence_steps,
        "diagnosis": diagnosis,
        "profile_section": _profile(handle, profile),
    }

    def ask() -> Revision:
        parsed = call_model(handle, prompt, variables, output_json=True,
                            usage=usage, run_dir=run_dir)
        return Revision(side="trigger",
                        fields=parse_trigger_fields(prompt, parsed),
                        reason=_reason(prompt, parsed))

    return _with_attempts(prompt, ask)


def revise_content(skill: Skill, *, evidence_steps: str, diagnosis: str,
                   playbook: str, profile: str, handle: Any,
                   usage: Any = None, run_dir: Any = None) -> Revision:
    """New ``steps`` and ``not_applicable_when`` for ``skill``."""
    prompt = "REVISE_CONTENT"
    variables = {
        "skill_card": render_card(skill),
        "playbook": playbook.strip() or "(none)",
        "evidence_steps": evidence_steps,
        "diagnosis": diagnosis,
        "profile_section": _profile(handle, profile),
    }

    def ask() -> Revision:
        parsed = call_model(handle, prompt, variables, output_json=True,
                            usage=usage, run_dir=run_dir)
        return Revision(side="content",
                        fields=parse_content_fields(prompt, parsed),
                        reason=_reason(prompt, parsed))

    return _with_attempts(prompt, ask)


def apply_revision(*, store: Any, phase: Any, row: TaskListRow,
                   phase_dir: Any, run_dir: Any,
                   sources: Sequence[TraceRef],
                   write: Callable[[Skill], Revision]) -> str:
    """Land one rewrite operation; returns the receipt's outcome
    (``receipted`` when an earlier call already landed it).

    ``write`` produces the rewrite of the stored card (the model call);
    ``sources`` are the evidence positions behind the new version.
    """
    if ledger.receipt_of(phase, row, phase_dir) is not None:
        return "receipted"
    side = row.action.split("_", 1)[1]
    if side not in SKILL_SIDES:
        raise CodeBugError(f"{row.action!r} is not a rewrite operation")
    skill = store.load("skills", row.target_id, Skill)
    if skill.status not in ledger.ACTABLE_STATUSES:
        raise CodeBugError(
            f"a {row.action} of {skill.skill_id!r} was planned, but the card "
            f"is {skill.status!r}")
    from_version = ledger.revision_plan(phase, row, phase_dir)
    if from_version is not None:
        if skill.version == from_version + 1:
            ledger.append_outcome(phase, row, skill.skill_id,
                                  OUTCOME_OF_SIDE[side], skill.version,
                                  phase_dir, row.reason)
            return OUTCOME_OF_SIDE[side]
        if skill.version != from_version:
            raise CodeBugError(
                f"skill {skill.skill_id!r} is at version {skill.version} "
                f"but this phase's revision ledger recorded {from_version}: "
                "the store drifted from the ledger")
    ledger.write_revision_plan(phase, row, skill.skill_id, skill.version,
                               phase_dir)
    try:
        revision = write(skill)
    except ModelOutputError as exc:
        ledger.append_outcome(phase, row, skill.skill_id, "revision_failed",
                              skill.version, phase_dir, str(exc)[:300])
        return "revision_failed"
    if revision.side != side \
            or set(revision.fields) != set(SKILL_SIDES[side]):
        raise CodeBugError(
            f"a {row.action} returned a {revision.side} rewrite with fields "
            f"{sorted(revision.fields)}")
    if revision.unchanged(skill):
        ledger.append_outcome(phase, row, skill.skill_id,
                              "unchanged_after_revision", skill.version,
                              phase_dir, row.reason)
        return "unchanged_after_revision"
    skill.rewrite_history.append({
        "side": side,
        "old": {name: getattr(skill, name) for name in revision.fields},
        "reason": revision.reason,
    })
    for name, value in revision.fields.items():
        setattr(skill, name, value)
    skill.version += 1
    record_version(
        store, phase_dir, run_dir, skill,
        Provenance(skill_id=skill.skill_id, version=skill.version,
                   phase_label=phase.label, cause=row.action,
                   sources=list(sources),
                   note=revision.reason or row.reason))
    ledger.append_outcome(phase, row, skill.skill_id, OUTCOME_OF_SIDE[side],
                          skill.version, phase_dir, row.reason)
    return OUTCOME_OF_SIDE[side]
