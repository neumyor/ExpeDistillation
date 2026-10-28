"""G3/G4/G5 generalization exam blocks (layer 4).

One skill version, one text exam (docs/decisions/generalization_exam_20261027.md):
``write_scene`` assembles the scene from a real trajectory and the skill under
examination, ``choose_skills`` makes the examined model pick skills from the
whole library catalog, ``write_plan`` turns the opened skills into a plan, and
``judge`` returns the closed-vocabulary verdict.

Every model call goes through B1 (``llm.call.call_model``) with a named prompt
from the B2 registry (EXAM_SCENE / EXAM_TRIGGER / EXAM_PLAN / EXAM_JUDGE); this
module only wires variables and validates the declared output. Field-level
failures raise ModelOutputError, never a guess: ``call_model``'s format budget
covers JSON parsing only, so a response that parses but breaks the contract is
terminal for the unit (the same precedent the retired gen_exam followed).

``choose_skills`` is the one block that resolves model-written names against a
caller-supplied set. The catalog the reader sees uses skill directory names
(``<dir>/SKILL.md`` on disk), but callers may know the same skill by its id, its
directory name, its folder file name, or the flat ``<dir>-SKILL.md`` form; all
four are normalized to ONE alias before matching, and the matched entry is
returned in the caller's own canonical spelling. A name outside the set is a
ModelOutputError -- silently dropping it would turn a broken trigger into a
"read nothing" verdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model

__all__ = ["choose_skills", "judge", "write_plan", "write_scene"]

#: The three natural-language fields one scene carries (the EXAM_SCENE JSON
#: contract). ``scene_text`` is what the reader sees; the other two are grading
#: standards the reader never sees.
SCENE_FIELDS: Tuple[str, ...] = (
    "scene_text", "expected_behavior", "reference_answer")

#: The only verdicts the EXAM_JUDGE prompt may emit; anything else is a format
#: error. Case-sensitive on purpose: guessing what "Success" meant would
#: silently flip the acceptance decision.
_VERDICT_TO_BOOL = {"success": True, "failure": False}

#: The on-disk suffixes a skill name may carry. ``/SKILL.md`` is the folder
#: file name, ``-SKILL.md`` the flat form some callers write, and a leading
#: ``./`` is a shell habit. All three normalize to the directory name.
_SKILL_FILE_SUFFIXES = ("/SKILL.md", "-SKILL.md")

#: A caller may pass one skill name, a list of names, or already-formatted
#: skill bodies; sequences render as separated blocks.
SkillBlock = Union[str, Sequence[str]]


def _render_block(value: SkillBlock, separator: str = "\n\n") -> str:
    """Render a skill block (one string or a sequence of strings)."""
    if isinstance(value, str):
        return value
    return separator.join(value)


def _normalize_skill_name(name: str) -> str:
    """One alias form of a skill name: the bare directory/id spelling.

    ``skill-a/SKILL.md``, ``skill-a-SKILL.md`` and ``./skill-a`` all return
    ``skill-a``; any other string is returned stripped and unchanged. Skill
    ids keep their slashes: only the two known file suffixes are stripped, so
    ``subtask/skill`` stays ``subtask/skill`` rather than collapsing to
    ``skill``.
    """
    text = name.strip()
    if text.startswith("./"):
        text = text[2:]
    text = text.rstrip("/")
    for suffix in _SKILL_FILE_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _alias_map(known_names: Iterable[str]) -> Dict[str, str]:
    """alias -> the caller's canonical spelling for every known skill.

    The caller's spelling wins on a collision: passing ``x`` and ``x/SKILL.md``
    is redundant, not ambiguous. A non-string or empty entry is a caller bug
    (ValueError), never a model failure.
    """
    aliases: Dict[str, str] = {}
    # One known name passed as a bare string is one name, not its characters.
    if isinstance(known_names, str):
        known_names = (known_names,)
    for raw in known_names:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                f"known_names entries must be non-empty strings, got {raw!r}")
        name = raw.strip()
        aliases.setdefault(_normalize_skill_name(name), name)
    return aliases


def _require_text(prompt_name: str, field: str, value: Any) -> str:
    """One required non-empty string field of a parsed model response."""
    if not isinstance(value, str) or not value.strip():
        raise ModelOutputError(
            f"{prompt_name}: field {field!r} must be a non-empty string, "
            f"got {value!r}")
    return value.strip()


def write_scene(
    task_instruction: str,
    prior_steps: str,
    future_steps: str,
    skill_content: str,
    change_note: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, str]:
    """Write one exam scene from a trajectory cut and the skill under exam (G3).

    Returns ``{"scene_text", "expected_behavior", "reference_answer"}``, each
    stripped and non-empty; a parsed response missing a field or carrying an
    empty/non-string value raises ModelOutputError. ``change_note`` is the
    natural-language reason the skill was written or revised (it steers which
    part of the trajectory makes a fair scene); ``skill_content`` is the skill
    text itself and must never surface in ``scene_text`` -- the prompt forbids
    naming the skill, and the scene is what the reader sees first.
    """
    parsed = call_model(
        handle,
        "EXAM_SCENE",
        {
            "task_instruction": task_instruction,
            "prior_steps": prior_steps,
            "future_steps": future_steps,
            "skill_content": skill_content,
            "change_note": change_note,
        },
        output_json=True,
        usage=usage,
        run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"EXAM_SCENE must return a JSON object, got {type(parsed).__name__}")
    return {
        field: _require_text("EXAM_SCENE", field, parsed.get(field))
        for field in SCENE_FIELDS
    }


def choose_skills(
    scene_text: str,
    skill_catalog: str,
    known_names: Iterable[str],
    max_reads: int,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Tuple[List[str], str]:
    """Pick up to ``max_reads`` skills from the catalog for one scene (G3).

    Returns ``(selected, reason)`` where ``selected`` carries the caller's
    canonical spelling of each matched name (see ``_normalize_skill_name``:
    ids, directory names, ``<dir>/SKILL.md`` file names and ``<dir>-SKILL.md``
    all match), deduplicated in model order. An empty list is legal.

    A parsed response that is not an object, a ``skills`` value that is not a
    list of non-empty strings, a name outside ``known_names``, more than
    ``max_reads`` distinct skills, or a non-string ``reason`` raises
    ModelOutputError. ``max_reads`` below zero (or not an int) is a caller bug
    and raises ValueError before any model call.
    """
    if isinstance(max_reads, bool) or not isinstance(max_reads, int) \
            or max_reads < 0:
        raise ValueError(
            f"max_reads must be an int >= 0, got {max_reads!r}")
    aliases = _alias_map(known_names)

    parsed = call_model(
        handle,
        "EXAM_TRIGGER",
        {
            "scene_text": scene_text,
            "skill_catalog": skill_catalog,
            "max_skill_reads": str(max_reads),
        },
        output_json=True,
        usage=usage,
        run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"EXAM_TRIGGER must return a JSON object, "
            f"got {type(parsed).__name__}")

    raw_skills = parsed.get("skills")
    if not isinstance(raw_skills, list):
        raise ModelOutputError(
            f"EXAM_TRIGGER: field 'skills' must be a list, "
            f"got {raw_skills!r}")
    selected: List[str] = []
    for raw in raw_skills:
        if not isinstance(raw, str) or not raw.strip():
            raise ModelOutputError(
                f"EXAM_TRIGGER: every 'skills' entry must be a non-empty "
                f"string, got {raw!r}")
        canonical = aliases.get(_normalize_skill_name(raw))
        if canonical is None:
            raise ModelOutputError(
                f"EXAM_TRIGGER: unknown skill {raw!r}; the catalog names "
                f"{sorted(aliases.values())}")
        if canonical not in selected:
            selected.append(canonical)
    if len(selected) > max_reads:
        raise ModelOutputError(
            f"EXAM_TRIGGER: returned {len(selected)} skills, at most "
            f"{max_reads} allowed: {selected!r}")

    reason = parsed.get("reason")
    if not isinstance(reason, str):
        raise ModelOutputError(
            f"EXAM_TRIGGER: field 'reason' must be a string, got {reason!r}")
    return selected, reason.strip()


def write_plan(
    scene_text: str,
    selected_skills: SkillBlock,
    selection_reason: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> str:
    """Write the ordered plan for one scene from the opened skills (G4).

    ``selected_skills`` is the opened material (one string, or a sequence of
    names/bodies rendered as separated blocks). Returns the stripped plan text;
    a missing or empty ``plan`` field raises ModelOutputError.
    """
    parsed = call_model(
        handle,
        "EXAM_PLAN",
        {
            "scene_text": scene_text,
            "selected_skills": _render_block(selected_skills),
            "selection_reason": selection_reason,
        },
        output_json=True,
        usage=usage,
        run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"EXAM_PLAN must return a JSON object, got {type(parsed).__name__}")
    return _require_text("EXAM_PLAN", "plan", parsed.get("plan"))


def judge(
    scene_text: str,
    expected_behavior: str,
    reference_answer: str,
    skill_catalog: str,
    selected_skills: SkillBlock,
    selection_reason: str,
    plan: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Tuple[bool, str]:
    """Grade one plan against its scene: ``(success, reason)`` (G5).

    ``success`` is True for verdict "success", False for "failure"; any other
    verdict, a non-object response, or a non-string reason raises
    ModelOutputError. The reference answer is a grading standard only: neither
    return value carries it, and the prompt forbids the reason from restating
    the fix. ``selected_skills`` is what the program recorded as opened, not
    what the model claims.
    """
    parsed = call_model(
        handle,
        "EXAM_JUDGE",
        {
            "scene_text": scene_text,
            "expected_behavior": expected_behavior,
            "reference_answer": reference_answer,
            "skill_catalog": skill_catalog,
            "selected_skills": _render_block(selected_skills),
            "selection_reason": selection_reason,
            "plan": plan,
        },
        output_json=True,
        usage=usage,
        run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"EXAM_JUDGE must return a JSON object, got {type(parsed).__name__}")
    verdict = parsed.get("verdict")
    if not isinstance(verdict, str) or verdict not in _VERDICT_TO_BOOL:
        raise ModelOutputError(
            f"EXAM_JUDGE verdict must be 'success' or 'failure', "
            f"got {verdict!r}")
    reason = parsed.get("reason")
    if not isinstance(reason, str):
        raise ModelOutputError(
            f"EXAM_JUDGE: field 'reason' must be a string, got {reason!r}")
    return _VERDICT_TO_BOOL[verdict], reason.strip()
