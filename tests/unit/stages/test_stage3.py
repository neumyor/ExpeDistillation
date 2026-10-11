"""Stage 3 tests: fabricated subtasks/trajectories in a tmp store, scripted
llm.fake handles, no network. Covers generation, the per-source mode, the
segment_select rule, step_range slicing, resume (full pairs and half pairs)
and the deterministic iteration order."""

import pytest

from expedistill.benchmarks.registry import plugin_names, register_plugin
from expedistill.core.config import ExperimentConfig
from expedistill.core.ids import (
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Exam, Skill, Step, Subtask, Trajectory
from expedistill.blocks.rules import register_segment_select
from expedistill.infra.reliability import (
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store, read_json, write_json
from expedistill.llm import json_script, make_fake_handle
from expedistill.prompts import get as get_prompt
from expedistill.stages import stage3_generation
from expedistill.stages.context import StageContext

BENCHMARK = "tb"
WEAK_PROFILE = "A small model; known to skip intermediate steps."


@pytest.fixture(autouse=True)
def tb_plugin_registered():
    """Keep the ``tb`` plugin registered for every test in this module.

    Registration normally happens once, at import of the plugin package.
    tests/unit/benchmarks/test_registry.py empties the registry to test it in
    isolation, and a later import of the package is a module-cache no-op, so
    by the time this module runs in the full suite ``tb`` may be gone. The
    stage resolves plugins by import-then-lookup, so re-register here instead
    of making the stage tolerate a wiped registry (a run process never wipes
    it; only the registry's own tests do).
    """
    import importlib

    # First import registers; after a registry reset the import is a no-op,
    # so the name check (and re-registration) must come after it.
    importlib.import_module("expedistill.benchmarks." + BENCHMARK)
    if BENCHMARK not in plugin_names():
        from expedistill.benchmarks.tb import TBPlugin
        register_plugin(TBPlugin())
    yield

# Distinct template openers, read from the B2 registry so a prompt edit does
# not silently break the mode assertion.
DISTILL_LINE = get_prompt("SKILL_DISTILL").splitlines()[0]
DIAGNOSE_LINE = get_prompt("SKILL_DIAGNOSE").splitlines()[0]


def make_config(**overrides):
    defaults = dict(
        benchmark=BENCHMARK,
        mode="full",
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model"},
        weak_profile=WEAK_PROFILE,
        rules={"segment_select": "strong_all_weak_failed"},
    )
    defaults.update(overrides)
    return ExperimentConfig(**defaults)


def make_ctx(tmp_path, *, author, examiner, config=None, run_dir=None):
    run_dir = run_dir or tmp_path / "run"
    store = Store(run_dir)
    ctx = StageContext(
        run_dir=run_dir,
        store=store,
        config=config or make_config(),
        usage=None,
        handles={"author": author, "examiner": examiner},
        workdir_root=tmp_path / "work",
    )
    return ctx, store


def make_trajectory(task, role, attempt, actions):
    task_id = make_task_id(BENCHMARK, task)
    trajectory_id = make_trajectory_id(task_id, role, attempt)
    steps = [Step(action=action, observation=f"obs after {action}")
             for action in actions]
    return Trajectory(
        trajectory_id=trajectory_id,
        task_id=task_id,
        benchmark=BENCHMARK,
        model_role=role,
        model_name=f"{role}-model",
        attempt_index=attempt,
        steps=steps,
        status="failure",
    )


def make_subtask(trajectory, index, source, success, step_range):
    return Subtask(
        subtask_id=make_subtask_id(trajectory.trajectory_id, index),
        task_id=trajectory.task_id,
        trajectory_id=trajectory.trajectory_id,
        source=source,
        trajectory_success=False,
        segment_index=index,
        step_range=step_range,
        label=f"segment {index} of {trajectory.trajectory_id}",
        description="does something concrete",
        success=success,
    )


def seed_store(store):
    """One strong and one weak trajectory, four subtasks over them.

    The D6 rule (strong_all_weak_failed) keeps the strong segments (both), the
    failed weak segment, and drops the weak segment that succeeded.
    """
    strong = make_trajectory("t1", "strong", 0, ["a0", "a1", "a2", "a3"])
    weak = make_trajectory("t1", "weak", 0, ["w0", "w1", "w2", "w3"])
    for trajectory in (strong, weak):
        store.save(trajectory, "trajectories")
    subtasks = [
        make_subtask(strong, 0, "strong", True, (0, 2)),
        make_subtask(strong, 1, "strong", False, (2, 4)),
        make_subtask(weak, 0, "weak", True, (0, 2)),     # dropped by the rule
        make_subtask(weak, 1, "weak", False, (2, 4)),
    ]
    for subtask in subtasks:
        store.save(subtask, "subtasks")
    return subtasks


def exam_script(index):
    return json_script({"question_text": f"question {index}",
                        "reference_answer": f"reference {index}"})


def expected_ids(subtasks):
    """The three kept subtask ids in subtask_id order."""
    kept = [s for s in subtasks if s.source == "strong" or not s.success]
    return sorted(s.subtask_id for s in kept)


def test_generates_skill_and_exam_per_selected_subtask(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              ["SKILL STRONG 0", "SKILL STRONG 1",
                               "SKILL WEAK 1"])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    summary = stage3_generation.run(ctx)

    assert summary["selected"] == expected_ids(subtasks)
    assert summary["generated"] == expected_ids(subtasks)
    assert summary["model_output_failed"] == []
    assert summary["infra_failed"] == []
    assert summary["exam_missing_advanced"] == []
    # The weak segment that succeeded produced nothing: exactly one skill and
    # one exam per kept subtask, none for the dropped one.
    assert len(author.client.calls) == 3
    assert len(examiner.client.calls) == 3
    for subtask_id in expected_ids(subtasks):
        skill = ctx.store.load("skills", make_skill_id(subtask_id), Skill)
        exam = ctx.store.load("exams", make_exam_id(subtask_id), Exam)
        assert skill.subtask_id == subtask_id
        assert skill.status == "pending"
        assert exam.subtask_id == subtask_id
    dropped = [s.subtask_id for s in subtasks if s.source == "weak"
               and s.success][0]
    assert not ctx.store.exists("skills", make_skill_id(dropped))
    # The summary is also on disk, identical to the returned dict.
    stored = read_json(ctx.run_dir / "stage3_generation"
                       / "stage3_summary.json")
    assert stored == summary


def test_mode_follows_subtask_source(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              ["SKILL STRONG 0", "SKILL STRONG 1",
                               "SKILL WEAK 1"])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    stage3_generation.run(ctx)

    # subtask_id order: strong seg00, strong seg01, weak seg01.
    users = [call["user"] for call in author.client.calls]
    assert DISTILL_LINE in users[0]
    assert DISTILL_LINE in users[1]
    assert DIAGNOSE_LINE in users[2]
    # The skill text lands in the stored skill, one per subtask, in order.
    assert ctx.store.load("skills",
                          make_skill_id(subtasks[0].subtask_id),
                          Skill).content == "SKILL STRONG 0"
    assert ctx.store.load("skills",
                          make_skill_id(subtasks[3].subtask_id),
                          Skill).content == "SKILL WEAK 1"


def test_steps_view_slices_step_range(tmp_path):
    seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              ["SKILL STRONG 0", "SKILL STRONG 1",
                               "SKILL WEAK 1"])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    stage3_generation.run(ctx)

    # Call 1 is strong seg01, whose step_range is (2, 4): only actions a2 and
    # a3 appear, and the view renumbers the sliced steps from 0.
    skill_user = author.client.calls[1]["user"]
    exam_user = examiner.client.calls[1]["user"]
    for user in (skill_user, exam_user):
        assert "action: a2" in user
        assert "action: a3" in user
        assert "action: a0" not in user
        assert "action: w" not in user
    assert "step 0\naction: a2" in skill_user
    # Call 0 is strong seg00, range (0, 2): a0/a1 only.
    first_user = author.client.calls[0]["user"]
    assert "action: a0" in first_user
    assert "action: a2" not in first_user


def test_resume_skips_existing_pairs_with_zero_calls(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              ["SKILL STRONG 0", "SKILL STRONG 1",
                               "SKILL WEAK 1"])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)
    stage3_generation.run(ctx)

    # Fresh handles: any resumed call would be recorded here.
    author2 = make_fake_handle("strong", [])
    examiner2 = make_fake_handle("strong", [])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    first = read_json(ctx.run_dir / "stage3_generation"
                      / "stage3_summary.json")
    summary = stage3_generation.run(ctx2)

    assert author2.client.calls == []
    assert examiner2.client.calls == []
    # The summary is cumulative: a re-run reproduces it exactly.
    assert summary == first
    assert summary["generated"] == expected_ids(subtasks)


def test_half_pair_is_regenerated(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              ["SKILL STRONG 0", "SKILL STRONG 1",
                               "SKILL WEAK 1"])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)
    stage3_generation.run(ctx)

    # A crash between the two saves leaves the strong seg01 pair halved.
    halved = subtasks[1].subtask_id
    ctx.store.path_for("exams", make_exam_id(halved)).unlink()

    author2 = make_fake_handle("strong", ["SKILL STRONG 1 REGEN"])
    examiner2 = make_fake_handle("strong", [exam_script(9)])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    summary = stage3_generation.run(ctx2)

    assert summary["generated"] == expected_ids(subtasks)
    # Only the halved pair was regenerated.
    assert len(author2.client.calls) == 1
    assert len(examiner2.client.calls) == 1
    skill = ctx2.store.load("skills", make_skill_id(halved), Skill)
    assert skill.content == "SKILL STRONG 1 REGEN"


def test_iteration_order_is_deterministic(tmp_path):
    # Same seed data twice: identical call sequences and identical summaries.
    summaries = []
    for index in range(2):
        fresh = tmp_path / f"run{index}"
        seed_store(Store(fresh))
        author = make_fake_handle("strong",
                                  ["SKILL STRONG 0", "SKILL STRONG 1",
                                   "SKILL WEAK 1"])
        examiner = make_fake_handle("strong",
                                    [exam_script(0), exam_script(1),
                                     exam_script(2)])
        ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner,
                          run_dir=fresh)
        summaries.append((
            stage3_generation.run(ctx),
            [call["user"] for call in author.client.calls],
        ))

    assert summaries[0] == summaries[1]


def test_uses_exactly_the_rule_named_in_config(tmp_path):
    # A rule that keeps only weak segments: the stage must apply exactly the
    # rule config.rules names, with no fallback of its own.
    @register_segment_select("stage3_test_only_weak")
    def _only_weak(subtask):
        return subtask.source == "weak"

    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong", ["SKILL WEAK 0", "SKILL WEAK 1"])
    examiner = make_fake_handle("strong", [exam_script(0), exam_script(1)])
    ctx, _ = make_ctx(
        tmp_path, author=author, examiner=examiner,
        config=make_config(rules={"segment_select": "stage3_test_only_weak"}))

    summary = stage3_generation.run(ctx)

    weak_ids = sorted(s.subtask_id for s in subtasks if s.source == "weak")
    assert summary["selected"] == weak_ids
    assert summary["generated"] == weak_ids


# ---------------------------------------------------------------------------
# Per-unit failure disposition
# ---------------------------------------------------------------------------

def _summary_on_disk(ctx):
    return read_json(ctx.run_dir / "stage3_generation" / "stage3_summary.json")


def _ledger(ctx):
    return UnitFailureRecorder(ctx.run_dir).list_failures()


def _failing_write_skill(monkeypatch, target_id, exc):
    """Make G2 raise ``exc`` for one subtask, delegate for all others."""
    real = stage3_generation.write_skill

    def flaky(subtask, *args, **kwargs):
        if subtask.subtask_id == target_id:
            raise exc
        return real(subtask, *args, **kwargs)

    monkeypatch.setattr(stage3_generation, "write_skill", flaky)


def test_g2_model_output_error_skips_only_that_unit(monkeypatch, tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    target = subtasks[1].subtask_id  # strong seg01, the middle unit
    _failing_write_skill(monkeypatch, target,
                         ModelOutputError("skill text unusable"))
    author = make_fake_handle("strong", ["SKILL STRONG 0", "SKILL WEAK 1"])
    examiner = make_fake_handle("strong", [exam_script(0), exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    summary = stage3_generation.run(ctx)

    others = [sid for sid in expected_ids(subtasks) if sid != target]
    assert summary["generated"] == others
    assert summary["model_output_failed"] == [target]
    assert summary["infra_failed"] == []
    assert _summary_on_disk(ctx) == summary
    assert not ctx.store.exists("skills", make_skill_id(target))
    assert not ctx.store.exists("exams", make_exam_id(target))
    ledger = _ledger(ctx)
    assert [(r["unit_id"], r["stage"], r["error_class"]) for r in ledger] == [
        (target, "stage3_generation", "ModelOutputError")]


def test_g3_model_output_error_skips_only_that_unit(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    target = subtasks[1].subtask_id
    author = make_fake_handle("strong",
                              ["SKILL STRONG 0", "SKILL STRONG 1",
                               "SKILL WEAK 1"])
    # Valid JSON with an empty field: gen_exam's own contract check raises
    # ModelOutputError for the second unit (no format repair applies).
    bad_exam = json_script({"question_text": "", "reference_answer": "x"})
    examiner = make_fake_handle("strong",
                                [exam_script(0), bad_exam, exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    summary = stage3_generation.run(ctx)

    others = [sid for sid in expected_ids(subtasks) if sid != target]
    assert summary["generated"] == others
    assert summary["model_output_failed"] == [target]
    # The pair is all or nothing: the skill written before G3 failed is not
    # left behind as a half pair.
    assert not ctx.store.exists("skills", make_skill_id(target))
    assert not ctx.store.exists("exams", make_exam_id(target))
    assert [r["unit_id"] for r in _ledger(ctx)] == [target]

    # The terminal record was written for the unit.
    assert [r["subtask_id"] for r in _failures_file(ctx)] == [target]


def test_infra_error_finishes_other_units_then_raises(monkeypatch, tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    target = subtasks[1].subtask_id
    _failing_write_skill(monkeypatch, target,
                         InfrastructureError("endpoint unreachable"))
    author = make_fake_handle("strong", ["SKILL STRONG 0", "SKILL WEAK 1"])
    examiner = make_fake_handle("strong", [exam_script(0), exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    with pytest.raises(InfrastructureError) as info:
        stage3_generation.run(ctx)

    assert "1 unit" in str(info.value)
    assert "resume" in str(info.value)
    others = [sid for sid in expected_ids(subtasks) if sid != target]
    # The summary was written before the raise and names the hole.
    summary = _summary_on_disk(ctx)
    assert summary["generated"] == others
    assert summary["infra_failed"] == [target]
    assert summary["model_output_failed"] == []
    for sid in others:
        assert ctx.store.exists("exams", make_exam_id(sid))
    assert not ctx.store.exists("skills", make_skill_id(target))
    assert [(r["unit_id"], r["error_class"]) for r in _ledger(ctx)] == [
        (target, "InfrastructureError")]

    # Resume (service back): exactly the failed unit is processed.
    monkeypatch.undo()
    author2 = make_fake_handle("strong", ["SKILL STRONG 1"])
    examiner2 = make_fake_handle("strong", [exam_script(1)])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    summary2 = stage3_generation.run(ctx2)

    assert summary2["generated"] == expected_ids(subtasks)
    assert summary2["infra_failed"] == []
    # Infrastructure failures are not terminal: no terminal record.
    assert _failures_file(ctx2) == []
    assert len(author2.client.calls) == 1
    assert len(examiner2.client.calls) == 1


def test_provider_refusal_propagates_immediately(monkeypatch, tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    first = expected_ids(subtasks)[0]
    _failing_write_skill(monkeypatch, first,
                         ProviderRefusalError("unknown model"))
    author = make_fake_handle("strong", [])
    examiner = make_fake_handle("strong", [])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    with pytest.raises(ProviderRefusalError):
        stage3_generation.run(ctx)

    # Stops at the first unit: no further calls, nothing recorded as a unit.
    assert author.client.calls == []
    assert examiner.client.calls == []
    assert _ledger(ctx) == []


def test_advanced_skill_with_missing_exam_is_never_overwritten(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    author = make_fake_handle("strong",
                              ["SKILL STRONG 0", "SKILL STRONG 1",
                               "SKILL WEAK 1"])
    examiner = make_fake_handle("strong",
                                [exam_script(0), exam_script(1),
                                 exam_script(2)])
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)
    stage3_generation.run(ctx)

    # Stage 4 already accepted the strong seg01 skill (a rewrite bumped its
    # version); then its exam file went missing.
    target = subtasks[1].subtask_id
    skill_path = ctx.store.path_for("skills", make_skill_id(target))
    advanced = read_json(skill_path)
    advanced.update(status="accepted", version=2,
                    content="REWRITTEN BY STAGE 4")
    write_json(skill_path, advanced)
    ctx.store.path_for("exams", make_exam_id(target)).unlink()

    author2 = make_fake_handle("strong", [])
    examiner2 = make_fake_handle("strong", [])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    summary = stage3_generation.run(ctx2)

    assert author2.client.calls == []
    assert examiner2.client.calls == []
    kept = ctx2.store.load("skills", make_skill_id(target), Skill)
    assert kept.content == "REWRITTEN BY STAGE 4"
    assert kept.status == "accepted"
    assert summary["generated"] == [sid for sid in expected_ids(subtasks)
                                    if sid != target]
    assert summary["exam_missing_advanced"] == [target]
    assert [(r["unit_id"], r["stage"]) for r in _ledger(ctx2)] == [
        (target, "stage3_generation")]

    # Re-running changes nothing and does not grow the ledger.
    ctx3, _ = make_ctx(tmp_path, author=make_fake_handle("strong", []),
                       examiner=make_fake_handle("strong", []))
    assert stage3_generation.run(ctx3) == summary
    assert len(_ledger(ctx3)) == 1


# ---------------------------------------------------------------------------
# Exhausted model-output repair is terminal within a run directory
# ---------------------------------------------------------------------------

def _g3_failure_handles():
    """Handles for a fresh run where the middle unit's G3 output is bad."""
    author = make_fake_handle("strong",
                              ["SKILL STRONG 0", "SKILL STRONG 1",
                               "SKILL WEAK 1"])
    bad_exam = json_script({"question_text": "", "reference_answer": "x"})
    examiner = make_fake_handle("strong",
                                [exam_script(0), bad_exam, exam_script(2)])
    return author, examiner


def _failures_file(ctx):
    from expedistill.infra.store import read_jsonl
    return read_jsonl(ctx.run_dir / "stage3_generation"
                      / "generation_failures.jsonl")


def test_rerun_of_finished_run_with_terminal_failure_is_a_noop(tmp_path):
    subtasks = seed_store(Store(tmp_path / "run"))
    target = subtasks[1].subtask_id
    author, examiner = _g3_failure_handles()
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)
    first = stage3_generation.run(ctx)
    assert first["model_output_failed"] == [target]

    # The model would now answer well, but the run already decided the unit.
    author2 = make_fake_handle("strong", ["SKILL STRONG 1 NOW GOOD"])
    examiner2 = make_fake_handle("strong", [exam_script(7)])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    second = stage3_generation.run(ctx2)

    assert author2.client.calls == []
    assert examiner2.client.calls == []
    assert second == first
    assert _summary_on_disk(ctx2) == first
    assert not ctx2.store.exists("skills", make_skill_id(target))
    assert len(_ledger(ctx2)) == 1
    assert [r["subtask_id"] for r in _failures_file(ctx2)] == [target]


class _Crash(BaseException):
    """Simulated process death (not an Exception: nothing may catch it)."""


def test_crash_after_failure_record_resumes_identically(monkeypatch,
                                                        tmp_path):
    # Reference: one uninterrupted run.
    seed_store(Store(tmp_path / "ref"))
    author, examiner = _g3_failure_handles()
    ref_ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner,
                          run_dir=tmp_path / "ref")
    reference = stage3_generation.run(ref_ctx)

    # Same run, but the process dies right after the terminal failure record
    # is written, before the ledger row and before moving on.
    seed_store(Store(tmp_path / "run"))
    author, examiner = _g3_failure_handles()
    ctx, _ = make_ctx(tmp_path, author=author, examiner=examiner)

    def crash(self, *args, **kwargs):
        raise _Crash()

    monkeypatch.setattr(UnitFailureRecorder, "record", crash)
    with pytest.raises(_Crash):
        stage3_generation.run(ctx)
    monkeypatch.undo()

    author2 = make_fake_handle("strong", ["SKILL WEAK 1"])
    examiner2 = make_fake_handle("strong", [exam_script(2)])
    ctx2, _ = make_ctx(tmp_path, author=author2, examiner=examiner2)
    resumed = stage3_generation.run(ctx2)

    assert resumed == reference
    # Only the unit after the crash point was generated on resume.
    assert len(author2.client.calls) == 1
    assert len(examiner2.client.calls) == 1

    def rows(c):
        return [(r["unit_id"], r["stage"], r["error_class"])
                for r in _ledger(c)]

    assert rows(ctx2) == rows(ref_ctx)
    assert ([r["subtask_id"] for r in _failures_file(ctx2)]
            == [r["subtask_id"] for r in _failures_file(ref_ctx)])
