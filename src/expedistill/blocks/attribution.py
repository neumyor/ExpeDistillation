"""G10 group attribution and G11 skill revision (live-run blocks, layer 4).

The live_run companion of blocks/author.py's exam-time G6: after a task
group's injected run fails to beat its stage-1 baseline (a verdict this
module never makes -- the named acceptance rule does, in the composite),
``attribute_failure`` asks the author model for one group-level diagnosis
plus a keep/revise/delete action per skill, and ``revise_skill`` asks the
rewriter model to rewrite one skill from its attribution problem plus
excerpts of the failed attempts. Blocks stay pure (AGENTS.md block rules):
the model is a parameter, nothing touches disk, and every call goes
through ``llm.call.call_model`` with a named prompt.

The module-top import below is a runtime guarantee, not style: the two
prompt names are deliberately absent from ``prompts/__init__.py`` (lazy
registration keeps the manifest's frozen prompt-name set stable for
in-flight exam runs), so importing this module is what registers them --
a caller that bypasses stage 0 cannot hit PromptNotFoundError.

Scope kept out of this module on purpose (decision section 2.3): the
marker-preservation check on a revised skill (``skill_markers(new) ==
skill_markers(old)``, the K5 precedent) belongs to the composite K6, which
also owns the version bump and rewrite_history append -- blocks may not
import each other, and skill_markers lives in blocks/author.py. The same
rule is why the version field of an attribution ledger row is not here
either: the model never provides it, K6 reads it from the store when
appending.

The FAILURE_CATEGORIES vocabulary below restates the tuple pinned by
blocks/author.py (each set is pinned by its prompt's output contract, and
the G10/G11 prompts share it with REWRITE by design, decision section 2.3).
It cannot be imported from there -- blocks may not import each other --
so a unit test asserts equality with blocks.author.FAILURE_CATEGORIES: the
two sets are one contract, and a future category change must consciously
touch both prompts, which is a new experiment either way.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Defensive registration (decision section 2.3): the prompts are absent from
# prompts/__init__.py on purpose, so this import is what puts their names in
# the registry for any caller that did not go through stage 0.
import expedistill.prompts.attribution  # noqa: F401

from expedistill.core.models import Skill
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model

__all__ = [
    "FAILURE_CATEGORIES",
    "attribute_failure",
    "revise_skill",
]

#: Why the weak model failed in live execution, for G11 revision. Must stay
#: equal to blocks.author.FAILURE_CATEGORIES (the REWRITE contract pins the
#: vocabulary); test_attribution.py enforces the equality.
FAILURE_CATEGORIES = ("misread", "missing_step", "wrong_trigger", "other")

#: The closed action vocabulary of a G10 skill_actions entry.
_ACTIONS = ("keep", "revise", "delete")


def _skills_listing(skills: List[Skill]) -> str:
    """Render the group as one deterministic listing block for the prompt.

    Each entry is the skill's id on its own line followed by the full
    four-section text; entries are separated by one blank line. The
    attributor echoes skill_ids back from this listing, so the id line must
    stay unambiguous (an id is a colon-suffixed header line, never embedded
    in prose).
    """
    return "\n\n".join(
        f"skill_id: {skill.skill_id}\n{skill.content}"
        for skill in skills
    )


def attribute_failure(
    skills: List[Skill],
    task_instruction: str,
    baseline_summary: str,
    live_views: str,
    weak_profile: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Attribute one group's failed live round (G10); return the plan dict.

    Called by K6 for a task group whose injected run did not beat its
    stage-1 baseline. ``task_instruction`` is the rendered task prompt the
    student model actually saw (decision section 2.3 -- under TB this is
    the task name and the real instruction shows up only in the live views);
    ``baseline_summary`` the per-attempt stage-1 status lines; ``live_views``
    the D5 views of EVERY attempt this round, successes included, so the
    attributor sees what the weak model did with the skills, not only that
    it failed.

    The returned dict is the validated plan: ``{"diagnosis": str,
    "skill_actions": [{"skill_id", "action", "problem"}]}`` with every entry
    normalized to exactly those three keys (a keep entry's missing or null
    problem becomes ""). Everything else is a ModelOutputError: a
    non-object response, an empty diagnosis, a non-list skill_actions, a
    non-object entry, an action outside keep/revise/delete, a non-string
    problem, a revise/delete without a non-empty problem, or coverage that
    is not exactly one action per input skill (duplicates, missing, and
    unknown ids are all rejected). Persisting the plan (attributions.jsonl,
    the version field) belongs to K6; this block writes nothing.
    """
    if not skills:
        # A group is non-empty by construction (K6 only attributes groups
        # with pending candidates); an empty call is a caller bug, and
        # spending a model call on it would be wasted budget.
        raise ValueError(
            "attribute_failure requires a non-empty skill group: there is "
            "nothing to attribute")

    variables = {
        "task_instruction": task_instruction,
        "baseline_summary": baseline_summary,
        "live_views": live_views,
        "skills_listing": _skills_listing(skills),
        "weak_profile": weak_profile,
    }
    parsed = call_model(
        handle, "LIVE_RUN_ATTRIBUTION", variables,
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            "LIVE_RUN_ATTRIBUTION: expected a JSON object, got "
            f"{type(parsed).__name__}")

    diagnosis = parsed.get("diagnosis")
    if not isinstance(diagnosis, str) or not diagnosis.strip():
        raise ModelOutputError(
            "LIVE_RUN_ATTRIBUTION: field 'diagnosis' must be a non-empty "
            f"string, got {diagnosis!r}")

    raw_actions = parsed.get("skill_actions")
    if not isinstance(raw_actions, list):
        raise ModelOutputError(
            "LIVE_RUN_ATTRIBUTION: field 'skill_actions' must be a list, "
            f"got {type(raw_actions).__name__}")

    actions: List[Dict[str, str]] = []
    for index, entry in enumerate(raw_actions):
        if not isinstance(entry, dict):
            raise ModelOutputError(
                f"LIVE_RUN_ATTRIBUTION: skill_actions[{index}] must be a "
                f"JSON object, got {type(entry).__name__}")
        skill_id = entry.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id:
            raise ModelOutputError(
                f"LIVE_RUN_ATTRIBUTION: skill_actions[{index}].skill_id "
                f"must be a non-empty string, got {skill_id!r}")
        action = entry.get("action")
        if action not in _ACTIONS:
            raise ModelOutputError(
                f"LIVE_RUN_ATTRIBUTION: skill_actions[{index}].action must "
                f"be one of {_ACTIONS}, got {action!r}")
        problem = entry.get("problem", "")
        if problem is None:
            problem = ""
        if not isinstance(problem, str):
            raise ModelOutputError(
                f"LIVE_RUN_ATTRIBUTION: skill_actions[{index}].problem must "
                f"be a string, got {problem!r}")
        if action in ("revise", "delete") and not problem.strip():
            raise ModelOutputError(
                f"LIVE_RUN_ATTRIBUTION: skill_actions[{index}] (action "
                f"{action!r} on {skill_id}) must carry a non-empty "
                "'problem': the revision or deletion needs a concrete, "
                "transferably phrased problem statement")
        actions.append(
            {"skill_id": skill_id, "action": action, "problem": problem})

    expected = [skill.skill_id for skill in skills]
    emitted = [entry["skill_id"] for entry in actions]
    counts: Dict[str, int] = {}
    for skill_id in emitted:
        counts[skill_id] = counts.get(skill_id, 0) + 1
    duplicates = sorted(sid for sid, count in counts.items() if count > 1)
    if duplicates:
        raise ModelOutputError(
            "LIVE_RUN_ATTRIBUTION: skill_actions must cover each skill "
            f"exactly once; duplicated skill_ids: {duplicates}")
    missing = sorted(set(expected) - set(emitted))
    if missing:
        raise ModelOutputError(
            "LIVE_RUN_ATTRIBUTION: skill_actions must cover every skill in "
            f"the group; missing skill_ids: {missing}")
    unknown = sorted(set(emitted) - set(expected))
    if unknown:
        raise ModelOutputError(
            "LIVE_RUN_ATTRIBUTION: skill_actions carries skill_ids that are "
            f"not in the group: {unknown}")

    return {"diagnosis": diagnosis, "skill_actions": actions}


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
    ``{"failure_category", "skill_text", "revision_reason"}`` with
    ``skill_text`` stripped; the caller (K6) owns bumping the skill version
    and appending to rewrite_history, plus the marker-preservation check
    (decision section 2.3). A failure_category outside FAILURE_CATEGORIES, a
    non-object response, an empty skill_text, or an empty revision_reason
    raises ModelOutputError.
    """
    variables = {
        "skill_text": skill_text,
        "attribution_report": attribution_report,
        "failure_view": failure_view,
        "weak_profile": weak_profile,
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

    return {
        "failure_category": failure_category,
        "skill_text": new_text.strip(),
        "revision_reason": revision_reason,
    }
