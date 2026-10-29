"""The provenance ledger: one write door, exact pointers, a boundary check."""

import pytest

from expedistill.composites import answer_sheets, phase_report, provenance, trace
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
    UnitSpec,
)
from expedistill.core.models import Skill, Step
from expedistill.core.trace import Provenance, TraceRef
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.infra.store import Store, read_jsonl
from tests.helpers.skills import make_skill

UNIT = make_unit_id(phase_label="p01:baseline", benchmark="tb",
                    task_ref="tb:t1", role="strong", skills_injected=None)


def _sheet(attempt, n_steps=6):
    trajectory = f"traj/tb:t1/strong/{attempt:02d}"
    sheet_id = f"{UNIT}::{trajectory}"
    execution = ExecutionRecord(
        sheet_id=sheet_id, unit_id=UNIT, phase_label="p01:baseline",
        task_ref="tb:t1", role="strong", arm=None, model_role="strong",
        model_name="m", sample_index=attempt,
        steps=[Step(action=f"a{i}", observation="o") for i in range(n_steps)],
        artifacts={"trajectory_id": trajectory})
    return AnswerSheet(
        sheet_id=sheet_id, phase_label="p01:baseline",
        coverage=CoverageRecord(unit_id=UNIT, attempt_index=attempt,
                                present=True, status="present"),
        execution=execution,
        grading=GradingRecord(verdict="failure", basis="b", rule_name="r"))


@pytest.fixture()
def env(tmp_path):
    sheets = [_sheet(0), _sheet(1)]
    dest = tmp_path / "phases" / "p01_baseline"
    answer_sheets.write_answer_sheets(
        sheets, dest / "answer_sheets", render_markdown=False)
    answer_sheets.write_coverage(
        [s.coverage for s in sheets], dest / "coverage.jsonl")
    phase_report.write_unit_specs(
        [UnitSpec(unit_id=UNIT, phase_label="p01:baseline", task_ref="tb:t1",
                  role="strong", arm=None, expected_attempts=2,
                  injected_skills=None)], dest)
    return tmp_path, Store(tmp_path), sheets


def _skill():
    return make_skill("traj/tb:t1/strong/00/seg01", "read the error")


def _prov(skill, sheet, step=2, cause="add", phase="p02:learn_add"):
    ref = trace.trace_at(sheet, step)
    return Provenance(skill.skill_id, skill.version, phase, cause, ref,
                      [ref], "note")


def test_record_version_saves_skill_and_row_together(env):
    run_dir, store, sheets = env
    skill = _skill()
    phase_dir = run_dir / "phases" / "p02_learn_add"

    provenance.record_version(store, phase_dir, run_dir, skill,
                              _prov(skill, sheets[0]))

    assert store.exists("skills", skill.skill_id)
    rows = read_jsonl(phase_dir / provenance.PROVENANCE_NAME)
    assert [row["skill_id"] for row in rows] == [skill.skill_id]
    found = provenance.find_provenance(run_dir, skill.skill_id, 1)
    assert found.scene.sheet_id == sheets[0].sheet_id


def test_record_version_is_replay_safe(env):
    run_dir, store, sheets = env
    skill = _skill()
    phase_dir = run_dir / "phases" / "p02_learn_add"
    row = _prov(skill, sheets[0])

    provenance.record_version(store, phase_dir, run_dir, skill, row)
    provenance.record_version(store, phase_dir, run_dir, skill, row)

    assert len(read_jsonl(phase_dir / provenance.PROVENANCE_NAME)) == 1


def test_record_version_refuses_a_pointer_that_does_not_resolve(env):
    run_dir, store, sheets = env
    skill = _skill()
    phase_dir = run_dir / "phases" / "p02_learn_add"
    bad = Provenance(skill.skill_id, 1, "p02:learn_add", "add",
                     TraceRef("p01:baseline", "ghost", 0))

    with pytest.raises(ModelOutputError):
        provenance.record_version(store, phase_dir, run_dir, skill, bad)

    assert not store.exists("skills", skill.skill_id)
    assert not (phase_dir / provenance.PROVENANCE_NAME).exists()


def test_record_version_refuses_a_row_for_another_version(env):
    run_dir, store, sheets = env
    skill = _skill()
    other = Provenance(skill.skill_id, 2, "p04:learn", "revise",
                       trace.trace_at(sheets[0], 0))
    with pytest.raises(CodeBugError, match="does not describe"):
        provenance.record_version(
            store, run_dir / "phases" / "p04_learn", run_dir, skill, other)


def test_check_is_clean_for_an_anchor_cut_scene(env):
    run_dir, store, sheets = env
    skill = _skill()
    provenance.record_version(
        store, run_dir / "phases" / "p02_learn_add", run_dir, skill,
        _prov(skill, sheets[0]))

    assert provenance.check_provenance(run_dir, [skill]) == []


def test_check_catches_a_scene_cut_from_the_wrong_attempt(env):
    """The 0/37 incident: anchor is attempt 00, scene came from attempt 01."""
    run_dir, store, sheets = env
    skill = _skill()
    provenance.record_version(
        store, run_dir / "phases" / "p02_learn_add", run_dir, skill,
        _prov(skill, sheets[1]))

    problems = provenance.check_provenance(run_dir, [skill])

    assert len(problems) == 1
    assert "anchor trajectory" in problems[0]


def test_check_reports_a_version_with_no_row_and_skips_merges(env):
    run_dir, store, sheets = env
    skill = _skill()
    merged = Skill(skill_id="bank/merge/0000", content="merged",
                   subtask_id=skill.subtask_id,
                   source_subtasks=[skill.subtask_id],
                   merged_from=[skill.skill_id])

    problems = provenance.check_provenance(run_dir, [skill, merged])

    assert problems == [f"{skill.skill_id} v1: no provenance row"]


def test_find_provenance_prefers_the_newest_phase(env):
    run_dir, store, sheets = env
    skill = _skill()
    old = _prov(skill, sheets[0], step=1)
    provenance.record_version(
        store, run_dir / "phases" / "p02_learn_add", run_dir, skill, old)
    newer = Provenance(skill.skill_id, 1, "p04:learn", "revise",
                       trace.trace_at(sheets[0], 4), [], "later")
    provenance.record_version(
        store, run_dir / "phases" / "p04_learn", run_dir, skill, newer)

    assert provenance.find_provenance(run_dir, skill.skill_id, 1).scene.step == 4
