"""The acceptance module's per-run grading core (layer 5, pure functions).

R1 (docs/decisions/execute_learn_repair_plan_20261016.md): one module
answers "did this run make it" for the whole loop.  The answer-sheet path
grades ONE trial at a time (``grade_sheet``) and leaves coverage and unit
aggregation to ``composites/coverage.py`` and
``composites/unit_summary.py``.

The acceptance module system (boundaries doc §2.3 / §3.4 -- everything
that needs a model to judge belongs to acceptance):

* this file: the named per-run verdict rules and ``grade_sheet``;
* ``composites/answer_sheets.py`` / ``coverage.py`` / ``unit_summary.py``:
  the trial-level envelope, coverage and pure unit aggregation.

Where G1's sites stand after this module:

1. ``benchmarks/tb/plugin.py``'s ``classify_result`` -- RETIRED from the
   execution path since R2b: the plugin reports facts (score, errors)
   and this module's named rules draw the conclusion, and
   ``classify_result`` survives only as the archive validator's
   historical lens (its one remaining consumer,
   scripts/analysis/tb_archive_validate.py);
2. ``batch_execute.py``'s infra retry -- W1-B / Wave 2: an invalid run is
   acceptance's verdict; the re-dispatch is the phase driver's.

R4 (acceptance side): nothing in this module writes the skill bank --
no ``discard_skill``, no status commit. Acceptance produces facts; the
actions are the decision module's.
"""

from typing import Any, Mapping, Tuple

from expedistill.core import vocabulary
from expedistill.core.answer_sheet import ExecutionRecord, GradingRecord
from expedistill.core.registry import make_registry
from expedistill.infra.reliability import ConfigError

from expedistill.benchmarks.registry import resolve_plugin

__all__ = [
    "VERDICT_RULES",
    "default_verdict_rule",
    "get_verdict_rule",
    "grade_sheet",
]


# ---------------------------------------------------------------------------
# Named per-run verdict rules (R1: named per benchmark, C11)
# ---------------------------------------------------------------------------

#: The named-rule registry. A rule consumes one trial's execution facts --
#: a mapping with the execution record's ``raw_correctness`` / ``metrics``
#: / ``aux`` -- and returns ``(verdict, basis)``. Benchmark-bound logic (TB's
#: pseudo-zero guard, an exception taxonomy) stays in the benchmark's
#: plugin (boundaries doc §2.3): the plugin reports the facts, the rule
#: draws the conclusion, so swapping a criterion is a new name, never an
#: edit of a registered rule.
VERDICT_RULES = make_registry("verdict rule")


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
    never silently scored as wrong."""
    correctness = facts.get("correctness")
    if correctness is None:
        return vocabulary.RUN_INVALID, ("scorer: no scorer verdict came back for "
                             "this run")
    if correctness:
        return vocabulary.RUN_SUCCESS, "scorer: the benchmark's own scorer"
    return vocabulary.RUN_FAILURE, "scorer: the benchmark's own scorer"


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
        return vocabulary.RUN_INVALID, "external_reward: no campaign reward"
    if reward > 0.0:
        return vocabulary.RUN_SUCCESS, "external_reward: campaign reward"
    return vocabulary.RUN_FAILURE, "external_reward: campaign reward"


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
            return vocabulary.RUN_INVALID, ("tb_scorer: the run died on "
                                 f"{exception_type}")
        if verifier_ran is False:
            return vocabulary.RUN_INVALID, ("tb_scorer: pseudo zero -- a reward "
                                 "exists but the verifier never ran")
        if verifier_ran is None:
            return vocabulary.RUN_INVALID, ("tb_scorer: the verifier's evidence "
                                 "never came back for this run")
        return vocabulary.RUN_INVALID, ("tb_scorer: no scorer verdict came back "
                             "for this run")
    if correctness:
        return vocabulary.RUN_SUCCESS, "tb_scorer: the benchmark's own scorer"
    return vocabulary.RUN_FAILURE, "tb_scorer: the benchmark's own scorer"


def default_verdict_rule(benchmark: str) -> str:
    """The default verdict-rule name of ``benchmark`` (its plugin's
    ``BenchmarkPlugin.verdict_rule``)."""
    return resolve_plugin(benchmark).verdict_rule


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

