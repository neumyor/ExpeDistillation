"""The acceptance verdict core's unit tests (composites/verdicts.py, R1).

Pins the vocabulary (per-run verdicts, unit statuses, the fixed cause
set), the unit-id rule frozen with acceptance, the named per-run verdict
rules, the stored-object entry (``verdict_from_run`` -- R2b deleted the
``verdict_from_trajectory`` / ``verdict_from_attempt`` bridges), and
``summarize_unit``'s status/cause invariants -- plus R4's acceptance
side: the verdict core and the report reader never touch the skill bank.
"""

from pathlib import Path

import pytest

from expedistill.composites import decision
from expedistill.composites import verdicts
from expedistill.core.contracts import AcceptanceReportRow, RunRecord
from expedistill.core.ids import (
    make_attempt_id,
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Attempt, Exam, Skill, Step, Trajectory
from expedistill.infra.reliability import CodeBugError, ConfigError

BENCHMARK = "tb"


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

def test_run_verdicts_and_unit_statuses_are_pinned():
    assert verdicts.RUN_VERDICTS == ("success", "failure", "invalid")
    assert verdicts.UNIT_STATUSES == ("normal", "not_testable",
                                      "not_run_control_perfect")


def test_not_testable_causes_are_exactly_the_fixed_set():
    assert verdicts.NOT_TESTABLE_CAUSES == frozenset({
        "leak",
        "injection_overflow",
        "exam_generation_failed",
        "invalid_redispatch_exhausted",
        "all_holes",
        "zero_execution",
    })


def test_weak_already_knows_is_decisions_one_definition():
    """The cause on a not_run_control_perfect row is the SAME string (and
    the same fact) as decision's weak_already_knows discard reason: one
    definition shared across the two vocabularies, pinned so a rename in
    either module cannot drift silently."""
    assert verdicts.CAUSE_WEAK_ALREADY_KNOWS == decision.WEAK_ALREADY_KNOWS
    assert verdicts.CAUSE_WEAK_ALREADY_KNOWS == "weak_already_knows"


def test_status_and_cause_sets_are_decisions_one_definition():
    """D1: the status/cause fixed sets are defined ONCE (in decision,
    where core/contracts.py places them) and re-exported under the
    acceptance-side names here; the re-exports are pinned by identity so
    neither module can silently grow a second copy."""
    assert verdicts.NOT_TESTABLE_CAUSES is decision.NOT_TESTABLE_CAUSES
    for name in ("CAUSE_LEAK", "CAUSE_INJECTION_OVERFLOW",
                 "CAUSE_EXAM_GENERATION_FAILED",
                 "CAUSE_INVALID_REDISPATCH_EXHAUSTED", "CAUSE_ALL_HOLES",
                 "CAUSE_ZERO_EXECUTION"):
        assert getattr(verdicts, name) is getattr(decision, name), name
    assert verdicts.STATUS_NORMAL is decision.ROW_NORMAL
    assert verdicts.STATUS_NOT_TESTABLE is decision.ROW_NOT_TESTABLE
    assert (verdicts.STATUS_NOT_RUN_CONTROL_PERFECT
            is decision.ROW_NOT_RUN_CONTROL_PERFECT)


def test_status_cause_for_terminal_covers_todays_terminals():
    assert verdicts.status_cause_for_terminal(
        decision.WEAK_ALREADY_KNOWS) == (
        "not_run_control_perfect", "weak_already_knows")
    assert verdicts.status_cause_for_terminal(
        decision.LIVE_INJECTION_OVERFLOW) == ("not_testable",
                                             "injection_overflow")
    assert verdicts.status_cause_for_terminal(
        decision.LIVE_UNDETERMINED) == ("not_testable", "all_holes")
    assert verdicts.status_cause_for_terminal(
        decision.LEAK_AUDIT) == ("not_testable", "leak")
    with pytest.raises(CodeBugError, match="unknown zero-execution"):
        verdicts.status_cause_for_terminal("no_such_terminal")


# ---------------------------------------------------------------------------
# Unit ids
# ---------------------------------------------------------------------------

def test_unit_ids_separate_every_axis():
    base = dict(phase_label="p0:d", benchmark=BENCHMARK,
                task_ref="tb:t1", role="weak", skills_injected=None)
    unit_id = verdicts.make_unit_id(**base)
    # Deterministic: same inputs, same id.
    assert verdicts.make_unit_id(**base) == unit_id
    # Every axis changes the id.
    assert verdicts.make_unit_id(
        **{**base, "phase_label": "p2:a"}) != unit_id
    assert verdicts.make_unit_id(
        **{**base, "role": "strong"}) != unit_id
    assert verdicts.make_unit_id(
        **{**base, "task_ref": "tb:t2"}) != unit_id
    assert verdicts.make_unit_id(
        **{**base, "skills_injected": []}) != unit_id
    assert verdicts.make_unit_id(
        **{**base, "skills_injected": "bankdigest"}) != unit_id


def test_canonical_skills_keeps_the_raw_shapes_apart():
    assert verdicts.canonical_skills(None) != verdicts.canonical_skills([])
    assert verdicts.canonical_skills(None) != verdicts.canonical_skills(
        "digest")
    assert verdicts.canonical_skills([]) != verdicts.canonical_skills(
        "digest")
    # Key order inside a member cannot change the canonical form (the
    # group's member order is part of its identity and stays as given).
    assert verdicts.canonical_skills(
        [{"skill_id": "a", "version": 1}, {"skill_id": "b", "version": 2}]) \
        == verdicts.canonical_skills(
            [{"version": 1, "skill_id": "a"},
             {"version": 2, "skill_id": "b"}])


# ---------------------------------------------------------------------------
# Named per-run verdict rules
# ---------------------------------------------------------------------------

def _record(**kwargs):
    fields = dict(unit_id="u", task_ref="t", skills_injected=None,
                  skills_block="", model_role="weak", model_name="m",
                  sample_index=0, dispatch_index=0, correctness=None,
                  metrics={}, aux={}, artifacts={})
    fields.update(kwargs)
    return RunRecord(**fields)


def test_scorer_rule_follows_the_benchmark_scorer():
    assert verdicts.verdict_from_run(
        _record(correctness=True), rule_name="scorer") == (
        "success", "scorer: the benchmark's own scorer")
    assert verdicts.verdict_from_run(
        _record(correctness=False), rule_name="scorer") == (
        "failure", "scorer: the benchmark's own scorer")


def test_scorer_rule_judges_an_unscored_run_invalid():
    # R2b: a run whose scorer verdict never came back (pseudo zero, a
    # trial that died before its verifier ran) is judged invalid -- no
    # measurement, excluded from every denominator -- never a failure and
    # never a wiring bug.
    assert verdicts.verdict_from_run(_record(), rule_name="scorer") == (
        "invalid", "scorer: no scorer verdict came back for this run")


def test_exam_judge_rule_reads_the_judges_verdict_and_reason():
    record = _record(aux={"judge_verdict": True, "judge_reason": "matches"})
    assert verdicts.verdict_from_run(record, rule_name="exam_judge") == (
        "success", "exam_judge: matches")
    record = _record(aux={"judge_verdict": False})
    assert verdicts.verdict_from_run(record, rule_name="exam_judge") == (
        "failure", "exam_judge: the G5 model judge")
    with pytest.raises(CodeBugError, match="no judge verdict"):
        verdicts.verdict_from_run(_record(), rule_name="exam_judge")


def test_unknown_rule_name_is_a_config_error():
    with pytest.raises(ConfigError, match="unknown verdict rule"):
        verdicts.verdict_from_run(_record(correctness=True),
                                  rule_name="no_such_rule")


def test_rules_are_named_per_benchmark():
    # tb binds tb_scorer since W2-A (wiring list rule 3: the rule that
    # consumes the plugin's verifier_ran / exception_type facts); the
    # other scored benchmarks keep the plain scorer rule.
    assert verdicts.default_verdict_rule("tb") == "tb_scorer"
    for benchmark in ("searchqa", "alfworld"):
        assert verdicts.default_verdict_rule(benchmark) == "scorer"
    assert verdicts.default_verdict_rule("exam") == "exam_judge"
    with pytest.raises(ConfigError, match="no default verdict rule"):
        verdicts.default_verdict_rule("no_such_benchmark")


def test_a_duplicate_rule_name_is_rejected():
    with pytest.raises(ValueError, match="already registered"):
        verdicts.register_verdict_rule("scorer")(lambda facts: facts)


# ---------------------------------------------------------------------------
# The stored-object entry (the R2b bridges' replacement)
# ---------------------------------------------------------------------------

def _traj(correctness, meta=None):
    task_id = make_task_id(BENCHMARK, "t1")
    return Trajectory(
        trajectory_id=make_trajectory_id(task_id, "weak", 0),
        task_id=task_id, benchmark=BENCHMARK, model_role="weak",
        model_name="m", attempt_index=0,
        steps=[Step(action="do", observation="obs")],
        correctness=correctness, meta=meta or {})


def _attempt(judge_verdict=False, judge_reason="wrong"):
    exam_id = make_exam_id(make_subtask_id(
        make_trajectory_id(make_task_id(BENCHMARK, "t1"), "strong", 0), 1))
    return Attempt(attempt_id=make_attempt_id(exam_id, 0),
                   exam_id=exam_id, purpose="bare",
                   judge_verdict=judge_verdict, judge_reason=judge_reason)


def test_the_r2b_bridges_are_gone():
    # R2b deleted them: every consumer judges through verdict_from_run.
    assert not hasattr(verdicts, "verdict_from_trajectory")
    assert not hasattr(verdicts, "verdict_from_attempt")


def test_reports_judge_stored_objects_through_verdict_from_run():
    from expedistill.composites import reports
    record = reports._run_record_from_trajectory(_traj(True))
    assert verdicts.verdict_from_run(
        record, rule_name="scorer")[0] == "success"
    record = reports._run_record_from_trajectory(_traj(None))
    assert verdicts.verdict_from_run(
        record, rule_name="scorer")[0] == "invalid"
    record = reports._run_record_from_attempt(
        _attempt(judge_verdict=True, judge_reason="matches"))
    assert verdicts.verdict_from_run(
        record, rule_name="exam_judge") == ("success", "exam_judge: matches")


# ---------------------------------------------------------------------------
# summarize_unit: the one row's invariants
# ---------------------------------------------------------------------------

def _runs(*verdicts_):
    return [{"run": f"r{i}", "verdict": verdict, "basis": "b"}
            for i, verdict in enumerate(verdicts_)]


def test_summarize_unit_counts_and_excludes_invalid_runs():
    row = verdicts.summarize_unit(
        unit_id="u", phase_label="p", role="weak", injected_skills=None,
        runs=_runs("success", "failure", "invalid", "success"))
    assert isinstance(row, AcceptanceReportRow)
    assert row.valid_count == 3
    assert row.success_count == 2
    assert row.status == "normal" and row.cause == ""
    # The row carries a copy, not the caller's mutable entries.
    row.runs[0]["verdict"] = "invalid"
    assert row.valid_count == 3


def test_summarize_unit_refuses_a_normal_row_without_valid_runs():
    with pytest.raises(CodeBugError, match="no valid run"):
        verdicts.summarize_unit(
            unit_id="u", phase_label="p", role="weak", injected_skills=None,
            runs=_runs("invalid"))
    with pytest.raises(CodeBugError, match="no valid run"):
        verdicts.summarize_unit(
            unit_id="u", phase_label="p", role="weak", injected_skills=None,
            runs=[])


def test_summarize_unit_refuses_a_cause_on_a_normal_row():
    with pytest.raises(CodeBugError, match="no cause"):
        verdicts.summarize_unit(
            unit_id="u", phase_label="p", role="weak", injected_skills=None,
            runs=_runs("success"), cause=verdicts.CAUSE_LEAK)


def test_summarize_unit_enforces_the_not_testable_contract():
    for cause in verdicts.NOT_TESTABLE_CAUSES:
        row = verdicts.summarize_unit(
            unit_id="u", phase_label="p", role="weak",
            injected_skills=[{"skill_id": "s", "version": 1}],
            runs=_runs("invalid", "invalid"),
            status=verdicts.STATUS_NOT_TESTABLE, cause=cause)
        assert row.valid_count == 0 and row.success_count == 0
    # A cause outside the fixed set, or a valid run, is refused.
    with pytest.raises(CodeBugError, match="fixed set"):
        verdicts.summarize_unit(
            unit_id="u", phase_label="p", role="weak",
            injected_skills=None, runs=[],
            status=verdicts.STATUS_NOT_TESTABLE, cause="made_up")
    with pytest.raises(CodeBugError, match="valid runs"):
        verdicts.summarize_unit(
            unit_id="u", phase_label="p", role="weak",
            injected_skills=None, runs=_runs("success"),
            status=verdicts.STATUS_NOT_TESTABLE,
            cause=verdicts.CAUSE_LEAK)


def test_summarize_unit_enforces_the_control_perfect_contract():
    row = verdicts.summarize_unit(
        unit_id="u", phase_label="p", role="weak",
        injected_skills=[{"skill_id": "s", "version": 1}], runs=[],
        status=verdicts.STATUS_NOT_RUN_CONTROL_PERFECT,
        cause=verdicts.CAUSE_WEAK_ALREADY_KNOWS)
    assert row.runs == [] and row.valid_count == 0
    # Another cause, or a run that happened, is refused.
    with pytest.raises(CodeBugError, match="weak_already_knows"):
        verdicts.summarize_unit(
            unit_id="u", phase_label="p", role="weak",
            injected_skills=None, runs=[],
            status=verdicts.STATUS_NOT_RUN_CONTROL_PERFECT,
            cause=verdicts.CAUSE_ZERO_EXECUTION)
    with pytest.raises(CodeBugError, match="never"):
        verdicts.summarize_unit(
            unit_id="u", phase_label="p", role="weak",
            injected_skills=None, runs=_runs("success"),
            status=verdicts.STATUS_NOT_RUN_CONTROL_PERFECT,
            cause=verdicts.CAUSE_WEAK_ALREADY_KNOWS)


def test_summarize_unit_refuses_unknown_statuses_and_verdicts():
    with pytest.raises(CodeBugError, match="unknown unit status"):
        verdicts.summarize_unit(
            unit_id="u", phase_label="p", role="weak", injected_skills=None,
            runs=_runs("success"), status="nope")
    with pytest.raises(CodeBugError, match="unknown run verdict"):
        verdicts.summarize_unit(
            unit_id="u", phase_label="p", role="weak", injected_skills=None,
            runs=[{"run": "r", "verdict": "nope", "basis": "b"}])
    with pytest.raises(CodeBugError, match="no 'verdict' key"):
        verdicts.summarize_unit(
            unit_id="u", phase_label="p", role="weak", injected_skills=None,
            runs=[{"run": "r", "basis": "b"}])


# ---------------------------------------------------------------------------
# R4, acceptance side: verdicts and the reader never touch the skill bank
# ---------------------------------------------------------------------------

def test_the_verdict_sources_never_call_a_bank_mutation():
    """R4: acceptance produces facts only -- the verdict core and the
    report reader must not call the bank-mutation helpers (the actions
    are the decision module's). Checked as CALLS, not mentions: the
    docstrings name the forbidden helpers on purpose."""
    package = Path(verdicts.__file__).parent
    for name in ("verdicts.py", "reports.py"):
        source = (package / name).read_text(encoding="utf-8")
        assert "discard_skill(" not in source, name
        assert "accept_skill(" not in source, name


def test_the_verdict_core_never_writes_the_bank(tmp_path, monkeypatch):
    """The behavioral half of R4's acceptance side: over a run directory
    that holds pending skills and every kind of measurement state, all
    three report kinds and the summary leave not one skill byte moved --
    and the bank-mutation helper is wired to explode if ever called."""
    from expedistill.composites import reports
    from expedistill.composites import skill_status
    from expedistill.infra.store import Store

    def _explode(*args, **kwargs):
        raise AssertionError(
            "acceptance called discard_skill -- actions are the "
            "decision module's (R4)")

    monkeypatch.setattr(skill_status, "discard_skill", _explode)

    store = Store(tmp_path / "run")
    task_id = make_task_id(BENCHMARK, "t1")
    subtask_id = make_subtask_id(
        make_trajectory_id(task_id, "strong", 0), 1)
    skill = Skill(skill_id=make_skill_id(subtask_id),
                 content="[Steps] 1. restart", subtask_id=subtask_id,
                 source_subtasks=[subtask_id])
    store.save(skill, "skills")
    store.save(Exam(exam_id=make_exam_id(subtask_id),
                    question_text="q", reference_answer="a",
                    subtask_id=subtask_id,
                    skill_id=skill.skill_id), "exams")
    store.save(_traj(False), "trajectories")

    skills_before = [
        (path.name, path.read_bytes())
        for path in Path(store.dir_for("skills")).rglob("*")
        if path.is_file()
    ]
    for kwargs in (
            dict(kind="baseline", benchmark=BENCHMARK, phase_label="p0:d",
                 n_attempts_strong=2, n_attempts_weak=3),
            dict(kind="exam", benchmark=BENCHMARK, phase_label="p2:a"),
            dict(kind="live", benchmark=BENCHMARK, phase_label="p4:b",
                 round_=0),
    ):
        rows = reports.acceptance_report(store, **kwargs)
        assert all(isinstance(row, AcceptanceReportRow) for row in rows)
    assert [
        (path.name, path.read_bytes())
        for path in Path(store.dir_for("skills")).rglob("*")
        if path.is_file()
    ] == skills_before
