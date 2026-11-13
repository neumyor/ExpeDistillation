"""The only renderers of a skill's text (layer 1, pure functions).

A skill is stored as fields (``core.models.Skill``); every consumer that
needs one block of skill text -- injection, SKILL.md, the learn phase's
writer inputs, judges -- calls ``render_card``, and every catalog line or SKILL.md
``description`` comes from ``description_text``. The folder delivery
(layer 6) and the folder materializer (layer 5) both import from here,
below both.

The description mode is the operator's choice:

- ``summary``: the skill's one-line WHAT sentence;
- ``when_to_use``: the ``when_to_use`` field;
- ``summary_then_when_to_use``: the summary followed by the trigger.

A missing preferred field falls back to the other one, then to an
explicit ``Skill <id>.`` line, because the Agent Skills format requires
a non-empty description. The result is always one line and at most
``SKILL_DESCRIPTION_MAX_CHARS`` characters; the catalog applies its own
shorter clip after this function returns.
"""

import re
from collections.abc import Mapping
from typing import Any, Union

__all__ = [
    "DEFAULT_DESCRIPTION_MODE",
    "DESCRIPTION_MODES",
    "SKILL_DESCRIPTION_MAX_CHARS",
    "SKILL_MARKERS",
    "description_text",
    "normalize_newlines",
    "normalize_step",
    "one_line",
    "render_card",
    "validate_description_mode",
    "when_to_use_line",
]

#: The section headers of a rendered card, in order.
SKILL_MARKERS = ("[When to use]", "[Steps]", "[Do not use when]")

#: The three supported composition rules.
DESCRIPTION_MODES = (
    "summary",
    "when_to_use",
    "summary_then_when_to_use",
)

#: The Agent Skills format needs both WHAT and WHEN; this is the default
#: because it satisfies that contract.
DEFAULT_DESCRIPTION_MODE = "summary_then_when_to_use"

#: The Agent Skills format's hard ceiling for one description.
SKILL_DESCRIPTION_MAX_CHARS = 1024

#: The separator between the summary and the trigger in the combined
#: mode. Kept explicit so the catalog and SKILL.md stay readable. A
#: trigger that already opens with "Use when" is joined with a space:
#: "Use when: Use this when ..." reads badly and adds no information.
_TRIGGER_JOINER = " Use when: "
_TRIGGER_PREFIXES = ("use when", "use this when", "use it when")

#: A step's own "1. " / "12) " prefix: the renderer numbers the steps, so
#: a stray self-numbering is stripped rather than double-numbered.
_SELF_NUMBERING_RE = re.compile(r"^\s*\d+[.)]\s+")


def one_line(text: str) -> str:
    """Collapse every whitespace run (newlines included) to one space."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()


def normalize_newlines(value: str) -> str:
    """``value`` with CRLF and lone CR newlines normalized to ``\\n``."""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def normalize_step(item: str) -> str:
    """One step as stored and rendered: newlines normalized, a stray
    leading "1. " / "12) " removed, outer whitespace stripped."""
    return _SELF_NUMBERING_RE.sub("", normalize_newlines(item)).strip()


def _field(record: Union[Any, Mapping[str, Any]], name: str,
           default: Any = None) -> Any:
    """Read one field from a Skill-like object or a bank row mapping."""
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def render_card(record: Union[Any, Mapping[str, Any]]) -> str:
    """The skill's full text, as the executor sees it when it opens it.

    An imported skill renders its ``verbatim_text``. Every other skill
    renders as::

        [When to use] {when_to_use}
        [Steps]
        1. {steps[0]}
        2. {steps[1]}
        [Do not use when] {not_applicable_when}

    A multi-line step keeps its continuation lines at column 0. Accepts a
    Skill or a bank row mapping.
    """
    verbatim = _field(record, "verbatim_text", "") or ""
    if verbatim:
        return verbatim
    (use_marker, steps_marker, close_marker) = SKILL_MARKERS
    items = [f"{index}. {normalize_step(item)}"
             for index, item in enumerate(_field(record, "steps", ()), 1)]
    when_to_use = normalize_newlines(_field(record, "when_to_use", "")).strip()
    not_applicable = normalize_newlines(
        _field(record, "not_applicable_when", "")).strip()
    return "\n".join([
        f"{use_marker} {when_to_use}",
        steps_marker,
        *items,
        f"{close_marker} {not_applicable}",
    ])


def when_to_use_line(record: Union[Any, Mapping[str, Any]]) -> str:
    """The skill's ``when_to_use`` field as one line, '' when empty."""
    return one_line(_field(record, "when_to_use", "") or "")


def validate_description_mode(mode: str) -> str:
    """Return ``mode`` unchanged, or raise ValueError with the allowed set."""
    if mode not in DESCRIPTION_MODES:
        raise ValueError(
            f"description_mode must be one of {DESCRIPTION_MODES}, got "
            f"{mode!r}")
    return mode


def description_text(record: Union[Any, Mapping[str, Any]],
                     mode: str = DEFAULT_DESCRIPTION_MODE) -> str:
    """One skill's description under ``mode``.

    The preferred field wins; an empty preferred field falls back to the
    other field, and both empty fall back to ``Skill <id>.``. The result
    is one line, non-empty, and clipped to the Agent Skills ceiling.
    """
    validate_description_mode(mode)
    skill_id = str(_field(record, "skill_id", "") or "unknown")
    summary = one_line(_field(record, "summary", "") or "")
    trigger = when_to_use_line(record)

    if mode == "summary":
        text = summary or trigger
    elif mode == "when_to_use":
        text = trigger or summary
    else:
        if summary and trigger:
            joiner = (" " if trigger.lower().startswith(_TRIGGER_PREFIXES)
                      else _TRIGGER_JOINER)
            text = summary + joiner + trigger
        else:
            text = summary or trigger
    if not text:
        text = f"Skill {skill_id}."
    return text[:SKILL_DESCRIPTION_MAX_CHARS].rstrip()
