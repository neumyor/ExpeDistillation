"""Unit tests for the five data-model objects."""
import pytest

from expedistill.core.models import (
    ATTEMPT_PURPOSES,
    FAILURE_REFERENCE_MARKERS,
    PROCEDURE_MARKERS,
    SKILL_STATUSES,
    TRAJECTORY_STATUSES,
    Attempt,
    Exam,
    Skill,
    Step,
    Subtask,
    Trajectory,
)


def make_trajectory(**overrides):
    base = dict(
        trajectory_id="traj/tb:task-1/strong/00",
        task_id="tb:task-1",
        benchmark="tb",
        model_role="strong",
        model_name="model-s",
        attempt_index=0,
        steps=[Step(action="ls", observation="a b")],
        status="success",
        meta={"n": 1},
    )
    base.update(overrides)
    return Trajectory(**base)


def make_subtask(**overrides):
    base = dict(
        subtask_id="traj/tb:task-1/strong/00/seg00",
        task_id="tb:task-1",
        trajectory_id="traj/tb:task-1/strong/00",
        source="strong",
        trajectory_success=True,
        segment_index=0,
        step_range=[0, 3],
        label="setup",
        description="prepare the workspace",
        success=True,
        context_summary="",
    )
    base.update(overrides)
    return Subtask(**base)


def make_skill(**overrides):
    base = dict(
        skill_id="traj/tb:task-1/strong/00/seg00/skill",
        content="Always run ls before editing.",
        subtask_id="traj/tb:task-1/strong/00/seg00",
    )
    base.update(overrides)
    return Skill(**base)


def make_attempt(**overrides):
    base = dict(
        attempt_id="traj/tb:task-1/strong/00/seg00/exam/att00",
        exam_id="traj/tb:task-1/strong/00/seg00/exam",
        purpose="bare",
        weak_answer="run ls",
    )
    base.update(overrides)
    return Attempt(**base)


class TestTrajectory:
    def test_success_is_derived_from_status(self):
        assert make_trajectory(status="success").success is True
        assert make_trajectory(status="failure").success is False
        assert make_trajectory(status="infra_error").success is False

    def test_derived_success_overrides_stored_value(self):
        # A stored record whose success disagrees with its status is repaired
        # on load, so the invariant can never be violated on a live object.
        traj = Trajectory.from_dict(
            {**make_trajectory(status="success").to_dict(), "success": False})
        assert traj.success is True

    @pytest.mark.parametrize("status", TRAJECTORY_STATUSES)
    def test_round_trip(self, status):
        traj = make_trajectory(status=status)
        restored = Trajectory.from_dict(traj.to_dict())
        assert restored == traj
        assert isinstance(restored.steps[0], Step)

    def test_steps_accept_plain_dicts(self):
        traj = make_trajectory(steps=[{"action": "ls", "observation": "a"}])
        assert traj.steps == [Step(action="ls", observation="a")]

    def test_bare_run_has_no_injected_skills(self):
        assert make_trajectory().skills_injected is None

    def test_to_dict_is_strict_and_json_safe(self):
        payload = make_trajectory().to_dict()
        assert isinstance(payload["steps"], list)
        assert isinstance(payload["steps"][0], dict)
        assert payload["schema_version"] == Trajectory.from_dict(payload).schema_version

    def test_from_dict_ignores_unknown_keys(self):
        payload = make_trajectory().to_dict()
        payload["future_field"] = "ignored"
        assert Trajectory.from_dict(payload) == make_trajectory()

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            make_trajectory(status="ok")

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError, match="model_role"):
            make_trajectory(model_role="medium")


class TestSubtask:
    def test_round_trip(self):
        sub = make_subtask()
        restored = Subtask.from_dict(sub.to_dict())
        assert restored == sub
        assert restored.step_range == (0, 3)

    def test_step_range_normalised_to_pair(self):
        assert make_subtask(step_range=[2, 5]).step_range == (2, 5)
        with pytest.raises(ValueError, match="step_range"):
            make_subtask(step_range=[1])
        with pytest.raises(ValueError, match="step_range"):
            make_subtask(step_range=[1, 2, 3])

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError, match="source"):
            make_subtask(source="oracle")

    def test_from_dict_ignores_unknown_keys(self):
        payload = {**make_subtask().to_dict(), "future_field": 1}
        assert Subtask.from_dict(payload) == make_subtask()


class TestSkill:
    @pytest.mark.parametrize("status", SKILL_STATUSES)
    def test_round_trip(self, status):
        skill = make_skill(
            status=status,
            version=3,
            discard_reason="retry_exhausted" if status == "discarded" else None,
            group="filesystem",
            rewrite_history=[
                {"content": "old text", "error_category": "missing step",
                 "reason": "spelled out the intermediate cd"}],
            merged_from=["traj/tb:t/strong/00/seg00/skill"],
        )
        assert Skill.from_dict(skill.to_dict()) == skill

    def test_defaults(self):
        skill = make_skill()
        assert skill.version == 1
        assert skill.status == "pending"
        assert skill.discard_reason is None
        assert skill.group is None
        assert skill.rewrite_history == []
        assert skill.merged_from == []

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            make_skill(status="promoted")

    def test_from_dict_ignores_unknown_keys(self):
        payload = {**make_skill().to_dict(), "future_field": None}
        assert Skill.from_dict(payload) == make_skill()


class TestExam:
    def test_round_trip(self):
        exam = Exam(
            exam_id="traj/tb:task-1/strong/00/seg00/exam",
            question_text="The output of ls is empty; what next?",
            reference_answer="Check the working directory with pwd.",
            subtask_id="traj/tb:task-1/strong/00/seg00",
        )
        assert Exam.from_dict(exam.to_dict()) == exam

    def test_no_skill_reference_field(self):
        # The exam must not carry a skill pointer: exams are written without
        # seeing the skill, and any such field would invite leaking it.
        payload = Exam(
            exam_id="e", question_text="q", reference_answer="a",
            subtask_id="s").to_dict()
        assert "skill" not in payload


class TestAttempt:
    @pytest.mark.parametrize("purpose", ATTEMPT_PURPOSES)
    def test_round_trip(self, purpose):
        att = make_attempt(
            purpose=purpose,
            skill_id="traj/tb:task-1/strong/00/seg00/skill",
            skill_version=2,
            weak_answer="do the thing",
            judge_verdict=False,
            judge_reason="missed the cd step",
            retry_round=1,
        )
        assert Attempt.from_dict(att.to_dict()) == att

    def test_bare_attempt_has_no_skill(self):
        att = make_attempt()
        assert att.skill_id is None
        assert att.skill_version is None
        assert att.judge_verdict is None

    def test_invalid_purpose_raises(self):
        with pytest.raises(ValueError, match="purpose"):
            make_attempt(purpose="probe")

    def test_from_dict_ignores_unknown_keys(self):
        payload = {**make_attempt().to_dict(), "future_field": []}
        assert Attempt.from_dict(payload) == make_attempt()


class TestStep:
    def test_round_trip(self):
        step = Step(action="cd /tmp", observation="")
        assert Step.from_dict(step.to_dict()) == step


class TestSkillSectionMarkers:
    """Protocol v2 pins these constants BIT FOR BIT: they are the contract
    surface other agents import -- the REWRITE/MERGE/SIMILARITY prompt
    literals, the G2 renderer in blocks/author.py, and the retrieval
    weighting in blocks/retrieve.py all key off the exact strings. A marker
    that drifts silently disarms every downstream marker check."""

    def test_procedure_markers_pinned(self):
        assert PROCEDURE_MARKERS == (
            "[When to use]", "[Steps]", "[Verify]", "[Do not use when]")

    def test_failure_reference_markers_pinned(self):
        assert FAILURE_REFERENCE_MARKERS == (
            "[Trigger]", "[Why it fails]", "[Do instead]", "[Do not use when]")

    def test_both_kinds_carry_four_sections(self):
        assert len(PROCEDURE_MARKERS) == 4
        assert len(FAILURE_REFERENCE_MARKERS) == 4

    def test_not_applicable_marker_is_shared_the_rest_disjoint(self):
        # "[Do not use when]" is the one section both skill kinds share; the
        # other three identify the kind (blocks/author.py skill_markers
        # decides kind membership from the full marker set).
        assert PROCEDURE_MARKERS[-1] == "[Do not use when]"
        assert FAILURE_REFERENCE_MARKERS[-1] == "[Do not use when]"
        assert (set(PROCEDURE_MARKERS) & set(FAILURE_REFERENCE_MARKERS)
                == {"[Do not use when]"})
