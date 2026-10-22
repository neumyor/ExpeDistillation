"""Skill text fields shared by the folder delivery and its materializer.

The catalog line and the SKILL.md YAML ``description`` must be derived
from the same skill text, but the delivery (layer 6) and the folder
materializer (layer 5) sit on opposite sides of the layer rule: the
composite may not import the methods layer. This module keeps the two
derivations in one place, below both.

The description mode is the operator's choice:

- ``summary``: the skill's one-line WHAT sentence;
- ``when_to_use``: the ``[When to use]`` section body;
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

from expedistill.core.models import SKILL_MARKERS

__all__ = [
    "DEFAULT_DESCRIPTION_MODE",
    "DESCRIPTION_MODES",
    "SKILL_DESCRIPTION_MAX_CHARS",
    "description_text",
    "one_line",
    "validate_description_mode",
    "when_to_use_line",
]

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

#: The section marker whose body says WHEN the skill applies.
_WHEN_TO_USE = SKILL_MARKERS[0]

#: The separator between the summary and the trigger in the combined
#: mode. Kept explicit so the catalog and SKILL.md stay readable. A
#: trigger that already opens with "Use when" is joined with a space:
#: "Use when: Use this when ..." reads badly and adds no information.
_TRIGGER_JOINER = " Use when: "
_TRIGGER_PREFIXES = ("use when", "use this when", "use it when")


def one_line(text: str) -> str:
    """Collapse every whitespace run (newlines included) to one space."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _field(record: Union[Any, Mapping[str, Any]], name: str,
           default: Any = None) -> Any:
    """Read one field from a Skill-like object or a bank row mapping."""
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def when_to_use_line(content: str) -> str:
    """The ``[When to use]`` section body as one line, '' when absent.

    The section body runs from the marker line's remainder (the
    renderer puts the first sentence on the marker line) to the next
    section marker or the end of the content.
    """
    if not isinstance(content, str):
        return ""
    body = []
    in_section = False
    for line in content.splitlines():
        if in_section and any(line.startswith(section_marker)
                              for section_marker in SKILL_MARKERS):
            break
        if not in_section:
            if line.startswith(_WHEN_TO_USE):
                in_section = True
                body.append(line[len(_WHEN_TO_USE):])
            continue
        body.append(line)
    if not in_section:
        return ""
    return one_line(" ".join(body))


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
    trigger = when_to_use_line(_field(record, "content", "") or "")

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
