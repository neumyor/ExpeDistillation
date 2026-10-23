"""The learn phase driver (layer 7): decision -> evidence -> author -> curate.

Repair plan R9 item 5 (docs/decisions/execute_learn_repair_plan_20261016.md):
this module replaces stage4_acceptance.py and stage5_curation.py with ONE
driver for the learn half of the loop (boundaries doc
execute_learn_module_boundaries_20261016.md sections 1.2, 2.4-2.7). One call
is one learn phase of the frozen sequence; the per-target landing lives in
``stages/learn_add.py / learn_apply.py / learn_exam.py`` (the plan's 400-line split rule).

1. **Decision** (section 2.4): the acceptance report of the nearest
   preceding execute phase, the control report that phase's ``compare``
   names, the previous learn phase's bank artifact and the checks left
   after this phase go through ``decision.decide_task_list`` -- one task
   list. The first learn phase after a baseline runs the baseline-gap
   path (current report empty, the D report as the control; the entry
   implements it, this driver only routes).
2. **Evidence and author** (sections 2.5/2.6), **the multi-version
   merge** (one skill can carry several report rows when the shared
   answer store holds attempts of several versions; the highest
   version's row decides) and the landing semantics are documented on
   ``learn_add / learn_apply / learn_exam``; the answer-sheet evidence of an exam
   revise comes from the evidence family's collector
   (``methods.evidence.exam_sheet``).
3. **Curation** (section 2.7): every learn phase ends with its own bank
   artifact (R7). The sequence's LAST learn phase runs the configured
   curation (``methods.curation.phase_bank.produce`` -- agentbrew with
   its merge_check, or none); every intermediate learn phase writes the
   as-is artifact (``produce_as_is``). The artifact carries every
   member, accepted and pending alike -- it is the only currency
   between phases, and the next execute phase's universe (the pending
   members) comes out of it. Both entries take the phase's own raw
   ``curate`` mapping (the v4 config has no single curation slot).

**The learn phase touches nothing but the skill store and its own phase
directory.** Execution, judging, redispatch and shortfall are the execute
phases' (R4: an execute phase never changes the bank; the reverse holds
here -- a learn phase never runs a task).

Resume equals an uninterrupted run: a FINISHED phase is skipped whole
(its summary with the bank entry is the commit point -- re-running it
would double the outcome receipts and rewrite the artifact from the
current store), an interrupted one re-derives the decision (a pure
function of frozen on-disk reports and the previous phase's artifact),
reads its landed skills off the phase's ledgers and skips them, and
asks again only the model calls whose results were never persisted. An
InfrastructureError is recorded per target and raised at the phase end
AFTER the summary is written but BEFORE the bank artifact is produced
(an incomplete learn phase must not publish a bank); the resume
re-enters and finishes it.

The phase's files all live in ``phases/<label>/``
(composites.phase_bank's layout): the bank artifact and summary from
the curation side, ``task_list.jsonl`` / ``revisions.jsonl`` /
``outcomes.jsonl`` from the path modules (the outcomes ledger is the
G10/G11 migration's successor to attributions.jsonl -- Wave 3's audit
scripts and Wave 4's golden regeneration align to that shape), and
``learn_summary.json`` mirroring the returned PhaseResult.
"""

import dataclasses
from typing import Any, Dict, List, Optional, Sequence, Tuple

from expedistill.composites import decision
from expedistill.composites import decision_strategies
from expedistill.composites.live_measurement import round_dir
from expedistill.composites.phase_bank import (
    load_phase_bank,
    phase_bank_dir,
)
from expedistill.composites.reports import acceptance_report
from expedistill.composites.skill_usage import SKILL_USAGE_NAME
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
from expedistill.infra.store import read_json, read_jsonl, write_json
from expedistill.methods.curation import phase_bank as curation_phase_bank
from expedistill.stages import learn_add, learn_apply, learn_exam, learn_ledger

__all__ = ["run"]

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

    phase_dir = phase_bank_dir(ctx.run_dir, phase.label)
    finished = _finished_summary(phase_dir)
    if finished is not None:
        # A crash AFTER this phase (or a --until-phase replay) must not
        # re-run it: the landing's receipts would double in outcomes
        # .jsonl and the bank artifact would be re-derived from the
        # CURRENT store (a later learn phase's accepted skills would
        # leak into this one's snapshot). The summary with its "bank"
        # entry is the phase's commit point -- it is written last,
        # after the artifact -- so it is the completion signal.
        return PhaseResult(label=phase.label, summary=finished)
    names, specs = _sequence(config, phase)
    phase_dir.mkdir(parents=True, exist_ok=True)

    (current_kind, current_rows, control_kind, control_rows,
     compare_rule) = _report_inputs(ctx, names, specs, phase.index)
    current_pos = _current_execute_position(names, specs, phase.index)
    current_spec = specs[names[current_pos]]
    bank = _previous_bank(ctx, names, specs, phase.index)
    checks_left = decision.remaining_checks(
        [specs[name].role for name in names], phase.index)
    current_round = _live_round(names, specs, current_pos)
    if current_kind is None:
        # P02: the baseline-gap add path stays exactly as it was.
        task_rows = decision.decide_task_list(
            current_kind=current_kind, current_report=current_rows,
            control_kind=control_kind, control_report=control_rows,
            bank=bank, checks_left=checks_left, compare_rule=compare_rule,
            n_attempts_weak=config.n_attempts_weak,
            task_select_rule=config.rules["task_select"])
        effective, superseded = task_rows, []
        infra_failed = learn_add.execute_add_rows(ctx, phase, effective)
    elif current_kind == PHASE_ROLE_EXAM:
        # Legacy exam path: multi-version rows still need the merge.
        task_rows = decision.decide_task_list(
            current_kind=current_kind, current_report=current_rows,
            control_kind=control_kind, control_report=control_rows,
            bank=bank, checks_left=checks_left, compare_rule=compare_rule,
            n_attempts_weak=config.n_attempts_weak,
            task_select_rule=config.rules["task_select"])
        effective, superseded = learn_exam.merge_task_list(
            task_rows, list(current_rows or ()))
        infra_failed = learn_exam.execute_exam_rows(
            ctx, phase, effective, current_rows, current_spec,
            current_round, superseded)
    else:
        # P04/P06: one per-skill decision row per measured skill.
        task_rows = decision_strategies.decide(
            name=phase.spec.decision.get("name", "task_level"),
            current_kind=current_kind, current_report=current_rows,
            control_kind=control_kind, control_report=control_rows,
            bank=bank, checks_left=checks_left, compare_rule=compare_rule,
            n_attempts_weak=config.n_attempts_weak,
            task_select_rule=config.rules["task_select"],
            usage_by_task=_usage_by_task(ctx.store, current_round))
        effective, superseded = task_rows, []
        infra_failed = learn_apply.execute_decision_rows(
            ctx, phase, effective, current_rows, current_spec,
            current_round)

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
# The completion signal (replay idempotency)
# ---------------------------------------------------------------------------

def _finished_summary(phase_dir) -> Optional[Dict[str, Any]]:
    """The phase's stored summary when the phase FINISHED (the summary
    carries its "bank" entry -- the curation side writes the artifact
    first and the summary last, so the entry is the commit point);
    None when the phase never ran or stopped on infra failures (that
    summary has no "bank" entry and the resume re-enters it)."""
    path = phase_dir / SUMMARY_NAME
    if not path.exists():
        return None
    summary = read_json(path)
    return summary if isinstance(summary, dict) and "bank" in summary \
        else None


# ---------------------------------------------------------------------------
# The sequence and the reports (the decision entry's inputs)
# ---------------------------------------------------------------------------

def _sequence(config: Any, phase: PhaseInstance) -> Tuple[List[str], dict]:
    """The frozen sequence and its parsed specs off the v4 config.

    Reads ``config.sequence`` / ``config.phases`` -- the products every
    loaded schema-4 config carries. A config object without them (or
    with them empty) bypassed parse_phases: that is a wiring bug, named
    here before any phase work starts.
    """
    names = config.sequence
    specs = config.phases
    if (not isinstance(names, list) or not names
            or not isinstance(specs, dict)):
        raise CodeBugError(
            "phase_learn needs the v4 config products "
            "config.sequence (a list of phase names) and config.phases "
            "(name -> parsed spec); got "
            f"sequence={names!r}, phases={specs!r}: a directly "
            "constructed config bypassed parse_phases")
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
                decision.DEFAULT_COMPARE_RULE)
    current_rows = _read_report(ctx, names, specs, current_pos)
    compare = spec.compare
    rule = (compare.rule
            if compare is not None and compare.rule is not None
            else decision.DEFAULT_COMPARE_RULE)
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
    if control_spec.role != PHASE_ROLE_BASELINE:
        raise CodeBugError(
            f"the control phase {compare.against!r} has role "
            f"{control_spec.role!r}; a control is a baseline phase's "
            "weak bare arm (the config's load-time checklist should "
            "have refused this shape)")
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
        rows = acceptance_report(
            ctx.store, kind="exam", benchmark=ctx.config.benchmark,
            phase_label=label)
        return _with_phase_verdicts(ctx, label, rows)
    if spec.role == PHASE_ROLE_LIVE:
        return acceptance_report(
            ctx.store, kind="live", benchmark=ctx.config.benchmark,
            phase_label=label, round_=_live_round(names, specs, position))
    raise CodeBugError(
        f"a report was asked for phase {name!r} of role {spec.role!r}; "
        "only baseline / exam / live phases have one")


def _with_phase_verdicts(ctx: Any, label: str,
                         rows: Sequence[AcceptanceReportRow]
                         ) -> List[AcceptanceReportRow]:
    """Override the recomputed exam rows with the phase's own verdicts.

    The exam phase records facts the stores cannot carry: a unit whose
    with-skill answer was SKIPPED because the bare control was perfect
    has no attempt anywhere, so the recomputed report calls it
    ``zero_execution`` (work still owed). The phase's minimal verdict
    file knows better (``not_run_control_perfect`` /
    ``weak_already_knows``), and the decision table needs that fact to
    reach "no headroom" and drop the skill.

    Only the status and cause are overridden; every count and artifact
    stays the recomputation's. A missing file (an interrupted phase, or
    a verdict written by an older build) leaves the rows unchanged.
    """
    path = (phase_bank_dir(ctx.run_dir, label)
            / "unit_verdicts.jsonl")
    if not path.exists():
        return list(rows)
    stored = {row["unit_id"]: row for row in read_jsonl(path)}
    out: List[AcceptanceReportRow] = []
    for row in rows:
        verdict = stored.get(row.unit_id)
        if verdict is None:
            out.append(row)
            continue
        out.append(dataclasses.replace(
            row, status=verdict.get("status", row.status),
            cause=verdict.get("cause", row.cause)))
    return out


def _live_round(names: Sequence[str], specs: dict, position: int) -> int:
    """The round a live phase measured in: its 0-based index among the
    sequence's live phases (the k-th live phase uses round k-1; the
    upgrade-mapped live sequence has exactly two, matching the pinned
    round vocabulary). Must agree with the execute driver's live-role
    round derivation (stages/phase_execute/live.py's _round_of)."""
    return sum(1 for earlier in range(position)
               if isinstance(specs[names[earlier]], ExecutePhaseSpec)
               and specs[names[earlier]].role == PHASE_ROLE_LIVE)


def _previous_bank(ctx: Any, names: Sequence[str], specs: dict,
                   index: int) -> List[Skill]:
    """The previous learn phase's bank artifact as Skill objects. The
    first learn phase of a sequence has no artifact to load, so it falls
    back to the run-level frozen bank -- the same pre-learn fallback
    rule :func:`phase_execute.phase_disk._bank_skills` applies to the
    delivery side. A from-scratch run's run-level bank does not exist
    at that point (the phase pipeline writes only phase artifacts), so
    the fallback reads empty and the first learn phase still starts
    from nothing. An ASSEMBLED run's pre-seeded bank IS the run-level
    currency, and the decision then knows which tasks the frozen bank
    already covers -- without the fallback it would re-offer an add for
    every weak-failed train task the store also has no skill for, and
    the authored skill could grow the bank the run was assembled to
    re-measure frozen."""
    previous = [position for position in range(index)
                if isinstance(specs[names[position]], LearnPhaseSpec)]
    if not previous:
        return [Skill.from_dict(row) for row in ctx.store.bank()]
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
    actions: Dict[str, int] = {}
    for row in rows:
        actions[row.action] = actions.get(row.action, 0) + 1
    return {
        "role": phase.spec.role,
        "current_phase": current_phase,
        "compare_rule": compare_rule,
        "checks_left": checks_left,
        "task_list": {**actions, "superseded_rows": len(superseded)},
        "outcomes": outcomes,
    }


def _usage_by_task(store, round_: int) -> Dict[str, List[dict]]:
    """The round's skill-usage rows, grouped by task id."""
    out: Dict[str, List[dict]] = {}
    for row in read_jsonl(round_dir(store, round_) / SKILL_USAGE_NAME):
        task_id = row.get("task_id")
        if task_id:
            out.setdefault(task_id, []).append(row)
    return out


def _phase_outcomes(phase: PhaseInstance, phase_dir) -> List[dict]:
    """This phase's receipt rows (the outcomes ledger is cumulative:
    a resumed phase's summary covers both halves)."""
    path = phase_dir / learn_ledger.OUTCOMES_NAME
    if not path.exists():
        return []
    return [record for record in read_jsonl(path)
            if record.get("phase_label") == phase.label]
