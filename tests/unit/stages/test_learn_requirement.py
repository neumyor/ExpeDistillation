"""The requirement landing: one skill per requirement, resumable, grounded."""

from types import SimpleNamespace

import pytest

from expedistill.composites import answer_sheets
from expedistill.composites.evidence_package import EvidencePackage
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
)
from expedistill.core.models import Exam, Step, Subtask
from expedistill.core.trace import TraceRef
from expedistill.infra.reliability import CodeBugError
from expedistill.infra.store import Store, read_jsonl
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.review.contracts import NewSkillRequirement
from expedistill.stages import learn_requirement
from tests.helpers.plugins import ensure_plugin
from tests.helpers.traces import baseline_sheet, write_baseline_sheets

@pytest.fixture(autouse=True)
def _tb():
    ensure_plugin("tb")


LIVE = baseline_sheet("tb:t1", "weak", 0, n_steps=8)
EXAM_ID = "traj/tb:t1/strong/00/seg00/skill/v01/exam"


def _requirement(sheet_id, *, steps=None, sheet_ids=None):
    subtask = Subtask(
        subtask_id="traj/tb:t1/weak/00/seg00", task_id="tb:t1",
        trajectory_id="traj/tb:t1/weak/00", source="weak",
        trajectory_success=False, segment_index=0, step_range=(0, 2),
        label="recovery", description="recover a stuck service",
        success=False, context_summary="")
    return NewSkillRequirement(
        requirement_id="req/abc",
        title="recover a stuck service",
        description="teach the recovery procedure",
        trigger="When a command fails,",
        evidence=EvidencePackage(
            target="req/abc", facts=[],
            segments=[{"sheet_id": sheet_id, "step_range": [0, 2]}],
            source_experiences=[{
                "subtask": subtask, "steps_view": "steps",
                "mode": "diagnose", "segment_position": "window",
            }]),
        source_sheet_ids=sheet_ids or [sheet_id],
        source_phase_label="p01:baseline" if steps is None else "p03:exam",
        source_steps=steps or {sheet_id: 3})


def _reply():
    return json_script({
        "summary": "recover a stopped service",
        "when_to_use": "When a command fails, check the service state.",
        "steps": ["check", "restart", "verify"],
        "not_applicable_when": "the service is already running",
    })


def _ctx(tmp_path, handle):
    run_dir = tmp_path / "run"
    return SimpleNamespace(
        run_dir=run_dir, store=Store(run_dir), usage=None,
        config=SimpleNamespace(benchmark="tb"),
        handle=lambda role: handle)


def test_writes_one_skill_and_skips_a_replayed_requirement(tmp_path):
    handle = make_fake_handle("author", [_reply()])
    ctx = _ctx(tmp_path, handle)
    write_baseline_sheets(ctx.run_dir, [LIVE])
    phase = SimpleNamespace(label="p04:learn")
    requirement = _requirement(LIVE.sheet_id)

    failed = learn_requirement.execute_requirements(
        ctx, phase, [requirement], weak_profile="")
    failed_again = learn_requirement.execute_requirements(
        ctx, phase, [requirement], weak_profile="")

    assert failed == set() and failed_again == set()
    assert len(handle.client.calls) == 1
    assert len(ctx.store.ids("skills")) == 1
    phase_dir = ctx.run_dir / "phases" / "p04_learn"
    outcomes = read_jsonl(phase_dir / learn_requirement.OUTCOMES_NAME)
    assert [row["outcome"] for row in outcomes] == ["added"]
    provenance = read_jsonl(phase_dir / "provenance.jsonl")
    assert provenance[0]["cause"] == "requirement"
    # Cut at the start of the failure window the review named, on the very
    # execution it cited.
    assert provenance[0]["scene"] == {
        "phase_label": "p01:baseline", "sheet_id": LIVE.sheet_id, "step": 3}
    # The writer was shown the real steps around that exact place.
    prompt = handle.client.calls[0]["user"]
    assert "weak0-a2" in prompt and "weak0-a3" in prompt


def test_a_requirement_from_a_paper_exam_answer_uses_the_exams_real_scene(
        tmp_path):
    """The exam of an exam: the answer sheet is paper, the scene is real."""
    handle = make_fake_handle("author", [_reply()])
    ctx = _ctx(tmp_path, handle)
    write_baseline_sheets(ctx.run_dir, [LIVE])
    real = TraceRef("p01:baseline", LIVE.sheet_id, 5)
    ctx.store.save(Exam(
        exam_id=EXAM_ID, skill_id="traj/tb:t1/strong/00/seg00/skill",
        skill_version=1, scene_text="s", expected_behavior="e",
        reference_answer="r", scene=real), "exams")
    answer_id = EXAM_ID + "/att00"
    execution = ExecutionRecord(
        sheet_id=answer_id, unit_id="p03:exam:tb:" + EXAM_ID + ":weak:abcd1234",
        phase_label="p03:exam", task_ref=EXAM_ID, role="weak", arm=None,
        model_role="weak", model_name="m", sample_index=0,
        steps=[Step(action="select", observation=""),
               Step(action="plan", observation="")], answer="plan")
    paper = AnswerSheet(
        sheet_id=answer_id, phase_label="p03:exam",
        coverage=CoverageRecord(unit_id=execution.unit_id, attempt_index=0,
                                present=True, status="present"),
        execution=execution,
        grading=GradingRecord(verdict="failure", basis="b", rule_name="r"))
    answer_sheets.write_answer_sheets(
        [paper], ctx.run_dir / "phases" / "p03_exam" / "answer_sheets",
        render_markdown=False)
    phase = SimpleNamespace(label="p04:learn")

    learn_requirement.execute_requirements(
        ctx, phase, [_requirement(answer_id, steps={answer_id: 1})])

    row = read_jsonl(
        ctx.run_dir / "phases" / "p04_learn" / "provenance.jsonl")[0]
    assert row["scene"] == real.to_dict()
    assert row["scene"]["phase_label"] == "p01:baseline"
    # The writer saw the REAL execution at the exam's cut (step 5 of the
    # weak attempt), never the paper answer ("select" / "plan" steps).
    prompt = handle.client.calls[0]["user"]
    assert "weak0-a4" in prompt and "weak0-a5" in prompt
    assert "select" not in prompt.split("FAILURE EVIDENCE:")[1]


def test_sources_on_one_execution_are_shown_once(tmp_path):
    """Two exam cuts on the same attempt must not repeat its steps."""
    handle = make_fake_handle("author", [_reply()])
    ctx = _ctx(tmp_path, handle)
    other = baseline_sheet("tb:t1", "weak", 1, n_steps=8)
    write_baseline_sheets(ctx.run_dir, [LIVE, other])
    requirement = _requirement(
        LIVE.sheet_id, sheet_ids=[LIVE.sheet_id, other.sheet_id])
    requirement.source_steps = {LIVE.sheet_id: 2, other.sheet_id: 6}
    # A second cut on LIVE, as two skills cut from one trajectory produce.
    scenes = learn_requirement._scenes_of(ctx, requirement) + [
        TraceRef("p01:baseline", LIVE.sheet_id, 4)]

    text = learn_requirement._render_scenes(ctx, scenes)

    assert text.count("action: weak0-a3") == 1
    assert text.count("action: weak1-a6") == 1
    assert "[execution 1]" in text and "[execution 2]" in text
    assert "the skill applies at step 2, 4" in text


def test_a_requirement_that_names_no_source_phase_is_a_code_bug(tmp_path):
    handle = make_fake_handle("author", [_reply()])
    ctx = _ctx(tmp_path, handle)
    requirement = _requirement(LIVE.sheet_id)
    requirement.source_phase_label = ""

    with pytest.raises(CodeBugError, match="source phase"):
        learn_requirement.execute_requirements(
            ctx, SimpleNamespace(label="p04:learn"), [requirement])


def test_deferred_requirements_are_recorded_once_and_never_written(tmp_path):
    phase_dir = tmp_path / "phases" / "p06_learn"
    phase_dir.mkdir(parents=True)
    requirement = _requirement(LIVE.sheet_id)

    first = learn_requirement.record_deferred(
        phase_dir, "p06:learn", [requirement])
    replay = learn_requirement.record_deferred(
        phase_dir, "p06:learn", [requirement])

    assert (first, replay) == (1, 0)
    rows = read_jsonl(phase_dir / learn_requirement.OUTCOMES_NAME)
    assert [row["outcome"] for row in rows] == [
        learn_requirement.DEFERRED_OUTCOME]
    assert rows[0]["skill_id"] is None
