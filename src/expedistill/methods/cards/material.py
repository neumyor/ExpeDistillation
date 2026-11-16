"""One subtask type's card material: its segments, ready to write from.

* strong material: the strong model's segments of the type that the named
  ``segment_select`` rule keeps (procedure material), the segments of the
  gap failures' tasks first, then by subtask id, at most
  ``max_segments``;
* weak material (``write_lessons``): the weak model's segments of the type
  that this learn phase cut and the rule keeps (lesson material), the
  failing segments of the gap evidence first, then by subtask id, at most
  ``max_segments``.

Each experience is a dict: ``subtask`` (the stored Subtask) and
``steps_view`` (its rendered steps, with the trajectory's own step
numbers). No store writes, no model calls.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Collection, Dict, List, Mapping

from expedistill.blocks.rules import get_segment_select
from expedistill.blocks.view import render_steps_view
from expedistill.core.models import Subtask, Trajectory

__all__ = ["Material", "collect_material"]


@dataclass(frozen=True)
class Material:
    """A type's material experiences, each list in prompt order."""

    strong: List[Dict[str, Any]] = field(default_factory=list)
    weak: List[Dict[str, Any]] = field(default_factory=list)


def collect_material(*, type_id: str, subtasks: Mapping[str, Subtask],
                     assignments: Mapping[str, str], rule_name: str,
                     phase_label: str, write_lessons: bool,
                     gap_tasks: Collection[str],
                     error_segments: Collection[str], max_segments: int,
                     load_trajectory: Callable[[str], Trajectory],
                     view_rules: Any = None) -> Material:
    """The material of ``type_id``; ``subtasks`` holds every stored
    segment, ``assignments`` every segment's type."""
    rule = get_segment_select(rule_name)
    of_type = [subtasks[subtask_id] for subtask_id in sorted(assignments)
               if assignments[subtask_id] == type_id
               and subtask_id in subtasks
               and rule(subtasks[subtask_id])]
    strong = sorted((sub for sub in of_type if sub.source == "strong"),
                    key=lambda sub: (sub.task_id not in gap_tasks,
                                     sub.subtask_id))[:max(0, max_segments)]
    weak = []
    if write_lessons:
        weak = sorted((sub for sub in of_type if sub.source == "weak"
                       and sub.phase_label == phase_label),
                      key=lambda sub: (sub.subtask_id not in error_segments,
                                       sub.subtask_id))[:max(0, max_segments)]

    def experience(subtask: Subtask) -> Dict[str, Any]:
        start, end = subtask.step_range
        steps = load_trajectory(subtask.trajectory_id).steps[start:end]
        return {"subtask": subtask,
                "steps_view": render_steps_view(steps, view_rules,
                                                start_index=start)}

    return Material(strong=[experience(sub) for sub in strong],
                    weak=[experience(sub) for sub in weak])
