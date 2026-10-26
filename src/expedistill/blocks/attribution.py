"""P04/P06 attribution and revision model blocks (layer 4).

``attribute_attempts`` asks one question per trajectory window: for every
active skill, what triggered it, what the model did next and where the
skill could improve. It returns facts only, never an action.
``revise_from_attempts`` rewrites one skill from all of its windows; the
writer is responsible for reconciling conflicting windows.

The module is deliberately independent of the decision module and of the
P02/exam evidence shapes. Model calls go through ``llm.call.call_model``;
nothing here touches disk.
"""

from typing import Any, Dict, List, Mapping, Optional

from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model
from expedistill.prompts.profile import profile_section

__all__ = [
    "SKILL_SUMMARY_FIELD",
    "attribute_attempts",
    "revise_from_attempts",
]

SKILL_SUMMARY_FIELD = "summary"


def _validate_summary(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ModelOutputError(
            f"{where}: field {SKILL_SUMMARY_FIELD!r} must be a string, "
            f"got {value!r}")
    summary = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not summary:
        raise ModelOutputError(
            f"{where}: field {SKILL_SUMMARY_FIELD!r} must be a non-empty "
            "string")
    if "\n" in summary:
        raise ModelOutputError(
            f"{where}: field {SKILL_SUMMARY_FIELD!r} must be ONE line")
    return summary


def _skills_listing(skills: List[Any]) -> str:
    return "\n\n".join(
        f"skill_id: {skill.skill_id}\n{skill.content}" for skill in skills)


def _canonical_skill_id(
    value: Any,
    expected: Mapping[str, Any],
    aliases: Mapping[str, str],
) -> Optional[str]:
    """Resolve a model-written id to the window's full skill id.

    The folder prompt shows the model both forms: the full skill id in
    ``SKILLS UNDER REVIEW`` and the folder directory name in the task's
    ``<available_skills>`` block. Models mix them. ``aliases`` maps the
    folder forms back to the full id; ``None`` means the entry belongs to
    another window (or is not a skill at all) and is ignored.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip().strip("\"'")
    if candidate in expected:
        return candidate
    mapped = aliases.get(candidate)
    if mapped in expected:
        return mapped
    compact = candidate.replace("/", "-")
    mapped = aliases.get(compact)
    if mapped in expected:
        return mapped
    if compact.endswith("-SKILL.md"):
        mapped = aliases.get(compact[:-len("-SKILL.md")])
        if mapped in expected:
            return mapped
    return None


_VALIDATION_ATTEMPTS = 2


def _parse_attribution(
    parsed: Any,
    skills: List[Any],
    aliases: Mapping[str, str],
) -> Dict[str, Dict[str, str]]:
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            "SKILL_ATTEMPT_ATTRIBUTION: expected a JSON object, got "
            f"{type(parsed).__name__}")
    entries = parsed.get("skills")
    if not isinstance(entries, list):
        raise ModelOutputError(
            "SKILL_ATTEMPT_ATTRIBUTION: field 'skills' must be a list, "
            f"got {type(entries).__name__}")
    expected = {skill.skill_id for skill in skills}
    out: Dict[str, Dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ModelOutputError(
                "SKILL_ATTEMPT_ATTRIBUTION: every skills entry must be "
                f"an object, got {entry!r}")
        skill_id = _canonical_skill_id(
            entry.get("skill_id"), expected, aliases)
        if skill_id is None:
            # The task prompt lists every skill of the task; the model
            # often reports those other windows' skills too. They are
            # not this window's evidence, so ignore them.
            continue
        if skill_id in out:
            # A model may repeat one skill under both its full id and
            # its folder name. The first entry is kept.
            continue
        values = {}
        for key in ("trigger_context", "behavior_after_trigger",
                    "improvement"):
            value = entry.get(key)
            if not isinstance(value, str):
                raise ModelOutputError(
                    "SKILL_ATTEMPT_ATTRIBUTION: field "
                    f"{key!r} must be a string, got {value!r}")
            values[key] = value.strip()
        if not values["trigger_context"]:
            raise ModelOutputError(
                "SKILL_ATTEMPT_ATTRIBUTION: trigger_context must be "
                f"non-empty for {skill_id!r}")
        if not values["behavior_after_trigger"]:
            raise ModelOutputError(
                "SKILL_ATTEMPT_ATTRIBUTION: behavior_after_trigger must "
                f"be non-empty for {skill_id!r}")
        out[skill_id] = values
    missing = expected - set(out)
    if missing:
        raise ModelOutputError(
            "SKILL_ATTEMPT_ATTRIBUTION: missing skills "
            f"{sorted(missing)}")
    return out


def attribute_attempts(
    *,
    task_instruction: str,
    attempt: int,
    outcome: str,
    window_kind: str,
    step_start: int,
    step_end: int,
    trajectory_window: str,
    skills: List[Any],
    weak_profile: str,
    handle: ModelHandle,
    usage: Any = None,
    run_dir: Optional[Any] = None,
    skill_aliases: Optional[Mapping[str, str]] = None,
    validation_attempts: int = _VALIDATION_ATTEMPTS,
) -> Dict[str, Dict[str, str]]:
    """One window's attribution for the listed skills.

    Returns ``{skill_id: {trigger_context, behavior_after_trigger,
    improvement}}``. Coverage is exactly one entry per input skill.

    ``skill_aliases`` maps folder directory/file names to full skill ids.
    The task instruction carries the whole task's ``<available_skills>``
    list, so a model may also report skills from other windows; those
    entries are ignored here, not treated as format errors.

    A response can be valid JSON but violate this block's business
    contract (e.g. ``{"skills": null}``, or the terminal agent's own
    ``analysis/plan/commands`` JSON). ``call_model`` only retries parse
    failures, so the whole call is retried here; stochastic sampling
    usually recovers, and an exhausted budget raises the last error.
    """
    if not skills:
        raise ValueError("attribute_attempts needs at least one skill")
    if validation_attempts < 1:
        raise ValueError(
            f"validation_attempts must be >= 1, got {validation_attempts}")
    variables = {
        "task_instruction": task_instruction,
        "attempt": str(attempt),
        "outcome": outcome,
        "window_kind": window_kind,
        "step_start": str(step_start),
        "step_end": str(step_end),
        "trajectory_window": trajectory_window,
        "skills_listing": _skills_listing(skills),
        "profile_section": profile_section(
            "THE MODEL THAT EXECUTED THE TASK (weak model profile):",
            weak_profile),
    }
    aliases = dict(skill_aliases or {})
    last_error: Optional[ModelOutputError] = None
    for _ in range(validation_attempts):
        try:
            parsed = call_model(
                handle, "SKILL_ATTEMPT_ATTRIBUTION", variables,
                output_json=True, usage=usage, run_dir=run_dir)
            return _parse_attribution(parsed, skills, aliases)
        except ModelOutputError as exc:
            last_error = exc
    raise last_error


def revise_from_attempts(
    *,
    skill_text: str,
    attempt_evidence: str,
    weak_profile: str,
    handle: ModelHandle,
    usage: Any = None,
    run_dir: Optional[Any] = None,
) -> Dict[str, str]:
    """Rewrite one skill from its complete window evidence."""
    parsed = call_model(
        handle, "SKILL_ATTEMPT_REVISION", {
            "skill_text": skill_text,
            "attempt_evidence": attempt_evidence,
            "profile_section": profile_section(
                "THE MODEL THAT WILL READ THE SKILL (weak model profile):",
                weak_profile),
        },
        output_json=True, usage=usage, run_dir=run_dir)
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            "SKILL_ATTEMPT_REVISION: expected a JSON object, got "
            f"{type(parsed).__name__}")
    new_text = parsed.get("skill_text")
    if not isinstance(new_text, str) or not new_text.strip():
        raise ModelOutputError(
            "SKILL_ATTEMPT_REVISION: field 'skill_text' must be a "
            f"non-empty string, got {new_text!r}")
    reason = parsed.get("revision_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ModelOutputError(
            "SKILL_ATTEMPT_REVISION: field 'revision_reason' must be a "
            f"non-empty string, got {reason!r}")
    summary = _validate_summary(parsed.get(SKILL_SUMMARY_FIELD),
                                "SKILL_ATTEMPT_REVISION")
    return {"skill_text": new_text.strip(),
            "revision_reason": reason.strip(),
            "summary": summary}
