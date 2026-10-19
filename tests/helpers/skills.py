"""Building a Skill record in tests.

The boilerplate every suite repeats -- the skill id derived from its
subtask (core.ids.make_skill_id) and the subtask as its own lineage --
lives here once; each suite keeps its own thin, scenario-shaped builder
on top (by task name, by index, saved or not)."""

from expedistill.core import ids
from expedistill.core.models import Skill

__all__ = ["make_skill"]


def make_skill(subtask_id: str, content: str = "skill text",
               **fields) -> Skill:
    """A Skill anchored on ``subtask_id``; any other Skill field (status,
    version, summary, merged_from, ...) passes through ``fields``."""
    fields.setdefault("source_subtasks", [subtask_id])
    return Skill(skill_id=ids.make_skill_id(subtask_id), content=content,
                 subtask_id=subtask_id, **fields)
