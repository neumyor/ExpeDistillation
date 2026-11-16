"""D9 skill injection (layer 1, pure functions).

This module is the single source of "skills go into the prompt" (design_spec 3.3, D9):
real execution (E1) renders the skills block here whichever phase runs it,
so the injection is one code path by construction, not by convention. Each
skill's text comes from ``inject.skill_text.render_card``.

The block format is pinned: downstream prompt templates (the executor
prompts' ``{skills_block}`` slot) depend on it, and prompt digests are frozen into the
run manifest, so any change here is a new experiment.

An empty skill set produces NO block at all (``""``), never a header with
no bullets: an empty block would claim experience exists and perturb the
prompt relative to a bare run.

Only pure functions here: no disk IO, no model calls, no global state.
"""

import hashlib
import re
from typing import Any, Dict, Mapping, Sequence, Union

from expedistill.core.models import Skill
from expedistill.inject.skill_text import render_card

__all__ = [
    "bank_reference",
    "estimate_tokens",
    "format_playbook_block",
    "format_skills_block",
]

#: First line of a non-empty block; the anchor the E1 executor prompts key on.
BLOCK_HEADER = "## Skill rules"

#: How each skill is rendered: its card on a single line, one bullet each.
_BULLET = "- {card}"

#: sha256 of the joined reference lines, truncated to this many hex chars.
_BANK_DIGEST_CHARS = 12


def _normalize(skill: Union[Skill, Mapping[str, Any]]) -> Dict[str, Any]:
    """``skill_id``, ``version`` and the rendered card of a Skill or a bank
    row mapping (``version`` defaults to 1 like the dataclass)."""
    if isinstance(skill, Skill):
        return {"skill_id": skill.skill_id, "version": skill.version,
                "card": render_card(skill)}
    if isinstance(skill, Mapping):
        return {"skill_id": skill["skill_id"],
                "version": skill.get("version", 1),
                "card": render_card(skill)}
    raise TypeError(
        f"expected a Skill or a mapping, got {type(skill).__name__}")


def _single_line(text: str) -> str:
    """Collapse internal newlines (with their surrounding blanks) to one
    space, so each bullet carries one skill on exactly one line."""
    return re.sub(r"\s*\n\s*", " ", text).strip()


#: First line of the playbook block every executor prompt shows above its
#: skills when the phase's playbook switch is on.
PLAYBOOK_HEADER = "Global playbook:"


def format_playbook_block(text: str) -> str:
    """The playbook as the executor sees it, ending in a blank line so the
    skills follow it; an empty playbook is the empty string."""
    text = (text or "").strip()
    if not text:
        return ""
    return f"{PLAYBOOK_HEADER}\n{text}\n\n"


def format_skills_block(
    skills: Sequence[Union[Skill, Mapping[str, Any]]],
    *,
    verbatim: bool = False,
) -> str:
    """Render skills as the rule text block inserted into a prompt (D9).

    An empty skill list yields ``""`` -- the bare-run case: the only
    difference between a skilled and a bare prompt is whether the block is
    empty.

    Output format (pinned; see module docstring): first line
    ``## Skill rules``, then one bullet per skill, ``- <card>`` with the
    card's internal newlines collapsed to single spaces, in input order.

    ``verbatim=True`` renders each card exactly, after the header and
    separated by one blank line, with no bullets and no newline collapsing.
    It exists for skill documents written by other methods (an imported
    markdown file must reach the model with its headings and lists intact).
    """
    records = [_normalize(skill) for skill in skills]
    if not records:
        return ""
    if verbatim:
        return "\n\n".join([BLOCK_HEADER] + [r["card"] for r in records])
    return "\n".join([BLOCK_HEADER] + [
        _BULLET.format(card=_single_line(r["card"])) for r in records])


def estimate_tokens(text: str) -> int:
    """Approximate the token count of ``text`` as ``max(1, len(text) // 4)``.

    A budgeting heuristic, NOT an exact tokenization: it is meant for
    ``call_model``'s estimated input size, where a rough size is enough and
    a per-model tokenizer would drag a model dependency into this layer.
    """
    return max(1, len(text) // 4)


def bank_reference(skills: Sequence[Union[Skill, Mapping[str, Any]]]) -> str:
    """Canonical digest of the whole bank: ``bank:<12 hex chars>``.

    The sha256 of the ``skill_id@version:card`` lines joined by newlines,
    truncated to 12 hex chars, in the given order: any card, version or
    membership change changes the reference. The summary is not on the
    card; every summary change lands together with a version change.
    Recorded in ``Trajectory.skills_injected`` when the whole bank is
    injected.
    """
    lines = [
        "{skill_id}@{version}:{card}".format(**_normalize(skill))
        for skill in skills
    ]
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return f"bank:{digest[:_BANK_DIGEST_CHARS]}"
