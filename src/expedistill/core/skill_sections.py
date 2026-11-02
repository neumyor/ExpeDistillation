"""Split and splice the three sections of a stored skill text (layer 0, pure).

A stored skill is ``[When to use] ...``, ``[Steps]`` and ``[Do not use
when] ...`` blocks, each starting at a marker at the beginning of a line
(``core.models.SKILL_MARKERS``). The *trigger* of a skill is its summary
plus the ``[When to use]`` block; the *body* is the other two blocks.
"""

from typing import Collection, Dict, List

from expedistill.core.models import SKILL_MARKERS

__all__ = ["BODY_MARKERS", "REVISE_SCOPES", "TRIGGER_MARKERS",
           "split_sections", "splice_sections"]

#: Which part of a skill a rewrite may change: everything, the body only,
#: or the trigger only.
REVISE_SCOPES = ("both", "body", "trigger")

#: The block that is part of a skill's trigger (with its summary).
TRIGGER_MARKERS = SKILL_MARKERS[:1]

#: The blocks behind the trigger.
BODY_MARKERS = SKILL_MARKERS[1:]


def split_sections(content: str) -> Dict[str, str]:
    """``{marker: block}``; a block runs from its marker line to the line
    before the next marker and keeps the marker. Text before the first
    marker is dropped."""
    blocks: Dict[str, List[str]] = {}
    current = None
    for line in content.split("\n"):
        marker = next((m for m in SKILL_MARKERS if line.startswith(m)), None)
        if marker is not None:
            current = marker
            blocks[current] = [line]
        elif current is not None:
            blocks[current].append(line)
    return {marker: "\n".join(lines) for marker, lines in blocks.items()}


def splice_sections(old: str, new: str, take_new: Collection[str]) -> str:
    """A skill text whose ``take_new`` blocks come from ``new`` and whose
    other blocks come from ``old``, in marker order.

    ValueError when either text lacks a block it must contribute.
    """
    old_blocks = split_sections(old)
    new_blocks = split_sections(new)
    parts = []
    for marker in SKILL_MARKERS:
        source, label = ((new_blocks, "new") if marker in take_new
                         else (old_blocks, "old"))
        if marker not in source:
            raise ValueError(f"the {label} skill text lacks {marker}")
        parts.append(source[marker])
    return "\n".join(parts)
