"""Regression: objects carry foreign-key ids, so the persisted ID must be
resolved through the store kind, not by first-match scanning. Exam has both
exam_id and skill_id; without the kind it would be stored under the foreign
key and two exams would silently overwrite each other."""
from expedistill.core import Attempt, Exam, Skill, Subtask, Trajectory
from expedistill.infra import Store


def _exam(i):
    skill_id = f"t/x/seg0{i}/skill"
    return Exam(exam_id=f"{skill_id}/v01/exam", skill_id=skill_id,
                skill_version=1, scene_text="q", expected_behavior="b",
                reference_answer="a",
                source_ref={"phase_label": "p02:learn", "scene_step": 0})


def test_exam_stored_by_exam_id(tmp_path):
    s = Store(tmp_path)
    s.save(_exam(1), "exams")
    assert s.exists("exams", "t/x/seg01/skill/v01/exam")
    assert s.ids("exams") == ["t/x/seg01/skill/v01/exam"]
    assert s.load("exams", "t/x/seg01/skill/v01/exam", Exam).scene_text == "q"


def test_two_exams_of_one_subtask_do_not_collide(tmp_path):
    s = Store(tmp_path)
    s.save(_exam(1), "exams")
    s.save(_exam(2), "exams")
    assert s.ids("exams") == ["t/x/seg01/skill/v01/exam",
                              "t/x/seg02/skill/v01/exam"]


def test_skill_and_subtask_ids_resolve_via_kind(tmp_path):
    s = Store(tmp_path)
    sub = Subtask(subtask_id="t/x/seg01", task_id="t/x",
                  trajectory_id="traj/t/x/weak/00", source="weak",
                  trajectory_success=False, segment_index=1,
                  step_range=[0, 3], label="l", description="d",
                  success=False, context_summary="")
    sk = Skill(skill_id="t/x/seg01/skill", subtask_id="t/x/seg01",
               source_subtasks=["t/x/seg01"], content="c", version=1,
               status="pending")
    at = Attempt(attempt_id="t/x/seg01/exam/att00",
                 exam_id="t/x/seg01/exam", skill_id=None,
                 skill_version=None, purpose="bare", weak_answer="a",
                 judge_verdict=None, judge_reason="", retry_round=0)
    tr = Trajectory(trajectory_id="traj/t/x/weak/00", task_id="t/x",
                    benchmark="tb", model_role="weak", model_name="w",
                    attempt_index=0, skills_injected=None, steps=[],
                    correctness=False, meta={})
    for obj, kind in ((sub, "subtasks"), (sk, "skills"),
                      (at, "attempts"), (tr, "trajectories")):
        s.save(obj, kind)
    assert s.ids("subtasks") == ["t/x/seg01"]
    assert s.ids("skills") == ["t/x/seg01/skill"]
    assert s.ids("attempts") == ["t/x/seg01/exam/att00"]
    assert s.ids("trajectories") == ["traj/t/x/weak/00"]


def test_object_id_with_unknown_kind_raises():
    import pytest
    with pytest.raises(ValueError):
        object_id_helper(_exam(1), "nope")


def object_id_helper(obj, kind):
    from expedistill.infra.store import object_id
    return object_id(obj, kind)
