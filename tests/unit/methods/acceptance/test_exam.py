"""Stage 4 tests: pending skills + exams seeded straight into a tmp store,
scripted llm.fake handles, no network. Covers the K4 drive per pending
skill, the outcome tallies, the missing-exam skip, the zero-call rerun over
terminal skills and the deterministic iteration order."""

import json
import threading

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
from expedistill.infra.store import Store, read_json, read_jsonl
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.acceptance import exam as exam_driver
from expedistill.stages import stage4_acceptance
from dataclasses import replace
from expedistill.stages.context import RunSettings, StageContext

WEAK_PROFILE = "A small model; known to skip intermediate steps."

# Question/reference texts; the scripted auditor judge returns a clean
# verdict for every (skill, version) it is asked about.
QUESTION = "What should be done when the service refuses connections?"
REFERENCE = "Update the configuration and restart the service."


def make_config(**overrides):
    defaults = dict(
        benchmark="tb",
        mode="full",
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model"},
        weak_profile=WEAK_PROFILE,
        methods={"acceptance": {"name": "exam", "retry_limit": 1}},
    )
    defaults.update(overrides)
    return ExperimentConfig(**defaults)


def make_ctx(tmp_path, *, answerer, judge, rewriter, auditor=None,
             config=None, run_dir=None):
    run_dir = run_dir or tmp_path / "run"
    store = Store(run_dir)
    ctx = StageContext(
        run_dir=run_dir,
        store=store,
        config=config or make_config(),
        usage=None,
        handles={"answerer": answerer, "judge": judge,
                 "rewriter": rewriter,
                 "auditor": auditor or make_fake_handle("auditor", [])},
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
                  subtask_id=subtask_id, source_subtasks=[subtask_id],
                  status=status)
    store.save(skill, "skills")
    if with_exam:
        exam = Exam(exam_id=make_exam_id(subtask_id), question_text=question,
                    reference_answer=reference, subtask_id=subtask_id,
                    skill_id=make_skill_id(subtask_id))
        store.save(exam, "exams")
    return skill


def incorrect():
    return json_script({"verdict": "incorrect",
                        "error_description": "skipped the restart step"})


def correct():
    return json_script({"verdict": "correct", "error_description": ""})


def clean_verdict():
    """The scripted auditor: the skill under audit carries no leak."""
    return json_script({"leaked": False, "reason": "generic method advice"})


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
                      rewriter=rewriter,
                      auditor=make_fake_handle("auditor",
                                               [clean_verdict()] * 2))

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
                      rewriter=rewriter,
                      config=make_config(methods={
                          "acceptance": {"name": "exam", "retry_limit": 0}}),
                      auditor=make_fake_handle("auditor",
                                               [clean_verdict()] * 2))

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
                      rewriter=rewriter,
                      auditor=make_fake_handle("auditor", [clean_verdict()]))

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
                      rewriter=rewriter,
                      auditor=make_fake_handle("auditor", [clean_verdict()]))
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
                      rewriter=rewriter,
                      auditor=make_fake_handle("auditor",
                                               [clean_verdict()] * 2))

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
                      rewriter=rewriter,
                      auditor=make_fake_handle("auditor", [clean_verdict()]))

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
                      rewriter=rewriter,
                      auditor=make_fake_handle("auditor", [clean_verdict()]))

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
                       rewriter=make_fake_handle("strong", []),
                       auditor=make_fake_handle("auditor", [clean_verdict()]))
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
                   subtask_id=anchor.subtask_id,
                   source_subtasks=[anchor.subtask_id], status="pending",
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
                      rewriter=make_fake_handle("strong", []),
                      auditor=make_fake_handle("auditor", [clean_verdict()]))
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


# ---------------------------------------------------------------------------
# Parallel dispatch (B5 run_units): workers from the context, equivalence
# between workers=1 and workers=4, a concurrency proof, the per-unit infra
# disposition under parallelism, and resume after a fatal crash.
# ---------------------------------------------------------------------------

def _question(i):
    return f"QMARK{i}: what should be done in this situation?"


def _skill_text(i):
    return f"SMARK{i}: original advice for situation {i}."


def _rewritten_text(i):
    return f"SMARK{i}: rewritten advice with the missing step spelled out."


def _seed_marked(store, i, *, status="pending", with_exam=True):
    """One (skill, exam) pair with content markers that make every prompt
    self-identifying, so scripted responses can dispatch on content instead
    of call order (call order is the one thing workers>1 does not pin)."""
    return seed_pair(store, "t1", i, skill_text=_skill_text(i),
                     question=_question(i), status=status,
                     with_exam=with_exam)


def _answerer(plan):
    """Answer handle dispatching on the exam marker and injected content.

    ``plan`` maps a question marker to ``{"bare": item, "skill":
    [(content marker, item), ...]}``; an item is an answer string or an
    exception to raise. The with-skill prompt is matched by the injected
    skill content, so the script is order-independent under any dispatch
    interleaving.
    """
    def respond(system, user):
        for marker, spec in plan.items():
            if marker not in user:
                continue
            if "## Skill rules" not in user:
                item = spec["bare"]
            else:
                item = next(out for token, out in spec["skill"]
                            if token in user)
            if isinstance(item, BaseException):
                raise item
            return item
        raise AssertionError(f"unexpected ANSWER prompt: {user[:120]!r}")
    return make_fake_handle("weak", respond)


def _judge(verdicts):
    """Judge handle dispatching on the weak answer's marker: verdict JSON or
    an exception to raise (every time, so K3's repair budget exhausts)."""
    def respond(system, user):
        for marker, item in verdicts.items():
            if marker in user:
                if isinstance(item, BaseException):
                    raise item
                return item
        raise AssertionError(f"unexpected JUDGE prompt: {user[:120]!r}")
    return make_fake_handle("strong", respond)


def _rewriter(rewrites):
    """Rewriter handle dispatching on the current skill content marker."""
    def respond(system, user):
        for marker, item in rewrites.items():
            if marker in user:
                return item
        raise AssertionError(f"unexpected REWRITE prompt: {user[:120]!r}")
    return make_fake_handle("strong", respond)


_CLEAN_AUDIT = json_script(
    {"leaked": False, "reason": "generic method advice"})


def _snapshot(store):
    """Field-by-field snapshot of the skills and attempts stores."""
    skills = {skill_id: store.load("skills", skill_id, Skill).to_dict()
              for skill_id in store.ids("skills")}
    attempts = {attempt_id: store.load("attempts", attempt_id,
                                       Attempt).to_dict()
                for attempt_id in store.ids("attempts")}
    return skills, attempts


def _ledger_rows(run_dir, relative):
    """Stage-4 ledger rows without wall-clock times, sorted: workers>1 pins
    every row's content but not the append order. ``relative`` is the path
    under the run dir: failed_units.jsonl sits at the root, K4's ledgers
    under stage4_acceptance/."""
    rows = [{key: value for key, value in row.items() if key != "time"}
            for row in read_jsonl(run_dir / relative)]
    return sorted(rows, key=lambda row: json.dumps(
        row, sort_keys=True, ensure_ascii=False))


def _mixed_scenario(store):
    """Seed the six-way mix and build its scripted handles.

    Skill 0 accepts; skill 1 the weak model already knows (bare correct ->
    weak_already_knows); skill 2's judge never satisfies its contract
    (model_output_error discard); skill 3 needs one rewrite round before it
    accepts on the rewritten version; skill 4's first version is voided by
    the leak audit and it accepts on the rewrite; skill 5's rewriter never
    satisfies its contract, so its round is consumed without a new version
    and it discards as retry_exhausted. Between them the units exercise all
    three K4 ledgers from several threads at once.
    """
    for i in range(6):
        _seed_marked(store, i)
    answerer = _answerer({
        f"QMARK{i}": {"bare": f"AMARK{i}bare",
                      "skill": [(_skill_text(i), f"AMARK{i}v1")]}
        for i in (0, 1, 2, 5)
    } | {
        f"QMARK{i}": {"bare": f"AMARK{i}bare",
                      "skill": [(_skill_text(i), f"AMARK{i}v1"),
                                (_rewritten_text(i), f"AMARK{i}v2")]}
        for i in (3, 4)
    })
    judge = _judge({
        "AMARK0bare": incorrect(), "AMARK0v1": correct(),
        "AMARK1bare": correct(),
        "AMARK2bare": ModelOutputError(
            "JUDGE for QMARK2: verdict outside the vocabulary"),
        "AMARK3bare": incorrect(), "AMARK3v1": incorrect(),
        "AMARK3v2": correct(),
        # Skill 4's v1 is voided by the leak audit before it is examined;
        # a call for it would be a bug, so it has no script entry.
        "AMARK4bare": incorrect(), "AMARK4v2": correct(),
        "AMARK5bare": incorrect(), "AMARK5v1": incorrect(),
    })
    rewriter = _rewriter({
        _skill_text(3): json_script({
            "failure_category": "missing_step",
            "skill_text": _rewritten_text(3),
            "rewrite_reason": "spelled out the intermediate step",
        }),
        _skill_text(4): json_script({
            "failure_category": "wrong_trigger",
            "skill_text": _rewritten_text(4),
            "rewrite_reason": "made the trigger condition concrete",
        }),
        # Skill 5's G6 never satisfies its contract: both tries of its
        # rewrite round are spent (rewrite_failures.jsonl).
        _skill_text(5): ModelOutputError(
            "G6 for QMARK5: rewrite outside the vocabulary"),
    })

    def audit_respond(system, user):
        # Skill 4's first version leaks; everything else is clean.
        if _skill_text(4) in user:
            return json_script({"leaked": True,
                                "reason": "a concrete value from the exam "
                                "situation"})
        return _CLEAN_AUDIT

    return answerer, judge, rewriter, make_fake_handle("auditor",
                                                       audit_respond)


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_runs_at_any_worker_count_produce_identical_stores_and_summaries(
        tmp_path, workers):
    def run_once(tag, worker_count):
        run_dir = tmp_path / f"run-{tag}"
        store = Store(run_dir)
        answerer, judge, rewriter, auditor = _mixed_scenario(store)
        ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                          rewriter=rewriter, auditor=auditor,
                          run_dir=run_dir)
        ctx = replace(ctx, run_settings=RunSettings(workers=worker_count))
        return ctx, store, stage4_acceptance.run(ctx)

    # The reference run is serial (workers=1, the old loop); every other
    # worker count (2, 4 = more units than workers either way) must land on
    # exactly the same stores, summaries and ledger contents.
    ctx1, store1, summary1 = run_once("serial", 1)
    ctxN, storeN, summaryN = run_once(f"w{workers}", workers)

    assert summary1 == summaryN == {
        "accepted": 3, "discarded": {"weak_already_knows": 1,
                                     "model_output_error": 1,
                                     "retry_exhausted": 1},
        "skipped_missing_exam": [], "infra_failed": [],
        "processed_this_call": 6}
    assert _snapshot(store1) == _snapshot(storeN)
    assert read_json(ctx1.run_dir / "stage4_acceptance"
                     / "stage4_summary.json") == summary1
    assert read_json(ctxN.run_dir / "stage4_acceptance"
                     / "stage4_summary.json") == summaryN
    for relative in ("stage4_acceptance/leak_verdicts.jsonl",
                     "stage4_acceptance/rewrite_failures.jsonl",
                     "failed_units.jsonl"):
        assert _ledger_rows(ctx1.run_dir, relative) == \
            _ledger_rows(ctxN.run_dir, relative)

    # The mix itself is pinned, not merely self-consistent: every skill's
    # terminal state is what its script says (status, discard reason,
    # version -- skills 3 and 4 accepted on a rewritten version 2 -- and
    # rewrite history length).
    expected = {
        0: ("accepted", None, 1, 0),
        1: ("discarded", "weak_already_knows", 1, 0),
        2: ("discarded", "model_output_error", 1, 0),
        3: ("accepted", None, 2, 1),
        4: ("accepted", None, 2, 1),
        5: ("discarded", "retry_exhausted", 1, 0),
    }
    for index, (status, reason, version, rewrites) in expected.items():
        skill = store1.load("skills", make_skill_id(make_subtask_id(
            make_trajectory_id(make_task_id("tb", "t1"), "strong", 0),
            index)), Skill)
        assert (skill.status, skill.discard_reason, skill.version,
                len(skill.rewrite_history)) == (status, reason, version,
                                                rewrites)
    # One leak verdict per examined version: skill 4's v1 is the leaked one.
    leaks = _ledger_rows(ctx1.run_dir,
                         "stage4_acceptance/leak_verdicts.jsonl")
    assert len(leaks) == 6
    assert [row for row in leaks if row["leaked"]] == [{
        "skill_id": make_skill_id(make_subtask_id(make_trajectory_id(
            make_task_id("tb", "t1"), "strong", 0), 4)),
        "skill_version": 1, "leaked": True,
        "reason": "a concrete value from the exam situation"}]
    # Skill 5's rewrite round burned both G6 tries without a new version.
    failures = _ledger_rows(ctx1.run_dir,
                            "stage4_acceptance/rewrite_failures.jsonl")
    assert [row["try_index"] for row in failures] == [0, 1]
    assert failures[-1]["exhausted"] is True
    # Only skill 2's judge produced a unit-failure ledger row.
    assert [row["error_class"] for row in _ledger_rows(
        ctx1.run_dir, "failed_units.jsonl")] == ["ModelOutputError"]


def test_workers4_runs_units_concurrently(tmp_path, monkeypatch):
    store = Store(tmp_path / "run")
    for i in range(4):
        _seed_marked(store, i)
    barrier = threading.Barrier(4, timeout=10)

    def stub_accept(*, store, skill, **kwargs):
        # Opens only with all four units really in flight; a serial loop
        # (or a pool that serializes) times out here and fails the test.
        barrier.wait()
        skill.status = "accepted"
        store.save(skill, "skills")
        return skill

    monkeypatch.setattr(exam_driver, "accept_skill", stub_accept)
    ctx, _ = make_ctx(tmp_path, answerer=make_fake_handle("weak", []),
                      judge=make_fake_handle("strong", []),
                      rewriter=make_fake_handle("strong", []))
    ctx = replace(ctx, run_settings=RunSettings(workers=4))

    summary = stage4_acceptance.run(ctx)

    assert summary == {"accepted": 4, "discarded": {},
                       "skipped_missing_exam": [], "infra_failed": [],
                       "processed_this_call": 4}
    for skill_id in store.ids("skills"):
        assert store.load("skills", skill_id, Skill).status == "accepted"


def test_infra_failed_unit_leaves_the_rest_complete_with_workers4(tmp_path):
    store = Store(tmp_path / "run")
    stuck = _seed_marked(store, 1)
    done = [_seed_marked(store, 0).skill_id,
            _seed_marked(store, 2).skill_id]
    answerer = _answerer({
        "QMARK0": {"bare": "AMARK0bare",
                   "skill": [(_skill_text(0), "AMARK0v1")]},
        # The stuck unit's first model call exhausts the endpoint.
        "QMARK1": {"bare": InfrastructureError("endpoint timed out")},
        "QMARK2": {"bare": "AMARK2bare",
                   "skill": [(_skill_text(2), "AMARK2v1")]},
    })
    judge = _judge({
        "AMARK0bare": incorrect(), "AMARK0v1": correct(),
        "AMARK2bare": incorrect(), "AMARK2v1": correct(),
    })
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=make_fake_handle("strong", []),
                      auditor=make_fake_handle("auditor", _CLEAN_AUDIT))
    ctx = replace(ctx, run_settings=RunSettings(workers=4))

    with pytest.raises(InfrastructureError) as excinfo:
        stage4_acceptance.run(ctx)

    assert "1 skill" in str(excinfo.value) and "resume" in str(excinfo.value)
    # The stuck skill stayed pending; every other unit ran to completion.
    assert store.load("skills", stuck.skill_id, Skill).status == "pending"
    for skill_id in done:
        assert store.load("skills", skill_id, Skill).status == "accepted"
    summary = read_json(ctx.run_dir / "stage4_acceptance"
                        / "stage4_summary.json")
    assert summary == {"accepted": 2, "discarded": {},
                       "skipped_missing_exam": [],
                       "infra_failed": [stuck.skill_id],
                       "processed_this_call": 2}
    failures = UnitFailureRecorder(ctx.run_dir).list_failures()
    assert [(f["unit_id"], f["error_class"]) for f in failures] == [
        (stuck.skill_id, "InfrastructureError")]


def _accepting_answers(indices):
    """Content-dispatch plan for all-accept skills: bare wrong, with-skill
    right, first version."""
    plan, verdicts = {}, {}
    for i in indices:
        plan[f"QMARK{i}"] = {"bare": f"AMARK{i}bare",
                             "skill": [(_skill_text(i), f"AMARK{i}v1")]}
        verdicts[f"AMARK{i}bare"] = incorrect()
        verdicts[f"AMARK{i}v1"] = correct()
    return plan, verdicts


def test_resume_after_fatal_crash_completes_only_pending(tmp_path):
    run_dir = tmp_path / "run"
    store = Store(run_dir)
    first = _seed_marked(store, 0)
    crashed = _seed_marked(store, 1)
    # Unit 1 must not raise before unit 0 has started answering: unit 0 is
    # then in flight when the batch stops, and runs to completion either
    # way, so the interrupted state is deterministic.
    unit0_answering = threading.Event()

    def crash_or_answer(system, user):
        if "QMARK0" in user:
            unit0_answering.set()
            return ("AMARK0bare" if "## Skill rules" not in user
                    else "AMARK0v1")
        assert "QMARK1" in user
        assert unit0_answering.wait(10), "unit 1 ran before unit 0 started"
        raise RuntimeError("worker thread exploded")

    plan, verdicts = _accepting_answers((0, 1))
    ctx, _ = make_ctx(tmp_path, answerer=make_fake_handle("weak",
                                                          crash_or_answer),
                      judge=_judge(verdicts),
                      rewriter=_rewriter({}),
                      auditor=make_fake_handle("auditor", _CLEAN_AUDIT),
                      run_dir=run_dir)
    ctx = replace(ctx, run_settings=RunSettings(workers=4))

    with pytest.raises(RuntimeError):
        stage4_acceptance.run(ctx)

    # The fatal crash recorded no unit failure, wrote no summary, and left
    # the crashed skill pending with nothing stored for its exam.
    assert store.load("skills", first.skill_id, Skill).status == "accepted"
    assert store.load("skills", crashed.skill_id, Skill).status == "pending"
    assert UnitFailureRecorder(run_dir).list_failures() == []
    assert not (run_dir / "stage4_acceptance"
                / "stage4_summary.json").exists()
    crashed_exam = make_exam_id(crashed.subtask_id)
    assert all(store.load("attempts", attempt_id, Attempt).exam_id
               != crashed_exam for attempt_id in store.ids("attempts"))

    # Resume: only the crashed skill is pending, so only its exam is asked
    # (its two answer calls); the finished one makes no model call.
    ctx2, _ = make_ctx(tmp_path, answerer=_answerer(plan),
                       judge=_judge(verdicts), rewriter=_rewriter({}),
                       auditor=make_fake_handle("auditor", _CLEAN_AUDIT),
                       run_dir=run_dir)
    ctx2 = replace(ctx2, run_settings=RunSettings(workers=4))
    summary = stage4_acceptance.run(ctx2)

    prompts = [call["user"]
               for call in ctx2.handle("answerer").client.calls]
    assert len(prompts) == 2 and all("QMARK1" in p for p in prompts)
    assert summary == {"accepted": 2, "discarded": {},
                       "skipped_missing_exam": [], "infra_failed": [],
                       "processed_this_call": 1}

    # The converged store equals an uninterrupted workers=4 run's.
    baseline_dir = tmp_path / "baseline"
    baseline_store = Store(baseline_dir)
    for i in (0, 1):
        _seed_marked(baseline_store, i)
    bctx, _ = make_ctx(tmp_path, answerer=_answerer(plan),
                       judge=_judge(verdicts), rewriter=_rewriter({}),
                       auditor=make_fake_handle("auditor", _CLEAN_AUDIT),
                       run_dir=baseline_dir)
    bctx = replace(bctx, run_settings=RunSettings(workers=4))
    stage4_acceptance.run(bctx)
    assert _snapshot(store) == _snapshot(baseline_store)


# ---------------------------------------------------------------------------
# Dual-mode dispatch (decision doc 2026-10-14): the acceptance key picks the
# protocol, and an exam run must not even import the live machinery
# ---------------------------------------------------------------------------

LIVE_ACCEPTANCE = {"name": "live_run", "n_attempts": 3,
                   "improvement": "strict_rate_gain"}


@pytest.mark.parametrize("acceptance", [
    None, {}, {"name": "exam", "retry_limit": 2}])
def test_exam_configs_run_the_exam_path_not_the_live_driver(
        tmp_path, monkeypatch, acceptance):
    """No slot, an empty slot and an explicit exam slot are all the exam:
    the real K4 path runs (the summary keeps its exam shape) and the live
    driver is never entered."""
    from expedistill.methods.acceptance import live_run as stage4_live_run
    calls = []
    monkeypatch.setattr(
        stage4_live_run, "run",
        lambda ctx, params: calls.append(ctx)
        or pytest.fail("live driver called"))

    store = Store(tmp_path / "run")
    seed_pair(store, "t1", 0, skill_text="skill one")
    answerer = make_fake_handle("weak", ["bare zero", "skilled zero"])
    judge = make_fake_handle("strong", [incorrect(), correct()])
    ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                      rewriter=make_fake_handle("strong", []),
                      auditor=make_fake_handle("auditor", [clean_verdict()]),
                      config=make_config(**(
                          {"methods": {"acceptance": acceptance}}
                          if acceptance is not None else {})))

    summary = stage4_acceptance.run(ctx)

    assert summary == {"accepted": 1, "discarded": {},
                       "skipped_missing_exam": [], "infra_failed": [],
                       "processed_this_call": 1}
    assert calls == []


def test_live_run_config_dispatches_to_the_live_driver(tmp_path, monkeypatch):
    from expedistill.methods.acceptance import live_run as stage4_live_run
    seen = []
    monkeypatch.setattr(
        stage4_live_run, "run",
        lambda ctx, params: seen.append(ctx) or {"mode": "live_run"})

    store = Store(tmp_path / "run")
    seed_pair(store, "t1", 0, skill_text="skill one")
    ctx, _ = make_ctx(tmp_path, answerer=make_fake_handle("weak", []),
                      judge=make_fake_handle("strong", []),
                      rewriter=make_fake_handle("strong", []),
                      config=make_config(
                          n_attempts_weak=3,
                          methods={"acceptance": dict(LIVE_ACCEPTANCE)}))

    summary = stage4_acceptance.run(ctx)

    assert summary == {"mode": "live_run"}
    assert seen == [ctx]


_EXAM_RUN_SUBPROCESS = """
import json, sys, tempfile
from pathlib import Path

from expedistill.core.config import ExperimentConfig
from expedistill.core.ids import (
    make_exam_id, make_skill_id, make_subtask_id, make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Exam, Skill
from expedistill.infra.store import Store
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.acceptance import exam as exam_driver
from expedistill.stages import stage4_acceptance
from dataclasses import replace
from expedistill.stages.context import RunSettings, StageContext

tmp = Path(tempfile.mkdtemp())
run_dir = tmp / "run"
store = Store(run_dir)
subtask_id = make_subtask_id(
    make_trajectory_id(make_task_id("tb", "t1"), "strong", 0), 0)
skill = Skill(skill_id=make_skill_id(subtask_id), content="a skill",
              subtask_id=subtask_id, source_subtasks=[subtask_id])
store.save(skill, "skills")
exam = Exam(exam_id=make_exam_id(subtask_id), question_text="the question",
            reference_answer="the answer", subtask_id=subtask_id,
            skill_id=make_skill_id(subtask_id))
store.save(exam, "exams")
config = ExperimentConfig(benchmark="tb", mode="full",
                          models={"executor_strong": "s",
                                  "executor_weak": "w"},
                          weak_profile="a weak model",
                          methods={"acceptance": {"name": "exam",
                                                  "retry_limit": 1}})
ctx = StageContext(
    run_dir=run_dir, store=store, config=config, usage=None,
    handles={"answerer": make_fake_handle("weak", ["bare", "skilled"]),
             "judge": make_fake_handle("strong", [
                 json_script({"verdict": "incorrect",
                              "error_description": "missed the step"}),
                 json_script({"verdict": "correct",
                              "error_description": ""})]),
             "rewriter": make_fake_handle("strong", []),
             "auditor": make_fake_handle(
                 "auditor", [json_script({"leaked": False,
                                          "reason": "generic advice"})])},
    workdir_root=tmp / "work")
summary = stage4_acceptance.run(ctx)
live_driver_loaded = "expedistill.methods.acceptance.live_run" in sys.modules
print(json.dumps({"summary": summary,
                  "live_driver_loaded": live_driver_loaded}))
"""


def test_an_exam_run_never_imports_the_live_modules(tmp_path):
    """The lazy-import seam (decision section 2.1): driven end to end with
    an exam config, a FRESH interpreter's sys.modules holds neither the
    live driver nor its blocks nor their prompts -- the import graph an
    in-flight exam run resumes under is provably unchanged. A subprocess
    because the shared test session imports K6 (and through it
    blocks/prompts.attribution) for other tests."""
    import subprocess
    import sys

    store = Store(tmp_path / "run")
    seed_pair(store, "t1", 0, skill_text="skill one")
    answerer = make_fake_handle("weak", ["bare zero", "skilled zero"])
    judge = make_fake_handle("strong", [incorrect(), correct()])

    # The in-process control: the exam run works and the live driver, if
    # absent from sys.modules, stays absent (the subprocess is the real
    # assertion; this one catches a module-level import sneaking in).
    import expedistill.methods.acceptance.live_run  # noqa: F401 - loading it IS the point
    sys.modules.pop("expedistill.methods.acceptance.live_run")
    try:
        ctx, _ = make_ctx(tmp_path, answerer=answerer, judge=judge,
                          rewriter=make_fake_handle("strong", []),
                          auditor=make_fake_handle("auditor",
                                                   [clean_verdict()]))
        stage4_acceptance.run(ctx)
        assert ("expedistill.methods.acceptance.live_run"
                not in sys.modules), "the exam path imported the live driver"
    finally:
        importlib_reload = __import__("importlib").import_module
        importlib_reload("expedistill.methods.acceptance.live_run")

    completed = subprocess.run(
        [sys.executable, "-c", _EXAM_RUN_SUBPROCESS],
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["summary"] == {
        "accepted": 1, "discarded": {}, "skipped_missing_exam": [],
        "infra_failed": [], "processed_this_call": 1}
    # The dispatch's own seam: the live driver is never loaded, end to
    # end, in a fresh interpreter. (The v3 full freeze deliberately puts
    # the two live prompt names in the frozen set -- they register at
    # prompts/__init__ import like every method prompt; the stage 0
    # golden pins that set from the manifest side.)
    assert payload["live_driver_loaded"] is False
