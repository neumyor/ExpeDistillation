"""Regression: objects carry foreign-key ids, so the persisted ID must be
resolved through the store kind, not by first-match scanning. A Subtask
carries its trajectory_id; without the kind it would be stored under the
foreign key and every segment of one trajectory would overwrite the
others."""
import pytest

from expedistill.core import (
    Skill,
    Subtask,
    Trajectory,
    make_skill_id,
    make_subtask_id,
    make_trajectory_id,
    make_type_id,
)
from expedistill.infra import Store
from expedistill.infra.store import object_id
from tests.helpers.skills import text_fields

TRAJ = make_trajectory_id("p01:baseline", "tb:x", "weak", 0)


def _subtask(index):
    return Subtask(subtask_id=make_subtask_id(TRAJ, index),
                   phase_label="p02:learn", task_id="tb:x",
                   trajectory_id=TRAJ, source="weak",
                   trajectory_success=False, segment_index=index,
                   step_range=[0, 3], label="l", description="d",
                   success=False, context_summary="")


def test_two_segments_of_one_trajectory_do_not_collide(tmp_path):
    s = Store(tmp_path)
    s.save(_subtask(1), "subtasks")
    s.save(_subtask(2), "subtasks")
    assert s.ids("subtasks") == [make_subtask_id(TRAJ, 1),
                                 make_subtask_id(TRAJ, 2)]


def test_skill_subtask_and_trajectory_ids_resolve_via_kind(tmp_path):
    s = Store(tmp_path)
    sub = _subtask(1)
    type_id = make_type_id(1)
    sk = Skill(skill_id=make_skill_id(type_id), subtask_type=type_id,
               source_subtasks=[sub.subtask_id], **text_fields("c"))
    tr = Trajectory(trajectory_id=TRAJ, task_id="tb:x", benchmark="tb",
                    model_role="weak", model_name="w", attempt_index=0,
                    skills_injected=None, steps=[], correctness=False,
                    meta={})
    for obj, kind in ((sub, "subtasks"), (sk, "skills"),
                      (tr, "trajectories")):
        s.save(obj, kind)
    assert s.ids("subtasks") == [sub.subtask_id]
    assert s.ids("skills") == [make_skill_id(type_id)]
    assert s.ids("trajectories") == [TRAJ]


@pytest.mark.parametrize("kind", ["nope", "exams", "attempts"])
def test_object_id_with_unknown_kind_raises(kind):
    with pytest.raises(ValueError):
        object_id(_subtask(1), kind)
