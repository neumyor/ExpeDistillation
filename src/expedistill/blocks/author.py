"""Validation of the skill fields a writing prompt returns (layer 4).

Every prompt that writes skill text returns JSON fields that map 1:1 onto
the stored ``Skill`` fields: CARD_WRITE returns all four (``summary``,
``when_to_use``, ``steps`` as a list, ``not_applicable_when``),
REVISE_TRIGGER only the trigger side and REVISE_CONTENT only the content
side. The parsers here validate one side or both and return the stored
form; the card the executor reads is rendered later by
``inject.skill_text.render_card``.

Values may span lines (the 2026-10-13 incident: heredoc-shaped steps carry
real newlines). The summary is the one field that must be a single line: it
is the skill's directory entry.
"""

from typing import Any, Dict, Tuple

from expedistill.core.models import CONTENT_FIELDS, TRIGGER_FIELDS
from expedistill.infra.reliability import ModelOutputError
from expedistill.inject.skill_text import normalize_newlines, normalize_step

__all__ = [
    "SKILL_SECTION_FIELDS",
    "SKILL_SUMMARY_FIELD",
    "parse_content_fields",
    "parse_skill_fields",
    "parse_trigger_fields",
]

#: The skill fields every full writing prompt returns besides the summary.
#: test_templates pins the prompts' "Output ONLY JSON" examples to it.
SKILL_SECTION_FIELDS: Tuple[str, ...] = (
    "when_to_use", "steps", "not_applicable_when")

#: The one-line directory entry every full writing prompt returns.
SKILL_SUMMARY_FIELD = "summary"


def _parse_summary(where: str, value: Any) -> str:
    """A non-empty string on ONE line; returns the stripped stored form."""
    if not isinstance(value, str):
        raise ModelOutputError(
            f"{where}: field {SKILL_SUMMARY_FIELD!r} must be a string, "
            f"got {value!r}")
    summary = normalize_newlines(value).strip()
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


def _parse_text(where: str, key: str, value: Any) -> str:
    if not isinstance(value, str) or not normalize_newlines(value).strip():
        raise ModelOutputError(
            f"{where}: field {key!r} must be a non-empty string, "
            f"got {value!r}")
    return normalize_newlines(value).strip()


def _parse_steps(where: str, value: Any) -> list:
    if not isinstance(value, list) or not value:
        raise ModelOutputError(
            f"{where}: field 'steps' must be a non-empty list of strings, "
            f"got {value!r}")
    steps = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ModelOutputError(
                f"{where}: 'steps'[{index}] must be a string, got {item!r}")
        step = normalize_step(item)
        if not step:
            raise ModelOutputError(
                f"{where}: 'steps'[{index}] is empty after stripping its "
                f"self-numbering: {item!r}")
        steps.append(step)
    return steps


def _object_with(where: str, parsed: Any, keys: Tuple[str, ...]) -> dict:
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"{where}: expected a JSON object, got {type(parsed).__name__}")
    for key in keys:
        if key not in parsed:
            raise ModelOutputError(
                f"{where}: missing required field {key!r}, got keys "
                f"{sorted(parsed)}")
    return parsed


def parse_trigger_fields(where: str, parsed: Any) -> Dict[str, Any]:
    """The trigger side (``summary``, ``when_to_use``) of one response."""
    parsed = _object_with(where, parsed, TRIGGER_FIELDS)
    return {
        "summary": _parse_summary(where, parsed["summary"]),
        "when_to_use": _parse_text(where, "when_to_use",
                                   parsed["when_to_use"]),
    }


def parse_content_fields(where: str, parsed: Any) -> Dict[str, Any]:
    """The content side (``steps``, ``not_applicable_when``) of one
    response; each step loses any self-numbering (the renderer numbers
    the steps)."""
    parsed = _object_with(where, parsed, CONTENT_FIELDS)
    return {
        "steps": _parse_steps(where, parsed["steps"]),
        "not_applicable_when": _parse_text(
            where, "not_applicable_when", parsed["not_applicable_when"]),
    }


def parse_skill_fields(where: str, parsed: Any) -> Dict[str, Any]:
    """Both sides of one full writing response: the four stored fields.

    Extra keys are ignored. Raises ModelOutputError on a missing or
    malformed field.
    """
    _object_with(where, parsed, SKILL_SECTION_FIELDS + (SKILL_SUMMARY_FIELD,))
    return {**parse_trigger_fields(where, parsed),
            **parse_content_fields(where, parsed)}
