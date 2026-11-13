"""Evidence routing (layer 5, pure functions).

Each diagnosed and located failure is routed by program facts alone:

==================================================== ==========
fact                                                 category
==================================================== ==========
the diagnosis says ``scope = general``               ``none``
the failing segment's type has no active card        ``gap``
it has one, first read at or after the failing step  ``trigger``
it has one, first read before the failing step       ``content``
==================================================== ==========

"Opened before" is strict: a card first read AT the failing step, or
later, was not open when the trial went wrong. The read steps come from the
trajectory's read actions (``composites.skill_use``), never from the model.

:func:`plan_cards` turns one phase's evidence into one unit per card or
type; :func:`content_failed_twice` is the delete rule (content evidence for
the same card in the previous learn phase too); :func:`trigger_facts` is
the trigger-evidence count text the playbook writer reads.
"""

from dataclasses import asdict, dataclass, field
from typing import (
    Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set)

from expedistill.core.models import Subtask
from expedistill.infra.reliability import CodeBugError

__all__ = [
    "CATEGORIES",
    "CONTENT",
    "CONTENT_FAILED_TWICE",
    "CardPlan",
    "Evidence",
    "GAP",
    "NONE",
    "TRIGGER",
    "content_failed_twice",
    "count_categories",
    "discarded_types",
    "error_segment",
    "plan_cards",
    "route",
    "trigger_facts",
]

GAP = "gap"
TRIGGER = "trigger"
CONTENT = "content"
NONE = "none"
CATEGORIES = (GAP, TRIGGER, CONTENT, NONE)

#: The diagnosis scopes (``methods.diagnose.Finding.scope``).
SCOPE_SUBTASK = "subtask"
SCOPE_GENERAL = "general"

#: The discard reason of the delete rule.
CONTENT_FAILED_TWICE = "content_failed_twice"


@dataclass(frozen=True)
class Evidence:
    """One routed failure (one row of ``evidence.jsonl``)."""

    sheet_id: str
    failing_step: int
    subtask_id: str
    type_id: str
    skill_id: Optional[str]
    opened_before: bool
    opened_step: Optional[int]
    category: str

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise CodeBugError(
                f"evidence category {self.category!r} is not one of "
                f"{CATEGORIES}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        return cls(**{name: data[name] for name in (
            "sheet_id", "failing_step", "subtask_id", "type_id",
            "skill_id", "opened_before", "opened_step", "category")})


def error_segment(segments: Sequence[Subtask],
                  failing_step: int) -> Optional[Subtask]:
    """The segment whose step range holds ``failing_step``, or None."""
    for subtask in segments:
        start, end = subtask.step_range
        if start <= failing_step < end:
            return subtask
    return None


def route(*, sheet_id: str, failing_step: int, scope: str,
          subtask_id: str, type_id: str, card: Optional[str],
          opened: Mapping[str, int]) -> Evidence:
    """Route one located failure. ``card`` is the active card of the
    failing segment's type (None: no active card); ``opened`` maps every
    card the trial read to its first-read step."""
    if scope not in (SCOPE_SUBTASK, SCOPE_GENERAL):
        raise CodeBugError(f"unknown diagnosis scope {scope!r}")
    opened_step = opened.get(card) if card is not None else None
    before = opened_step is not None and opened_step < failing_step
    if scope == SCOPE_GENERAL:
        category = NONE
    elif card is None:
        category = GAP
    else:
        category = CONTENT if before else TRIGGER
    return Evidence(sheet_id=sheet_id, failing_step=failing_step,
                    subtask_id=subtask_id, type_id=type_id, skill_id=card,
                    opened_before=before, opened_step=opened_step,
                    category=category)


def count_categories(evidence: Iterable[Evidence]) -> Dict[str, int]:
    """How many evidence rows fall in each category (every category
    listed, zero included)."""
    counts = {category: 0 for category in CATEGORIES}
    for item in evidence:
        counts[item.category] += 1
    return counts


def content_failed_twice(skill_id: str,
                         previous: Iterable[Evidence]) -> bool:
    """The delete rule: the previous learn phase also gave ``skill_id``
    content evidence."""
    return any(item.category == CONTENT and item.skill_id == skill_id
               for item in previous)


@dataclass(frozen=True)
class CardPlan:
    """One unit of the card step: a type with gap evidence (``skill_id``
    None: a new card), or one active card with its content and trigger
    evidence (``delete``: the delete rule fires instead of a content
    rewrite)."""

    type_id: str
    skill_id: Optional[str]
    gap: List[Evidence] = field(default_factory=list)
    content: List[Evidence] = field(default_factory=list)
    trigger: List[Evidence] = field(default_factory=list)
    delete: bool = False


def plan_cards(evidence: Sequence[Evidence],
               previous: Sequence[Evidence]) -> List[CardPlan]:
    """One plan per type with gap evidence and per card with trigger or
    content evidence, in type-id order; each plan's evidence keeps the
    input order (sheet-id order)."""
    by_type: Dict[str, Dict[str, Any]] = {}
    for item in evidence:
        if item.category == NONE:
            continue
        slot = by_type.setdefault(item.type_id, {
            "skill_id": item.skill_id, GAP: [], CONTENT: [], TRIGGER: []})
        if slot["skill_id"] != item.skill_id:
            raise CodeBugError(
                f"type {item.type_id!r} is routed to two cards in one "
                f"phase ({slot['skill_id']!r} and {item.skill_id!r})")
        slot[item.category].append(item)
    plans: List[CardPlan] = []
    for type_id in sorted(by_type):
        slot = by_type[type_id]
        skill_id = slot["skill_id"]
        plans.append(CardPlan(
            type_id=type_id, skill_id=skill_id, gap=slot[GAP],
            content=slot[CONTENT], trigger=slot[TRIGGER],
            delete=(skill_id is not None and bool(slot[CONTENT])
                    and content_failed_twice(skill_id, previous))))
    return plans


def trigger_facts(evidence: Iterable[Evidence],
                  type_names: Mapping[str, str]) -> str:
    """The playbook writer's facts after the first learn phase: per card
    with trigger evidence, how many failed trials did not open it before
    their failing step. Empty when there is none."""
    counts: Dict[str, int] = {}
    types: Dict[str, str] = {}
    for item in evidence:
        if item.category != TRIGGER:
            continue
        counts[item.skill_id] = counts.get(item.skill_id, 0) + 1
        types[item.skill_id] = item.type_id
    if not counts:
        return ""
    lines = ["Cards the agent had in its catalog but did not open before "
             "the step where a failed trial went wrong:"]
    for skill_id in sorted(counts):
        type_id = types[skill_id]
        lines.append(
            f"- the card for \"{type_names.get(type_id, type_id)}\": "
            f"{counts[skill_id]} failed trial(s)")
    return "\n".join(lines)


def discarded_types(skills: Iterable[Any]) -> Set[str]:
    """The types whose card is discarded (no new card is written for
    them: the type's one card id is taken)."""
    return {skill.subtask_type for skill in skills
            if skill.status == "discarded" and skill.subtask_type}
