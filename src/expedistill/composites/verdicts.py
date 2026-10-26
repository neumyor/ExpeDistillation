"""The acceptance module's per-run grading core (layer 5, pure functions).

R1 (docs/decisions/execute_learn_repair_plan_20261016.md): one module
answers "did this run make it" for the whole loop.  The answer-sheet path
grades ONE trial at a time (``grade_sheet``) and leaves coverage and unit
aggregation to ``composites/coverage.py`` and
``composites/unit_summary.py``.

The acceptance module system (boundaries doc §2.3 / §3.4 -- everything
that needs a model to judge belongs to acceptance):

* this file: the named per-run verdict rules and ``grade_sheet``;
* ``composites/exam_grading.py``: the G5 judge under its crash-persistent
  repair budget (entered through :func:`grade_attempt` below);
* ``composites/acceptance_leak.py``: the D8 leak judge and its
  first-verdict ledger;
* ``composites/answer_sheets.py`` / ``coverage.py`` / ``unit_summary.py``:
  the trial-level envelope, coverage and pure unit aggregation.

Where G1's five sites stand after this module (the ones outside this
wave's file ownership converge onto it when their owners land):

1. ``benchmarks/tb/plugin.py``'s ``classify_result`` -- RETIRED from the
   execution path since R2b: the plugin reports facts (score, errors)
   and this module's named rules draw the conclusion, and
   ``classify_result`` survives only as the archive validator's
   historical lens (its one remaining consumer,
   scripts/analysis/tb_archive_validate.py);
2. ``batch_execute.py``'s infra retry -- W1-B / Wave 2: an invalid run is
   acceptance's verdict; the re-dispatch is the phase driver's;
3. ``exam_once``'s judging -- the judge call is routed through
   :func:`grade_attempt` here since R1 (the answer half stays execution);
4. the former ``acceptance.py``'s leak determination --
   ``acceptance_leak.py``, whose verdict maps onto the vocabulary here;
5. the former ``live_acceptance.py``'s round judgment --
   ``live_verdicts.py`` keeps K6's frozen verdicts-ledger protocol (its
   rows are pinned) for the execute-phase driver's live role.

R4 (acceptance side): nothing in this module writes the skill bank --
no ``discard_skill``, no status commit. Acceptance produces facts; the
actions are the decision module's.
"""

from typing import Any, Mapping, Tuple

from expedistill.core import vocabulary
from expedistill.core.answer_sheet import ExecutionRecord, GradingRecord
from expedistill.core.contracts import RunRecord
from expedistill.core.registry import make_registry
from expedistill.infra.reliability import CodeBugError, ConfigError

# Same layer (5).
from expedistill.composites import exam_grading

# Re-exported for methods/delivery/units.py, which still reads the unit-id
# rule through this module; the definitions live in unit_ids.
from expedistill.composites.unit_ids import canonical_skills, make_unit_id

__all__ = [
    "BENCHMARK_VERDICT_RULES",
    "CAUSE_ALL_HOLES",
    "CAUSE_EXAM_GENERATION_FAILED",
    "CAUSE_INJECTION_OVERFLOW",
    "CAUSE_INVALID_REDISPATCH_EXHAUSTED",
    "CAUSE_LEAK",
    "CAUSE_WEAK_ALREADY_KNOWS",
    "CAUSE_ZERO_EXECUTION",
    "NOT_TESTABLE_CAUSES",
    "RUN_FAILURE",
    "RUN_INVALID",
    "RUN_SUCCESS",
    "RUN_VERDICTS",
    "STATUS_NORMAL",
    "STATUS_NOT_RUN_CONTROL_PERFECT",
    "STATUS_NOT_TESTABLE",
    "UNIT_STATUSES",
    "canonical_skills",
    "default_verdict_rule",
    "get_verdict_rule",
    "grade_attempt",
    "grade_sheet",
    "make_unit_id",
    "register_verdict_rule",
    "verdict_from_run",
]


# ---------------------------------------------------------------------------
# Vocabulary: per-run verdicts, unit statuses, causes
# ---------------------------------------------------------------------------

#: One run's verdict. ``invalid`` means the run did not complete normally
#: and is none of the model's business (sandbox, network, leak, ...);
#: invalid runs are excluded from every denominator.
RUN_SUCCESS = vocabulary.RUN_SUCCESS
RUN_FAILURE = vocabulary.RUN_FAILURE
RUN_INVALID = vocabulary.RUN_INVALID
RUN_VERDICTS = vocabulary.RUN_VERDICTS

#: A unit's status (UnitSummary.status). A normal unit's score is
#: its counts alone; ``not_testable`` means no valid run exists and the
#: cause says why; ``not_run_control_perfect`` means the control was
#: already perfect so the with-skill run was skipped ("no headroom").
#: Defined once in composites.decision (core/contracts.py names that
#: module as the status/cause definition site, next to the discard
#: vocabulary) and re-exported under the acceptance-side names, so the
#: two modules cannot drift (D1; the re-exports are pinned by test).
STATUS_NORMAL = vocabulary.STATUS_NORMAL
STATUS_NOT_TESTABLE = vocabulary.STATUS_NOT_TESTABLE
STATUS_NOT_RUN_CONTROL_PERFECT = vocabulary.STATUS_NOT_RUN_CONTROL_PERFECT
UNIT_STATUSES = vocabulary.UNIT_STATUSES

#: Why a unit is ``not_testable`` (the fixed cause set, R0): the same
#: single definition as above, re-exported. These values are the ONLY
#: legal causes on a not_testable row.
CAUSE_LEAK = vocabulary.CAUSE_LEAK
CAUSE_INJECTION_OVERFLOW = vocabulary.CAUSE_INJECTION_OVERFLOW
CAUSE_EXAM_GENERATION_FAILED = vocabulary.CAUSE_EXAM_GENERATION_FAILED
CAUSE_INVALID_REDISPATCH_EXHAUSTED = \
    vocabulary.CAUSE_INVALID_REDISPATCH_EXHAUSTED
CAUSE_ALL_HOLES = vocabulary.CAUSE_ALL_HOLES
CAUSE_ZERO_EXECUTION = vocabulary.CAUSE_ZERO_EXECUTION
NOT_TESTABLE_CAUSES = vocabulary.NOT_TESTABLE_CAUSES

#: The cause on a ``not_run_control_perfect`` row: the baseline was
#: all-success under a strict-family rule, so the with-skill run was
#: skipped with zero executions. The same string (and fact) as decision's
#: ``weak_already_knows`` discard reason -- one definition, two
#: vocabularies (boundaries doc §4.1's "no headroom" conclusion).
CAUSE_WEAK_ALREADY_KNOWS = vocabulary.CAUSE_WEAK_ALREADY_KNOWS


# ---------------------------------------------------------------------------
# Named per-run verdict rules (R1: named per benchmark, C11)
# ---------------------------------------------------------------------------

#: The named-rule registry. A rule consumes one run's reported facts -- a
#: mapping with the RunRecord's ``correctness`` / ``metrics`` / ``aux``
#: -- and returns ``(verdict, basis)``. Benchmark-bound logic (TB's
#: pseudo-zero guard, an exception taxonomy) stays in the benchmark's
#: plugin (boundaries doc §2.3): the plugin reports the facts, the rule
#: draws the conclusion, so swapping a criterion is a new name, never an
#: edit of a registered rule.
VERDICT_RULES = make_registry("verdict rule")


def register_verdict_rule(name: str):
    """Decorator filing a verdict rule under ``name``; duplicates raise."""
    return VERDICT_RULES.register(name)


def get_verdict_rule(name: str):
    """Return the verdict rule registered under ``name``."""
    try:
        return VERDICT_RULES.get(name)
    except KeyError as exc:
        # The listing is already in the registry's KeyError message.
        raise ConfigError(str(exc)) from None


@VERDICT_RULES.register("scorer")
def _scorer_rule(facts: Mapping[str, Any]) -> Tuple[str, str]:
    """The benchmark's own scorer decided: correctness True -> success,
    False -> failure. ``None`` is an invalid run, not a failure and not a
    wiring bug -- since R2b it is the honest per-run value "the run came
    back with no scorer verdict" (a pseudo zero, a trial that died before
    its verifier ran), and the run is excluded from every denominator,
    never silently scored as wrong. A benchmark with NO scorer at all is
    a different situation and is caught one level up: its default binding
    is a model-judging rule (``exam_judge``), not this one."""
    correctness = facts.get("correctness")
    if correctness is None:
        return RUN_INVALID, ("scorer: no scorer verdict came back for "
                             "this run")
    if correctness:
        return RUN_SUCCESS, "scorer: the benchmark's own scorer"
    return RUN_FAILURE, "scorer: the benchmark's own scorer"


@VERDICT_RULES.register("external_reward")
def _external_reward_rule(facts: Mapping[str, Any]) -> Tuple[str, str]:
    """One externally supplied campaign reward (0/1) as a trial verdict.

    There is no local run or verifier evidence; the rule exists so the
    grading basis names the external provenance instead of pretending the
    benchmark scorer produced it.
    """
    metrics = facts.get("metrics") or {}
    reward = metrics.get("reward")
    if reward is None:
        return RUN_INVALID, "external_reward: no campaign reward"
    if reward > 0.0:
        return RUN_SUCCESS, "external_reward: campaign reward"
    return RUN_FAILURE, "external_reward: campaign reward"


@VERDICT_RULES.register("exam_judge")
def _exam_judge_rule(facts: Mapping[str, Any]) -> Tuple[str, str]:
    """The G5 model judge decided (the exam benchmark: the question is
    generated on the fly, so there is no scorer -- the judge's verdict
    travels the run's ``aux`` from execution to acceptance, with the
    judge's own reason beside it as the basis text)."""
    aux = facts.get("aux") or {}
    verdict = aux.get("judge_verdict")
    if verdict is None:
        raise CodeBugError(
            "the exam_judge rule judged a run with no judge verdict in "
            "aux: an unjudged attempt is in-flight work, never a "
            "reported run")
    basis = "exam_judge: " + (aux.get("judge_reason") or "the G5 model judge")
    if verdict:
        return RUN_SUCCESS, basis
    return RUN_FAILURE, basis


@VERDICT_RULES.register("tb_scorer")
def _tb_scorer_rule(facts: Mapping[str, Any]) -> Tuple[str, str]:
    """The scorer rule over the facts TB's plugin reports: the same
    verdicts as ``scorer`` with the run's own
    provenance named in the basis, so a ruled-invalid run says WHICH
    fact killed it -- a pseudo zero (a reward whose verifier never ran,
    ``verifier_ran`` False), a run whose verifier evidence never came
    back (``verifier_ran`` None -- the log was not collected), a run
    that died on a model output error (``exception_type``), or no
    scorer verdict at all.

    The rule consumes generic fact KEYS, never a benchmark branch
    (AGENTS section 4 rule 3: benchmark-bound logic lives in the plugin,
    which is where these facts come from; other benchmarks simply never
    report them and keep the ``scorer`` binding)."""
    correctness = facts.get("correctness")
    if correctness is None:
        metrics = facts.get("metrics") or {}
        exception_type = metrics.get("exception_type")
        verifier_ran = metrics.get("verifier_ran")
        if exception_type:
            return RUN_INVALID, ("tb_scorer: the run died on "
                                 f"{exception_type}")
        if verifier_ran is False:
            return RUN_INVALID, ("tb_scorer: pseudo zero -- a reward "
                                 "exists but the verifier never ran")
        if verifier_ran is None:
            return RUN_INVALID, ("tb_scorer: the verifier's evidence "
                                 "never came back for this run")
        return RUN_INVALID, ("tb_scorer: no scorer verdict came back "
                             "for this run")
    if correctness:
        return RUN_SUCCESS, "tb_scorer: the benchmark's own scorer"
    return RUN_FAILURE, "tb_scorer: the benchmark's own scorer"


#: The default named rule per benchmark (R1: the verdict rules are named
#: per benchmark; C11 lets the config name one explicitly, this table is
#: the default binding). Every benchmark with a scorer scores through
#: the scorer rule; TB's binding consumes its plugin's provenance
#: facts (``tb_scorer``); the exam benchmark (questions generated on
#: the fly) is judged by the model judge.
BENCHMARK_VERDICT_RULES = {
    "tb": "tb_scorer",
    "searchqa": "scorer",
    "alfworld": "scorer",
    "exam": "exam_judge",
}


def default_verdict_rule(benchmark: str) -> str:
    """The default verdict-rule name of ``benchmark``."""
    try:
        return BENCHMARK_VERDICT_RULES[benchmark]
    except KeyError:
        known = ", ".join(sorted(BENCHMARK_VERDICT_RULES))
        raise ConfigError(
            f"no default verdict rule for benchmark {benchmark!r}; "
            f"known: {known}") from None


def verdict_from_run(record: RunRecord, *,
                     rule_name: str) -> Tuple[str, str]:
    """One run's verdict under the named rule: the frozen-contract entry
    point (execution hands over RunRecords, acceptance judges them
    here). The pre-R2b bridges ``verdict_from_trajectory`` /
    ``verdict_from_attempt`` are gone: a stored Trajectory maps onto a
    RunRecord (its ``meta`` is the run metrics; an exam Attempt's judge
    verdict travels ``aux``) and every consumer judges through this
    entry."""
    rule = get_verdict_rule(rule_name)
    return rule({"correctness": record.correctness,
                 "metrics": record.metrics,
                 "aux": record.aux})


def grade_sheet(execution: ExecutionRecord,
                rule_name: str) -> GradingRecord:
    """Grade ONE trial-level execution record under a named rule.

    This is the only acceptance entry in the answer-sheet world: it reads
    the execution facts (raw correctness, run metrics, aux) and returns a
    trial-level ``GradingRecord``.  It does not read coverage, unit specs,
    controls or the skill bank.
    """
    rule = get_verdict_rule(rule_name)
    verdict, basis = rule({
        "correctness": execution.raw_correctness,
        "metrics": execution.metrics,
        "aux": execution.aux,
    })
    return GradingRecord(verdict=verdict, basis=basis, rule_name=rule_name)


# ---------------------------------------------------------------------------
# Grading (the model judge): acceptance's entry, exam_grading's budget
# ---------------------------------------------------------------------------

def grade_attempt(*, store, exam, attempt, judge_handle,
                  usage=None, run_dir=None) -> Tuple[bool, str]:
    """Judge one answered exam attempt: the G5 model judge under
    ``exam_grading``'s crash-persistent repair budget (a resumed call
    continues the remaining tries and raises without a model call when
    none are left). Acceptance owns grading (boundaries doc §3.4), so
    this is the entry execution-side wiring (``exam_once``) calls; the
    budget protocol itself lives in ``exam_grading``."""
    return exam_grading.judge_with_persistent_budget(
        store, exam, attempt, judge_handle, usage, run_dir)
