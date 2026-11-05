"""The exam learn landing: where a revised skill's next exam scene comes from."""

from types import SimpleNamespace

from expedistill.composites.evidence_package import EvidencePackage
from expedistill.core import ids
from expedistill.core.answer_sheet import UnitSummary
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Exam
from expedistill.core.trace import TraceRef
from expedistill.infra.store import Store, read_jsonl
from expedistill.stages import learn_exam
from tests.helpers.skills import make_skill
from tests.helpers.traces import baseline_sheet, write_baseline_sheets

SKILL = make_skill("traj/tb:t1/strong/00/seg03", "validate the outputs",
                   summary="validate outputs")
OTHER = make_skill("traj/tb:t1/strong/00/seg01", "inspect the inputs",
                   summary="inspect inputs")
SHEET = baseline_sheet("tb:t1", "strong", 0, n_steps=10)


def _exam(skill, scene_step):
    return Exam(
        exam_id=ids.make_exam_id(skill.skill_id, 1), skill_id=skill.skill_id,
        skill_version=1, scene_text=f"scene at step {scene_step}",
        expected_behavior="act", reference_answer="act",
        scene=TraceRef("p01:baseline", SHEET.sheet_id, scene_step))


def _unit(unit_id, skill):
    return UnitSummary(
        unit_id=unit_id, phase_label="p03:exam",
        task_ref=ids.make_exam_id(skill.skill_id, 1), role="weak", arm=None,
        expected_attempts=1,
        injected_skills=[{"skill_id": skill.skill_id, "version": 1}],
        valid_count=1, success_count=0, status="normal", cause="")


def test_a_revision_records_the_scene_of_its_basis_exam(tmp_path):
    run_dir = tmp_path / "run"
    store = Store(run_dir)
    write_baseline_sheets(run_dir, [SHEET])
    store.save(SKILL, "skills")
    store.save(_exam(SKILL, 6), "exams")
    store.save(_exam(OTHER, 4), "exams")
    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    row = TaskListRow(target_kind="skill", target_id=SKILL.skill_id,
                      action="revise", reason="widen the trigger",
                      basis_unit="exam-own")
    outcome = SimpleNamespace(
        skill_id=SKILL.skill_id, outcome="revised", content="new body",
        summary="validate outputs", failure_category="",
        revision_reason="the trigger missed the final stage")
    package = EvidencePackage(target=SKILL.skill_id, old_skill=SKILL)

    learn_exam._land_revision(
        SimpleNamespace(store=store, run_dir=run_dir),
        SimpleNamespace(label="p04:learn"),
        row, package, outcome, phase_dir,
        [_unit("exam-other", OTHER), _unit("exam-own", SKILL)])

    origin = read_jsonl(phase_dir / "provenance.jsonl")[0]
    assert origin["version"] == 2 and origin["cause"] == "revise"
    # Cut at the skill's OWN exam (step 6), not the other skill's (step 4),
    # and at the same real execution the first exam used.
    assert origin["scene"] == {"phase_label": "p01:baseline",
                               "sheet_id": SHEET.sheet_id, "step": 6}


# ---------------------------------------------------------------------------
# revise_scope: the phase's restriction reaches the rewrite and its landing
# ---------------------------------------------------------------------------

SCOPED = make_skill(
    "traj/tb:t1/strong/00/seg05",
    "\n".join([
        "[When to use] At task start, when the input is a CSV.",
        "[Steps]", "1. old step",
        "[Do not use when] The input is not tabular."]),
    summary="old summary")
REWRITTEN = "\n".join([
    "[When to use] After the first check, when a header is present.",
    "[Steps]", "1. new step",
    "[Do not use when] There is no header."])


def _land_scoped(tmp_path, scope, monkeypatch, weak_profile=None):
    from expedistill.composites import decision
    from expedistill.llm import json_script, make_fake_handle
    from expedistill.methods.review import attribution
    from tests.helpers.plugins import ensure_plugin
    ensure_plugin("tb")
    monkeypatch.setattr(attribution, "task_instruction_for",
                        lambda benchmark, task: "do the task")
    run_dir = tmp_path / "run"
    store = Store(run_dir)
    write_baseline_sheets(run_dir, [SHEET])
    store.save(SCOPED, "skills")
    store.save(_exam(SCOPED, 5), "exams")
    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    row = TaskListRow(target_kind="skill", target_id=SCOPED.skill_id,
                      action=decision.REVISE, reason="r",
                      basis_unit="exam-own")
    package = EvidencePackage(
        target=SCOPED.skill_id, old_skill=SCOPED,
        facts=[{"kind": "exam_question", "text": "q"},
               {"kind": "weak_answer", "text": "a"},
               {"kind": "judge_reason", "text": "j"},
               {"kind": "source_context", "text": "ctx"}])
    handle = make_fake_handle("rewriter", json_script({
        "failure_category": "wrong_trigger", "skill_text": REWRITTEN,
        "rewrite_reason": "r", "summary": "new summary"}))
    ctx = SimpleNamespace(
        store=store, run_dir=run_dir, usage=None,
        config=SimpleNamespace(benchmark="tb",
                               effective_weak_profile=lambda: ""),
        handle=lambda role: handle)
    phase = SimpleNamespace(label="p04:learn")
    item = SimpleNamespace(row=row, evidence=package)
    current = [_unit("exam-own", SCOPED)]

    learn_exam._land_revise(ctx, phase, item, current, phase_dir, scope,
                            weak_profile)
    return store.load("skills", SCOPED.skill_id, type(SCOPED)), handle


def test_a_body_scoped_landing_keeps_the_trigger_and_the_summary(
        tmp_path, monkeypatch):
    stored, _ = _land_scoped(tmp_path, "body", monkeypatch)
    assert stored.version == 2
    assert stored.summary == "old summary"
    assert stored.content.splitlines()[0] \
        == "[When to use] At task start, when the input is a CSV."
    assert "1. new step" in stored.content


def test_a_trigger_scoped_landing_keeps_the_body(tmp_path, monkeypatch):
    stored, _ = _land_scoped(tmp_path, "trigger", monkeypatch)
    assert stored.version == 2
    assert stored.summary == "new summary"
    assert stored.content.splitlines()[0] \
        == "[When to use] After the first check, when a header is present."
    assert "1. old step" in stored.content
    assert "[Do not use when] The input is not tabular." in stored.content


def test_the_accumulated_profile_reaches_the_rewrite_prompt(
        tmp_path, monkeypatch):
    stored, handle = _land_scoped(
        tmp_path, "both", monkeypatch,
        weak_profile="skips verification before declaring success")
    assert stored.version == 2
    user = handle.client.calls[0]["user"]
    assert "skips verification before declaring success" in user
    assert "THE MODEL THAT WILL READ THE SKILL (weak model profile):" in user


def test_without_a_profile_the_config_profile_decides(tmp_path, monkeypatch):
    # Phases that keep no accumulated profile (weak_profile None) fall back
    # to the config's effective profile, which the fixture leaves empty:
    # the rewriter is told no profile is provided.
    from expedistill.prompts.profile import profile_section
    stored, handle = _land_scoped(tmp_path, "both", monkeypatch)
    assert stored.version == 2
    assert profile_section("skill_reader", "") in handle.client.calls[0]["user"]
