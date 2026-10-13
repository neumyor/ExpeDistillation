"""G2 skill writing, G3 exam generation, G6 skill rewrite (layer 4).

Pure computation plus model calls (AGENTS.md block rules): the blocks know no
stage, write no disk, and take the segment steps as an already-rendered text
view (D5 output), never as live objects. Every model call goes through
``llm.call.call_model`` with a named prompt; the block's own job is only to
assemble the template variables from the Subtask/Skill, validate the declared
output, and build the resulting object. IDs come from ``core.ids``, never from
model output.

Protocol v2 (docs/decisions/stage23_protocol_v2_20261013.md section 3.3)
also makes this module the home of the G2 output contract. The section
markers themselves live in ``core.models`` (a second block consumer exists),
and around them: ``skill_markers`` reads the marker set a stored content
carries, ``validate_skill_sections`` checks a parsed G2 response against its
mode's JSON contract, and ``render_skill_content`` turns a validated response
into the deterministic marker text stored in ``Skill.content`` (the single
representation every later consumer -- injection, G7 similarity, G8 merge,
D8 audit -- reads). ``write_skill`` refuses a subtask outcome that
contradicts its mode before any model call, and ``rewrite_skill`` requires
the rewrite to keep the original's marker set.

G6 deliberately returns a plain dict instead of a new Skill and never mutates
its argument: version bumps and ``rewrite_history`` appends are bookkeeping,
which belongs to the composites (K4), not to this block. The rewrite-history
entry shape is pinned as ``{content, error_category, reason}`` where
``content`` is the PREVIOUS version's text.
"""

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from expedistill.core.ids import make_exam_id, make_skill_id
from expedistill.core.models import (
    FAILURE_REFERENCE_MARKERS,
    PROCEDURE_MARKERS,
    Exam,
    Skill,
    Subtask,
)
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model

__all__ = [
    "FAILURE_CATEGORIES",
    "SKILL_SECTION_FIELDS",
    "gen_exam",
    "render_skill_content",
    "rewrite_skill",
    "skill_markers",
    "validate_skill_sections",
    "write_skill",
]

#: Why the weak model failed its acceptance exam (README stage 4). Pinned by
#: the REWRITE prompt's output contract; changing the set means changing the
#: prompt, which is a new experiment.
FAILURE_CATEGORIES = ("misread", "missing_step", "wrong_trigger", "other")

#: The JSON contract each write_skill mode works against (protocol v2,
#: decision section 3.2): the parsed response must carry exactly these keys,
#: which map 1:1 onto the four stored sections. test_templates pins the G2
#: prompts' "Output ONLY JSON" examples to this mapping.
SKILL_SECTION_FIELDS: Dict[str, Tuple[str, ...]] = {
    "distill": ("when_to_use", "steps", "verify", "not_applicable_when"),
    "diagnose": ("trigger", "why_it_fails", "do_instead",
                 "not_applicable_when"),
}

#: The checklist field of each mode's contract: a non-empty list of strings
#: in validation, a renderer-numbered list in rendering.
_CHECKLIST_FIELDS: Dict[str, str] = {
    "distill": "steps",
    "diagnose": "do_instead",
}

#: A checklist item's own "1. " / "12) " prefix. The G2 prompts forbid
#: self-numbering, but a stray one is stripped rather than double-numbered.
_SELF_NUMBERING_RE = re.compile(r"^\s*\d+[.)]\s+")

#: All eight section markers. A stored field value must not start with any
#: of them (validate_skill_sections): a forged marker would poison
#: skill_markers' reading of the rendered content.
_ALL_MARKERS = PROCEDURE_MARKERS + FAILURE_REFERENCE_MARKERS


def _strip_self_numbering(item: str) -> str:
    """One checklist item without a stray leading "1. " / "12) " prefix."""
    return _SELF_NUMBERING_RE.sub("", item).strip()


def skill_markers(content: str) -> Optional[Tuple[str, ...]]:
    """The marker set a stored skill content carries, None when neither.

    Precise semantics: returns PROCEDURE_MARKERS iff content contains all
    four procedure markers AND no failure-only marker;
    FAILURE_REFERENCE_MARKERS iff all four failure markers AND no
    procedure-only marker ("[Do not use when]" is shared); otherwise None
    (partial or mixed). Line-start anchored, and ONLY ever applied to the
    stored multi-line content -- never to the single-line compressed
    injection block (the compression keeps the marker text but destroys
    line structure).
    """
    lines = content.split("\n")
    present = {
        marker for marker in _ALL_MARKERS
        if any(line.startswith(marker) for line in lines)
    }
    procedure = set(PROCEDURE_MARKERS)
    failure = set(FAILURE_REFERENCE_MARKERS)
    if procedure <= present and not present & (failure - procedure):
        return PROCEDURE_MARKERS
    if failure <= present and not present & (procedure - failure):
        return FAILURE_REFERENCE_MARKERS
    return None


def render_skill_content(mode: str, sections: Dict[str, Any]) -> str:
    """Deterministic marker-text rendering of one validated sections dict.

    Mode "distill" renders a procedure skill:

        [When to use] {when_to_use}
        [Steps]
        1. {steps[0]}
        2. {steps[1]}
        [Verify] {verify}
        [Do not use when] {not_applicable_when}

    Mode "diagnose" renders a failure reference, the same layout with
    [Trigger]/{trigger}, [Why it fails]/{why_it_fails}, [Do instead] +
    numbered {do_instead}, and [Do not use when]/{not_applicable_when}. The
    renderer numbers the checklist itself; same sections in, byte-identical
    content out (the stage-3 determinism invariant, decision section 4.2).
    A stray self-numbering on an item is stripped here too, so it is never
    double-numbered.
    """
    if mode not in SKILL_SECTION_FIELDS:
        raise ValueError(
            f'mode must be "distill" or "diagnose", got {mode!r}')
    items = [f"{index}. {_strip_self_numbering(item)}"
             for index, item in enumerate(sections[_CHECKLIST_FIELDS[mode]], 1)]
    if mode == "distill":
        (when_to_use, _steps, verify, not_applicable) = SKILL_SECTION_FIELDS[mode]
        (use_marker, steps_marker, verify_marker, close_marker) = \
            PROCEDURE_MARKERS
        lines = [
            f"{use_marker} {sections[when_to_use].strip()}",
            steps_marker,
            *items,
            f"{verify_marker} {sections[verify].strip()}",
            f"{close_marker} {sections[not_applicable].strip()}",
        ]
    else:
        (trigger, why_it_fails, _do_instead, not_applicable) = \
            SKILL_SECTION_FIELDS[mode]
        (trigger_marker, why_marker, instead_marker, close_marker) = \
            FAILURE_REFERENCE_MARKERS
        lines = [
            f"{trigger_marker} {sections[trigger].strip()}",
            f"{why_marker} {sections[why_it_fails].strip()}",
            instead_marker,
            *items,
            f"{close_marker} {sections[not_applicable].strip()}",
        ]
    return "\n".join(lines)


def _reject_bad_text(mode: str, where: str, value: str) -> None:
    """Reject an embedded newline or a forged leading section marker."""
    if "\n" in value or "\r" in value:
        raise ModelOutputError(
            f"write_skill(mode={mode!r}): {where} must not contain embedded "
            f"newlines: {value!r}")
    for marker in _ALL_MARKERS:
        if value.startswith(marker):
            raise ModelOutputError(
                f"write_skill(mode={mode!r}): {where} must not start with "
                f"section marker {marker}: {value!r}")


def validate_skill_sections(mode: str, sections: Dict[str, Any]) -> None:
    """Field-level validation of one parsed G2 response (protocol v2).

    ``sections`` must be a dict (extract_json can return a top-level
    array); the mode's required keys (SKILL_SECTION_FIELDS) must be
    present; every string field must be a non-empty string; the checklist
    field (steps / do_instead) a non-empty list of strings, each stripped
    of a leading self-numbering ("1. ", "12) ") and then non-empty. String
    fields and checklist items must contain no embedded newlines and must
    not START WITH any section marker: a forged marker at a line start
    would make skill_markers read a legal skill as None and silently
    disarm the rewrite/K5 marker checks. Extra keys are ignored (lenient
    read, gen_exam precedent) but the checklist type check is never
    skipped. Raises ModelOutputError otherwise.
    """
    if mode not in SKILL_SECTION_FIELDS:
        raise ValueError(
            f'mode must be "distill" or "diagnose", got {mode!r}')
    if not isinstance(sections, dict):
        raise ModelOutputError(
            f"write_skill(mode={mode!r}): sections must be a JSON object, "
            f"got {type(sections).__name__}")
    for key in SKILL_SECTION_FIELDS[mode]:
        if key not in sections:
            raise ModelOutputError(
                f"write_skill(mode={mode!r}): missing required field "
                f"{key!r}, got keys {sorted(sections)}")
    for key in SKILL_SECTION_FIELDS[mode]:
        value = sections[key]
        if key == _CHECKLIST_FIELDS[mode]:
            if not isinstance(value, list) or not value:
                raise ModelOutputError(
                    f"write_skill(mode={mode!r}): field {key!r} must be a "
                    f"non-empty list of strings, got {value!r}")
            for index, item in enumerate(value):
                if not isinstance(item, str):
                    raise ModelOutputError(
                        f"write_skill(mode={mode!r}): {key!r}[{index}] must "
                        f"be a string, got {item!r}")
                stripped = _strip_self_numbering(item)
                if not stripped:
                    raise ModelOutputError(
                        f"write_skill(mode={mode!r}): {key!r}[{index}] is "
                        f"empty after stripping its self-numbering: "
                        f"{item!r}")
                _reject_bad_text(mode, f"{key!r}[{index}]", stripped)
        else:
            if not isinstance(value, str) or not value.strip():
                raise ModelOutputError(
                    f"write_skill(mode={mode!r}): field {key!r} must be a "
                    f"non-empty string, got {value!r}")
            _reject_bad_text(mode, f"field {key!r}", value.strip())


def write_skill(
    subtask: Subtask,
    steps_view: str,
    weak_profile: str,
    mode: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Skill:
    """Write one structured skill for one subtask (G2, protocol v2).

    ``mode`` picks the section structure (README stage 3): "distill"
    renders a four-section procedure skill from a SUCCEEDED subtask,
    "diagnose" a four-section failure reference from a FAILED one; the
    mode-to-structure mapping is the extension point when a third skill
    kind appears. Any other mode is a caller bug and raises ValueError
    before any model call, and so does a subtask outcome that contradicts
    the mode: the guards below turn an old v1 selection rule combined
    with the v2 prompts from silent distortion into a loud refusal
    (run_units treats the ValueError as a fatal config error).

    The named prompt returns JSON; the parsed dict is validated by
    validate_skill_sections and rendered by render_skill_content, whose
    marker text is what lands in ``Skill.content``. Field-level failures
    raise ModelOutputError and are terminal for the unit (gen_exam
    precedent): call_model's format retries only cover JSON parsing,
    never field content.
    """
    if mode == "distill":
        prompt_name = "SKILL_DISTILL"
        variables = {
            "label": subtask.label,
            "description": subtask.description,
            "steps_view": steps_view,
            "weak_profile": weak_profile,
        }
    elif mode == "diagnose":
        prompt_name = "SKILL_DIAGNOSE"
        variables = {
            "label": subtask.label,
            "description": subtask.description,
            "steps_view": steps_view,
            "context_summary": subtask.context_summary,
            "weak_profile": weak_profile,
        }
    else:
        raise ValueError(
            f'mode must be "distill" or "diagnose", got {mode!r}')

    if mode == "distill" and subtask.success is not True:
        raise ValueError(
            'write_skill(mode="distill") requires subtask.success is True '
            f"(procedure material), got success={subtask.success!r} on "
            f"{subtask.subtask_id}")
    if mode == "diagnose" and subtask.success is not False:
        raise ValueError(
            'write_skill(mode="diagnose") requires subtask.success is False '
            f"(diagnosis material), got success={subtask.success!r} on "
            f"{subtask.subtask_id}")

    parsed = call_model(
        handle, prompt_name, variables,
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"{prompt_name}: expected a JSON object, got {type(parsed).__name__}")
    validate_skill_sections(mode, parsed)
    return Skill(
        skill_id=make_skill_id(subtask.subtask_id),
        content=render_skill_content(mode, parsed),
        subtask_id=subtask.subtask_id,
        version=1,
        status="pending",
        rewrite_history=[],
    )


def gen_exam(
    subtask: Subtask,
    steps_view: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Exam:
    """Generate the exam question for one subtask (G3).

    The exam is written from the subtask only, never from a skill (README
    section 2). The JSON shape is part of the EXAM_GEN prompt contract;
    a parsed response missing keys or carrying empty values is a
    ModelOutputError.
    """
    variables = {
        "label": subtask.label,
        "description": subtask.description,
        "steps_view": steps_view,
    }
    parsed = call_model(
        handle, "EXAM_GEN", variables,
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"EXAM_GEN: expected a JSON object, got {type(parsed).__name__}")

    question_text = parsed.get("question_text")
    reference_answer = parsed.get("reference_answer")
    for name, value in (
        ("question_text", question_text),
        ("reference_answer", reference_answer),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ModelOutputError(
                f"EXAM_GEN: field {name!r} must be a non-empty string, "
                f"got {value!r}")

    return Exam(
        exam_id=make_exam_id(subtask.subtask_id),
        question_text=question_text.strip(),
        reference_answer=reference_answer.strip(),
        subtask_id=subtask.subtask_id,
    )


def rewrite_skill(
    skill: Skill,
    question_text: str,
    weak_answer: str,
    error_description: str,
    weak_profile: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, str]:
    """Rewrite a failed skill (G6); returns the new version's fields.

    No reference answer is ever passed in (README stage 4). The returned dict
    is ``{"content", "failure_category", "rewrite_reason"}``; the caller (K4)
    owns bumping ``skill.version`` and appending to ``rewrite_history``. The
    ``Skill`` argument is left untouched.

    Protocol v2 keeps the skill's structure: when the original content
    carries a section-marker set (every stored v2 skill does), the
    rewritten text must carry the SAME set (skill_markers set equality,
    not substring containment) or the call fails with ModelOutputError --
    a rewrite that drops a section or swaps the skill's kind would
    silently change what every marker-based consumer reads. An original
    without markers (test fixtures, foreign content; unreachable inside a
    pure v2 run) is passed through without a marker check.
    """
    variables = {
        "skill_text": skill.content,
        "question_text": question_text,
        "weak_answer": weak_answer,
        "error_description": error_description,
        "weak_profile": weak_profile,
    }
    parsed = call_model(
        handle, "REWRITE", variables,
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"REWRITE: expected a JSON object, got {type(parsed).__name__}")

    failure_category = parsed.get("failure_category")
    if failure_category not in FAILURE_CATEGORIES:
        raise ModelOutputError(
            f"REWRITE: failure_category must be one of {FAILURE_CATEGORIES}, "
            f"got {failure_category!r}")
    skill_text = parsed.get("skill_text")
    if not isinstance(skill_text, str) or not skill_text.strip():
        raise ModelOutputError(
            f"REWRITE: field 'skill_text' must be a non-empty string, "
            f"got {skill_text!r}")
    new_text = skill_text.strip()
    markers = skill_markers(skill.content)
    if markers is not None:
        new_markers = skill_markers(new_text)
        if new_markers != markers:
            raise ModelOutputError(
                "REWRITE: the rewritten skill must keep the same four "
                f"bracket sections as the original ({' / '.join(markers)}), "
                f"got {new_markers!r}")
    rewrite_reason = parsed.get("rewrite_reason")
    if not isinstance(rewrite_reason, str):
        raise ModelOutputError(
            f"REWRITE: field 'rewrite_reason' must be a string, "
            f"got {rewrite_reason!r}")

    return {
        "content": new_text,
        "failure_category": failure_category,
        "rewrite_reason": rewrite_reason,
    }
