"""The acceptance verdict core's unit tests (composites/verdicts.py, R1).

Pins the vocabulary (per-run verdicts, unit statuses, the fixed cause
set), the named per-run verdict rules and the stored-object entry
(``verdict_from_run`` -- R2b deleted the ``verdict_from_trajectory`` /
``verdict_from_attempt`` bridges) -- plus R4's acceptance side: the
verdict core never touches the skill bank.
"""

from pathlib import Path

import pytest

from expedistill.composites import decision
from expedistill.composites import verdicts
from expedistill.core.contracts import RunRecord
from expedistill.infra.reliability import CodeBugError, ConfigError


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

def test_the_r2b_bridges_are_gone():
    # R2b deleted them: every consumer judges through verdict_from_run.
    assert not hasattr(verdicts, "verdict_from_trajectory")
    assert not hasattr(verdicts, "verdict_from_attempt")


# ---------------------------------------------------------------------------
# R4, acceptance side: the verdict core never touches the skill bank
# ---------------------------------------------------------------------------

def test_the_verdict_source_never_calls_a_bank_mutation():
    """R4: acceptance produces facts only -- the verdict core must not
    call the bank-mutation helpers (the actions are the decision
    module's). Checked as CALLS, not mentions: the docstrings name the
    forbidden helpers on purpose."""
    source = Path(verdicts.__file__).read_text(encoding="utf-8")
    assert "discard_skill(" not in source
    assert "accept_skill(" not in source
