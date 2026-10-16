"""The evidence package: the evidence family's output shape (R6).

The frozen interface shapes live in core/contracts.py, which pins the
four cross-module hand-offs (execution unit list, run record, acceptance
report row, task list row). The evidence package is the FIFTH hand-off
-- the boundaries doc (execute_learn_module_boundaries_20261016.md
section 2.5) freezes its FIELD SET in prose (target, facts, segments,
old_skill, source_experiences), and this module is its definition site
because core is closed for this wave (contracts.py is frozen).

The package carries FACTS ONLY: failure cases, measurement numbers and
attribution basis (repair plan R6). It never carries an action -- the
decision module decides actions into the task list, and the author
module decides the per-skill handling of a package from these facts.
``ensure_fact_only`` is the structural guard both drivers and tests
lean on: no ``action`` key anywhere in the serialized package, and no
fact whose kind is an action word. It deliberately does not scan the
fact TEXT for action words: a problem statement like "the skill tells
the model to delete a file" is a fact, and word-matching it would be a
false positive, not a guard.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from expedistill.core.models import Skill

__all__ = [
    "ACTION_FACT_KINDS",
    "EvidencePackage",
    "ensure_fact_only",
]

#: The action words that must never appear as a fact kind or a dict key
#: inside a package (the closed action vocabulary of the loop: the task
#: list's keep/revise/delete/add, plus the legacy G10's keep/revise/
#: delete). Keyed, structural checks only -- see the module docstring.
ACTION_FACT_KINDS = ("keep", "revise", "delete", "add")


@dataclass
class EvidencePackage:
    """One target's evidence, as facts (boundaries section 2.5).

    ``target`` is what the evidence is about: a skill id on a revision
    package, a task id on an add package. ``facts`` is a list of
    ``{"kind": str, "text": str}`` entries (measurement numbers,
    failure cases, attribution basis); ``segments`` points at the
    underlying artifacts (trajectory ids with step ranges, or answer
    ids); ``old_skill`` is present on a revision; ``source_experiences``
    is present on an add (the segments to learn from).
    """

    #: What this package is evidence about (a skill id or a task id).
    target: str

    #: The facts, ``{"kind": ..., "text": ...}`` entries, no actions.
    facts: List[Dict[str, str]] = field(default_factory=list)

    #: Artifact pointers: trajectory ids with step ranges, or answer ids.
    segments: List[Dict[str, Any]] = field(default_factory=list)

    #: The current skill, on a revision package; None on an add.
    old_skill: Optional[Skill] = None

    #: The experiences to learn from, on an add package; each entry
    #: carries the subtask, its rendered steps view and its write mode.
    source_experiences: List[Dict[str, Any]] = field(default_factory=list)

    def fact(self, kind: str) -> Optional[str]:
        """The first fact text of ``kind``, or None when absent."""
        for entry in self.facts:
            if entry["kind"] == kind:
                return entry["text"]
        return None

    def has_fact(self, kind: str) -> bool:
        return any(entry["kind"] == kind for entry in self.facts)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)


def ensure_fact_only(package: EvidencePackage) -> None:
    """Assert the package's structure carries no action (R6's guard).

    Raises ValueError naming the offender when any dict inside the
    package has an ``action`` key, or when a fact's kind is an action
    word. Returns silently on a fact-only package.
    """
    for obj in _walk(asdict(package)):
        if "action" in obj:
            raise ValueError(
                f"evidence package of {package.target!r} carries an "
                f"action key: {obj!r}; the evidence module reports "
                "facts only (boundaries section 2.5)")
    for entry in package.facts:
        if entry["kind"] in ACTION_FACT_KINDS:
            raise ValueError(
                f"evidence package of {package.target!r} has a fact of "
                f"kind {entry['kind']!r}: facts never name an action")
