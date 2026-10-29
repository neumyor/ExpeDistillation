"""Unit tests for the five data-model objects."""
import pytest

from expedistill.core.ids import make_exam_id, make_skill_id
from expedistill.core.trace import TraceRef
from expedistill.core.models import (
    ATTEMPT_PURPOSES,
    EXAM_SCHEMA_VERSION,
    SKILL_MARKERS,
    SKILL_SCHEMA_VERSION,
    SKILL_STATUSES,
    TRAJECTORY_SCHEMA_VERSION,
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
        correctness=True,
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
        attempt_id="traj/tb:task-1/strong/00/seg00/skill/v01/exam/att00",
        exam_id="traj/tb:task-1/strong/00/seg00/skill/v01/exam",
        purpose="bare",
        weak_answer="run ls",
    )
    base.update(overrides)
    return Attempt(**base)


def make_exam(**overrides):
    skill_id = "traj/tb:task-1/strong/00/seg00/skill"
    base = dict(
        exam_id=make_exam_id(skill_id, 2),
        skill_id=skill_id,
        skill_version=2,
        scene_text="The workspace is empty after ls.",
        expected_behavior="Find where the config lives before editing it.",
        reference_answer="Run pwd, then list the project root.",
        scene=TraceRef("p02:learn", "s1", 3),
    )
    base.update(overrides)
    return Exam(**base)


class TestTrajectory:
    @pytest.mark.parametrize("correctness", [True, False, None])
    def test_round_trip(self, correctness):
        traj = make_trajectory(correctness=correctness)
        restored = Trajectory.from_dict(traj.to_dict())
        assert restored == traj
        assert restored.correctness is correctness
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

    def test_invalid_correctness_raises(self):
        with pytest.raises(ValueError, match="correctness"):
            make_trajectory(correctness="ok")

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError, match="model_role"):
            make_trajectory(model_role="medium")

    # -- the v1 -> v2 migration (R2b) --------------------------------------

    @staticmethod
    def _v1_row(status, **extra):
        # A v1 record: status + the derived success flag, schema_version 1.
        correctness = {"success": True, "failure": False}.get(status)
        v2 = make_trajectory(correctness=correctness).to_dict()
        row = {key: value for key, value in v2.items()
               if key not in ("correctness", "schema_version")}
        row["status"] = status
        row["success"] = status == "success"
        row["schema_version"] = 1
        row.update(extra)
        return row

    def test_v1_success_migrates_to_true(self):
        traj = Trajectory.from_dict(self._v1_row("success", success=False))
        assert traj.correctness is True
        assert traj.schema_version == TRAJECTORY_SCHEMA_VERSION

    def test_v1_failure_migrates_to_false(self):
        traj = Trajectory.from_dict(self._v1_row("failure"))
        assert traj.correctness is False
        assert traj.schema_version == TRAJECTORY_SCHEMA_VERSION

    def test_v1_infra_error_refuses_loudly(self):
        # K1 never stored an infra_error trajectory, so a v1 row carrying
        # one is a corrupt/pre-contract run: never a silent default.
        with pytest.raises(ValueError, match="infra_error"):
            Trajectory.from_dict(self._v1_row("infra_error"))

    def test_v1_row_without_a_version_is_treated_as_v1(self):
        row = self._v1_row("success")
        del row["schema_version"]
        assert Trajectory.from_dict(row).correctness is True

    def test_v1_row_without_a_status_refuses_loudly(self):
        row = self._v1_row("success")
        del row["status"]
        with pytest.raises(ValueError, match="status"):
            Trajectory.from_dict(row)


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
            summary="check the config before editing it",
            rewrite_history=[
                {"content": "old text", "summary": "old one-liner",
                 "error_category": "missing step",
                 "reason": "spelled out the intermediate cd"}],
            merged_from=["traj/tb:t/strong/00/seg00/skill"],
        )
        assert Skill.from_dict(skill.to_dict()) == skill

    def test_defaults(self):
        skill = make_skill()
        assert skill.version == 1
        assert skill.status == "pending"
        assert skill.summary == ""
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
        exam = make_exam()
        restored = Exam.from_dict(exam.to_dict())
        assert restored == exam
        assert restored.schema_version == EXAM_SCHEMA_VERSION == 4
        assert restored.scene == TraceRef("p02:learn", "s1", 3)
        assert exam.to_dict()["scene"] == {
            "phase_label": "p02:learn", "sheet_id": "s1", "step": 3}

    def test_identity_fields_are_required(self):
        # Every identity/scene field has no default: a forgotten one fails
        # at construction instead of silently storing a broken exam.
        for name in ("exam_id", "skill_id", "skill_version", "scene_text",
                     "expected_behavior", "reference_answer", "scene"):
            payload = make_exam().to_dict()
            del payload[name]
            with pytest.raises(TypeError, match=name):
                Exam.from_dict(payload)

    def test_from_dict_ignores_unknown_keys(self):
        payload = {**make_exam().to_dict(), "future_field": "ignored"}
        assert Exam.from_dict(payload) == make_exam()

    @pytest.mark.parametrize("version", [1, 2, 3, 5])
    def test_only_the_current_schema_is_read(self, version):
        payload = {**make_exam().to_dict(), "schema_version": version}
        with pytest.raises(ValueError, match=f"schema-v{version}"):
            Exam.from_dict(payload)

    def test_record_without_a_version_is_refused_loudly(self):
        payload = make_exam().to_dict()
        del payload["schema_version"]
        with pytest.raises(ValueError, match="schema-vNone"):
            Exam.from_dict(payload)

    def test_a_malformed_scene_is_refused(self):
        payload = make_exam().to_dict()
        payload["scene"] = {"phase_label": "p02:learn", "sheet_id": "s1"}
        with pytest.raises(ValueError, match="missing"):
            Exam.from_dict(payload)


class TestExamId:
    def test_exam_id_is_per_skill_version(self):
        skill_id = make_skill_id("traj/tb:task-1/strong/00/seg00")
        assert make_exam_id(skill_id, 1) == f"{skill_id}/v01/exam"
        assert make_exam_id(skill_id, 12) == f"{skill_id}/v12/exam"

    def test_negative_version_raises(self):
        with pytest.raises(ValueError, match="version"):
            make_exam_id(make_skill_id("traj/tb:t/strong/00/seg00"), -1)


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

    def test_steps_round_trip(self):
        att = make_attempt(steps=[
            Step(action="cat skills/a.md", observation="a: do x"),
            Step(action="plan", observation="1. do x"),
        ])
        payload = att.to_dict()
        assert payload["steps"] == [
            {"action": "cat skills/a.md", "observation": "a: do x"},
            {"action": "plan", "observation": "1. do x"},
        ]
        restored = Attempt.from_dict(payload)
        assert restored == att
        assert isinstance(restored.steps[0], Step)

    def test_steps_accept_plain_dicts(self):
        att = make_attempt(steps=[{"action": "ls", "observation": ""}])
        assert att.steps == [Step(action="ls", observation="")]

    def test_old_record_without_steps_defaults_to_empty(self):
        # steps was added without a schema bump; a v1 record written
        # before the field must still load, with an empty list.
        payload = make_attempt().to_dict()
        del payload["steps"]
        restored = Attempt.from_dict(payload)
        assert restored.steps == []
        assert restored.schema_version == 1


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

    def test_skill_markers_pinned(self):
        # The 2026-10-19 ruling: one three-section structure for every skill.
        assert SKILL_MARKERS == (
            "[When to use]", "[Steps]", "[Do not use when]")


# ---------------------------------------------------------------------------
# v1 -> v2 migration (schema bump 2026-10-15; the chain now lands on v3)
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
            make_skill().to_dict(),  # a current-schema row passes through
        ]
        migrated = migrate_skill_rows(rows)
        assert [row["skill_id"] for row in migrated] == [
            make_skill_id(_SUB_A), make_skill_id(_SUB_B),
            "bank/merge/0000", "bank/merge/0001",
            make_skill_id(_SUB_C), rows[5]["skill_id"]]
        assert all(row["schema_version"] == SKILL_SCHEMA_VERSION
                   for row in migrated)
        # The nested merge resolves its transitive sources through the
        # map the wrapper built; the current-schema row is the SAME object.
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


# ---------------------------------------------------------------------------
# v2 -> v3 migration (schema bump 2026-10-17, skill-folder M1)
# ---------------------------------------------------------------------------

def _v2_segment_row(subtask_id, content="c", summary=None):
    """A stored v2 skill row: exactly the fields a pre-v3 run wrote
    (no summary), optionally with a stray summary key."""
    row = {
        "skill_id": make_skill_id(subtask_id),
        "content": content,
        "subtask_id": subtask_id,
        "source_subtasks": [subtask_id],
        "version": 1,
        "status": "accepted",
        "discard_reason": None,
        "group": None,
        "rewrite_history": [],
        "merged_from": [],
        "schema_version": 2,
    }
    if summary is not None:
        row["summary"] = summary
    return row


class TestV2ToV3Migration:
    def test_from_dict_clears_the_summary(self):
        # The M1 alignment ruling: a v2 skill never carried a summary,
        # and the honest migration is the EMPTY string -- M2's folder
        # rendering falls back to the [When to use] section for those.
        # Explicit (the v2 row goes through a migration function), never
        # a silent dataclass default.
        skill = Skill.from_dict(_v2_segment_row(_SUB_A, content="check"))
        assert skill.summary == ""
        assert skill.schema_version == SKILL_SCHEMA_VERSION
        assert skill.source_subtasks == [_SUB_A]
        assert skill.content == "check"
        assert skill.status == "accepted"

    def test_from_dict_keeps_a_summary_key_a_v2_row_already_carried(self):
        # Dropping data a record actually holds would be worse than
        # honoring it: a v2 row with a summary key is not a clean v2
        # row, but its bytes are real.
        skill = Skill.from_dict(
            _v2_segment_row(_SUB_A, summary="a stray one-liner"))
        assert skill.summary == "a stray one-liner"
        assert skill.schema_version == SKILL_SCHEMA_VERSION

    def test_migrate_record_clears_the_summary(self):
        row = _v2_segment_row(_SUB_A, content="check")
        migrated = migrate_skill_record(row, {})
        assert migrated["summary"] == ""
        assert migrated["schema_version"] == SKILL_SCHEMA_VERSION
        assert Skill.from_dict(migrated).summary == ""

    def test_migrate_rows_migrates_a_v2_run(self):
        rows = [_v2_segment_row(_SUB_A), _v2_segment_row(_SUB_B)]
        migrated = migrate_skill_rows(rows)
        assert all(row["summary"] == "" for row in migrated)
        assert all(row["schema_version"] == SKILL_SCHEMA_VERSION
                   for row in migrated)

    def test_current_schema_rows_pass_through_unchanged(self):
        row = make_skill(summary="the current shape").to_dict()
        assert migrate_skill_record(row, {}) is row
