"""Stage 4 tests: pending skills + exams seeded straight into a tmp store,
scripted llm.fake handles, no network. Covers the K4 drive per pending
skill, the outcome tallies, the missing-exam skip, the zero-call rerun over
terminal skills and the deterministic iteration order."""

from expedistill.core.config import ExperimentConfig
from expedistill.core.ids import (
    make_exam_id,
    make_merged_skill_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
import pytest

from expedistill.core.models import Attempt, Exam, Skill
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store, read_json
from expedistill.llm import json_script, make_fake_handle
from expedistill.stages import stage4_acceptance
from expedistill.stages.context import StageContext

WEAK_PROFILE = "A small model; known to skip intermediate steps."

# Question/reference texts deliberately free of leak-audit "specifics" (no
# quoted spans, numbers, file-like tokens or acronyms) so every fabricated
# pair passes D8 regardless of the threshold under test.
QUESTION = "What should be done when the service refuses connections?"
REFERENCE = "Update the configuration and restart the service."


def make_config(**overrides):
    defaults = dict(
        benchmark="tb",
        mode="full",
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model"},
        weak_profile=WEAK_PROFILE,
        retry_limit=1,
        leak_threshold=0.5,
    )
    defaults.update(overrides)
    return ExperimentConfig(**defaults)


def make_ctx(tmp_path, *, answerer, judge, rewriter, config=None,
             run_dir=None):
    run_dir = run_dir or tmp_path / "run"
    store = Store(run_dir)
    ctx = StageContext(
        run_dir=run_dir,
        store=store,
        config=config or make_config(),
        usage=None,
        handles={"answerer": answerer, "judge": judge,
                 "rewriter": rewriter},
        workdir_root=tmp_path / "work",
    )
    return ctx, store


def seed_pair(store, task, seg_index, *, skill_text="a skill",
              question=QUESTION, reference=REFERENCE, status="pending",
              with_exam=True):
    """Store one (skill, exam) pair hanging on one subtask, stage-3 style."""
    subtask_id = make_subtask_id(
        make_trajectory_id(make_task_id("tb", task), "strong", 0), seg_index)
    skill = Skill(skill_id=make_skill_id(subtask_id), content=skill_text,
                  subtask_id=subtask_id, status=status)
    store.save(skill, "skills")
    if with_exam:
        exam = Exam(exam_id=make_exam_id(subtask_id), question_text=question,
                    reference_answer=reference, subtask_id=subtask_id)
        store.save(exam, "exams")
    return skill


def incorrect():
    return json_script({"verdict": "incorrect",
                        "error_description": "skipped the restart step"})


def correct():
    return json_script({"verdict": "correct", "error_description": ""})


def test_accepts_each_pending_skill_and_tallies(tmp_path):
    store = Store(tmp_path / "run")
    seed_pair(store, "t1", 0, skill_text="skill one")
    seed_pair(store, "t1", 1, skill_text="skill two")
    answerer = make_fake_handle("weak",
                                ["bare one", "skilled one",
                                 "bare two", "skilled two"])
    judge = make_fake_handle("strong",
                             [incorrect(), correct(),
                              incorrect(), correct()])
    rewriter = make_fake_handle("strong", [])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=rewriter)

    summary = stage4_acceptance.run(ctx)

    assert summary == {"accepted": 2, "discarded": {},
                       "skipped_missing_exam": [], "infra_failed": [],
                       "processed_this_call": 2}
    # One bare + one with-skill attempt per skill.
    assert len(store.ids("attempts")) == 4
    for skill_id in store.ids("skills"):
        assert store.load("skills", skill_id, Skill).status == "accepted"
    assert rewriter.client.calls == []
    # The summary is also on disk, identical to the returned dict.
    stored = read_json(ctx.run_dir / "stage4_acceptance"
                       / "stage4_summary.json")
    assert stored == summary


def test_tallies_discards_by_reason(tmp_path):
    store = Store(tmp_path / "run")
    seed_pair(store, "t1", 0, skill_text="skill accepted")
    seed_pair(store, "t1", 1, skill_text="skill already known")
    seed_pair(store, "t1", 2, skill_text="skill never works")
    # t1 seg0: bare wrong, with-skill right -> accepted.
    # t1 seg1: bare right -> discarded weak_already_knows.
    # t1 seg2: bare wrong, with-skill wrong, retry_limit 0 -> retry_exhausted.
    answerer = make_fake_handle("weak",
                                ["bare zero", "skilled zero",
                                 "bare one",
                                 "bare two", "skilled two"])
    judge = make_fake_handle("strong",
                             [incorrect(), correct(),
                              correct(),
                              incorrect(), incorrect()])
    rewriter = make_fake_handle("strong", [])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=rewriter, config=make_config(retry_limit=0))

    summary = stage4_acceptance.run(ctx)

    assert summary["accepted"] == 1
    assert summary["discarded"] == {"weak_already_knows": 1,
                                    "retry_exhausted": 1}
    assert summary["skipped_missing_exam"] == []
    statuses = {store.load("skills", skill_id, Skill).skill_id:
                store.load("skills", skill_id, Skill).status
                for skill_id in store.ids("skills")}
    assert set(statuses.values()) == {"accepted", "discarded"}


def test_missing_exam_is_skipped_not_crashed(tmp_path):
    store = Store(tmp_path / "run")
    orphan = seed_pair(store, "t1", 5, with_exam=False)  # skill, no exam
    seed_pair(store, "t1", 0, skill_text="skill one")
    answerer = make_fake_handle("weak", ["bare zero", "skilled zero"])
    judge = make_fake_handle("strong", [incorrect(), correct()])
    rewriter = make_fake_handle("strong", [])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=rewriter)

    summary = stage4_acceptance.run(ctx)

    assert summary == {"accepted": 1, "discarded": {},
                       "skipped_missing_exam": [orphan.skill_id],
                       "infra_failed": [], "processed_this_call": 1}
    # The orphan stayed pending and consumed no model call.
    assert store.load("skills", orphan.skill_id, Skill).status == "pending"
    assert len(answerer.client.calls) == 2


def test_rerun_over_terminal_skills_makes_zero_calls(tmp_path):
    store = Store(tmp_path / "run")
    seed_pair(store, "t1", 0, skill_text="skill one")
    answerer = make_fake_handle("weak", ["bare zero", "skilled zero"])
    judge = make_fake_handle("strong", [incorrect(), correct()])
    rewriter = make_fake_handle("strong", [])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=rewriter)
    stage4_acceptance.run(ctx)

    answerer2 = make_fake_handle("weak", [])
    judge2 = make_fake_handle("strong", [])
    rewriter2 = make_fake_handle("strong", [])
    ctx2, _ = make_ctx(tmp_path, answerer=answerer2, judge=judge2,
                       rewriter=rewriter2)
    summary = stage4_acceptance.run(ctx2)

    assert answerer2.client.calls == []
    assert judge2.client.calls == []
    assert rewriter2.client.calls == []
    # The summary is cumulative over the stored candidates: a zero-call
    # rerun reports the same verdicts, not zeros.
    assert summary == {"accepted": 1, "discarded": {},
                       "skipped_missing_exam": [], "infra_failed": [],
                       "processed_this_call": 0}
    # A non-pending skill that was never touched by the loop also stays put
    # (and is tallied by its stored status).
    seed_pair(ctx2.store, "t1", 9, status="accepted")
    summary = stage4_acceptance.run(ctx2)
    assert summary["accepted"] == 2
    assert summary["processed_this_call"] == 0
    assert answerer2.client.calls == []


def test_iteration_order_follows_sorted_skill_ids(tmp_path):
    store = Store(tmp_path / "run")
    # Seeded out of order: seg1 sorts after seg0, and its exam question is
    # distinguishable, so the call order exposes the iteration order.
    seed_pair(store, "t1", 1, skill_text="skill one",
              question="What about the second question here?")
    seed_pair(store, "t1", 0, skill_text="skill zero")
    answerer = make_fake_handle("weak",
                                ["bare zero", "skilled zero",
                                 "bare one", "skilled one"])
    judge = make_fake_handle("strong",
                             [incorrect(), correct(),
                              incorrect(), correct()])
    rewriter = make_fake_handle("strong", [])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=rewriter)

    stage4_acceptance.run(ctx)

    first_bare_prompt = answerer.client.calls[0]["user"]
    assert QUESTION in first_bare_prompt          # seg0's exam is asked first
    assert "second question" not in first_bare_prompt
    second_bare_prompt = answerer.client.calls[2]["user"]
    assert "second question" in second_bare_prompt
    # Attempts are stored under deterministic ids for both exams.
    attempts = [store.load("attempts", attempt_id, Attempt)
                for attempt_id in store.ids("attempts")]
    assert len(attempts) == 4


# ---------------------------------------------------------------------------
# Unit failures: one skill's failure never stops the others
# ---------------------------------------------------------------------------

def test_model_output_error_discards_the_skill_and_continues(tmp_path):
    store = Store(tmp_path / "run")
    broken = seed_pair(store, "t1", 0, skill_text="skill zero")
    seed_pair(store, "t1", 1, skill_text="skill one")
    # seg0: the bare judge breaks its contract on both K3 repair tries.
    answerer = make_fake_handle("weak", ["bare zero", "bare one",
                                         "skilled one"])
    judge = make_fake_handle("strong", [
        ModelOutputError("JUDGE: verdict outside the vocabulary"),
        ModelOutputError("JUDGE: still outside the vocabulary"),
        incorrect(), correct()])
    rewriter = make_fake_handle("strong", [])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=rewriter)

    summary = stage4_acceptance.run(ctx)

    assert summary == {"accepted": 1,
                       "discarded": {"model_output_error": 1},
                       "skipped_missing_exam": [], "infra_failed": [],
                       "processed_this_call": 2}
    stored = store.load("skills", broken.skill_id, Skill)
    assert (stored.status, stored.discard_reason) == (
        "discarded", "model_output_error")
    failures = UnitFailureRecorder(ctx.run_dir).list_failures()
    assert [(f["unit_id"], f["stage"], f["error_class"])
            for f in failures] == [
        (broken.skill_id, "stage4_acceptance", "ModelOutputError")]
    assert "still outside the vocabulary" in failures[0]["message"]

    # Terminal: a resumed stage does not retry the discarded skill.
    idle = [make_fake_handle(role, []) for role in ("weak", "strong",
                                                     "strong")]
    ctx2, _ = make_ctx(tmp_path, answerer=idle[0], judge=idle[1],
                       rewriter=idle[2])
    stage4_acceptance.run(ctx2)
    assert all(handle.client.calls == [] for handle in idle)


def test_infrastructure_error_keeps_the_skill_pending_and_fails_the_stage(
        tmp_path):
    store = Store(tmp_path / "run")
    stuck = seed_pair(store, "t1", 0, skill_text="skill zero")
    other = seed_pair(store, "t1", 1, skill_text="skill one")
    answerer = make_fake_handle("weak", [
        InfrastructureError("endpoint timed out after retries"),
        "bare one", "skilled one"])
    judge = make_fake_handle("strong", [incorrect(), correct()])
    rewriter = make_fake_handle("strong", [])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=rewriter)

    with pytest.raises(InfrastructureError) as excinfo:
        stage4_acceptance.run(ctx)

    message = str(excinfo.value)
    assert "1 skill" in message and "resume" in message
    # The other skill was processed in full before the stage failed.
    assert store.load("skills", other.skill_id, Skill).status == "accepted"
    assert store.load("skills", stuck.skill_id, Skill).status == "pending"
    summary = read_json(ctx.run_dir / "stage4_acceptance"
                        / "stage4_summary.json")
    assert summary == {"accepted": 1, "discarded": {},
                       "skipped_missing_exam": [],
                       "infra_failed": [stuck.skill_id],
                       "processed_this_call": 1}
    failures = UnitFailureRecorder(ctx.run_dir).list_failures()
    assert [(f["unit_id"], f["stage"], f["error_class"])
            for f in failures] == [
        (stuck.skill_id, "stage4_acceptance", "InfrastructureError")]

    # Resume retries exactly the pending skill and completes the stage.
    answerer2 = make_fake_handle("weak", ["bare zero", "skilled zero"])
    judge2 = make_fake_handle("strong", [incorrect(), correct()])
    ctx2, _ = make_ctx(tmp_path, answerer=answerer2, judge=judge2,
                       rewriter=make_fake_handle("strong", []))
    summary2 = stage4_acceptance.run(ctx2)
    assert summary2 == {"accepted": 2, "discarded": {},
                        "skipped_missing_exam": [], "infra_failed": [],
                        "processed_this_call": 1}
    assert store.load("skills", stuck.skill_id, Skill).status == "accepted"


@pytest.mark.parametrize("error", [
    ConfigError("bad config"),
    ProviderRefusalError("unknown model"),
    CodeBugError("broken invariant"),
    RuntimeError("plain bug"),
])
def test_other_errors_stop_the_stage_immediately(tmp_path, error):
    store = Store(tmp_path / "run")
    first = seed_pair(store, "t1", 0, skill_text="skill zero")
    second = seed_pair(store, "t1", 1, skill_text="skill one")
    answerer = make_fake_handle("weak", [error, "bare one", "skilled one"])
    judge = make_fake_handle("strong", [incorrect(), correct()])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=make_fake_handle("strong", []))

    with pytest.raises(type(error)):
        stage4_acceptance.run(ctx)

    # Nothing after the failing skill ran, nothing was recorded as a unit
    # failure, and no summary claims the stage finished.
    assert len(answerer.client.calls) == 1
    assert judge.client.calls == []
    for skill in (first, second):
        assert store.load("skills", skill.skill_id, Skill).status == "pending"
    assert UnitFailureRecorder(ctx.run_dir).list_failures() == []
    assert not (ctx.run_dir / "stage4_acceptance"
                / "stage4_summary.json").exists()


# ---------------------------------------------------------------------------
# Only stage-3 candidates; cumulative summary; crash-safe failure ledger
# ---------------------------------------------------------------------------

def test_pending_merged_skill_of_stage5_is_not_accepted(tmp_path):
    """K5 stores an in-flight merged skill as pending before its regression
    exam. A stage-4 rerun must leave it to K5, not accept it on the anchor's
    exam alone."""
    store = Store(tmp_path / "run")
    anchor = seed_pair(store, "t1", 0, skill_text="skill zero",
                       status="merged")
    merged = Skill(skill_id=make_merged_skill_id(0), content="merged text",
                   subtask_id=anchor.subtask_id, status="pending",
                   merged_from=[anchor.skill_id])
    store.save(merged, "skills")
    answerer = make_fake_handle("weak", ["bare", "skilled"])
    judge = make_fake_handle("strong", [incorrect(), correct()])
    rewriter = make_fake_handle("strong", [])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=rewriter)

    summary = stage4_acceptance.run(ctx)

    assert store.load("skills", merged.skill_id, Skill).to_dict() == (
        merged.to_dict())
    assert answerer.client.calls == [] and judge.client.calls == []
    assert store.ids("attempts") == []
    # The merged source passed acceptance before stage 5 merged it.
    assert summary == {"accepted": 1, "discarded": {},
                       "skipped_missing_exam": [], "infra_failed": [],
                       "processed_this_call": 0}


def test_summary_is_cumulative_across_resumes(tmp_path):
    store = Store(tmp_path / "run")
    seed_pair(store, "t1", 0, skill_text="skill zero")
    stuck = seed_pair(store, "t1", 1, skill_text="skill one")
    seed_pair(store, "t1", 2, skill_text="skill two")
    answerer = make_fake_handle("weak", [
        "bare zero", "skilled zero",
        InfrastructureError("endpoint down"),
        "bare two"])
    judge = make_fake_handle("strong", [incorrect(), correct(), correct()])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=make_fake_handle("strong", []))
    with pytest.raises(InfrastructureError):
        stage4_acceptance.run(ctx)

    answerer2 = make_fake_handle("weak", ["bare one"])
    judge2 = make_fake_handle("strong", [correct()])
    ctx2, _ = make_ctx(tmp_path, answerer=answerer2, judge=judge2,
                       rewriter=make_fake_handle("strong", []))
    summary = stage4_acceptance.run(ctx2)

    assert summary == {"accepted": 1,
                       "discarded": {"weak_already_knows": 2},
                       "skipped_missing_exam": [], "infra_failed": [],
                       "processed_this_call": 1}
    assert read_json(ctx.run_dir / "stage4_acceptance"
                     / "stage4_summary.json") == summary
    assert store.load("skills", stuck.skill_id, Skill).status == "discarded"


def _moe_judge_ctx(tmp_path):
    """One candidate whose bare judge exhausts K3's repair budget."""
    answerer = make_fake_handle("weak", ["bare zero"])
    judge = make_fake_handle("strong", [
        ModelOutputError("JUDGE: bad verdict"),
        ModelOutputError("JUDGE: bad verdict again")])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=make_fake_handle("strong", []))
    return ctx


class _Crash(Exception):
    pass


def _moe_failures(ctx):
    return [f for f in UnitFailureRecorder(ctx.run_dir).list_failures()
            if f["error_class"] == "ModelOutputError"]


def test_crash_between_discard_and_ledger_row_is_repaired(tmp_path,
                                                          monkeypatch):
    store = Store(tmp_path / "run")
    skill = seed_pair(store, "t1", 0, skill_text="skill zero")
    ctx = _moe_judge_ctx(tmp_path)
    monkeypatch.setattr(UnitFailureRecorder, "record",
                        lambda *a, **k: (_ for _ in ()).throw(_Crash()))
    with pytest.raises(_Crash):
        stage4_acceptance.run(ctx)
    monkeypatch.undo()
    # The terminal discard was stored first.
    stored = store.load("skills", skill.skill_id, Skill)
    assert (stored.status, stored.discard_reason) == (
        "discarded", "model_output_error")
    assert _moe_failures(ctx) == []

    idle = [make_fake_handle(role, []) for role in ("weak", "strong",
                                                     "strong")]
    ctx2, _ = make_ctx(tmp_path, answerer=idle[0], judge=idle[1],
                       rewriter=idle[2])
    summary = stage4_acceptance.run(ctx2)
    stage4_acceptance.run(ctx2)   # a second rerun adds no duplicate

    assert all(handle.client.calls == [] for handle in idle)
    rows = _moe_failures(ctx2)
    assert [(r["unit_id"], r["stage"]) for r in rows] == [
        (skill.skill_id, "stage4_acceptance")]
    assert summary["discarded"] == {"model_output_error": 1}


def test_crash_before_the_discard_is_stored_leaves_no_ledger_row(
        tmp_path, monkeypatch):
    store = Store(tmp_path / "run")
    skill = seed_pair(store, "t1", 0, skill_text="skill zero")
    ctx = _moe_judge_ctx(tmp_path)
    original = Store.save

    def save(self, obj, kind):
        if kind == "skills" and getattr(obj, "status", None) == "discarded":
            raise _Crash()
        return original(self, obj, kind)

    monkeypatch.setattr(Store, "save", save)
    with pytest.raises(_Crash):
        stage4_acceptance.run(ctx)
    monkeypatch.undo()
    # Nothing claims the skill failed while it is still pending.
    assert store.load("skills", skill.skill_id, Skill).status == "pending"
    assert _moe_failures(ctx) == []

    # Resume: the stored answer is judged again (still broken) -> exactly
    # one discard and one ledger row, as in an uninterrupted run.
    ctx2 = _moe_judge_ctx(tmp_path)
    stage4_acceptance.run(ctx2)
    stored = store.load("skills", skill.skill_id, Skill)
    assert (stored.status, stored.discard_reason) == (
        "discarded", "model_output_error")
    assert len(_moe_failures(ctx2)) == 1
    assert ctx2.handle("answerer").client.calls == []
