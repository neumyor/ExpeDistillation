"""The execute-phase driver: delivery -> execution -> acceptance.

One call runs ONE position of the frozen phase sequence
(``run(ctx, phase) -> PhaseResult``, the hand-off contract frozen in
core/config.py); the phase's role decides the shape:

* ``baseline``: both bare arms (strong, weak) over the train set,
  stored in the RUN-LEVEL trajectory store;
* ``practice``: the executor (the weak model by default) with the
  previous learn phase's active cards as a skill folder and the evolved
  playbook, over the frozen train or val list; its trajectories land in
  the run-level store too (their ids carry the phase label, so no two
  phases share one) for the next learn phase to read;
* ``test``: the with-bank arm over the test set against its control
  arm (a fresh bare arm inside the phase, or the external campaign's
  score file), full re-run every time.

No execute phase reuses another phase's results: every phase measures
its own units (only a crash resume inside the phase keeps stored units,
K1's own resume model), and a COMPLETED instance replays as itself
(``_finished_summary``, the completion signal every execute phase has).
The final test does not run the redispatch loop; the baseline and the
practice phases do. The acceptance itself
is the trial-level ``answer_sheets/`` + ``coverage.jsonl`` view, never a
persisted unit report.

Wired here:

* the invalid-redispatch loop (the config's ``invalid_redispatch_limit``):
  an attempt whose dispatch died with nothing stored is re-dispatched
  up to the limit. A STORED run without a scorer verdict is graded
  invalid and is not re-run: its trajectory id is fixed per attempt.
  A still-invalid unit after the budget is spent reports ``not_testable`` /
  ``invalid_redispatch_exhausted`` (or ``all_holes`` when every stored
  run came back without a scorer verdict -- the same mapping
  ``unit_summary`` applies). The pass count is durable per unit
  (``redispatch.json``), so a resumed phase continues the same budget,
  and a unit whose budget is spent never re-enters K1.
* unit ids: delivery fills the task ref and the sha8 of the canonical
  injected skills; this driver rebuilds the full frozen key with
  :func:`unit_ids.make_unit_id` (one canonicalization rule, one rebuild
  site).
* the bank: delivery receives the PREVIOUS learn phase's bank artifact
  (:func:`phase_bank.load_phase_bank`), members and versions included;
  before the first learn phase the run-level frozen bank stands in.
* judgment: every real trial is graded through the acceptance module's
  per-trial entry (:func:`verdicts.grade_sheet`) under the phase's
  named rule (the config's ``acceptance`` field, else the benchmark's
  default binding -- ``tb_scorer`` for TB, the rule that consumes the
  ``verifier_ran`` / ``exception_type`` facts).

R4 (the invariant this driver must not break): an execute phase makes
no bank writes; every phase leaves
the bank byte-identical, pinned by tests/unit/stages/test_phase_execute.py.

Phase directory layout (this driver owns the execute-phase half; the
learn-phase half is phase_bank's):

    <run_dir>/phases/<label>/
        phase.json                what ran here (label, index, name,
                                  role), a record for readers
        <arm>/store/              the arm's Trajectory store for the
                                  test phase's two arms (the baseline
                                  and practice phases keep the run-level
                                  store; phase-tagged trajectory ids
                                  never collide there)
        units.jsonl               the resolved delivery rows, full
                                  frozen unit ids attached (rewritten at
                                  the start of every pass of the phase)
        unit_specs.jsonl          one UnitSpec per expected unit (the
                                  pure summarizer's universe)
        answer_sheets/            trial-level AnswerSheet JSON + Markdown
                                  (execution + coverage + grading); the
                                  test phase writes both arms here
        coverage.jsonl            one CoverageRecord per expected trial
                                  (present / missing / terminal + cause)
        <arm>/redispatch.json     per-unit re-dispatch bookkeeping
        <arm>/accepted_infra_shortfall.json   the durable accepted holes
                                  (practice and test; the baseline keeps
                                  its record under stage1_baseline/)
        skill_folder/             the folder delivery's materialized
                                  skill folder (practice, folder test)
        phase_summary.json        the summary also returned as PhaseResult

Resume is disk-only, as everywhere: K1 skips stored units, and the
redispatch and accepted-hole records are read before anything
dispatches.

The package's split:

* ``phase_disk`` -- what is on disk: the phase directory, earlier
  phases, the previous learn phase's bank artifact, the units
  persistence and the skill folder's materialization;
* ``resolve`` -- the phase's raw config mappings resolved in one call
  (``resolve_phase``) into the delivery driver, execution params, the
  playbook, the verdict rule and the redispatch budget;
* ``engine`` -- the real-execution engine shared by the roles
  (plan, dispatch, re-dispatch);
* ``baseline`` / ``practice`` / ``final_test`` -- the three roles, one
  module each.
"""

from expedistill.core.config import (
    PHASE_ROLE_BASELINE,
    PHASE_ROLE_PRACTICE,
    PHASE_ROLE_TEST,
    ExecutePhaseSpec,
    PhaseInstance,
    PhaseResult,
)
from expedistill.infra.reliability import CodeBugError, ConfigError
from expedistill.infra.store import read_json, write_json
from expedistill.stages.context import StageContext

from expedistill.stages.phase_execute.phase_disk import (
    PHASE_RECORD_NAME,
    REDISPATCH_NAME,
    SUMMARY_NAME,
    UNITS_NAME,
    execute_phase_dir,
)
from expedistill.stages.phase_execute.baseline import run_baseline
from expedistill.stages.phase_execute.final_test import run_test
from expedistill.stages.phase_execute.practice import run_practice

__all__ = [
    "PHASE_RECORD_NAME",
    "REDISPATCH_NAME",
    "SUMMARY_NAME",
    "UNITS_NAME",
    "run",
]


def _finished_summary(directory, role: str):
    """The phase's stored summary when its last pass finished EVERYTHING
    (the completion signal of an execute phase), else None.

    The learn phases have had this since Wave 2 (the summary's ``bank``
    entry is their commit point); the execute phases relied on per-role
    replay rules alone, which left a real hole: a COMPLETED phase
    re-entered by a resume re-derives its universe from the CURRENT
    store -- a store its own successors have since mutated -- and can
    plan units that belong to a later measurement round. A finished phase is
    therefore skipped whole: its measurement is frozen at its sequence
    position, exactly like an uninterrupted run's.

    "Finished" is the summary's own account: ``complete is True``. A
    summary naming another role is not a completion signal -- the phase
    re-enters and the audit's phase-dirs check reports the drift.
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
    return summary if summary.get("complete") is True else None


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
    set (a resume retries only the holes, the ``allow_infra_shortfall`` run
    setting accepts them durably).
    """
    spec = phase.spec
    if not isinstance(spec, ExecutePhaseSpec):
        raise ConfigError(
            f"phase_execute cannot run phase {phase.label!r} of kind "
            f"{getattr(spec, 'kind', None)!r}: the learn-phase driver "
            "(stages/phase_learn.py) owns learn phases")
    directory = execute_phase_dir(ctx, phase)
    finished = _finished_summary(directory, spec.role)
    if finished is not None:
        return PhaseResult(label=phase.label, summary=finished)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / PHASE_RECORD_NAME, {
        "label": phase.label, "index": phase.index, "name": phase.name,
        "role": spec.role}, indent=2)
    # No entry-level verdict-rule precheck: each role derives the rule
    # it actually judges under, so an unknown name fails inside its role,
    # at the one derivation point.
    if spec.role == PHASE_ROLE_BASELINE:
        summary = run_baseline(ctx, phase, spec)
    elif spec.role == PHASE_ROLE_PRACTICE:
        summary = run_practice(ctx, phase, spec)
    elif spec.role == PHASE_ROLE_TEST:
        summary = run_test(ctx, phase, spec)
    else:  # parse_phases freezes the role set; a stranger is a bug
        raise CodeBugError(f"unknown execute role {spec.role!r}")
    return PhaseResult(label=phase.label, summary=summary)
