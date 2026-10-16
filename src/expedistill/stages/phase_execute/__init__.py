"""The execute-phase driver: delivery -> execution -> acceptance.

One call runs ONE position of the frozen phase sequence
(``run(ctx, phase) -> PhaseResult``, the hand-off contract frozen in
core/config.py); the phase's role decides the shape:

* ``baseline`` (D): both bare arms (strong, weak) over the train set,
  stored in the RUN-LEVEL trajectory store -- every frozen consumer of
  a bare baseline (live baseline loading, the report reader, the C
  phase's reused control, the audit tooling) reads them there;
* ``exam`` (A): one exam per pending bank skill -- the exam is
  generated at execution time, answered bare, audited for leaks, then
  answered with the skill injected;
* ``live`` (B): one unit per task whose bank group is pending, the
  whole group injected, ``n_attempts`` real executions, judged through
  the frozen live verdicts-ledger protocol -- the k-th live phase of a
  sequence measures round ``k-1``, and the rounds are pinned to {0, 1},
  so a sequence carries at most two;
* ``test`` (C): the with-bank arm over the test set against its control
  arm (the baseline phase's weak bare trajectories, or a fresh bare
  arm inside the phase), full re-run every time.

The internal order is frozen (the boundaries doc, section 4.3 rule 3):
control units run first and are accepted first; then the decision
module's :func:`decision.control_perfect_skips_run` decides, per unit,
whether the with-skill run is skipped (an all-success control under a
strict-family compare rule cannot be improved on); only then do the
with-skill units run and get accepted. On the live role the same fact
takes the ledger's shape: the entry guard short-circuits the group
through ``judge_round``'s zero-execution ``weak_already_knows`` terminal.
The final test C calls neither the skip logic nor the redispatch loop:
it re-runs everything on every call of ITS OWN phase instance (rule 1's
own exception -- only a crash resume inside the phase keeps stored
units, K1's own resume model); a COMPLETED instance replays as itself
(``_finished_summary``, the completion signal every execute phase now
has) and nothing is ever reused ACROSS C phases.

The frozen companions, wired here:

* rule 1 (unchanged units reuse the last acceptance): the resolved
  units and the acceptance report are persisted per phase
  (``units.jsonl`` / ``acceptance_report.jsonl`` inside the phase
  directory) and :func:`decision.reusable_units` joins the new units
  against every earlier execute phase, most recent first -- a reused
  unit is not re-executed and its new row is the previous row's
  verdicts under the new unit id. A ``zero_execution`` row is a
  visibility fact (work still owed), never an acceptance result, so it
  is never reused. On the live role this is also the no-op guard's
  phase-world shape: a revision that produced no new version leaves the
  block byte-identical, so the next live phase reuses the last verdict
  instead of re-measuring the same injection.
* the invalid-redispatch loop (the config's ``invalid_redispatch_limit``):
  a unit whose runs are all invalid (or whose dispatch died with
  nothing stored) is re-dispatched up to the limit; a still-invalid
  unit after the budget is spent reports ``not_testable`` /
  ``invalid_redispatch_exhausted`` (or ``all_holes`` when every stored
  run came back without a scorer verdict -- the same mapping the frozen
  report reader applies). The pass count is durable per unit
  (``redispatch.json``), so a resumed phase continues the same budget,
  and a unit whose budget is spent never re-enters K1.
* unit ids: delivery fills the task ref and the sha8 of the canonical
  injected skills; this driver rebuilds the full frozen key with
  :func:`verdicts.make_unit_id` (one canonicalization rule, one rebuild
  site).
* the bank: delivery receives the PREVIOUS learn phase's bank artifact
  (:func:`phase_bank.load_phase_bank`), members and versions included;
  before the first learn phase the run-level frozen bank stands in.
  (The live group view keeps its store derivation -- the same one the
  frozen audit line and the pending universe are built from, so the
  ledger and the delivery cannot disagree about what a group is.)
* judgment: every real run is judged through the acceptance module's
  frozen entry (:func:`verdicts.verdict_from_run`) under the phase's
  named rule (the config's ``acceptance`` field, else the benchmark's
  default binding -- ``tb_scorer`` for TB, the rule that consumes the
  ``verifier_ran`` / ``exception_type`` facts). The live round judgment
  stays the verdicts ledger's (``judge_round``): its row IS the resume
  protocol, and ``finalize_group`` is the ledger protocol's commit step
  -- the phase driver keeps calling both (the coordinator's 2026-10-16
  ruling; folding the verdict protocol into acceptance is a later
  optional consolidation, not this wave).

R4 (the invariant this driver must not break): an execute phase makes
no DIRECT bank calls -- no ``discard_skill`` / ``accept_skill`` anywhere
in it. The two sanctioned exceptions are the frozen protocols' own
commits: the live finalization sweep (the ledger protocol's commit,
above) and nothing else; every other phase leaves the bank
byte-identical, pinned by tests/unit/test_w2a_phase_execute.py.

Phase directory layout (this driver owns the execute-phase half; the
learn-phase half is phase_bank's):

    <run_dir>/phases/<label>/
        phase.json                what ran here (label, index, role) --
                                  the disk fact later phases derive from
        <arm>/store/              the arm's Trajectory store for the
                                  phases whose trajectories must NOT land
                                  in the run-level store (the C phase's
                                  two arms; a fresh-bare live control).
                                  The D phase's bare arms and the live
                                  rounds keep their frozen historical
                                  locations (the run-level store and
                                  ``live_run/roundNN``): every frozen
                                  consumer reads them there, and the
                                  run-level store holds ONLY bare runs,
                                  so no id ever collides.
        units.jsonl               the resolved delivery rows, full
                                  frozen unit ids attached (rewritten at
                                  the start of every pass of the phase;
                                  the exam role reads its pre-overwrite
                                  content as the same-phase replay
                                  marker)
        acceptance_report.jsonl   the phase's report rows (not the C
                                  phase: its numbers go to the summary
                                  alone, the boundaries doc's rule that
                                  test results never flow into learning)
        redispatch.json           per-unit re-dispatch bookkeeping
        <arm>/accepted_infra_shortfall.json   the durable accepted holes
        generation_failures.jsonl (exam phases) terminal exam-generation
                                  failures, first row per exam wins
        phase_summary.json        the summary also returned as PhaseResult

Resume is disk-only, as everywhere: K1 skips stored units, exam
answering reuses its unjudged pending attempt (and a REPLAYED exam
phase -- same phase instance, an earlier pass already planned the unit --
skips an answer whose attempt is already judged: a finished measurement
is never re-answered), judge_round re-derives and byte-compares its
stored row, and the redispatch and accepted-hole records are read
before anything dispatches.

The package's split (the plan's 400-line rule; the Wave 2 review's
blocking finding):

* ``phase_disk`` -- what is on disk: the phase directory, earlier
  phases, the previous learn phase's bank artifact, the units / report
  persistence and rule 1's reuse map;
* ``resolve`` -- the phase's raw config mappings resolved into delivery
  drivers, execution params, verdict / compare rules and the
  redispatch budget;
* ``engine`` -- the real-execution engine shared by the roles that
  dispatch K1 (plan, dispatch, judge, re-dispatch);
* ``baseline`` / ``exam`` / ``live`` / ``final_test`` -- the four
  roles, one module each.
"""

from expedistill.composites.batch_execute import batch_execute
from expedistill.core.config import (
    PHASE_ROLE_BASELINE,
    PHASE_ROLE_EXAM,
    PHASE_ROLE_LIVE,
    PHASE_ROLE_TEST,
    ExecutePhaseSpec,
    PhaseInstance,
    PhaseResult,
)
from expedistill.infra.reliability import CodeBugError, ConfigError
from expedistill.infra.store import read_json, write_json
from expedistill.stages.context import StageContext

from expedistill.stages.phase_execute.phase_disk import (
    GENERATION_FAILURES_NAME,
    PHASE_RECORD_NAME,
    REDISPATCH_NAME,
    REPORT_NAME,
    SUMMARY_NAME,
    UNITS_NAME,
    _phase_dir,
)
from expedistill.stages.phase_execute.baseline import _run_baseline
from expedistill.stages.phase_execute.exam import _run_exam
from expedistill.stages.phase_execute.final_test import _run_test
from expedistill.stages.phase_execute.live import _run_live

__all__ = [
    "GENERATION_FAILURES_NAME",
    "PHASE_RECORD_NAME",
    "REDISPATCH_NAME",
    "REPORT_NAME",
    "SUMMARY_NAME",
    "UNITS_NAME",
    "batch_execute",
    "run",
]

# ``batch_execute`` is re-exported on purpose: the tests wrap the
# package-level name as their K1 seam (tests/unit/test_w2a_phase_execute
# .py's _pin_k1), and every dispatch inside this package goes through
# this attribute at call time (engine.py / live.py).


def _finished_summary(directory, role: str):
    """The phase's stored summary when its last pass finished EVERYTHING
    (the completion signal of an execute phase), else None.

    The learn phases have had this since Wave 2 (the summary's ``bank``
    entry is their commit point); the execute phases relied on per-role
    replay rules alone, which left a real hole: a COMPLETED phase
    re-entered by a resume re-derives its universe from the CURRENT
    store -- a store its own successors have since mutated -- and can
    plan units that belong to a later measurement round (the golden
    matrix's revision-window crash caught the live role hijacking its
    successor's round through rule-1 reuse). A finished phase is
    therefore skipped whole: its measurement is frozen at its sequence
    position, exactly like an uninterrupted run's.

    "Finished" is the summary's own account: ``complete is True`` for
    the baseline / live / test roles, an empty ``infra_failed`` list for
    the exam role (its summary carries no ``complete`` key; the driver
    writes it and then raises when units are still pending). A summary
    naming another role is not a completion signal -- the phase re-enters
    and the audit's phase-dirs check reports the drift.
    """
    path = directory / SUMMARY_NAME
    if not path.exists():
        return None
    try:
        summary = read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(summary, dict) or summary.get("role") != role:
        return None
    if role == PHASE_ROLE_EXAM:
        done = not summary.get("infra_failed")
    else:
        done = summary.get("complete") is True
    return summary if done else None


def run(ctx: StageContext, phase: PhaseInstance) -> PhaseResult:
    """Run one execute-phase position; returns its summary as PhaseResult.

    A phase whose last pass completed everything is skipped whole (its
    stored summary returned as-is): a resume replays the sequence from
    the first position, and a finished phase's artifacts must stay the
    bytes its own pass wrote -- re-deriving them would mix a later
    phase's store mutations into an earlier phase's record.

    Raises ConfigError for a learn phase (``stages/phase_learn.py``
    owns those) and InfrastructureError AFTER the summary and report
    are written when units are still missing outside the accepted-hole
    set (the old stages' disposition: a resume retries only the holes,
    the ``allow_infra_shortfall`` run setting accepts them durably).
    """
    spec = phase.spec
    if not isinstance(spec, ExecutePhaseSpec):
        raise ConfigError(
            f"phase_execute cannot run phase {phase.label!r} of kind "
            f"{getattr(spec, 'kind', None)!r}: the learn-phase driver "
            "(stages/phase_learn.py) owns learn phases")
    directory = _phase_dir(ctx, phase)
    finished = _finished_summary(directory, spec.role)
    if finished is not None:
        return PhaseResult(label=phase.label, summary=finished)
    directory.mkdir(parents=True, exist_ok=True)
    # What ran here -- the disk fact later phases derive from (the live
    # round number, the compare targets, the reuse sources).
    write_json(directory / PHASE_RECORD_NAME, {
        "label": phase.label, "index": phase.index, "name": phase.name,
        "role": spec.role}, indent=2)
    # No entry-level verdict-rule precheck: each role derives the rule
    # it actually judges under (baseline and exam re-check theirs before
    # any work; live judges through the compare rule, the test phase
    # does not judge), so an unknown name fails inside its role, at the
    # one derivation point.
    if spec.role == PHASE_ROLE_BASELINE:
        summary = _run_baseline(ctx, phase, spec)
    elif spec.role == PHASE_ROLE_EXAM:
        summary = _run_exam(ctx, phase, spec)
    elif spec.role == PHASE_ROLE_LIVE:
        summary = _run_live(ctx, phase, spec)
    elif spec.role == PHASE_ROLE_TEST:
        summary = _run_test(ctx, phase, spec)
    else:  # parse_phases freezes the role set; a stranger is a bug
        raise CodeBugError(f"unknown execute role {spec.role!r}")
    return PhaseResult(label=phase.label, summary=summary)
