"""The acceptance verdict core's unit tests (composites/verdicts.py, R1).

Pins the named per-trial verdict rules and the single grading entry
(``grade_sheet``) -- plus R4's acceptance side: the verdict core never
touches the skill bank.
"""

from pathlib import Path

import pytest

from expedistill.composites import verdicts
from expedistill.core.answer_sheet import ExecutionRecord
from expedistill.infra.reliability import ConfigError
from tests.helpers.plugins import ensure_plugin


# ---------------------------------------------------------------------------
# Named per-run verdict rules
# ---------------------------------------------------------------------------

def _record(**kwargs):
    fields = dict(
        sheet_id="sheet-1", unit_id="u", phase_label="p03:live",
        task_ref="t", role="weak", arm=None, model_role="weak",
        model_name="m", sample_index=0, raw_correctness=None,
        metrics={}, aux={}, artifacts={})
    fields.update(kwargs)
    return ExecutionRecord(**fields)


def _graded(record, rule_name):
    grading = verdicts.grade_sheet(record, rule_name)
    return grading.verdict, grading.basis


def test_scorer_rule_follows_the_benchmark_scorer():
    assert _graded(_record(raw_correctness=True), "scorer") == (
        "success", "scorer: the benchmark's own scorer")
    assert _graded(_record(raw_correctness=False), "scorer") == (
        "failure", "scorer: the benchmark's own scorer")


def test_scorer_rule_judges_an_unscored_run_invalid():
    # A run whose scorer verdict never came back (pseudo zero, a trial
    # that died before its verifier ran) is judged invalid -- no
    # measurement, excluded from every denominator -- never a failure and
    # never a wiring bug.
    assert _graded(_record(), "scorer") == (
        "invalid", "scorer: no scorer verdict came back for this run")


def test_unknown_rule_name_is_a_config_error():
    with pytest.raises(ConfigError, match="unknown verdict rule"):
        verdicts.grade_sheet(_record(raw_correctness=True), "no_such_rule")


def test_rules_are_named_per_benchmark():
    # tb binds tb_scorer since W2-A (wiring list rule 3: the rule that
    # consumes the plugin's verifier_ran / exception_type facts); the
    # other scored benchmarks keep the plain scorer rule.
    for benchmark in ("tb", "searchqa", "alfworld"):
        ensure_plugin(benchmark)
    assert verdicts.default_verdict_rule("tb") == "tb_scorer"
    for benchmark in ("searchqa", "alfworld"):
        assert verdicts.default_verdict_rule(benchmark) == "scorer"
    with pytest.raises(ConfigError, match="no_such_benchmark"):
        verdicts.default_verdict_rule("no_such_benchmark")


def test_a_duplicate_rule_name_is_rejected():
    with pytest.raises(ValueError, match="already registered"):
        verdicts.VERDICT_RULES.register("scorer")(lambda facts: facts)


# ---------------------------------------------------------------------------
# The old run-record entries are gone
# ---------------------------------------------------------------------------

def test_the_old_grading_entries_are_gone():
    # ``grade_sheet`` is the only per-trial grading entry; the R2b bridges
    # and the RunRecord-shaped entry were removed with the answer-sheet
    # refactor.
    assert not hasattr(verdicts, "verdict_from_trajectory")
    assert not hasattr(verdicts, "verdict_from_attempt")
    assert not hasattr(verdicts, "verdict_from_run")


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
