"""G2 skill writing and G6 skill rewrite (layer 4).

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
and around them: ``has_skill_sections`` checks a stored content carries
all three sections, ``validate_skill_sections`` checks a parsed G2 response against its
mode's JSON contract, and ``render_skill_content`` turns a validated response
into the deterministic marker text stored in ``Skill.content`` (the single
representation every later consumer -- injection, G7 similarity, G8 merge,
D8 audit -- reads). ``write_skill`` refuses a subtask outcome that
contradicts its mode before any model call, and ``rewrite_skill`` requires
the rewrite to keep the three sections. Since the 2026-10-19 ruling
(docs/decisions/unified_skill_structure_20261019.md) the distill and
diagnose modes share one three-section structure.

2026-10-13 incident (run tb21_split_v2_20261013_2125, 67/315 stage-3 units
killed): validate_skill_sections rejected ANY embedded newline in a field
value, but the G2 prompts ask for "the exact command FORM", and the model
legally answers with heredoc-shaped steps -- real multi-line commands. The
over-strict check killed legal payloads as ModelOutputError terminal units.
The fix (docs/decisions/g2_multiline_and_json_extraction_fix_20261014.md)
keeps the forged-marker defense but moves its carrier: values may span lines,
and the check runs PER LINE on the line-start markers (a forged marker would
poison has_skill_sections' reading of the rendered content). Sections that pass
validation render so that a section marker can appear at a line start only
where the renderer itself wrote it: scalar values and checklist items are
normalized and checked line by line before the renderer puts their
continuation lines at column 0.

G6 deliberately returns a plain dict instead of a new Skill and never mutates
its argument: version bumps and ``rewrite_history`` appends are bookkeeping,
which belongs to the composites (K4), not to this block. The rewrite-history
entry shape is pinned as ``{content, summary, error_category, reason}`` where
``content`` and ``summary`` are the PREVIOUS version's text and one-liner
(the old summary is evidence of what the reading agent saw before the
rewrite).

Schema v3 (skill-folder M1, 2026-10-17): every G2 / G6 output also carries
``summary`` -- ONE line saying what the skill IS (the directory line), not
when to apply it. It is not a fifth section: it never enters the rendered
content, and the summary contract (non-empty, no newline) is enforced by
``_validate_summary`` in validate_skill_sections and rewrite_skill alike.
"""

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from expedistill.core.ids import make_skill_id
from expedistill.prompts.profile import profile_section
from expedistill.core.models import (
    SKILL_MARKERS,
    Skill,
    Subtask,
)
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model

__all__ = [
    "FAILURE_CATEGORIES",
    "NO_SEGMENT_POSITION",
    "SKILL_SECTION_FIELDS",
    "SKILL_SUMMARY_FIELD",
    "has_skill_sections",
    "render_skill_content",
    "rewrite_skill",
    "validate_skill_sections",
    "write_skill",
]

#: Why the weak model failed its acceptance exam (README stage 4). Pinned by
#: the REWRITE prompt's output contract; changing the set means changing the
#: prompt, which is a new experiment.
FAILURE_CATEGORIES = ("misread", "missing_step", "wrong_trigger", "other")

#: The JSON contract both write_skill modes work against: the parsed
#: response must carry exactly these keys, which map 1:1 onto the three
#: stored sections (SKILL_MARKERS). Since the 2026-10-19 ruling the
#: distill and diagnose modes share ONE structure; the mode only picks
#: the prompt and the material (a succeeded vs a failed subtask).
#: test_templates pins the G2 prompts' "Output ONLY JSON" examples to it.
SKILL_SECTION_FIELDS: Tuple[str, ...] = (
    "when_to_use", "steps", "not_applicable_when")

#: The checklist field: a non-empty list of strings in validation, a
#: renderer-numbered list in rendering.
_CHECKLIST_FIELD = "steps"

#: The write modes and their default prompts.
_MODE_PROMPTS: Dict[str, str] = {
    "distill": "SKILL_DISTILL",
    "diagnose": "SKILL_DIAGNOSE",
}

#: The non-section field every G2 / G6 output carries alongside the three
#: sections (schema v3): the skill's one-line directory entry. Pinned by
#: the SKILL_DISTILL / SKILL_DIAGNOSE / REWRITE prompts' output contracts;
#: blocks/attribution.py mirrors it for SKILL_ATTEMPT_REVISION (blocks may
#: not import each other, a unit test pins the mirror).
SKILL_SUMMARY_FIELD = "summary"

#: What the G2 prompts show when the caller knows nothing about where
#: the segment sits in its trajectory (the timing hint then rests on the
#: steps alone).
NO_SEGMENT_POSITION = "Unknown: no position information is available."

#: A checklist item's own "1. " / "12) " prefix. The G2 prompts forbid
#: self-numbering, but a stray one is stripped rather than double-numbered.
_SELF_NUMBERING_RE = re.compile(r"^\s*\d+[.)]\s+")



def _strip_self_numbering(item: str) -> str:
    """One checklist item without a stray leading "1. " / "12) " prefix."""
    return _SELF_NUMBERING_RE.sub("", item).strip()


def _normalize_newlines(value: str) -> str:
    """``value`` with CRLF and lone CR newlines normalized to ``\\n``.

    A stray ``\\r`` (a copy-pasted CRLF heredoc) would otherwise survive
    validation and be rendered byte-for-byte into the stored content, where
    no consumer expects it. Validation and rendering BOTH pass through this
    one function, so the bytes the renderer writes are exactly the bytes the
    validator checked -- the contract the 2026-10-14 fix pins.
    """
    return value.replace("\r\n", "\n").replace("\r", "\n")


def has_skill_sections(content: str) -> bool:
    """True when a stored skill content carries all three section markers.

    Line-start anchored, and ONLY ever applied to the stored multi-line
    content -- never to the single-line compressed injection block (the
    compression keeps the marker text but destroys line structure).
    Content written before the 2026-10-19 ruling with the retired
    failure-reference sections ([Trigger] / [Why it fails] / [Do instead])
    reads as False: it is not migrated, the bank is regenerated.
    """
    lines = content.split("\n")
    return all(any(line.startswith(marker) for line in lines)
               for marker in SKILL_MARKERS)


def render_skill_content(sections: Dict[str, Any]) -> str:
    """Deterministic marker-text rendering of one validated sections dict.

    Both write modes render the same layout:

        [When to use] {when_to_use}
        [Steps]
        1. {steps[0]}
        2. {steps[1]}
        [Do not use when] {not_applicable_when}

    The renderer numbers the checklist itself; same sections in,
    byte-identical content out (the stage-3 determinism invariant,
    decision section 4.2). A stray self-numbering on an item is stripped
    here too, so it is never double-numbered.

    Multi-line values are legal (the 2026-10-13 incident: heredoc-shaped
    steps). A checklist item renders its FIRST line behind the renderer's
    number ("2. ...") and every continuation line at column 0, verbatim;
    a multi-line scalar likewise continues at column 0. Scalars and items
    pass through the same ``_normalize_newlines`` as validation.
    """
    (when_to_use, steps, not_applicable) = SKILL_SECTION_FIELDS
    (use_marker, steps_marker, close_marker) = SKILL_MARKERS
    items = [f"{index}. {_strip_self_numbering(_normalize_newlines(item))}"
             for index, item in enumerate(sections[steps], 1)]
    lines = [
        f"{use_marker} {_normalize_newlines(sections[when_to_use]).strip()}",
        steps_marker,
        *items,
        f"{close_marker} "
        f"{_normalize_newlines(sections[not_applicable]).strip()}",
    ]
    return "\n".join(lines)


def _reject_bad_text(mode: str, where: str, value: str) -> None:
    """Reject a forged section marker at the start of ANY line of ``value``.

    Embedded newlines are legal content (the 2026-10-13 incident: the G2
    prompts ask for the exact command form, and heredoc-shaped steps carry
    real newlines; the old any-newline rejection killed 67/315 stage-3
    units). What stays forbidden is a line -- any line, not just the first
    -- starting with one of the three section markers: the renderer writes
    a multi-line value's continuation lines at column 0 verbatim, and
    has_skill_sections is line-start anchored, so a forged line-start
    marker in a value would poison its reading of the rendered content.
    """
    for line_number, line in enumerate(value.split("\n"), 1):
        for marker in SKILL_MARKERS:
            if line.startswith(marker):
                raise ModelOutputError(
                    f"write_skill(mode={mode!r}): {where} must not carry "
                    f"section marker {marker} at the start of line "
                    f"{line_number}: {value!r}")


def _validate_summary(where: str, value: Any) -> str:
    """Validate one model-written skill summary; return the stored form.

    The schema v3 contract (skill-folder M1): a non-empty string, ONE
    line (the directory entry is a single line, so any embedded newline
    -- after the same ``_normalize_newlines`` pass the sections get, so a
    stray \\r is caught too -- is a format error). There is no length
    cap. The returned value is the stripped, newline-normalized line that
    lands in ``Skill.summary``.
    """
    if not isinstance(value, str):
        raise ModelOutputError(
            f"{where}: field {SKILL_SUMMARY_FIELD!r} must be a string, "
            f"got {value!r}")
    summary = _normalize_newlines(value).strip()
    if not summary:
        raise ModelOutputError(
            f"{where}: field {SKILL_SUMMARY_FIELD!r} must be a non-empty "
            f"string, got {value!r}")
    if "\n" in summary:
        raise ModelOutputError(
            f"{where}: field {SKILL_SUMMARY_FIELD!r} must be ONE line "
            "(the skill's directory entry); got a value with an embedded "
            f"newline: {value!r}")
    return summary


def validate_skill_sections(mode: str, sections: Dict[str, Any]) -> None:
    """Field-level validation of one parsed G2 response (protocol v2).

    ``sections`` must be a dict (extract_json can return a top-level
    array); the mode's required keys (SKILL_SECTION_FIELDS) plus the
    schema v3 ``summary`` field must be present; every string field must
    be a non-empty string; the checklist field (steps / do_instead) a
    non-empty list of strings, each stripped of a leading
    self-numbering ("1. ", "12) ") and then non-empty.
    Values MAY span multiple lines (the 2026-10-13 incident: the newline
    rejection was over-strict); what is checked instead is that NO line of
    a normalized value STARTS WITH any section marker: the renderer emits
    continuation lines at column 0, and a forged marker at a line start
    would poison has_skill_sections' reading of the rendered content. The summary is the one field the
    multi-line license does NOT cover: it is a directory line, so
    ``_validate_summary`` rejects any embedded newline. The checked bytes
    are the same ``_normalize_newlines`` bytes the renderer will write.
    Extra keys are ignored (lenient read, the exam blocks' precedent) but the
    checklist type check is never skipped. Raises ModelOutputError
    otherwise.
    """
    if mode not in _MODE_PROMPTS:
        raise ValueError(
            f'mode must be "distill" or "diagnose", got {mode!r}')
    if not isinstance(sections, dict):
        raise ModelOutputError(
            f"write_skill(mode={mode!r}): sections must be a JSON object, "
            f"got {type(sections).__name__}")
    for key in SKILL_SECTION_FIELDS + (SKILL_SUMMARY_FIELD,):
        if key not in sections:
            raise ModelOutputError(
                f"write_skill(mode={mode!r}): missing required field "
                f"{key!r}, got keys {sorted(sections)}")
    for key in SKILL_SECTION_FIELDS:
        value = sections[key]
        if key == _CHECKLIST_FIELD:
            if not isinstance(value, list) or not value:
                raise ModelOutputError(
                    f"write_skill(mode={mode!r}): field {key!r} must be a "
                    f"non-empty list of strings, got {value!r}")
            for index, item in enumerate(value):
                if not isinstance(item, str):
                    raise ModelOutputError(
                        f"write_skill(mode={mode!r}): {key!r}[{index}] must "
                        f"be a string, got {item!r}")
                stripped = _strip_self_numbering(_normalize_newlines(item))
                if not stripped:
                    raise ModelOutputError(
                        f"write_skill(mode={mode!r}): {key!r}[{index}] is "
                        f"empty after stripping its self-numbering: "
                        f"{item!r}")
                _reject_bad_text(mode, f"{key!r}[{index}]", stripped)
        else:
            if (not isinstance(value, str)
                    or not _normalize_newlines(value).strip()):
                raise ModelOutputError(
                    f"write_skill(mode={mode!r}): field {key!r} must be a "
                    f"non-empty string, got {value!r}")
            _reject_bad_text(mode, f"field {key!r}",
                             _normalize_newlines(value).strip())
    _validate_summary(f"write_skill(mode={mode!r})",
                      sections[SKILL_SUMMARY_FIELD])


def write_skill(
    subtask: Subtask,
    steps_view: str,
    weak_profile: str,
    mode: str,
    handle: ModelHandle,
    *,
    segment_position: str = "",
    prompt_name: Optional[str] = None,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Skill:
    """Write one structured skill for one subtask (G2, protocol v2).

    ``mode`` picks the prompt and the material: "distill" writes from a
    SUCCEEDED subtask (what to do), "diagnose" from a FAILED one (which
    mistake to avoid and what to do instead). Both render the same three
    sections (the 2026-10-19 ruling). Any other mode is a caller bug and
    raises ValueError before any model call, and so does a subtask outcome
    that contradicts the mode: the guards below turn an old v1 selection
    rule combined
    with the v2 prompts from silent distortion into a loud refusal
    (run_units treats the ValueError as a fatal config error).

    ``prompt_name`` overrides the mode's default prompt (SKILL_DISTILL /
    SKILL_DIAGNOSE): a prompt-strategy variant registers its own prompt
    under a new name and passes it here, keeping the mode's three-section
    output contract. None -- every current caller -- keeps the default,
    so the parameter is invisible to the frozen runs.

    The named prompt returns JSON; the parsed dict is validated by
    validate_skill_sections and rendered by render_skill_content, whose
    marker text is what lands in ``Skill.content``. The response's
    ``summary`` (schema v3) lands verbatim -- stripped and
    newline-normalized -- in ``Skill.summary``. Field-level failures
    raise ModelOutputError and are terminal for the unit (the exam
    blocks' precedent): call_model's format retries only cover JSON parsing,
    never field content.

    ``segment_position`` is plain text saying where the segment sits in
    its trajectory (the steps view restarts at step 0, so the prompt
    cannot see it otherwise); it is the hint for the card's trigger
    timing. Empty means unknown and renders NO_SEGMENT_POSITION.
    """
    if mode not in _MODE_PROMPTS:
        raise ValueError(
            f'mode must be "distill" or "diagnose", got {mode!r}')
    name = prompt_name or _MODE_PROMPTS[mode]
    variables = {
        "label": subtask.label,
        "description": subtask.description,
        "steps_view": steps_view,
        "segment_position": segment_position.strip() or NO_SEGMENT_POSITION,
        "profile_section": profile_section(
            "THE MODEL THAT WILL READ THE SKILL (weak model profile):",
            weak_profile),
    }
    if mode == "diagnose":
        variables["context_summary"] = subtask.context_summary

    if mode == "distill" and subtask.success is not True:
        raise ValueError(
            'write_skill(mode="distill") requires subtask.success is True '
            f"(success material), got success={subtask.success!r} on "
            f"{subtask.subtask_id}")
    if mode == "diagnose" and subtask.success is not False:
        raise ValueError(
            'write_skill(mode="diagnose") requires subtask.success is False '
            f"(failure material), got success={subtask.success!r} on "
            f"{subtask.subtask_id}")

    parsed = call_model(
        handle, name, variables,
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"{name}: expected a JSON object, got {type(parsed).__name__}")
    validate_skill_sections(mode, parsed)
    return Skill(
        skill_id=make_skill_id(subtask.subtask_id),
        content=render_skill_content(parsed),
        subtask_id=subtask.subtask_id,
        source_subtasks=[subtask.subtask_id],
        summary=_validate_summary(f"write_skill(mode={mode!r})",
                                  parsed[SKILL_SUMMARY_FIELD]),
        version=1,
        status="pending",
        rewrite_history=[],
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
    is ``{"content", "failure_category", "rewrite_reason", "summary"}``;
    the caller (K4) owns bumping ``skill.version`` and appending to
    ``rewrite_history`` (storing the OLD summary alongside the old
    content). The ``summary`` value is the rewritten skill's fresh
    one-liner (schema v3): the content changed, so the old line no longer
    describes the skill. The ``Skill`` argument is left untouched.

    Protocol v2 keeps the skill's structure: when the original content
    carries a section-marker set (every stored v2 skill does), the
    rewritten text must carry all three sections too (has_skill_sections)
    or the call fails with ModelOutputError -- a rewrite that drops a
    section would silently change what every marker-based consumer
    reads. An original without the three sections (test fixtures, foreign
    content, a pre-2026-10-19 failure reference) is passed through
    without the check.
    """
    variables = {
        "skill_text": skill.content,
        "question_text": question_text,
        "weak_answer": weak_answer,
        "error_description": error_description,
        "profile_section": profile_section(
            "THE MODEL THAT WILL READ THE SKILL (weak model profile):",
            weak_profile),
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
    if has_skill_sections(skill.content) and not has_skill_sections(new_text):
        raise ModelOutputError(
            "REWRITE: the rewritten skill must keep the three bracket "
            f"sections ({' / '.join(SKILL_MARKERS)})")
    rewrite_reason = parsed.get("rewrite_reason")
    if not isinstance(rewrite_reason, str):
        raise ModelOutputError(
            f"REWRITE: field 'rewrite_reason' must be a string, "
            f"got {rewrite_reason!r}")
    summary = _validate_summary("REWRITE", parsed.get(SKILL_SUMMARY_FIELD))

    return {
        "content": new_text,
        "failure_category": failure_category,
        "rewrite_reason": rewrite_reason,
        "summary": summary,
    }
