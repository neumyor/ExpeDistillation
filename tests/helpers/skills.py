"""Building a Skill record in tests.

The boilerplate every suite repeats -- the skill id derived from its
subtask type (core.ids.make_skill_id) and the four text fields -- lives
here once; each suite keeps its own thin, scenario-shaped builder on top
(by type serial, saved or not)."""

from expedistill.core import ids
from expedistill.core.models import Skill

__all__ = ["SKILL_FIELDS", "make_skill", "text_fields"]

#: Default text fields of a test skill.
SKILL_FIELDS = {
    "summary": "Check the state before acting.",
    "steps": ["Look at the current state.", "Act on what you saw."],
    "not_applicable_when": "the state is already known.",
}


def text_fields(when_to_use: str = "skill text", **overrides) -> dict:
    """The four text fields of a test skill: ``when_to_use`` plus the
    defaults, any of which ``overrides`` replaces."""
    fields = {name: list(value) if isinstance(value, list) else value
              for name, value in SKILL_FIELDS.items()}
    fields["when_to_use"] = when_to_use
    fields.update(overrides)
    return fields


def make_skill(type_id: str, when_to_use: str = "skill text",
               **fields) -> Skill:
    """The card of subtask type ``type_id`` (``ids.make_type_id(n)``)
    whose ``when_to_use`` is the second argument; any other Skill field
    (status, version, summary, steps, source_subtasks, ...) passes through
    ``fields``."""
    fields.setdefault("source_subtasks", [])
    for name, value in text_fields(when_to_use).items():
        fields.setdefault(name, value)
    return Skill(skill_id=ids.make_skill_id(type_id), subtask_type=type_id,
                 **fields)
