"""D9 skill injection and D10 bank-length accounting (layer 1, pure functions).

This module is the single source of "skills go into the prompt" (README 1.5):
stage-4/5 exam answering (G4) and stage-6 real execution (E1) both render the
skills block here, so "the injection used at acceptance equals the injection
used at test" holds by construction, not by convention.

The block format is pinned: downstream prompt templates (e.g. G4 ANSWER's
``{skills_block}`` slot) depend on it, and prompt digests are frozen into the
run manifest, so any change here is a new experiment.

Old-repo note (ExpeToSkill-merged): its executor wrapped the rule text in a
benchmark-specific sentence ("The following are some experience ... Use these
as references") and injected the body verbatim. That wrapper is a benchmark
concern there; here the block is benchmark-agnostic and the wrapper (if any)
belongs to the benchmark plugin's task prompt. What is deliberately kept from
the old repo is its null-state lesson: an empty skill set must produce NO
block at all (``""``), never a header with no bullets — an empty block would
claim experience exists and perturb the prompt relative to a bare run.

Only pure functions here: no disk IO, no model calls, no global state.
"""

import hashlib
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from expedistill.core.models import Skill

__all__ = [
    "bank_reference",
    "count_bank_tokens",
    "estimate_tokens",
    "format_skills_block",
    "skill_ref",
]

#: First line of a non-empty block; the anchor G4/E1 prompts key on.
BLOCK_HEADER = "## Skill rules"

#: A group's skills appear under this heading before their bullets.
_GROUP_HEADER = "### {group}"

#: How each skill is rendered: content on a single line, one bullet per skill.
_BULLET = "- {content}"

#: Normalization target shared by every function in this module.
_SkillRecord = Dict[str, Any]

#: sha256 of the joined reference lines, truncated to this many hex chars.
_BANK_DIGEST_CHARS = 12


def _normalize(skill: Union[Skill, Mapping[str, Any]]) -> _SkillRecord:
    """Accept Skill objects or dicts (skill_id, version, content, group).

    ``version`` and ``group`` fall back to the same defaults as the Skill
    dataclass; ``skill_id`` and ``content`` are required of both shapes.
    """
    if isinstance(skill, Skill):
        return {
            "skill_id": skill.skill_id,
            "version": skill.version,
            "content": skill.content,
            "group": skill.group,
        }
    if isinstance(skill, Mapping):
        return {
            "skill_id": skill["skill_id"],
            "version": skill.get("version", 1),
            "content": skill["content"],
            "group": skill.get("group"),
        }
    raise TypeError(
        f"expected a Skill or a mapping, got {type(skill).__name__}")


def _single_line(content: str) -> str:
    """Collapse internal newlines (with their surrounding blanks) to one space.

    A multi-line skill would break the one-bullet-per-skill layout, so each
    bullet carries the content on exactly one line. Everything else about the
    content is kept verbatim.
    """
    return re.sub(r"\s*\n\s*", " ", content).strip()


def _group_of(skill: _SkillRecord,
              groups: Optional[Mapping[str, Optional[str]]]) -> Optional[str]:
    """The group to file a skill under: an explicit override wins, else the
    skill's own group. An override mapping to None (or a blank group) un-groups
    the skill, so curation can also dissolve a group."""
    group = skill["group"]
    if groups is not None:
        group = groups.get(skill["skill_id"], group)
    return group or None


def format_skills_block(
    skills: Sequence[Union[Skill, Mapping[str, Any]]],
    groups: Optional[Mapping[str, Optional[str]]] = None,
) -> str:
    """Render skills as the rule text block inserted into a prompt (D9).

    An empty skill list yields ``""`` — the bare-run case: G4's ANSWER
    template and the E1 task prompts insert this block both ways, so the only
    difference between a skilled and a bare prompt is whether the block is
    empty (README G4).

    Output format (pinned; see module docstring):

    * first line ``## Skill rules``;
    * one bullet per skill: ``- <content>`` with the content's internal
      newlines collapsed to single spaces (one skill, one line);
    * grouped skills appear under ``### <group>`` headers, groups in
      alphabetical order (stable input order within a group); skills without
      a group come last, under no header.

    ``groups`` optionally maps ``skill_id -> group`` and overrides each
    skill's own ``group`` (curation assigns groups this way); absent entries
    keep the skill's own group, and a ``None`` value un-groups the skill.

    Accepts Skill objects and dicts (``skill_id``, ``version``, ``content``,
    ``group``) interchangeably.
    """
    records = [_normalize(skill) for skill in skills]
    if not records:
        return ""

    grouped: Dict[str, List[_SkillRecord]] = {}
    ungrouped: List[_SkillRecord] = []
    for record in records:
        group = _group_of(record, groups)
        if group is None:
            ungrouped.append(record)
        else:
            grouped.setdefault(group, []).append(record)

    lines = [BLOCK_HEADER]
    for group in sorted(grouped):
        lines.append(_GROUP_HEADER.format(group=group))
        lines.extend(_BULLET.format(content=_single_line(r["content"]))
                     for r in grouped[group])
    lines.extend(_BULLET.format(content=_single_line(r["content"]))
                 for r in ungrouped)
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """Approximate the token count of ``text`` as ``max(1, len(text) // 4)``.

    A budgeting heuristic, NOT an exact tokenization: it is meant for D10's
    curation budget check (``bank_token_budget``), where a rough size is
    enough and a per-model tokenizer would drag a model dependency into this
    layer. It is off by design for non-English-heavy text; every 4 characters
    count as one token.
    """
    return max(1, len(text) // 4)


def count_bank_tokens(
    skills: Sequence[Union[Skill, Mapping[str, Any]]],
    groups: Optional[Mapping[str, Optional[str]]] = None,
) -> int:
    """Estimated tokens of the injected block for ``skills`` (D10).

    An empty bank injects nothing and therefore costs nothing (0), rather
    than ``estimate_tokens("")`` == 1; the budget check must not reject a
    zero-token budget because of a phantom token. Otherwise this is exactly
    ``estimate_tokens(format_skills_block(skills, groups))``.
    """
    block = format_skills_block(skills, groups)
    if not block:
        return 0
    return estimate_tokens(block)


def bank_reference(skills: Sequence[Union[Skill, Mapping[str, Any]]]) -> str:
    """Canonical digest of the whole bank: ``bank:<12 hex chars>``.

    The digest is the sha256 of the ``skill_id@version:content`` lines joined
    by newlines, truncated to 12 hex chars, in the given order. It covers the
    skills themselves (ids, versions, raw content) — NOT the rendered block, so
    regrouping does not change the reference, but any content, version or
    membership change does. Recorded in ``Trajectory.skills_injected`` when the
    whole bank is injected at stage 6.

    Known edge, left on record (the skill-folder review's note): the
    ``summary`` field sits OUTSIDE the digest. A summary-only change would
    flow through the catalog text and the unit ids it feeds, not through
    this fingerprint; today every summary change lands together with a
    version/content change (the writers move all three), a constraint that
    holds by construction, not by mechanism.
    """
    lines = [
        "{skill_id}@{version}:{content}".format(**_normalize(skill))
        for skill in skills
    ]
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return f"bank:{digest[:_BANK_DIGEST_CHARS]}"


def skill_ref(skill: Union[Skill, Mapping[str, Any]]) -> str:
    """Canonical single-skill reference: ``<skill_id>@v<version>``.

    Recorded in ``Trajectory.skills_injected`` and Attempt records whenever
    that one skill is the injection (stage-4 acceptance answers).
    """
    record = _normalize(skill)
    return f"{record['skill_id']}@v{record['version']}"
