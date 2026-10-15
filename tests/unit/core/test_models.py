"""Unit tests for the five data-model objects."""
import pytest

from expedistill.core.ids import make_exam_id, make_skill_id
from expedistill.core.models import (
    ATTEMPT_PURPOSES,
    EXAM_SCHEMA_VERSION,
    FAILURE_REFERENCE_MARKERS,
    PROCEDURE_MARKERS,
    SKILL_SCHEMA_VERSION,
    SKILL_STATUSES,
    TRAJECTORY_STATUSES,
    Attempt,
    Exam,
    Skill,
    Step,
    Subtask,
    Trajectory,
    merged_source_subtasks,
    migrate_skill_record,
    migrate_skill_rows,
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
        source_subtasks=["traj/tb:task-1/strong/00/seg00"],
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

    def test_source_subtasks_is_required(self):
        # No default on purpose: every construction site names its sources
        # (AGENTS "修改数据对象" rule 3), so a forgotten one fails here
        # instead of silently storing an empty list.
        with pytest.raises(TypeError, match="source_subtasks"):
            Skill(skill_id="traj/tb:t/strong/00/seg00/skill",
                  content="c", subtask_id="traj/tb:t/strong/00/seg00")

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            make_skill(status="promoted")

    def test_from_dict_ignores_unknown_keys(self):
        payload = {**make_skill().to_dict(), "future_field": None}
        assert Skill.from_dict(payload) == make_skill()

    def test_v2_record_missing_source_subtasks_raises(self):
        payload = make_skill().to_dict()
        del payload["source_subtasks"]
        with pytest.raises(TypeError, match="source_subtasks"):
            Skill.from_dict(payload)


class TestExam:
    def test_round_trip(self):
        exam = Exam(
            exam_id="traj/tb:task-1/strong/00/seg00/exam",
            question_text="The output of ls is empty; what next?",
            reference_answer="Check the working directory with pwd.",
            subtask_id="traj/tb:task-1/strong/00/seg00",
            skill_id="traj/tb:task-1/strong/00/seg00/skill",
        )
        assert Exam.from_dict(exam.to_dict()) == exam

    def test_skill_id_is_required(self):
        with pytest.raises(TypeError, match="skill_id"):
            Exam(exam_id="traj/tb:t/strong/00/seg00/exam",
                 question_text="q", reference_answer="a",
                 subtask_id="traj/tb:t/strong/00/seg00")

    def test_v2_record_missing_skill_id_raises(self):
        payload = Exam(
            exam_id="traj/tb:task-1/strong/00/seg00/exam",
            question_text="q?", reference_answer="a.",
            subtask_id="traj/tb:task-1/strong/00/seg00",
            skill_id="traj/tb:task-1/strong/00/seg00/skill").to_dict()
        del payload["skill_id"]
        with pytest.raises(TypeError, match="skill_id"):
            Exam.from_dict(payload)


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


# ---------------------------------------------------------------------------
# v1 -> v2 migration (schema bump 2026-10-15)
# ---------------------------------------------------------------------------

_SUB_A = "traj/tb:t1/strong/00/seg00"
_SUB_B = "traj/tb:t1/strong/00/seg01"
_SUB_C = "traj/tb:t1/weak/00/seg00"


def _v1_segment_row(subtask_id, content="c"):
    """A stored v1 skill row exactly as write_skill wrote it before v2."""
    return {
        "skill_id": make_skill_id(subtask_id),
        "content": content,
        "subtask_id": subtask_id,
        "version": 1,
        "status": "accepted",
        "discard_reason": None,
        "group": None,
        "rewrite_history": [],
        "merged_from": [],
        "schema_version": 1,
    }


def _v1_merge_row(merge_id, merged_from, subtask_id=_SUB_A):
    """A stored v1 merge-product row (K5 wrote merged_from, never a
    source_subtasks)."""
    return {**_v1_segment_row(subtask_id), "skill_id": merge_id,
            "merged_from": list(merged_from)}


class TestV1ToV2Migration:
    def test_from_dict_derives_segment_source_subtasks(self):
        row = _v1_segment_row(_SUB_A, content="check first")
        skill = Skill.from_dict(row)
        assert skill.source_subtasks == [_SUB_A]
        assert skill.schema_version == SKILL_SCHEMA_VERSION
        # Everything else survives the migration untouched.
        assert skill.skill_id == make_skill_id(_SUB_A)
        assert skill.content == "check first"
        assert skill.status == "accepted"

    def test_from_dict_rejects_v1_merge_product_loudly(self):
        row = _v1_merge_row("bank/merge/0000",
                            [make_skill_id(_SUB_A), make_skill_id(_SUB_B)])
        # Both pointers: the migration function to use instead, and the
        # maintenance-branch rule (a v1 run never resumes on this code).
        with pytest.raises(ValueError, match="migrate_skill_record"):
            Skill.from_dict(row)
        with pytest.raises(ValueError, match="maintenance branch"):
            Skill.from_dict(row)

    def test_from_dict_rejects_v1_unknown_id_shape_loudly(self):
        row = _v1_segment_row(_SUB_A)
        row["skill_id"] = "bank/somewhere/else"
        with pytest.raises(ValueError, match="cannot be derived"):
            Skill.from_dict(row)

    def test_from_dict_derives_exam_skill_id(self):
        row = {
            "exam_id": make_exam_id(_SUB_A),
            "question_text": "q?",
            "reference_answer": "a.",
            "subtask_id": _SUB_A,
            "schema_version": 1,
        }
        exam = Exam.from_dict(row)
        assert exam.skill_id == make_skill_id(_SUB_A)
        assert exam.schema_version == EXAM_SCHEMA_VERSION

    def test_from_dict_rejects_v1_exam_of_unknown_shape(self):
        row = {
            "exam_id": "somewhere/else",
            "question_text": "q?",
            "reference_answer": "a.",
            "subtask_id": _SUB_A,
            "schema_version": 1,
        }
        with pytest.raises(ValueError, match="cannot be derived"):
            Exam.from_dict(row)

    def test_migrate_record_derives_a_segment_row(self):
        row = _v1_segment_row(_SUB_A)
        migrated = migrate_skill_record(row, {})
        assert Skill.from_dict(migrated).source_subtasks == [_SUB_A]

    def test_migrate_record_unions_a_flat_merge(self):
        rows = {
            make_skill_id(_SUB_A): _v1_segment_row(_SUB_A),
            make_skill_id(_SUB_B): _v1_segment_row(_SUB_B),
        }
        merge = _v1_merge_row("bank/merge/0000",
                             [make_skill_id(_SUB_A), make_skill_id(_SUB_B)])
        migrated = migrate_skill_record(merge, rows)
        assert migrated["source_subtasks"] == [_SUB_A, _SUB_B]
        assert migrated["schema_version"] == SKILL_SCHEMA_VERSION
        assert Skill.from_dict(migrated).skill_id == "bank/merge/0000"

    def test_migrate_record_recurses_into_a_nested_merge(self):
        rows = {
            make_skill_id(_SUB_A): _v1_segment_row(_SUB_A),
            make_skill_id(_SUB_B): _v1_segment_row(_SUB_B),
            "bank/merge/0000": _v1_merge_row(
                "bank/merge/0000",
                [make_skill_id(_SUB_A), make_skill_id(_SUB_B)]),
            make_skill_id(_SUB_C): _v1_segment_row(_SUB_C),
            # The nested case: one source is itself a merge product.
            "bank/merge/0001": _v1_merge_row(
                "bank/merge/0001", ["bank/merge/0000",
                                    make_skill_id(_SUB_C)]),
        }
        migrated = migrate_skill_record(rows["bank/merge/0001"], rows)
        assert migrated["source_subtasks"] == [_SUB_A, _SUB_B, _SUB_C]

    def test_migrate_record_passes_v2_rows_through(self):
        row = make_skill().to_dict()
        assert migrate_skill_record(row, {}) is row

    def test_migrate_rows_migrates_a_whole_run_in_order(self):
        # The batch seam the full-store readers use: segment rows, a
        # flat merge, a nested merge and a v2 row mixed, in store order.
        rows = [
            _v1_segment_row(_SUB_A),
            _v1_segment_row(_SUB_B),
            _v1_merge_row("bank/merge/0000",
                          [make_skill_id(_SUB_A), make_skill_id(_SUB_B)]),
            _v1_merge_row("bank/merge/0001",
                          ["bank/merge/0000", make_skill_id(_SUB_C)]),
            _v1_segment_row(_SUB_C),
            make_skill().to_dict(),  # a v2 row passes through
        ]
        migrated = migrate_skill_rows(rows)
        assert [row["skill_id"] for row in migrated] == [
            make_skill_id(_SUB_A), make_skill_id(_SUB_B),
            "bank/merge/0000", "bank/merge/0001",
            make_skill_id(_SUB_C), rows[5]["skill_id"]]
        assert all(row["schema_version"] == SKILL_SCHEMA_VERSION
                   for row in migrated)
        # The nested merge resolves its transitive sources through the
        # map the wrapper built; the v2 row is the SAME object.
        assert migrated[3]["source_subtasks"] == [_SUB_A, _SUB_B, _SUB_C]
        assert migrated[5] is rows[5]

    def test_migrate_rows_propagates_the_sibling_error(self):
        rows = [_v1_merge_row("bank/merge/0000", [make_skill_id(_SUB_A)])]
        with pytest.raises(ValueError, match="not among the sibling records"):
            migrate_skill_rows(rows)

    def test_migrate_record_rejects_unknown_v1_shape(self):
        row = _v1_segment_row(_SUB_A)
        row["skill_id"] = "bank/somewhere/else"
        with pytest.raises(ValueError, match="cannot be derived"):
            migrate_skill_record(row, {})

    def test_migrate_record_rejects_missing_sibling(self):
        merge = _v1_merge_row("bank/merge/0000", [make_skill_id(_SUB_A)])
        with pytest.raises(ValueError, match="not among the sibling records"):
            migrate_skill_record(merge, {})

    def test_migrate_record_rejects_merged_from_cycle(self):
        rows = {
            "bank/merge/0000": _v1_merge_row("bank/merge/0000",
                                              ["bank/merge/0001"]),
            "bank/merge/0001": _v1_merge_row("bank/merge/0001",
                                              ["bank/merge/0000"]),
        }
        with pytest.raises(ValueError, match="twice in one merged_from"):
            migrate_skill_record(rows["bank/merge/0000"], rows)

    def test_merged_source_subtasks_unions_first_seen_order(self):
        assert merged_source_subtasks([["b", "a"], ["a", "c"]]) \
            == ["b", "a", "c"]
        assert merged_source_subtasks([]) == []
