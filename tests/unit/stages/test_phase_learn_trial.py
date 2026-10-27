"""phase_learn's trial_level wiring: analysis -> requirements -> bank."""

import pytest

import expedistill.benchmarks.tb  # noqa: F401
from expedistill.composites import answer_sheets as acceptance_sheets
from expedistill.composites import phase_report
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
    UnitSpec,
)
from expedistill.core.config import (
    CompareSpec,
    ExecutePhaseSpec,
    LearnPhaseSpec,
    PhaseInstance,
)
from expedistill.core.models import Step
from expedistill.infra.store import Store
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.review import trial
from expedistill.stages import phase_learn
from expedistill.stages.context import RunSettings, StageContext
from tests.helpers.plugins import ensure_plugin
from tests.helpers.skills import make_skill

TASK = "tb:t1"
SKILL = make_skill("traj/tb:t1/weak/00/seg00", "read the error first",
                   summary="read the error")


@pytest.fixture(autouse=True)
def _tb():
    ensure_plugin("tb")


class _Config:
    benchmark = "tb"
    n_attempts_weak = 1
    use_weak_profile = False
    weak_profile = ""
    rules = {"task_select": "any_weak_failed",
             "segment_select": "strong_success_weak_failed",
             "trajectory_select": "all"}

    def effective_weak_profile(self):
        return ""


def _phases():
    baseline = ExecutePhaseSpec(
        role="baseline", tasks="train", delivery={"name": "none"},
        execution={"name": "real",
                   "arms": ["executor_strong", "executor_weak"]})
    live = ExecutePhaseSpec(
        role="live", tasks="train", delivery={"name": "folder_by_task"},
        execution={"name": "real", "n_attempts": 1},
        compare=CompareSpec(against="fresh_bare", rule="strict_rate_gain"))
    learn = LearnPhaseSpec(
        review={"name": "review", "decision": "trial_level"},
        author={"name": "distill_diagnose"},
        curate={"name": "none", "token_budget": 1000})
    test = ExecutePhaseSpec(
        role="test", tasks="test", delivery={"name": "whole_bank"},
        execution={"name": "real"}, compare=CompareSpec(against="fresh_bare"))
    return {"baseline": baseline, "live": live, "learn": learn, "test": test}


def _seed_live(tmp_path):
    run_dir = tmp_path / "run"
    store = Store(run_dir)
    unit = make_unit_id(
        phase_label="p02:live", benchmark="tb", task_ref=TASK, role="weak",
        skills_injected=[{"skill_id": SKILL.skill_id, "version": 1}])
    execution = ExecutionRecord(
        sheet_id=f"{unit}::0", unit_id=unit, phase_label="p02:live",
        task_ref=TASK, role="weak", arm=None, model_role="weak",
        model_name="weak-model", sample_index=0,
        skills_injected=[{"skill_id": SKILL.skill_id, "version": 1}],
        steps=[Step(action="cat skill", observation=""),
               Step(action="wrong", observation="error")],
        raw_correctness=False,
        artifacts={"trajectory_id": "traj/tb:t1/weak/00"})
    sheet = AnswerSheet(
        sheet_id=execution.sheet_id, phase_label="p02:live",
        coverage=CoverageRecord(
            unit_id=unit, attempt_index=0, present=True,
            status="present"),
        execution=execution,
        grading=GradingRecord(
            verdict="failure", basis="tb_scorer", rule_name="tb_scorer"))
    spec = UnitSpec(
        unit_id=unit, phase_label="p02:live", task_ref=TASK, role="weak",
        arm=None, expected_attempts=1,
        injected_skills=[{"skill_id": SKILL.skill_id, "version": 1}])
    dest = run_dir / "phases" / "p02_live"
    acceptance_sheets.write_answer_sheets(
        [sheet], dest / "answer_sheets", render_markdown=False)
    acceptance_sheets.write_coverage(
        [sheet.coverage], dest / "coverage.jsonl")
    phase_report.write_unit_specs([spec], dest)
    return run_dir, store, sheet


def test_trial_review_lands_a_requirement_skill(tmp_path, monkeypatch):
    run_dir, store, sheet = _seed_live(tmp_path)
    monkeypatch.setattr(
        trial.attribution, "task_instruction_for",
        lambda benchmark, task: "do the task")
    handle = make_fake_handle("author", [
        json_script({
            "reason": "no skill covers the recovery procedure",
            "profile_note": "the model does not plan a recovery",
            "skills": [],
            "new_skill_note": "needs a recovery procedure",
        }),
        json_script({"profile_text": "the model needs explicit recovery steps"}),
        json_script({
            "skill_actions": [],
            "new_skill_requirements": [{
                "title": "recover a stuck service",
                "description": "teach the recovery procedure",
                "trigger": "When a command fails,",
                "source_sheet_ids": [sheet.sheet_id],
            }],
        }),
        json_script({
            "summary": "recover a stopped service",
            "when_to_use": "When a command fails, check the service state.",
            "steps": ["check", "restart", "verify"],
            "not_applicable_when": "the service is already running",
        }),
    ])
    config = _Config()
    config.sequence = ["baseline", "live", "learn", "test"]
    config.phases = _phases()
    ctx = StageContext(
        run_dir=run_dir, store=store, config=config, usage=None,
        handles={"author": handle, "segmenter": handle},
        workdir_root=tmp_path / "work",
        run_settings=RunSettings(workers=2))
    phase = PhaseInstance(
        index=2, name="learn", label="p03:learn",
        spec=config.phases["learn"], task_ids=())

    result = phase_learn.run(ctx, phase)

    assert result.summary["trial_findings"] == 1
    assert result.summary["new_skill_requirements"] == 1
    assert result.summary["profile_updated"] is True
    assert store.ids("skills")
    profile = run_dir / "phases" / "p03_learn" / "model_profile.json"
    assert "recovery" in profile.read_text()
