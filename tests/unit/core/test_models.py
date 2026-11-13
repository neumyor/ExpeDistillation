"""Unit tests for the persisted data-model objects."""
import pytest

from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_trajectory_id,
    make_type_id,
)
from expedistill.core.models import (
    EVOLVING_TEXT_KINDS,
    SKILL_SCHEMA_VERSION,
    SKILL_STATUSES,
    SUBTASK_SCHEMA_VERSION,
    SUBTASK_TYPE_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION,
    EvolvingText,
    Skill,
    Step,
    Subtask,
    SubtaskType,
    Trajectory,
)

TRAJ_ID = make_trajectory_id("p01:baseline", "tb:task-1", "strong", 0)
SEG_ID = make_subtask_id(TRAJ_ID, 0)
TYPE_ID = make_type_id(1)


def make_trajectory(**overrides):
    base = dict(
        trajectory_id=TRAJ_ID,
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
        subtask_id=SEG_ID,
        phase_label="p02:learn",
        task_id="tb:task-1",
        trajectory_id=TRAJ_ID,
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


def make_type(**overrides):
    base = dict(
        type_id=TYPE_ID,
        name="heat the held object",
        entry_state="The target object is in hand and must be hot.",
        phase_label="p02:learn",
    )
    base.update(overrides)
    return SubtaskType(**base)


def make_skill(**overrides):
    base = dict(
        skill_id=make_skill_id(TYPE_ID),
        subtask_type=TYPE_ID,
        source_subtasks=[SEG_ID],
        summary="Inspect the workspace before editing.",
        when_to_use="Before editing a file you have not listed.",
        steps=["Run ls.", "Edit only a file you saw."],
        not_applicable_when="the file was just listed.",
    )
    base.update(overrides)
    return Skill(**base)


def _versioned_cases():
    """(class, a valid record, its current schema) for every object that
    reads only its current schema."""
    return [
        (Trajectory, make_trajectory().to_dict(), TRAJECTORY_SCHEMA_VERSION),
        (Subtask, make_subtask().to_dict(), SUBTASK_SCHEMA_VERSION),
        (SubtaskType, make_type().to_dict(), SUBTASK_TYPE_SCHEMA_VERSION),
        (Skill, make_skill().to_dict(), SKILL_SCHEMA_VERSION),
    ]


@pytest.mark.parametrize("case", range(4))
@pytest.mark.parametrize("delta", [-1, 1, None, True])
def test_only_the_current_schema_is_read(case, delta):
    cls, row, current = _versioned_cases()[case]
    if delta is None:
        del row["schema_version"]
    elif delta is True:
        row["schema_version"] = True
    else:
        row["schema_version"] = current + delta
    with pytest.raises(ValueError, match=f"schema v{current} only"):
        cls.from_dict(row)


@pytest.mark.parametrize("case", range(4))
def test_round_trip_and_unknown_keys(case):
    cls, row, current = _versioned_cases()[case]
    restored = cls.from_dict({**row, "future_field": "ignored"})
    assert restored.to_dict() == row
    assert row["schema_version"] == current


def test_schema_versions_of_this_design():
    # The versions the design spec (4.1, 2.6) names for this round.
    assert (TRAJECTORY_SCHEMA_VERSION, SUBTASK_SCHEMA_VERSION,
            SUBTASK_TYPE_SCHEMA_VERSION, SKILL_SCHEMA_VERSION) == (3, 2, 1, 5)


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

    def test_invalid_correctness_raises(self):
        with pytest.raises(ValueError, match="correctness"):
            make_trajectory(correctness="ok")

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

    def test_phase_label_is_required(self):
        payload = make_subtask().to_dict()
        del payload["phase_label"]
        with pytest.raises(TypeError, match="phase_label"):
            Subtask.from_dict(payload)
        with pytest.raises(ValueError, match="phase_label"):
            make_subtask(phase_label=" ")


class TestSubtaskType:
    def test_round_trip(self):
        row = make_type()
        assert SubtaskType.from_dict(row.to_dict()) == row
        assert set(row.to_dict()) == {
            "type_id", "name", "entry_state", "phase_label",
            "schema_version"}

    @pytest.mark.parametrize(
        "name", ["type_id", "name", "entry_state", "phase_label"])
    def test_every_field_is_required_and_non_empty(self, name):
        payload = make_type().to_dict()
        del payload[name]
        with pytest.raises(TypeError, match=name):
            SubtaskType.from_dict(payload)
        with pytest.raises(ValueError, match=name):
            make_type(**{name: ""})


class TestSkill:
    @pytest.mark.parametrize("status", SKILL_STATUSES)
    def test_round_trip(self, status):
        skill = make_skill(
            status=status,
            version=3,
            discard_reason=("content_failed_twice"
                            if status == "discarded" else None),
            rewrite_history=[
                {"side": "trigger",
                 "old": {"summary": "old one-liner",
                         "when_to_use": "old trigger"},
                 "reason": "spelled out the entry state"}],
        )
        assert Skill.from_dict(skill.to_dict()) == skill

    def test_statuses(self):
        assert SKILL_STATUSES == ("active", "discarded")

    def test_defaults(self):
        skill = make_skill()
        assert skill.version == 1
        assert skill.status == "active"
        assert skill.verbatim_text == ""
        assert skill.discard_reason is None
        assert skill.rewrite_history == []

    def test_field_set(self):
        assert list(make_skill().to_dict()) == [
            "skill_id", "subtask_type", "source_subtasks", "summary",
            "when_to_use", "steps", "not_applicable_when", "verbatim_text",
            "version", "status", "discard_reason", "rewrite_history",
            "schema_version"]

    @pytest.mark.parametrize("name", ["subtask_type", "source_subtasks"])
    def test_lineage_fields_are_required(self, name):
        payload = make_skill().to_dict()
        del payload[name]
        with pytest.raises(TypeError, match=name):
            Skill.from_dict(payload)

    def test_a_written_card_names_its_type(self):
        with pytest.raises(ValueError, match="subtask_type"):
            make_skill(subtask_type="")
        with pytest.raises(ValueError, match="subtask_type"):
            make_skill(subtask_type=None)

    @pytest.mark.parametrize("status", ["pending", "accepted", "merged"])
    def test_old_statuses_are_refused(self, status):
        with pytest.raises(ValueError, match="status"):
            make_skill(status=status)

    @pytest.mark.parametrize(
        "field", ["summary", "when_to_use", "not_applicable_when"])
    def test_empty_text_field_raises(self, field):
        with pytest.raises(ValueError, match=field):
            make_skill(**{field: "  "})

    def test_empty_steps_raise(self):
        with pytest.raises(ValueError, match="steps"):
            make_skill(steps=[])

    def test_steps_must_be_strings(self):
        with pytest.raises(ValueError, match="steps"):
            make_skill(steps=["ok", 3])

    def test_an_imported_skill_has_no_type_and_may_leave_fields_empty(self):
        skill = make_skill(skill_id="import/skillopt/doc", subtask_type="",
                           source_subtasks=[], summary="An imported skill.",
                           when_to_use="", steps=[], not_applicable_when="",
                           verbatim_text="# Imported\nbody")
        assert Skill.from_dict(skill.to_dict()) == skill


class TestEvolvingText:
    @pytest.mark.parametrize("kind", EVOLVING_TEXT_KINDS)
    def test_round_trip(self, kind):
        text = EvolvingText(kind=kind, text="Check twice.", version=2,
                            phase_label="p02:learn",
                            source_sheet_ids=["sheet/a"])
        assert EvolvingText.from_dict(text.to_dict()) == text

    def test_defaults_and_empty_text(self):
        text = EvolvingText(kind="profile", text="", version=1,
                            phase_label="p02:learn")
        assert text.source_sheet_ids == []

    def test_invalid_kind_raises(self):
        with pytest.raises(ValueError, match="kind"):
            EvolvingText(kind="manual", text="", version=1,
                         phase_label="p02:learn")

    @pytest.mark.parametrize("version", [0, -1, True, "1"])
    def test_invalid_version_raises(self, version):
        with pytest.raises(ValueError, match="version"):
            EvolvingText(kind="playbook", text="", version=version,
                         phase_label="p02:learn")


class TestStep:
    def test_round_trip(self):
        step = Step(action="cd /tmp", observation="")
        assert Step.from_dict(step.to_dict()) == step
