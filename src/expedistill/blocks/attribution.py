"""G11 skill revision and usage attribution (live-run blocks, layer 4).

``revise_skill`` asks the rewriter model to rewrite one skill from its
attribution problem plus excerpts of the failed attempts.
``attribute_usage`` asks for the per-skill relevance/application facts
(no actions), and ``retrigger_skill`` rewrites only the summary and
[When to use] section when a relevant skill was never read. Blocks stay pure (AGENTS.md block rules):
the model is a parameter, nothing touches disk, and every call goes
through ``llm.call.call_model`` with a named prompt.

The two prompts register at import of ``prompts/__init__`` (the v3
full freeze): no defensive import is needed here, and a caller that
bypasses stage 0 still finds the names registered.

Scope kept out of this module on purpose (decision section 2.3): the
section-preservation check on a revised skill (``has_skill_sections``,
the K5 precedent) belongs to the author driver, which also owns the
version bump and rewrite_history append -- blocks may not import each
other, and has_skill_sections lives in blocks/author.py. The same
rule is why the version field of an attribution ledger row is not here
either: the model never provides it, K6 reads it from the store when
appending.

The FAILURE_CATEGORIES vocabulary below restates the tuple pinned by
blocks/author.py (each set is pinned by its prompt's output contract, and
the G11 prompt shares it with REWRITE by design, decision section 2.3).
It cannot be imported from there -- blocks may not import each other --
so a unit test asserts equality with blocks.author.FAILURE_CATEGORIES: the
two sets are one contract, and a future category change must consciously
touch both prompts, which is a new experiment either way.

The same mirroring rule covers the schema v3 ``summary`` contract (the
skill's one-line directory entry, skill-folder M1): the validator below
restates blocks/author.py's rule (non-empty, one line, at most
SKILL_SUMMARY_MAX_CHARS) on top of the shared core constant, and a unit
test drives both validators with the same payloads so they cannot drift
apart unnoticed.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from expedistill.core.models import (
    SKILL_MARKERS,
    SKILL_SUMMARY_MAX_CHARS,
    Skill,
)
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model
from expedistill.prompts.profile import profile_section

__all__ = [
    "FAILURE_CATEGORIES",
    "SKILL_SUMMARY_FIELD",
    "attribute_usage",
    "retrigger_skill",
    "revise_skill",
]

#: Why the weak model failed in live execution, for G11 revision. Must stay
#: equal to blocks.author.FAILURE_CATEGORIES (the REWRITE contract pins the
#: vocabulary); test_attribution.py enforces the equality.
FAILURE_CATEGORIES = ("misread", "missing_step", "wrong_trigger", "other")

#: The usage attribution's per-skill vocabularies (``LIVE_USAGE_ATTRIBUTION``).
RELEVANCE_VALUES = ("relevant", "irrelevant", "unclear")
APPLICATION_VALUES = ("applied", "partially_applied", "not_applied",
                      "contradicted", "unclear")

#: The revised skill's one-line summary field (schema v3), mirroring
#: blocks.author.SKILL_SUMMARY_FIELD; test_attribution.py pins the mirror.
SKILL_SUMMARY_FIELD = "summary"


def _validate_summary(value: Any) -> str:
    """The G11 mirror of blocks/author.py's summary rule (module docstring).

    A non-empty string, ONE line, at most SKILL_SUMMARY_MAX_CHARS; the
    stripped, newline-normalized line is the value the caller stores in
    ``Skill.summary``. A stray ``\\r`` counts as a newline (the same
    normalization the G2/G6 side applies).
    """
    if not isinstance(value, str):
        raise ModelOutputError(
            f"LIVE_SKILL_REVISION: field {SKILL_SUMMARY_FIELD!r} must be "
            f"a string, got {value!r}")
    summary = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not summary:
        raise ModelOutputError(
            f"LIVE_SKILL_REVISION: field {SKILL_SUMMARY_FIELD!r} must be "
            f"a non-empty string, got {value!r}")
    if "\n" in summary:
        raise ModelOutputError(
            f"LIVE_SKILL_REVISION: field {SKILL_SUMMARY_FIELD!r} must be "
            "ONE line (the skill's directory entry); got a value with an "
            f"embedded newline: {value!r}")
    if len(summary) > SKILL_SUMMARY_MAX_CHARS:
        raise ModelOutputError(
            f"LIVE_SKILL_REVISION: field {SKILL_SUMMARY_FIELD!r} must be "
            f"at most {SKILL_SUMMARY_MAX_CHARS} characters, got "
            f"{len(summary)}")
    return summary

def revise_skill(
    skill_text: str,
    attribution_report: str,
    failure_view: str,
    weak_profile: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, str]:
    """Rewrite one skill from its live failure (G11); return the new fields.

    The G11 sibling of blocks.author.rewrite_skill, driven by execution
    evidence instead of an exam: ``attribution_report`` carries the group's
    diagnosis plus this skill's problem, ``failure_view`` excerpts of the
    round-0 failed attempts' D5 views. The returned dict is
    ``{"failure_category", "skill_text", "revision_reason", "summary"}``
    with ``skill_text`` stripped; ``summary`` is the revised skill's fresh
    one-liner (schema v3) -- the content changed, so the old directory
    line no longer describes the skill. The caller (K6) owns bumping the
    skill version and appending to rewrite_history (storing the OLD
    summary alongside the old content), plus the marker-preservation
    check (decision section 2.3). A failure_category outside
    FAILURE_CATEGORIES, a non-object response, an empty skill_text, an
    empty revision_reason, or an invalid summary raises ModelOutputError.
    """
    variables = {
        "skill_text": skill_text,
        "attribution_report": attribution_report,
        "failure_view": failure_view,
        "profile_section": profile_section(
            "THE MODEL THAT WILL READ THE SKILL (weak model profile):",
            weak_profile),
    }
    parsed = call_model(
        handle, "LIVE_SKILL_REVISION", variables,
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            "LIVE_SKILL_REVISION: expected a JSON object, got "
            f"{type(parsed).__name__}")

    failure_category = parsed.get("failure_category")
    if failure_category not in FAILURE_CATEGORIES:
        raise ModelOutputError(
            "LIVE_SKILL_REVISION: failure_category must be one of "
            f"{FAILURE_CATEGORIES}, got {failure_category!r}")
    new_text = parsed.get("skill_text")
    if not isinstance(new_text, str) or not new_text.strip():
        raise ModelOutputError(
            "LIVE_SKILL_REVISION: field 'skill_text' must be a non-empty "
            f"string, got {new_text!r}")
    revision_reason = parsed.get("revision_reason")
    if (not isinstance(revision_reason, str)
            or not revision_reason.strip()):
        raise ModelOutputError(
            "LIVE_SKILL_REVISION: field 'revision_reason' must be a "
            f"non-empty string, got {revision_reason!r}")
    summary = _validate_summary(parsed.get(SKILL_SUMMARY_FIELD))

    return {
        "failure_category": failure_category,
        "skill_text": new_text.strip(),
        "revision_reason": revision_reason,
        "summary": summary,
    }


def attribute_usage(
    skills: List[Skill],
    task_instruction: str,
    baseline_summary: str,
    live_views: str,
    usage_table: str,
    weak_profile: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Attribute one failed group with the recorded usage facts.

    Returns ``{"diagnosis": str, "skills": [{"skill_id", "relevance",
    "application", "evidence_steps", "problem"}]}``.  Coverage is exactly
    one entry per input skill; the vocabularies are
    :data:`RELEVANCE_VALUES` / :data:`APPLICATION_VALUES`.  The result is
    fact-only: no action is accepted or returned.
    """
    if not skills:
        raise ValueError(
            "attribute_usage requires a non-empty skill group: there is "
            "nothing to attribute")
    variables = {
        "task_instruction": task_instruction,
        "baseline_summary": baseline_summary,
        "live_views": live_views,
        "skills_listing": "\n\n".join(
            f"skill_id: {skill.skill_id}\nsummary: {skill.summary}\n"
            f"{skill.content}" for skill in skills),
        "usage_table": usage_table,
        "profile_section": profile_section(
            "THE MODEL THAT EXECUTED THE TASK (weak model profile):",
            weak_profile),
    }
    parsed = call_model(
        handle, "LIVE_USAGE_ATTRIBUTION", variables,
        output_json=True, usage=usage, run_dir=run_dir)
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            "LIVE_USAGE_ATTRIBUTION: expected a JSON object, got "
            f"{type(parsed).__name__}")
    diagnosis = parsed.get("diagnosis")
    if not isinstance(diagnosis, str) or not diagnosis.strip():
        raise ModelOutputError(
            "LIVE_USAGE_ATTRIBUTION: field 'diagnosis' must be a "
            f"non-empty string, got {diagnosis!r}")
    raw_skills = parsed.get("skills")
    if not isinstance(raw_skills, list):
        raise ModelOutputError(
            "LIVE_USAGE_ATTRIBUTION: field 'skills' must be a list, got "
            f"{type(raw_skills).__name__}")

    entries: List[Dict[str, Any]] = []
    for index, entry in enumerate(raw_skills):
        if not isinstance(entry, dict):
            raise ModelOutputError(
                f"LIVE_USAGE_ATTRIBUTION: skills[{index}] must be a JSON "
                f"object, got {type(entry).__name__}")
        skill_id = entry.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id:
            raise ModelOutputError(
                f"LIVE_USAGE_ATTRIBUTION: skills[{index}].skill_id must "
                f"be a non-empty string, got {skill_id!r}")
        relevance = entry.get("relevance")
        if relevance not in RELEVANCE_VALUES:
            raise ModelOutputError(
                f"LIVE_USAGE_ATTRIBUTION: skills[{index}].relevance must "
                f"be one of {RELEVANCE_VALUES}, got {relevance!r}")
        application = entry.get("application")
        if application not in APPLICATION_VALUES:
            raise ModelOutputError(
                f"LIVE_USAGE_ATTRIBUTION: skills[{index}].application "
                f"must be one of {APPLICATION_VALUES}, got {application!r}")
        steps = entry.get("evidence_steps", [])
        if not isinstance(steps, list) or any(
                not isinstance(step, int) or step < 0 for step in steps):
            raise ModelOutputError(
                f"LIVE_USAGE_ATTRIBUTION: skills[{index}].evidence_steps "
                f"must be a list of non-negative integers, got {steps!r}")
        problem = entry.get("problem", "")
        if not isinstance(problem, str):
            raise ModelOutputError(
                f"LIVE_USAGE_ATTRIBUTION: skills[{index}].problem must "
                f"be a string, got {problem!r}")
        if application in ("not_applied", "contradicted") \
                and not problem.strip():
            raise ModelOutputError(
                f"LIVE_USAGE_ATTRIBUTION: skills[{index}] (application "
                f"{application!r}) needs a non-empty problem")
        entries.append({
            "skill_id": skill_id,
            "relevance": relevance,
            "application": application,
            "evidence_steps": list(steps),
            "problem": problem,
        })

    expected = [skill.skill_id for skill in skills]
    emitted = [entry["skill_id"] for entry in entries]
    if sorted(emitted) != sorted(expected) or len(set(emitted)) != len(emitted):
        raise ModelOutputError(
            "LIVE_USAGE_ATTRIBUTION: skills must name every input skill "
            f"exactly once; expected {sorted(expected)}, got {emitted}")
    return {"diagnosis": diagnosis, "skills": entries}


def _replace_when_to_use(content: str, when_to_use: str) -> str:
    """Replace the [When to use] section body, preserving every other byte."""
    marker = SKILL_MARKERS[0]
    lines = content.split("\n")
    try:
        start = next(index for index, line in enumerate(lines)
                     if line.startswith(marker))
    except StopIteration:
        raise ModelOutputError(
            f"LIVE_SKILL_RETRIGGER: the skill has no {marker} section") \
            from None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if any(lines[index].startswith(other) for other in SKILL_MARKERS[1:]):
            end = index
            break
    body_lines = when_to_use.replace("\r\n", "\n").replace("\r", "\n") \
        .strip().split("\n")
    if not body_lines or not body_lines[0].strip():
        raise ModelOutputError(
            "LIVE_SKILL_RETRIGGER: field 'when_to_use' must be non-empty")
    for line in body_lines:
        if any(line.startswith(other) for other in SKILL_MARKERS):
            raise ModelOutputError(
                "LIVE_SKILL_RETRIGGER: the trigger must not start a line "
                f"with a section marker: {line!r}")
    replacement = [f"{marker} {body_lines[0].strip()}"] + body_lines[1:]
    new_lines = lines[:start] + replacement + lines[end:]
    new_content = "\n".join(new_lines)
    if not all(any(line.startswith(other) for line in new_lines)
               for other in SKILL_MARKERS):
        raise ModelOutputError(
            "LIVE_SKILL_RETRIGGER: the rewrite dropped a required section")
    return new_content


def retrigger_skill(
    skill_text: str,
    old_summary: str,
    sibling_descriptions: str,
    task_instruction: str,
    first_actions: str,
    usage_report: str,
    weak_profile: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Rewrite only a skill's summary and [When to use] section.

    The returned dict carries ``content`` (the full rewritten skill),
    ``summary``, ``reason`` and ``failure_category='wrong_trigger'``.
    """
    variables = {
        "skill_text": skill_text,
        "old_summary": old_summary,
        "sibling_descriptions": sibling_descriptions,
        "task_instruction": task_instruction,
        "first_actions": first_actions,
        "usage_report": usage_report,
        "profile_section": profile_section(
            "THE MODEL THAT EXECUTED THE TASK (weak model profile):",
            weak_profile),
    }
    parsed = call_model(
        handle, "LIVE_SKILL_RETRIGGER", variables,
        output_json=True, usage=usage, run_dir=run_dir)
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            "LIVE_SKILL_RETRIGGER: expected a JSON object, got "
            f"{type(parsed).__name__}")
    summary = _validate_summary(parsed.get(SKILL_SUMMARY_FIELD))
    when_to_use = parsed.get("when_to_use")
    if not isinstance(when_to_use, str) or not when_to_use.strip():
        raise ModelOutputError(
            "LIVE_SKILL_RETRIGGER: field 'when_to_use' must be a "
            f"non-empty string, got {when_to_use!r}")
    reason = parsed.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ModelOutputError(
            "LIVE_SKILL_RETRIGGER: field 'reason' must be a non-empty "
            f"string, got {reason!r}")
    return {
        "content": _replace_when_to_use(skill_text, when_to_use),
        "summary": summary,
        "reason": reason,
        "failure_category": "wrong_trigger",
    }
