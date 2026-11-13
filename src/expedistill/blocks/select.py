"""D6 segment selection (layer 4).

A thin block: it filters the given Subtasks with a segment_select rule,
preserving input order (the caller's ordering is meaningful -- it mirrors
the trajectories' segment order). The caller resolves the configured rule
name in ``blocks/rules.py``; which segments are worth a skill is the rule's
business, never a code edit here.
"""

from __future__ import annotations

from typing import Callable, List

from expedistill.core.models import Subtask

__all__ = ["select_subtasks"]


def select_subtasks(
    subtasks: List[Subtask],
    rule: Callable[[Subtask], bool],
) -> List[Subtask]:
    """Return the Subtasks ``rule`` keeps, in input order."""
    return [subtask for subtask in subtasks if rule(subtask)]
