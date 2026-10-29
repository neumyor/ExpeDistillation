"""Which source sheet an exam scene is reconstructed from.

A task's attempts share one unit id, so the unit is not a unique pointer
to the trajectory a skill came from; the skill's anchor trajectory is.
"""

from types import SimpleNamespace

import pytest

from expedistill.composites import answer_sheets, phase_report
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
    UnitSpec,
)
from expedistill.core.models import Step
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.stages.phase_execute import exam as exam_phase
from tests.helpers.skills import make_skill

UNIT = make_unit_id(
    phase_label="p01:baseline", benchmark="tb", task_ref="tb:t1",
    role="strong", skills_injected=None)


def _sheet(attempt: int, steps: int, trajectory: str) -> AnswerSheet:
    sheet_id = f"{UNIT}::{trajectory}"
    execution = ExecutionRecord(
        sheet_id=sheet_id, unit_id=UNIT, phase_label="p01:baseline",
        task_ref="tb:t1", role="strong", arm=None,
        model_role="strong", model_name="strong-model",
        sample_index=attempt, steps=[Step(action=f"a{i}", observation="")
                                     for i in range(steps)],
        artifacts={"trajectory_id": trajectory}, raw_correctness=False)
    return AnswerSheet(
        sheet_id=sheet_id, phase_label="p01:baseline",
        coverage=CoverageRecord(
            unit_id=UNIT, attempt_index=attempt, present=True,
            status="present"),
        execution=execution,
        grading=GradingRecord(
            verdict="failure", basis="tb_scorer", rule_name="tb_scorer"))


@pytest.fixture()
def run_dir(tmp_path):
    sheets = [
        _sheet(0, 5, "traj/tb:t1/strong/00"),
        _sheet(1, 9, "traj/tb:t1/strong/01"),
        _sheet(2, 3, "traj/tb:t1/strong/02"),
    ]
    dest = tmp_path / "phases" / "p01_baseline"
    answer_sheets.write_answer_sheets(
        sheets, dest / "answer_sheets", render_markdown=False)
    answer_sheets.write_coverage(
        [sheet.coverage for sheet in sheets], dest / "coverage.jsonl")
    phase_report.write_unit_specs(
        [UnitSpec(unit_id=UNIT, phase_label="p01:baseline",
                  task_ref="tb:t1", role="strong", arm=None,
                  expected_attempts=3, injected_skills=None)], dest)
    return tmp_path


def test_anchor_trajectory_beats_the_shared_unit(run_dir):
    skill = make_skill("traj/tb:t1/strong/00/seg01", "read the error")
    ref = {"phase_label": "p01:baseline", "unit_id": UNIT,
           "scene_step": 1}

    sheet = exam_phase._source_sheet(
        SimpleNamespace(run_dir=run_dir), skill, ref)

    assert sheet.execution.artifacts["trajectory_id"] == \
        "traj/tb:t1/strong/00"
    assert sheet.execution.sample_index == 0


def test_exact_sheet_id_wins_over_the_anchor(run_dir):
    skill = make_skill("traj/tb:t1/strong/00/seg01", "read the error")
    ref = {"phase_label": "p01:baseline", "unit_id": UNIT,
           "sheet_id": f"{UNIT}::traj/tb:t1/strong/02", "scene_step": 1}

    sheet = exam_phase._source_sheet(
        SimpleNamespace(run_dir=run_dir), skill, ref)

    assert sheet.execution.sample_index == 2


def test_ambiguous_unit_without_anchor_raises(run_dir):
    # A requirement skill's synthetic subtask names no trajectory.
    skill = make_skill("req/abcdef/seg00", "prove the artifact works")
    ref = {"phase_label": "p01:baseline", "unit_id": UNIT,
           "scene_step": 0}

    with pytest.raises(CodeBugError):
        exam_phase._source_sheet(
            SimpleNamespace(run_dir=run_dir), skill, ref)


def test_unknown_sheet_id_raises(run_dir):
    skill = make_skill("traj/tb:t1/strong/00/seg01", "read the error")
    ref = {"phase_label": "p01:baseline", "sheet_id": "missing",
           "scene_step": 0}

    with pytest.raises(ModelOutputError):
        exam_phase._source_sheet(
            SimpleNamespace(run_dir=run_dir), skill, ref)
