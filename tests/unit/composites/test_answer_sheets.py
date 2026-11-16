"""Trial-level answer-sheet builders, assembly, grading and export tests.

Builders read the canonical ``Trajectory`` / external reward facts; assembly joins executions with coverage (defaulting a missing
coverage row to present and emitting a gap sheet for a non-present row
without an execution); grading fills only present trials; the writers
produce the derived JSON + Markdown artifacts and the coverage JSONL.
"""

import pytest

from expedistill.composites import answer_sheets
from expedistill.composites.unit_ids import make_gap_sheet_id, make_unit_id
from expedistill.core.answer_sheet import CoverageRecord
from expedistill.core.ids import make_trajectory_id
from expedistill.core.models import Step, Trajectory
from expedistill.infra.store import object_filename, read_json, read_jsonl

TASK = "tb:task-1"
PHASE = "p03:practice"


def _trajectory(**overrides):
    base = dict(
        trajectory_id=make_trajectory_id(PHASE, TASK, "weak", 0),
        task_id=TASK,
        benchmark="tb",
        model_role="weak",
        model_name="model-a",
        attempt_index=0,
        skills_injected=[{"skill_id": "s1", "version": 2}],
        steps=[Step(action="ls", observation="a.txt")],
        correctness=True,
        meta={"reward": 1.0, "verifier_ran": True},
    )
    base.update(overrides)
    return Trajectory(**base)


def test_the_sheet_filename_fits_a_long_alfworld_game_path():
    task = ("alfworld:json_2.1.1/valid_unseen/"
            "look_at_obj_in_light-AlarmClock-None-DeskLamp-308/"
            "trial_T20190908_222917_366542/game.tw-pddl")
    trajectory = _trajectory(
        trajectory_id=make_trajectory_id("p01:baseline", task, "weak", 0),
        task_id=task,
        benchmark="alfworld")
    unit = make_unit_id(phase_label="p01:baseline", benchmark="alfworld",
                        task_ref=task, role="weak", skills_injected=None)
    record = answer_sheets.execution_from_trajectory(
        trajectory, unit_id=unit, phase_label="p01:baseline")

    # Filename = percent-encoded sheet id + ".json" + the atomic-write temp
    # suffix; the filesystem limit is 255 bytes per name.
    name = object_filename(record.sheet_id)
    assert len(name.encode()) + len(".json") + 10 <= 255


def test_execution_from_trajectory_carries_the_arm_and_the_aux_verdict():
    # Golden snapshots the sheet JSON; the arm and the aux verdict are the
    # fields it cannot reach.
    trajectory = _trajectory()
    record = answer_sheets.execution_from_trajectory(
        trajectory, unit_id="unit-1", phase_label="p03:live",
        arm="with_bank", aux_success=True)
    assert record.sheet_id == f"unit-1::att{trajectory.attempt_index:02d}"
    assert (record.arm, record.aux_success) == ("with_bank", True)
    assert (record.kind, record.source) == ("trajectory", "local")
    assert record.artifacts == {"trajectory_id": trajectory.trajectory_id}


def test_execution_from_trajectory_carries_the_folder_manifest():
    # A folder unit's sheet keeps the manifest the executor saw (files,
    # catalog and playbook) beside the trajectory id.
    folder = {"files": [{"skill_id": "type/001/skill", "version": 1,
                         "filename": "type-001-skill/SKILL.md"}],
              "catalog_text": "catalog", "playbook_text": "Look first."}
    trajectory = _trajectory()
    record = answer_sheets.execution_from_trajectory(
        trajectory, unit_id="unit-1", phase_label=PHASE, folder=folder)
    assert record.artifacts == {"trajectory_id": trajectory.trajectory_id,
                                "skill_folder": folder}
    assert record.artifacts["skill_folder"] is not folder


def test_execution_from_trajectory_adds_no_scorer_verdict_of_its_own():
    record = answer_sheets.execution_from_trajectory(
        _trajectory(correctness=None), unit_id="unit-1",
        phase_label="p03:live")
    assert record.raw_correctness is None
    assert record.aux_success is None
    assert record.arm is None


@pytest.mark.parametrize("reward, correct, sample_index",
                         [(1.0, True, 2), (0.0, False, 0)])
def test_execution_from_external_reads_the_reward_as_correctness(
        reward, correct, sample_index):
    record = answer_sheets.execution_from_external(
        task_ref=TASK, unit_id="unit-3", phase_label="p06:test",
        reward=reward, sample_index=sample_index, source="campaign.csv",
        sha256="deadbeef", role="weak")

    assert record.sheet_id == f"external:unit-3:{sample_index}"
    assert (record.unit_id, record.phase_label, record.task_ref) \
        == ("unit-3", "p06:test", TASK)
    assert (record.role, record.arm, record.model_role, record.model_name) \
        == ("weak", "control", "weak", "external_campaign")
    assert record.sample_index == sample_index
    assert record.rewards == [reward]
    assert record.raw_correctness is correct
    assert record.metrics == {"reward": reward}
    assert record.artifacts == {"source": "campaign.csv",
                                "sha256": "deadbeef"}
    assert (record.source, record.kind) == ("external", "external")


def _sheets_for_assembly():
    trajectory_record = answer_sheets.execution_from_trajectory(
        _trajectory(), unit_id="unit-1", phase_label="p03:live")
    external_record = answer_sheets.execution_from_external(
        task_ref="tb:task-2", unit_id="unit-2", phase_label="p06:test",
        reward=1.0, sample_index=0, source="campaign.csv",
        sha256="deadbeef", role="weak")
    coverage = [
        CoverageRecord(unit_id="unit-1", attempt_index=0, present=True,
                       status="present"),
        CoverageRecord(unit_id="unit-gap", attempt_index=0, present=False,
                       status="terminal", cause="leak"),
        # A present row with no execution contributes no sheet.
        CoverageRecord(unit_id="unit-skip", attempt_index=0, present=True,
                       status="present"),
    ]
    return [trajectory_record, external_record], coverage, coverage[1]


def test_assemble_joins_executions_and_creates_gap_sheets():
    executions, coverage, gap_record = _sheets_for_assembly()
    sheets = answer_sheets.assemble_answer_sheets(
        executions, coverage, phase_label="p03:live")

    by_id = {sheet.sheet_id: sheet for sheet in sheets}
    assert set(by_id) == {
        executions[0].sheet_id,
        executions[1].sheet_id,
        make_gap_sheet_id("unit-gap", 0),
    }
    # Sorting is by sheet id, so a resume writes the same order.
    assert [sheet.sheet_id for sheet in sheets] == sorted(by_id)

    # An execution with an explicit coverage row keeps that row.
    assert by_id[executions[0].sheet_id].coverage.status == "present"
    assert by_id[executions[0].sheet_id].execution is executions[0]

    # An execution without a row defaults to present.
    defaulted = by_id[executions[1].sheet_id].coverage
    assert defaulted.unit_id == "unit-2"
    assert defaulted.attempt_index == 0
    assert defaulted.present is True
    assert defaulted.status == "present"
    assert defaulted.cause == ""

    # A non-present row without an execution becomes a gap sheet.
    gap = by_id[make_gap_sheet_id("unit-gap", 0)]
    assert gap.execution is None
    assert gap.coverage is gap_record
    assert gap.phase_label == "p03:live"

    # The present-but-unexecuted row produced nothing.
    assert "unit-skip" not in {sheet.coverage.unit_id for sheet in sheets}


def test_grade_answer_sheets_fills_only_present_trials():
    executions, coverage, _ = _sheets_for_assembly()
    executions.append(answer_sheets.execution_from_trajectory(
        _trajectory(
            trajectory_id=make_trajectory_id(PHASE, TASK, "weak", 1),
            attempt_index=1, correctness=None),
        unit_id="unit-4", phase_label="p03:live"))
    sheets = answer_sheets.assemble_answer_sheets(
        executions, coverage, phase_label="p03:live")

    graded = answer_sheets.grade_answer_sheets(sheets, "scorer")
    by_id = {sheet.sheet_id: sheet for sheet in graded}
    assert by_id[executions[0].sheet_id].grading.verdict == "success"
    assert by_id[executions[0].sheet_id].grading.rule_name == "scorer"
    assert by_id[executions[2].sheet_id].grading.verdict == "invalid"
    gap = by_id[make_gap_sheet_id("unit-gap", 0)]
    assert gap.grading is None
    # In-place: the caller's sheet objects carry the grading too.
    assert by_id[executions[0].sheet_id] in sheets
    assert all(sheet.grading is not None for sheet in sheets
               if sheet.execution is not None)


def test_grade_answer_sheets_routes_an_external_reward_through_its_rule():
    record = answer_sheets.execution_from_external(
        task_ref=TASK, unit_id="unit-3", phase_label="p06:test",
        reward=0.0, sample_index=0, source="campaign.csv",
        sha256="deadbeef", role="weak")
    sheets = answer_sheets.assemble_answer_sheets(
        [record], [], phase_label="p06:test")
    graded = answer_sheets.grade_answer_sheets(sheets, "external_reward")
    assert graded[0].grading.verdict == "failure"
    assert graded[0].grading.rule_name == "external_reward"
    assert "campaign reward" in graded[0].grading.basis


def test_write_answer_sheets_writes_json_and_markdown(tmp_path):
    executions, coverage, _ = _sheets_for_assembly()
    sheets = answer_sheets.grade_answer_sheets(
        answer_sheets.assemble_answer_sheets(
            executions, coverage, phase_label="p03:live"),
        "scorer")
    directory = tmp_path / "derived" / "answer_sheets"

    answer_sheets.write_answer_sheets(sheets, directory)

    for sheet in sheets:
        stem = object_filename(sheet.sheet_id)[:-len(".json")]
        json_path = directory / f"{stem}.json"
        md_path = directory / f"{stem}.md"
        assert json_path.exists()
        assert md_path.exists()
        payload = read_json(json_path)
        assert payload["sheet_id"] == sheet.sheet_id
        assert payload["phase_label"] == sheet.phase_label
        assert payload["coverage"] == sheet.coverage.to_dict()
        if sheet.execution is None:
            assert payload["execution"] is None
        else:
            assert payload["execution"]["kind"] == sheet.execution.kind
            expected_steps = (None if sheet.execution.steps is None
                              else [step.to_dict()
                                    for step in sheet.execution.steps])
            assert payload["execution"]["steps"] == expected_steps
        md_text = md_path.read_text(encoding="utf-8")
        assert md_text.startswith(f"# Answer sheet `{sheet.sheet_id}`")


def test_write_coverage_writes_one_json_object_per_line(tmp_path):
    records = [
        CoverageRecord(unit_id="unit-1", attempt_index=0, present=True,
                       status="present"),
        CoverageRecord(unit_id="unit-1", attempt_index=1, present=False,
                       status="missing", cause="accepted_hole"),
    ]
    path = tmp_path / "derived" / "coverage.jsonl"

    answer_sheets.write_coverage(records, path)

    assert read_jsonl(path) == [record.to_dict() for record in records]
    assert path.read_text(encoding="utf-8").count("\n") == 2
