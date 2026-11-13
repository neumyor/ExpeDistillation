"""The provenance ledger: one write door, exact pointers, a boundary check."""

import pytest

from expedistill.composites import provenance, trace
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
)
from expedistill.core.ids import make_trajectory_id
from expedistill.core.models import Step
from expedistill.core.trace import Provenance, TraceRef
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.infra.store import (
    Store, object_filename, read_jsonl, write_json)
from tests.helpers.skills import make_skill

LABEL = "p01:baseline"
UNIT = make_unit_id(phase_label=LABEL, benchmark="tb", task_ref="tb:t1",
                    role="strong", skills_injected=None)


def _sheet(attempt, n_steps=6):
    trajectory = make_trajectory_id(LABEL, "tb:t1", "strong", attempt)
    sheet_id = f"{UNIT}::{trajectory}"
    execution = ExecutionRecord(
        sheet_id=sheet_id, unit_id=UNIT, phase_label=LABEL,
        task_ref="tb:t1", role="strong", arm=None, model_role="strong",
        model_name="m", sample_index=attempt,
        steps=[Step(action=f"a{i}", observation="o") for i in range(n_steps)],
        artifacts={"trajectory_id": trajectory})
    return AnswerSheet(
        sheet_id=sheet_id, phase_label=LABEL,
        coverage=CoverageRecord(unit_id=UNIT, attempt_index=attempt,
                                present=True, status="present"),
        execution=execution,
        grading=GradingRecord(verdict="failure", basis="b", rule_name="r"))


@pytest.fixture()
def env(tmp_path):
    sheets = [_sheet(0), _sheet(1)]
    dest = tmp_path / "phases" / "p01_baseline" / "answer_sheets"
    for sheet in sheets:
        write_json(dest / object_filename(sheet.sheet_id), sheet.to_dict())
    return tmp_path, Store(tmp_path), sheets


def _skill():
    return make_skill("type/001", "read the error")


def _prov(skill, *sheets, step=2, cause="add", phase="p02:learn"):
    return Provenance(skill.skill_id, skill.version, phase, cause,
                      [trace.trace_at(sheet, step) for sheet in sheets],
                      "note")


def test_record_version_saves_skill_and_row_together(env):
    run_dir, store, sheets = env
    skill = _skill()
    phase_dir = run_dir / "phases" / "p02_learn"

    provenance.record_version(store, phase_dir, run_dir, skill,
                              _prov(skill, sheets[0], sheets[1]))

    assert store.exists("skills", skill.skill_id)
    rows = read_jsonl(phase_dir / provenance.PROVENANCE_NAME)
    assert [row["skill_id"] for row in rows] == [skill.skill_id]
    found = provenance.find_provenance(run_dir, skill.skill_id, 1)
    assert [ref.sheet_id for ref in found.sources] == [
        sheets[0].sheet_id, sheets[1].sheet_id]


def test_record_version_is_replay_safe(env):
    run_dir, store, sheets = env
    skill = _skill()
    phase_dir = run_dir / "phases" / "p02_learn"
    row = _prov(skill, sheets[0])

    provenance.record_version(store, phase_dir, run_dir, skill, row)
    provenance.record_version(store, phase_dir, run_dir, skill, row)

    assert len(read_jsonl(phase_dir / provenance.PROVENANCE_NAME)) == 1


def test_record_version_refuses_a_pointer_that_does_not_resolve(env):
    run_dir, store, sheets = env
    skill = _skill()
    phase_dir = run_dir / "phases" / "p02_learn"
    bad = Provenance(skill.skill_id, 1, "p02:learn", "add",
                     [TraceRef(LABEL, "ghost", 0)])

    with pytest.raises(ModelOutputError):
        provenance.record_version(store, phase_dir, run_dir, skill, bad)

    assert not store.exists("skills", skill.skill_id)
    assert not (phase_dir / provenance.PROVENANCE_NAME).exists()


def test_record_version_refuses_a_row_without_sources(env):
    run_dir, store, _sheets = env
    skill = _skill()
    with pytest.raises(CodeBugError, match="no source"):
        provenance.record_version(
            store, run_dir / "phases" / "p02_learn", run_dir, skill,
            Provenance(skill.skill_id, 1, "p02:learn", "add", []))


def test_record_version_refuses_a_row_for_another_version(env):
    run_dir, store, sheets = env
    skill = _skill()
    other = Provenance(skill.skill_id, 2, "p04:learn", "revise_content",
                       [trace.trace_at(sheets[0], 0)])
    with pytest.raises(CodeBugError, match="does not describe"):
        provenance.record_version(
            store, run_dir / "phases" / "p04_learn", run_dir, skill, other)


def test_check_is_clean_for_a_recorded_version(env):
    run_dir, store, sheets = env
    skill = _skill()
    provenance.record_version(
        store, run_dir / "phases" / "p02_learn", run_dir, skill,
        _prov(skill, sheets[0]))

    assert provenance.check_provenance(run_dir, [skill]) == []


def test_check_reports_a_version_with_no_row(env):
    run_dir, _store, _sheets = env
    skill = _skill()

    assert provenance.check_provenance(run_dir, [skill]) == [
        f"{skill.skill_id} v1: no provenance row"]


def test_check_reports_a_source_that_no_longer_resolves(env):
    run_dir, store, sheets = env
    skill = _skill()
    provenance.record_version(
        store, run_dir / "phases" / "p02_learn", run_dir, skill,
        _prov(skill, sheets[0]))
    (run_dir / "phases" / "p01_baseline" / "answer_sheets"
     / object_filename(sheets[0].sheet_id)).unlink()

    problems = provenance.check_provenance(run_dir, [skill])

    assert len(problems) == 1 and "no answer sheet" in problems[0]


def test_find_provenance_prefers_the_newest_phase(env):
    run_dir, store, sheets = env
    skill = _skill()
    provenance.record_version(
        store, run_dir / "phases" / "p02_learn", run_dir, skill,
        _prov(skill, sheets[0], step=1))
    newer = Provenance(skill.skill_id, 1, "p04:learn", "revise_content",
                       [trace.trace_at(sheets[0], 4)], "later")
    provenance.record_version(
        store, run_dir / "phases" / "p04_learn", run_dir, skill, newer)

    found = provenance.find_provenance(run_dir, skill.skill_id, 1)
    assert found.sources[0].step == 4
