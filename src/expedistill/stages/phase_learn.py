"""The learn phase driver (layer 7): decision -> evidence -> author -> curate.

Repair plan R9 item 5 (docs/decisions/execute_learn_repair_plan_20261016.md):
this module replaces stage4_acceptance.py and stage5_curation.py with ONE
driver for the learn half of the loop (boundaries doc
execute_learn_module_boundaries_20261016.md sections 1.2, 2.4-2.7). One call
is one learn phase of the frozen sequence; the per-target landing lives in
``stages/learn_tasks.py`` (the plan's 400-line split rule).

1. **Decision** (section 2.4): the acceptance report of the nearest
   preceding execute phase, the control report that phase's ``compare``
   names, the previous learn phase's bank artifact and the checks left
   after this phase go through ``decision.decide_task_list`` -- one task
   list. The first learn phase after a baseline runs the baseline-gap
   path (current report empty, the D report as the control; the entry
   implements it, this driver only routes).
2. **Evidence and author** (sections 2.5/2.6), **the D4 multi-version
   merge** and the landing semantics are documented on
   ``learn_tasks.execute_rows``; the answer-sheet evidence of an exam
   revise is assembled there too (a disclosed deviation: the evidence
   family has no exam collector yet).
3. **Curation** (section 2.7): every learn phase ends with its own bank
   artifact (R7). The sequence's LAST learn phase runs the configured
   curation (``methods.curation.phase_bank.produce`` -- agentbrew with
   its merge_check, or none); every intermediate learn phase writes the
   as-is artifact (``produce_as_is``). The call sites pass the phase's
   raw ``curate`` mapping per the frozen Wave 2 contract (W2-C's v4
   flip; at the frozen commit those entries still resolve through the
   v3 slots, so the mapping-first form is the known coordination point
   with W2-C's second commit).

**The learn phase touches nothing but the skill store and its own phase
directory.** Execution, judging, redispatch and shortfall are the execute
phases' (R4: an execute phase never changes the bank; the reverse holds
here -- a learn phase never runs a task).

Resume equals an uninterrupted run: the decision re-derives (a pure
function of frozen on-disk reports and the previous phase's artifact),
landed skills are read off the phase's ledgers and skipped, and only
model calls whose results were never persisted are asked again (the
standing K3/K4 exemption). An InfrastructureError is recorded per
target and raised at the phase end AFTER the summary is written but
BEFORE the bank artifact is produced (an incomplete learn phase must
not publish a bank); the resume re-enters and finishes it.

The phase's files all live in ``phases/<label>/``
(composites.phase_bank's layout): the bank artifact and summary from
the curation side, ``task_list.jsonl`` / ``revisions.jsonl`` /
``outcomes.jsonl`` from learn_tasks (the outcomes ledger is the
G10/G11 migration's successor to attributions.jsonl -- Wave 3's audit
scripts and Wave 4's golden regeneration align to that shape), and
``learn_summary.json`` mirroring the returned PhaseResult.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from expedistill.composites import decision
from expedistill.composites.phase_bank import (
    load_phase_bank,
    phase_bank_dir,
)
from expedistill.composites.reports import acceptance_report
from expedistill.core.config import (
    FRESH_BARE,
    PHASE_ROLE_BASELINE,
    PHASE_ROLE_EXAM,
    PHASE_ROLE_LIVE,
    PHASE_ROLE_TEST,
    ExecutePhaseSpec,
    LearnPhaseSpec,
    PhaseInstance,
    PhaseResult,
    phase_label,
)
from expedistill.core.contracts import AcceptanceReportRow
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError, InfrastructureError
from expedistill.infra.store import read_jsonl, write_json
from expedistill.methods.curation import phase_bank as curation_phase_bank
from expedistill.stages import learn_tasks

__all__ = ["run"]

#: The compare rule the decision entry defaults to (decision's own
#: default; named here so the config fallback is explicit at the site).
_DEFAULT_COMPARE_RULE = "strict_rate_gain"

#: The phase's summary file (the returned PhaseResult in JSON form).
SUMMARY_NAME = "learn_summary.json"


def run(ctx: Any, phase: PhaseInstance) -> PhaseResult:
    """Drive one learn phase; return its summary as a PhaseResult."""
    config = ctx.config
    if not isinstance(phase.spec, LearnPhaseSpec):
        raise CodeBugError(
            f"phase_learn received phase {phase.label!r} whose spec is a "
            f"{type(phase.spec).__name__}, not a LearnPhaseSpec")
    if phase.label != phase_label(phase.index, phase.name):
        raise CodeBugError(
            f"phase_learn received label {phase.label!r} for sequence "
            f"entry {phase.index} ({phase.name!r}); the frozen derivation "
            f"is {phase_label(phase.index, phase.name)!r}")

    names, specs = _sequence(config, phase)
    phase_dir = phase_bank_dir(ctx.run_dir, phase.label)
    phase_dir.mkdir(parents=True, exist_ok=True)

    (current_kind, current_rows, control_kind, control_rows,
     compare_rule) = _report_inputs(ctx, names, specs, phase.index)
    current_pos = _current_execute_position(names, specs, phase.index)
    current_spec = specs[names[current_pos]]
    bank = _previous_bank(ctx, names, specs, phase.index)
    checks_left = decision.remaining_checks(
        [specs[name].role for name in names], phase.index)
    task_rows = decision.decide_task_list(
        current_kind=current_kind, current_report=current_rows,
        control_kind=control_kind, control_report=control_rows,
        bank=bank, checks_left=checks_left, compare_rule=compare_rule,
        n_attempts_weak=config.n_attempts_weak,
        task_select_rule=config.rules["task_select"])
    effective, superseded = learn_tasks.merge_task_list(
        task_rows, list(current_rows or ()))
    infra_failed = learn_tasks.execute_rows(
        ctx, phase, effective, current_rows, current_spec,
        _live_round(names, specs, current_pos), superseded)

    summary = _summary(phase, effective, superseded, phase_dir,
                       current_phase=names[current_pos],
                       compare_rule=compare_rule, checks_left=checks_left)
    if infra_failed:
        summary["infra_failed"] = sorted(infra_failed)
        write_json(phase_dir / SUMMARY_NAME, summary, indent=2)
        raise InfrastructureError(
            f"{phase.label}: {len(infra_failed)} task-list target(s) still "
            "pending on infrastructure errors "
            f"({', '.join(sorted(infra_failed))}); see failed_units.jsonl, "
            "then resume the run to retry them")
    summary["bank"] = _produce_bank(ctx, phase, names, specs)
    write_json(phase_dir / SUMMARY_NAME, summary, indent=2)
    return PhaseResult(label=phase.label, summary=summary)


# ---------------------------------------------------------------------------
# The sequence and the reports (the decision entry's inputs)
# ---------------------------------------------------------------------------

def _sequence(config: Any, phase: PhaseInstance) -> Tuple[List[str], dict]:
    """The frozen sequence and its parsed specs off the v4 config.

    Reads ``config.sequence`` / ``config.phases`` -- the products W2-C's
    v4 flip (schema 4) puts on ExperimentConfig. Anything missing is a
    loud CodeBugError naming the coordination point, never an
    AttributeError from the middle of a phase.
    """
    names = getattr(config, "sequence", None)
    specs = getattr(config, "phases", None)
    if (not isinstance(names, list) or not names
            or not isinstance(specs, dict)):
        raise CodeBugError(
            "phase_learn needs the schema-v4 config products "
            "config.sequence (a list of phase names) and config.phases "
            "(name -> parsed spec); got "
            f"sequence={names!r}, phases={specs!r}. These fields land "
            "with W2-C's v4 flip (core/config.py, R9) -- until then the "
            "phase drivers cannot run against a real ExperimentConfig")
    if not 0 <= phase.index < len(names):
        raise CodeBugError(
            f"phase index {phase.index} is outside the sequence of "
            f"{len(names)} phase(s)")
    if names[phase.index] != phase.name:
        raise CodeBugError(
            f"the sequence names {names[phase.index]!r} at index "
            f"{phase.index} but the phase instance says {phase.name!r}")
    missing = [name for name in names if name not in specs]
    if missing:
        raise CodeBugError(
            f"the sequence names phase(s) {missing} with no parsed spec")
    return list(names), specs


def _current_execute_position(names: Sequence[str], specs: dict,
                              index: int) -> int:
    """The position of the nearest preceding EXECUTE phase.

    A learn phase learns from the measurement that just finished. A
    test-role phase is refused here (boundary section 3 rule 9: test
    results never flow into learning) -- the config's checklist already
    discourages the shape, and the loud refusal is the backstop.
    """
    for position in range(index - 1, -1, -1):
        spec = specs[names[position]]
        if not isinstance(spec, ExecutePhaseSpec):
            continue
        if spec.role == PHASE_ROLE_TEST:
            raise CodeBugError(
                f"learn phase at index {index} would follow the "
                f"final-test phase {names[position]!r}: test results do "
                "not flow into learning (boundary section 3 rule 9)")
        return position
    raise CodeBugError(
        f"learn phase at index {index} has no preceding execute phase to "
        "learn from: the sequence is "
        f"{[names[j] for j in range(index)]!r}")


def _report_inputs(ctx: Any, names: Sequence[str], specs: dict,
                   index: int) -> Tuple[Optional[str],
                                        List[AcceptanceReportRow],
                                        Optional[str],
                                        Optional[List[AcceptanceReportRow]],
                                        str]:
    """The decision entry's two reports and its compare rule.

    * the nearest preceding execute phase is the CURRENT measurement
      (a baseline there means no acceptance phase has run since: the
      first-learn baseline-gap path, current report empty, the D report
      as the control);
    * an exam/live phase's ``compare`` names the control: an earlier
      phase's report, or ``fresh_bare`` / no compare -- the phase's own
      no-injection rows, which the decision entry reads out of the
      current report itself (control report None).
    """
    current_pos = _current_execute_position(names, specs, index)
    name = names[current_pos]
    spec = specs[name]
    if spec.role == PHASE_ROLE_BASELINE:
        control_rows = _read_report(ctx, names, specs, current_pos)
        return (None, [], PHASE_ROLE_BASELINE, control_rows,
                _DEFAULT_COMPARE_RULE)
    current_rows = _read_report(ctx, names, specs, current_pos)
    compare = spec.compare
    rule = (compare.rule
            if compare is not None and compare.rule is not None
            else _DEFAULT_COMPARE_RULE)
    if compare is None or compare.against == FRESH_BARE:
        return spec.role, current_rows, None, None, rule
    earlier = [position for position in range(current_pos)
               if names[position] == compare.against]
    if not earlier:
        raise CodeBugError(
            f"phase {name!r} compares against {compare.against!r}, which "
            "never appears before it in the sequence; the config's "
            "load-time checklist should have refused this")
    control_pos = earlier[-1]
    control_spec = specs[compare.against]
    if control_spec.role not in (PHASE_ROLE_BASELINE, PHASE_ROLE_LIVE):
        raise CodeBugError(
            f"the control phase {compare.against!r} has role "
            f"{control_spec.role!r}; a control is a bare measurement "
            "(baseline or live)")
    control_rows = _read_report(ctx, names, specs, control_pos)
    return spec.role, current_rows, control_spec.role, control_rows, rule


def _read_report(ctx: Any, names: Sequence[str], specs: dict,
                 position: int) -> List[AcceptanceReportRow]:
    """One execute phase's acceptance report, read off the run's disk."""
    name = names[position]
    spec = specs[name]
    label = phase_label(position, name)
    if spec.role == PHASE_ROLE_BASELINE:
        return acceptance_report(
            ctx.store, kind="baseline", benchmark=ctx.config.benchmark,
            phase_label=label,
            n_attempts_strong=ctx.config.n_attempts_strong,
            n_attempts_weak=ctx.config.n_attempts_weak)
    if spec.role == PHASE_ROLE_EXAM:
        return acceptance_report(
            ctx.store, kind="exam", benchmark=ctx.config.benchmark,
            phase_label=label)
    if spec.role == PHASE_ROLE_LIVE:
        return acceptance_report(
            ctx.store, kind="live", benchmark=ctx.config.benchmark,
            phase_label=label, round_=_live_round(names, specs, position))
    raise CodeBugError(
        f"a report was asked for phase {name!r} of role {spec.role!r}; "
        "only baseline / exam / live phases have one")


def _live_round(names: Sequence[str], specs: dict, position: int) -> int:
    """The round a live phase measured in: its 0-based index among the
    sequence's live phases (the k-th live phase uses round k-1; the
    upgrade-mapped live sequence has exactly two, matching the pinned
    round vocabulary). Coordination point with W2-A's phase_execute."""
    return sum(1 for earlier in range(position)
               if isinstance(specs[names[earlier]], ExecutePhaseSpec)
               and specs[names[earlier]].role == PHASE_ROLE_LIVE)


def _previous_bank(ctx: Any, names: Sequence[str], specs: dict,
                   index: int) -> List[Skill]:
    """The previous learn phase's bank artifact as Skill objects (the
    first learn phase of a sequence has none: the empty bank)."""
    previous = [position for position in range(index)
                if isinstance(specs[names[position]], LearnPhaseSpec)]
    if not previous:
        return []
    position = previous[-1]
    rows = load_phase_bank(ctx.run_dir,
                           phase_label(position, names[position]))
    return [Skill.from_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Curation: the sequence-position branch (R7 / wiring list item 11)
# ---------------------------------------------------------------------------

def _produce_bank(ctx: Any, phase: PhaseInstance, names: Sequence[str],
                  specs: dict) -> Dict[str, Any]:
    """End the phase with its own bank artifact: the configured
    curation at the sequence's LAST learn phase, the as-is artifact at
    every intermediate one (the budget valve stays either way)."""
    last = not any(
        isinstance(specs[name], LearnPhaseSpec)
        for name in names[phase.index + 1:])
    if last:
        return curation_phase_bank.produce(ctx, phase.spec.curate,
                                           phase.label)
    return curation_phase_bank.produce_as_is(ctx, phase.spec.curate,
                                             phase.label)


# ---------------------------------------------------------------------------
# The summary
# ---------------------------------------------------------------------------

def _summary(phase: PhaseInstance, rows: Sequence[Any],
             superseded: Sequence[str], phase_dir, *,
             current_phase: str, compare_rule: str,
             checks_left: int) -> Dict[str, Any]:
    """The phase's JSON-safe summary: the effective actions (a revise
    that produced no new version counts as keep -- ``revise_outcome``),
    the landed outcomes read back from the ledger (cumulative across
    resumes), and the decision's provenance."""
    records = _phase_outcomes(phase, phase_dir)
    outcomes: Dict[str, int] = {}
    for record in records:
        outcome = record["outcome"]
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    revised_targets = {record["target_id"] for record in records
                       if record["outcome"] == "revised"}
    actions: Dict[str, int] = {}
    for row in rows:
        if row.action == decision.REVISE:
            effective = decision.revise_outcome(
                row, produced_new_version=row.target_id in revised_targets)
        else:
            effective = row
        actions[effective.action] = actions.get(effective.action, 0) + 1
    return {
        "role": phase.spec.role,
        "current_phase": current_phase,
        "compare_rule": compare_rule,
        "checks_left": checks_left,
        "task_list": {**actions, "superseded_rows": len(superseded)},
        "outcomes": outcomes,
    }


def _phase_outcomes(phase: PhaseInstance, phase_dir) -> List[dict]:
    """This phase's receipt rows (the outcomes ledger is cumulative:
    a resumed phase's summary covers both halves)."""
    path = phase_dir / learn_tasks.OUTCOMES_NAME
    if not path.exists():
        return []
    return [record for record in read_jsonl(path)
            if record.get("phase_label") == phase.label]
