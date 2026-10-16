"""The execute-phase driver (R9/R8): delivery -> execution -> acceptance.

Plan R9 item 5: this driver replaces the four execution stages -- the
baseline stage (stage1), the final-test stage (stage6) and, for the
execute side of the old pipeline, the segmentation / generation stages
(stage2/stage3, whose content is learn-phase work and moves to
``stages/phase_learn.py``, Wave 2-B). One call runs ONE position of the
frozen phase sequence (``run(ctx, phase) -> PhaseResult``, the frozen
hand-off contract in core/config.py); the phase's role decides the shape:

* ``baseline`` (D): both bare arms (strong, weak) over the train set,
  stored in the RUN-LEVEL trajectory store -- every frozen consumer of
  a bare baseline (live baseline loading, the report reader, the
  C phase's reused control, the audit tooling) reads them there;
* ``exam`` (A): one exam per pending bank skill -- the exam is
  generated at execution time (the R8 ruling: the exam-generation call
  moved here from the authoring phase), answered bare, audited for
  leaks, then answered with the skill injected;
* ``live`` (B): one unit per task whose bank group is pending, the
  whole group injected, ``n_attempts`` real executions, judged through
  the frozen live verdicts-ledger protocol -- the k-th live phase of a
  sequence measures round ``k-1`` (``live_verdicts.judge_round`` /
  ``finalize_group``, exactly the old driver's call shape; the rounds
  are pinned to {0, 1}, so a sequence carries at most two);
* ``test`` (C): the with-bank arm over the test set against its control
  arm (the baseline phase's weak bare trajectories, or a fresh bare
  arm inside the phase), full re-run every time.

The frozen internal order (plan R9 item 6, boundary doc section 4.3
rule 3): control units run first and are accepted first; then the
decision module's :func:`decision.control_perfect_skips_run` decides,
per unit, whether the with-skill run is skipped (an all-success control
under a strict-family compare rule cannot be improved on); only then do
the with-skill units run and get accepted. On the live role the same
fact takes the ledger's shape: the entry guard short-circuits the group
through ``judge_round``'s zero-execution ``weak_already_knows`` terminal.
The final test C calls neither the skip logic nor the redispatch loop:
it re-runs everything on every call (boundary doc section 4.3 rule 1's
last sentence).

The other frozen companions, wired here (the Wave 1 review's list):

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
* the invalid-redispatch loop (wiring list rule 6, config's
  ``invalid_redispatch_limit``): a unit whose runs are all invalid (or
  whose dispatch died with nothing stored) is re-dispatched up to the
  limit; a still-invalid unit after the budget is spent reports
  ``not_testable`` / ``invalid_redispatch_exhausted`` (or ``all_holes``
  when every stored run came back without a scorer verdict -- the same
  mapping the frozen report reader applies). The pass count is durable
  per unit (``redispatch.json``), so a resumed phase continues the same
  budget, and a unit whose budget is spent never re-enters K1.
* unit ids: delivery fills the task ref and the sha8 of the canonical
  injected skills; this driver rebuilds the full frozen key with
  :func:`verdicts.make_unit_id` (P2 closed -- one canonicalization
  rule, one rebuild site).
* the bank: delivery receives the PREVIOUS learn phase's bank artifact
  (:func:`phase_bank.load_phase_bank`, R7's cross-phase load), members
  and versions included; before the first learn phase the run-level
  frozen bank stands in. (The live group view keeps its store
  derivation -- the same one the frozen audit line and the pending
  universe are built from, so the ledger and the delivery cannot
  disagree about what a group is.)
* judgment: every real run is judged through the acceptance module's
  frozen entry (:func:`verdicts.verdict_from_run`) under the phase's
  named rule (the config's ``acceptance`` field, else the benchmark's
  default binding -- ``tb_scorer`` for TB, the rule that consumes the
  ``verifier_ran`` / ``exception_type`` facts). The live round judgment
  stays the verdicts ledger's (``judge_round``): its row IS the resume
  protocol and E6's pinned shape, and ``finalize_group`` is the ledger
  protocol's commit step (the 2026-10-16 coordinator ruling: the phase
  driver keeps calling both; "acceptance takes over the verdict
  protocol" is a later optional consolidation, not this wave).

R4 (the invariant this driver must not break): an execute phase makes
no DIRECT bank calls -- no ``discard_skill`` / ``accept_skill`` anywhere
in it. The two sanctioned exceptions are the frozen protocols'
own commits: the live finalization sweep (the ledger protocol's commit,
above) and nothing else; every other phase leaves the bank
byte-identical, pinned by tests/unit/test_w2a_phase_execute.py.

Phase directory layout (this driver owns the execute-phase half; the
learn-phase half is phase_bank's and W2-B's):

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
                                  frozen unit ids attached
        acceptance_report.jsonl   the phase's report rows (not the C
                                  phase: its numbers go to the summary,
                                  boundary doc section 3 rule 9)
        redispatch.json           per-unit re-dispatch bookkeeping
        <arm>/accepted_infra_shortfall.json   the durable accepted holes
        generation_failures.jsonl (exam phases) terminal exam-generation
                                  failures, first row per exam wins
        phase_summary.json        the summary also returned as PhaseResult

Resume is disk-only, as everywhere: K1 skips stored units, exam
answering reuses its unjudged pending attempt, judge_round re-derives
and byte-compares its stored row, and the redispatch and accepted-hole
records are read before anything dispatches.
"""

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from expedistill.benchmarks.aux import aux_wiring
from expedistill.benchmarks.base import Task
from expedistill.benchmarks.registry import resolve_plugin
from expedistill.blocks import metrics as metrics_block
from expedistill.blocks import tasks as tasks_block
from expedistill.blocks.author import gen_exam
from expedistill.blocks.view import render_steps_view
from expedistill.composites import decision, shortfall, verdicts
from expedistill.composites import live_measurement as lm
from expedistill.composites import live_verdicts as lv
from expedistill.composites import phase_bank, reports
from expedistill.composites.acceptance_leak import (
    find_leak_verdict,
    leak_verdict_for,
)
from expedistill.composites.acceptance_skill_status import (
    _exam_attempts,
    _find_bare,
)
from expedistill.composites.batch_execute import batch_execute
from expedistill.composites.exam_grading import judge_budget_exhausted
from expedistill.composites.exam_once import exam_once
from expedistill.core import ids
from expedistill.core.config import (
    DEFAULT_INVALID_REDISPATCH_LIMIT,
    EXECUTION_NAMES,
    FRESH_BARE,
    PHASE_ROLE_BASELINE,
    PHASE_ROLE_EXAM,
    PHASE_ROLE_LIVE,
    PHASE_ROLE_TEST,
    ExecutePhaseSpec,
    PhaseInstance,
    PhaseResult,
)
from expedistill.core.contracts import (
    AcceptanceReportRow,
    ExecutionUnitRow,
)
from expedistill.core.models import Exam, Skill, Subtask, Trajectory
from expedistill.infra.parallel import run_units
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import (
    Store,
    append_jsonl,
    read_json,
    read_jsonl,
    write_json,
)
from expedistill.inject import bank_reference, estimate_tokens
from expedistill.methods import registry as method_registry
from expedistill.stages.context import StageContext

# Private-name seams, the same discipline reports' own imports of
# live_measurement's and acceptance_skill_status's helpers follow (the
# W1-A merge note): the frozen Trajectory / Attempt -> RunRecord
# mappings live once in their owning modules; this driver judges through
# them, never a local re-implementation.
from expedistill.composites.reports import (
    _run_record_from_attempt,
    _run_record_from_trajectory,
)

__all__ = ["run"]

#: The execute-phase layout inside ``<run>/phases/<label>/`` (module
#: docstring). The learn-phase artifacts beside them are phase_bank's.
PHASE_RECORD_NAME = "phase.json"
UNITS_NAME = "units.jsonl"
REPORT_NAME = "acceptance_report.jsonl"
REDISPATCH_NAME = "redispatch.json"
SUMMARY_NAME = "phase_summary.json"
GENERATION_FAILURES_NAME = "generation_failures.jsonl"

#: The baseline phase's two arms (core.config's frozen pair, re-checked
#: at execution-params parsing).
_BASELINE_ARMS = ("executor_strong", "executor_weak")
_ROLE_OF_ARM = {"executor_strong": "strong", "executor_weak": "weak"}

#: A phase directory's name: ``pNN_<name>`` (phase_bank's colon mapping).
_PHASE_DIR_RE = re.compile(r"p([0-9]+)_(.+)")


def run(ctx: StageContext, phase: PhaseInstance) -> PhaseResult:
    """Run one execute-phase position; returns its summary as PhaseResult.

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
    directory.mkdir(parents=True, exist_ok=True)
    # What ran here -- the disk fact later phases derive from (the live
    # round number, the compare targets, the reuse sources).
    write_json(directory / PHASE_RECORD_NAME, {
        "label": phase.label, "index": phase.index, "name": phase.name,
        "role": spec.role}, indent=2)
    _resolve_verdict_rule(spec, ctx.config.benchmark)
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


# ---------------------------------------------------------------------------
# Phase directory, earlier phases, bank (all disk-only derivations)
# ---------------------------------------------------------------------------

def _phase_dir(ctx: StageContext, phase: PhaseInstance,
               arm: str = "") -> Path:
    """The phase's directory (phase_bank's validated label mapping)."""
    directory = phase_bank.phase_bank_dir(ctx.run_dir, phase.label)
    return directory / arm if arm else directory


def _arm_dir(ctx: StageContext, phase: PhaseInstance, arm: str) -> Path:
    """An arm's directory (store + ledgers), created on first use."""
    directory = _phase_dir(ctx, phase, arm)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _earlier_phases(ctx: StageContext, phase: PhaseInstance
                    ) -> List[Dict[str, Any]]:
    """The earlier phases' ``phase.json`` records, newest first.
    Derived from the phases directory alone: which phases ran is a disk
    fact, and PhaseInstance names only this phase (the frozen hand-off
    contract carries no sequence)."""
    phases_dir = Path(ctx.run_dir) / phase_bank.PHASES_DIR
    if not phases_dir.exists():
        return []
    records: List[Tuple[int, Dict[str, Any]]] = []
    for entry in phases_dir.iterdir():
        record_path = entry / PHASE_RECORD_NAME
        if not record_path.exists():
            continue
        try:
            record = read_json(record_path)
        except (OSError, ValueError) as exc:
            raise CodeBugError(
                f"unreadable phase record {record_path}: {exc}") from None
        if not isinstance(record.get("index"), int) \
                or record["index"] >= phase.index:
            continue
        records.append((record["index"], record))
    return [record for _, record in sorted(records, key=lambda p: -p[0])]


def _bank_skills(ctx: StageContext, phase: PhaseInstance) -> List[Skill]:
    """The bank delivery receives: the previous learn phase's artifact
    (R7's cross-phase load), else the run-level frozen bank (before the
    first learn phase there is no artifact to load). The artifact's
    PRESENCE is the signal -- a learn phase ends with one by contract,
    whatever else it writes beside it."""
    phases_dir = Path(ctx.run_dir) / phase_bank.PHASES_DIR
    if phases_dir.exists():
        found = []
        for entry in phases_dir.iterdir():
            match = _PHASE_DIR_RE.fullmatch(entry.name)
            if match is None or int(match.group(1)) - 1 >= phase.index:
                continue
            if (entry / phase_bank.BANK_NAME).exists():
                found.append((int(match.group(1)),
                               f"p{int(match.group(1)):02d}:{match.group(2)}"))
        if found:
            label = max(found)[1]
            return [Skill.from_dict(row)
                    for row in phase_bank.load_phase_bank(ctx.run_dir,
                                                          label)]
    return [Skill.from_dict(row) for row in ctx.store.bank()]


def _write_units(ctx: StageContext, phase: PhaseInstance,
                 units: Sequence[ExecutionUnitRow]) -> None:
    """Persist the resolved unit list (one resolution per phase; the
    rewrite never grows a second copy of a row)."""
    path = _phase_dir(ctx, phase) / UNITS_NAME
    if path.exists():
        path.unlink()
    for unit in units:
        append_jsonl(path, asdict(unit))


def _write_report(ctx: StageContext, phase: PhaseInstance,
                  rows: Sequence[AcceptanceReportRow]) -> None:
    """Persist the phase's acceptance report (one JSON row per unit)."""
    path = _phase_dir(ctx, phase) / REPORT_NAME
    if path.exists():
        path.unlink()
    for row in rows:
        append_jsonl(path, asdict(row))


def _reused_rows(ctx: StageContext, phase: PhaseInstance,
                 new_units: Sequence[ExecutionUnitRow]
                 ) -> Dict[str, AcceptanceReportRow]:
    """Rule 1's reuse map: for each new unit that matches an earlier
    execute phase's unit (same task, same role, same rendered block --
    :func:`decision.reuse_key`), the previous row it reuses, keyed by
    the new unit id. Most recent phase wins; a ``zero_execution`` row is
    not an acceptance result and is never reused (its work is still
    owed -- the earlier phase resumes it, the later one does not
    inherit a nothing)."""
    reused: Dict[str, AcceptanceReportRow] = {}
    for record in _earlier_phases(ctx, phase):
        directory = phase_bank.phase_bank_dir(ctx.run_dir,
                                              record["label"])
        units_path = directory / UNITS_NAME
        report_path = directory / REPORT_NAME
        if not units_path.exists() or not report_path.exists():
            continue
        matches = decision.reusable_units(
            new_units, [ExecutionUnitRow(**row)
                        for row in read_jsonl(units_path)])
        if not matches:
            continue
        rows = {row.unit_id: row for row in (
            AcceptanceReportRow(**entry)
            for entry in read_jsonl(report_path))}
        for new_id, previous_id in matches.items():
            if new_id in reused:
                continue  # a more recent phase already answered this unit
            row = rows.get(previous_id)
            if row is None:
                raise CodeBugError(
                    f"phase {record['label']!r} has a unit {previous_id!r} "
                    "but no report row for it: the phase directory is "
                    "internally inconsistent and a human must look at "
                    "this run")
            if (row.status == verdicts.STATUS_NOT_TESTABLE
                    and row.cause == verdicts.CAUSE_ZERO_EXECUTION):
                continue
            reused[new_id] = row
    return reused


def _rebased_row(row: AcceptanceReportRow, *, unit_id: str,
                 phase_label: str) -> AcceptanceReportRow:
    """A reused unit's row under its new unit id (rule 1: the verdicts
    carry over unchanged; only the phase identity is new)."""
    return replace(row, unit_id=unit_id, phase_label=phase_label)


# ---------------------------------------------------------------------------
# Method resolution (the phase's raw mappings -> driver + params)
# ---------------------------------------------------------------------------

class _PhaseMethods:
    """A one-slot view of the experiment config for method resolution.

    SWAP POINT (the frozen contract, W2-C's second commit): the methods
    registry gains ``resolve(family, raw_mapping[, config])`` for the
    five content families; until it lands, the phase's raw mapping
    rides through the existing slot API under its family key. When it
    lands, :func:`_resolve_delivery`'s body becomes
    ``method_registry.resolve("delivery", raw, ctx.config)`` and this
    class dies; nothing else changes.
    """

    def __init__(self, family: str, raw: Dict[str, Any]) -> None:
        self.methods = {family: dict(raw)}


def _resolve_delivery(ctx: StageContext, spec: ExecutePhaseSpec
                      ) -> Tuple[Any, Any]:
    """(driver module, params) for the phase's delivery mapping."""
    return method_registry.resolve(
        "delivery", _PhaseMethods("delivery", spec.delivery))


@dataclass(frozen=True)
class _RealParams:
    """The ``real`` execution's parameters, parsed here until the
    registry's ``execution`` family lands (SWAP POINT, same note as
    :class:`_PhaseMethods`): ``n_attempts`` None means the weak depth
    at runtime, ``max_injection_tokens`` is the injection cap, ``arms``
    is the baseline phase's fixed pair (W2-C's frozen contract)."""

    n_attempts: Optional[int]
    max_injection_tokens: Optional[int]
    arms: Tuple[str, ...] = ()


def _execution_params(spec: ExecutePhaseSpec) -> _RealParams:
    """The phase's execution parameters, unknown keys refused loudly
    (core.config validates the shape; the values' contracts belong to
    the family that owns them, so this parsing is the swap point for
    the registry's ``execution`` family)."""
    raw = spec.execution
    name = raw.get("name")
    if name == "exam":
        unknown = sorted(set(raw) - {"name"})
        if unknown:
            raise ConfigError(
                f"the exam execution takes no parameters, got {unknown}")
        return _RealParams(n_attempts=1, max_injection_tokens=None)
    if name != EXECUTION_NAMES[0]:
        raise ConfigError(f"unknown execution name {name!r}")
    allowed = {"name", "n_attempts", "max_injection_tokens"}
    if spec.role == PHASE_ROLE_BASELINE:
        allowed |= {"arms"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(
            f"the real execution for role {spec.role!r} does not take "
            f"{unknown}; accepted keys: {sorted(allowed)}")
    n_attempts = raw.get("n_attempts")
    if n_attempts is not None and not _is_positive_int(n_attempts):
        raise ConfigError(
            "execution.n_attempts must be a positive int or null, got "
            f"{n_attempts!r}")
    cap = raw.get("max_injection_tokens")
    if cap is not None and not _is_positive_int(cap):
        raise ConfigError(
            "execution.max_injection_tokens must be a positive int or "
            f"null, got {cap!r}")
    arms: Tuple[str, ...] = ()
    if spec.role == PHASE_ROLE_BASELINE:
        arms = tuple(raw.get("arms") or ())
        if arms != _BASELINE_ARMS:
            raise ConfigError(
                f"execution.arms must be exactly {list(_BASELINE_ARMS)} "
                f"for a baseline phase, got {list(arms)}")
    return _RealParams(n_attempts=n_attempts, max_injection_tokens=cap,
                       arms=arms)


def _is_positive_int(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and value > 0)


def _resolve_verdict_rule(spec: ExecutePhaseSpec, benchmark: str) -> str:
    """The phase's named verdict rule (the config's ``acceptance``, else
    the benchmark's default binding); an unknown name fails before any
    work (get_verdict_rule's ConfigError)."""
    name = (spec.acceptance if spec.acceptance is not None
            else verdicts.default_verdict_rule(benchmark))
    verdicts.get_verdict_rule(name)
    return name


def _compare_rule(spec: ExecutePhaseSpec) -> str:
    """The phase's compare rule name (decision's compare family; the
    family's default when the config does not name one)."""
    rule = spec.compare.rule if spec.compare is not None else None
    return rule or "strict_rate_gain"


def _redispatch_limit(ctx: StageContext) -> int:
    """The config's invalid-redispatch budget (the v4 global; the frozen
    default until the v4 config object carries the field)."""
    return int(getattr(ctx.config, "invalid_redispatch_limit",
                       DEFAULT_INVALID_REDISPATCH_LIMIT))


# ---------------------------------------------------------------------------
# Task objects and the real-arm engine (dispatch -> judge -> redispatch)
# ---------------------------------------------------------------------------

def _task_objects(ctx: StageContext, raw_ids: Sequence[str]) -> List[Task]:
    """Rebuild the phase's Task objects through D1 (deterministic from
    the frozen config); a plugin answering outside the requested ids is
    a ConfigError before any execution starts (the stage-1 rule)."""
    tasks = tasks_block.load_tasks(ctx.config.benchmark, sorted(raw_ids))
    unexpected = sorted({task.task_id for task in tasks} - set(raw_ids))
    if unexpected:
        raise ConfigError(
            f"benchmark {ctx.config.benchmark!r} loaded tasks outside the "
            f"requested set: {unexpected}")
    return sorted(tasks, key=lambda task: task.task_id)


def _prefixed_task_map(ctx: StageContext,
                       raw_ids: Sequence[str]) -> Dict[str, Task]:
    """``{prefixed task id: Task}`` over the raw id list (the join of
    delivery rows, K1's Task objects and the trajectory ids)."""
    return {ids.make_task_id(ctx.config.benchmark, task.task_id): task
            for task in _task_objects(ctx, raw_ids)}


@dataclass
class _Plan:
    """One real-execution unit: the delivery row with its FULL frozen
    unit id attached, its role and its Task object."""

    unit: ExecutionUnitRow
    role: str
    task: Task
    n_attempts: int


def _plan_units(rows: Sequence[ExecutionUnitRow],
                tasks: Optional[Dict[str, Task]], *, phase_label: str,
                benchmark: str, role: str, n_attempts: int) -> List[_Plan]:
    """Rebuild each delivery row's full frozen unit id (verdicts' frozen
    rule; the row's task ref and sha8 already carry the pieces -- the
    P2 ruling's one rebuild site) and pair it with its Task object when
    the phase dispatches K1 (the exam role pairs none).

    The row's task ref is normalized to the PREFIXED task id: delivery
    answers whatever identity its tasks carried (a Task object's raw
    id, a prefixed id, an exam ref), while the frozen unit id, the
    trajectory ids and the reports all speak the prefixed form. Exam
    refs pass through unchanged (they are not task ids)."""
    plans: List[_Plan] = []
    for row in rows:
        ref = row.task_ref
        if tasks is not None and ref not in tasks:
            prefixed = ids.make_task_id(benchmark, ref)
            if prefixed in tasks:
                ref = prefixed
            else:
                raise CodeBugError(
                    f"delivery resolved unit {row.unit_id!r} outside the "
                    "phase's task set; the driver and the delivery "
                    "disagree")
        plans.append(_Plan(
            unit=replace(
                row,
                task_ref=ref,
                unit_id=verdicts.make_unit_id(
                    phase_label=phase_label, benchmark=benchmark,
                    task_ref=ref, role=role,
                    skills_injected=row.skills_injected)),
            role=role, task=tasks[ref] if tasks is not None else None,
            n_attempts=n_attempts))
    plans.sort(key=lambda plan: plan.unit.unit_id)
    return plans


def _skills_for_task(plans: Sequence[_Plan]
                     ) -> Optional[Callable[[Task], Tuple[Any, str]]]:
    """K1's per-task injection resolver over the planned rows (None when
    every unit is a bare scalar pair -- the frozen whole-bank and bare
    paths, so K1 records the same scalar bookkeeping it always did)."""
    if all(plan.unit.skills_injected is None for plan in plans):
        return None
    by_task = {plan.task.task_id: (plan.unit.skills_injected,
                                   plan.unit.skills_block)
               for plan in plans}

    def resolve(task: Task) -> Tuple[Any, str]:
        try:
            return by_task[task.task_id]
        except KeyError:
            raise CodeBugError(
                f"task {task.task_id!r} has no delivery resolution: the "
                "per-task mapping was built over a different task set"
            ) from None

    return resolve


def _redispatch_state(path: Path) -> Dict[str, Dict[str, int]]:
    """The durable per-unit re-dispatch bookkeeping: ``{unit_id:
    {"passes": n}}`` -- how many times the phase re-offered the unit's
    holes to K1 (counted against the config's budget)."""
    if not path.exists():
        return {}
    try:
        state = read_json(path)
    except (OSError, ValueError) as exc:
        raise CodeBugError(
            f"unreadable redispatch record {path}: {exc}") from None
    if not isinstance(state, dict):
        raise CodeBugError(f"invalid redispatch record {path}")
    return state


def _save_redispatch_state(path: Path,
                           state: Dict[str, Dict[str, int]]) -> None:
    write_json(path, state, indent=2)


def _dispatch_arm(ctx: StageContext, phase: PhaseInstance, *,
                  plans: Sequence[_Plan], store: Store, arm: str,
                  skip_unit_ids: Set[str],
                  skills_for_task: Optional[Callable] = None
                  ) -> None:
    """One K1 batch over the arm's plans at the arm's frozen depth."""
    if not plans:
        return
    role = plans[0].role
    batch_execute(
        store=store,
        benchmark=ctx.config.benchmark,
        tasks=[plan.task for plan in plans],
        role=role,
        model_name=ctx.config.models[f"executor_{role}"],
        handle=ctx.handle(f"executor_{role}"),
        n_attempts=plans[0].n_attempts,
        skills_for_task=(skills_for_task if skills_for_task is not None
                         else _skills_for_task(plans)),
        workdir_root=(Path(ctx.workdir_root)
                      / phase.label.replace(":", "_") / arm),
        usage=ctx.usage,
        run_dir=ctx.run_dir,
        workers=ctx.run_settings.workers,
        dispatch_stagger=ctx.run_settings.k1_stagger,
        skip_unit_ids=skip_unit_ids,
    )


def _unit_runs(store: Store, plan: _Plan, *, accepted: Set[str],
               rule_name: str
               ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """One unit's judged runs: every expected attempt either judged
    under the named rule, an accepted hole (no run at all) or a missing
    invalid run. Returns ``(runs, missing_ids)`` -- the frozen reader's
    shape (the run key is the trajectory id, verdict and basis)."""
    runs: List[Dict[str, Any]] = []
    missing: List[str] = []
    for attempt in range(plan.n_attempts):
        trajectory_id = ids.make_trajectory_id(
            plan.unit.task_ref, plan.role, attempt)
        if trajectory_id in accepted:
            continue
        if store.exists("trajectories", trajectory_id):
            trajectory = store.load("trajectories", trajectory_id,
                                    Trajectory)
            verdict, basis = verdicts.verdict_from_run(
                _run_record_from_trajectory(trajectory),
                rule_name=rule_name)
        else:
            missing.append(trajectory_id)
            verdict = verdicts.RUN_INVALID
            basis = ("missing: dispatched but never completed (infra "
                     "retries exhausted)")
        runs.append({"run": trajectory_id, "verdict": verdict,
                     "basis": basis})
    return runs, missing


def _unit_row_for(plan: _Plan, *, runs: List[Dict[str, Any]],
                  missing: int, phase_label: str) -> AcceptanceReportRow:
    """The unit's report row over its judged runs (the frozen reader's
    status mapping: any valid run -> normal; every unit a hole or every
    stored run unscored -> all_holes; a dispatch that died mid-unit ->
    invalid_redispatch_exhausted)."""
    if any(run["verdict"] != verdicts.RUN_INVALID for run in runs):
        status, cause = verdicts.STATUS_NORMAL, ""
    elif not runs or missing == 0:
        status, cause = (verdicts.STATUS_NOT_TESTABLE,
                         verdicts.CAUSE_ALL_HOLES)
    else:
        status, cause = (verdicts.STATUS_NOT_TESTABLE,
                         verdicts.CAUSE_INVALID_REDISPATCH_EXHAUSTED)
    return verdicts.summarize_unit(
        unit_id=plan.unit.unit_id, phase_label=phase_label,
        role=plan.role, injected_skills=plan.unit.skills_injected,
        runs=runs, status=status, cause=cause,
        artifacts={"trajectory_ids": [run["run"] for run in runs]})


def _run_arm_units(ctx: StageContext, phase: PhaseInstance, *,
                   plans: Sequence[_Plan], store: Store, arm: str,
                   rule_name: str, limit: int
                   ) -> Tuple[List[AcceptanceReportRow], Set[str]]:
    """Dispatch, judge and re-dispatch one arm's units.

    Returns ``(rows, unaccepted_holes)``. The re-dispatch loop is the
    frozen rule 6: a unit whose dispatch died with nothing stored is
    re-offered to K1 while the budget lasts. The bookkeeping is durable,
    so a resumed phase continues the same budget instead of restarting
    it, and a unit whose budget is already spent never re-enters K1
    (the initial batch excludes it; its row reports the cause and the
    phase raises for the operator's shortfall decision)."""
    state_path = _phase_dir(ctx, phase, arm) / REDISPATCH_NAME
    state = _redispatch_state(state_path)
    accepted_path = _phase_dir(ctx, phase, arm) / shortfall.SHORTFALL_NAME
    accepted = set(shortfall.read_unit_ids(accepted_path))
    open_budget = [plan for plan in plans
                   if state.get(plan.unit.unit_id,
                                {}).get("passes", 0) < limit]
    _dispatch_arm(ctx, phase, plans=open_budget, store=store, arm=arm,
                  skip_unit_ids=accepted)
    rows: List[AcceptanceReportRow] = []
    holes: Set[str] = set()
    for plan in plans:
        entry = state.setdefault(plan.unit.unit_id, {"passes": 0})
        while True:
            runs, missing = _unit_runs(store, plan, accepted=accepted,
                                       rule_name=rule_name)
            if not missing or entry["passes"] >= limit:
                break  # nothing to re-offer, or the budget is spent
            entry["passes"] += 1  # one more K1 offer of the holes
            _save_redispatch_state(state_path, state)
            _dispatch_arm(ctx, phase, plans=[plan], store=store, arm=arm,
                          skip_unit_ids=accepted)
        _save_redispatch_state(state_path, state)
        holes.update(missing)
        rows.append(_unit_row_for(plan, runs=runs, missing=len(missing),
                                  phase_label=phase.label))
    return rows, holes


def _accept_new_holes(ctx: StageContext, phase: PhaseInstance, arm: str,
                      holes: Set[str]) -> List[str]:
    """Durably accept the arm's new holes when the run setting says so
    (the old stages' rule): the record is written BEFORE the report
    reads the shrunken sample, and every later call honours it with or
    without the flag. Returns the full accepted set."""
    accepted_path = _phase_dir(ctx, phase, arm) / shortfall.SHORTFALL_NAME
    accepted = set(shortfall.read_unit_ids(accepted_path))
    if holes and ctx.run_settings.allow_infra_shortfall:
        accepted |= holes
        shortfall.write_unit_ids(accepted_path, accepted)
    return sorted(accepted)


def _arm_trajectories(store: Store) -> List[Trajectory]:
    """Every trajectory in one arm store, sorted by id."""
    return [store.load("trajectories", trajectory_id, Trajectory)
            for trajectory_id in store.ids("trajectories")]


# ---------------------------------------------------------------------------
# The baseline phase (D)
# ---------------------------------------------------------------------------

def _run_baseline(ctx: StageContext, phase: PhaseInstance,
                  spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """Both bare arms over the phase's task set, stored in the RUN-LEVEL
    trajectory store with the stage-1 accepted-holes record beside them
    -- every frozen consumer of a bare baseline (live baseline loading,
    the report reader, the C phase's reused control) reads them there.
    One report row per (task, role) (the D phases' role split); the
    failure-set derivation is NOT here (G3/R3: decision applies the
    named task-select rule to the weak rows)."""
    params = _execution_params(spec)
    delivery, delivery_params = _resolve_delivery(ctx, spec)
    tasks = _prefixed_task_map(ctx, list(phase.task_ids))
    rows = delivery.resolve(None, list(tasks),
                            delivery_params)  # the none driver ignores bank
    rule_name = _resolve_verdict_rule(spec, ctx.config.benchmark)
    limit = _redispatch_limit(ctx)
    depths = {"strong": ctx.config.n_attempts_strong,
              "weak": ctx.config.n_attempts_weak}

    plans_by_role: Dict[str, List[_Plan]] = {}
    for arm in params.arms:
        role = _ROLE_OF_ARM[arm]
        plans_by_role[role] = _plan_units(
            rows, tasks, phase_label=phase.label,
            benchmark=ctx.config.benchmark, role=role,
            n_attempts=depths[role])
    _write_units(ctx, phase, [plan.unit for role in ("strong", "weak")
                              for plan in plans_by_role[role]])

    # The run-level store holds ONLY bare trajectories (the with-skill
    # arms of every other phase live in their own stores), so the bare
    # arms keep their historical home and no id ever collides.
    stage1_holes = set(shortfall.stage1_accepted_unit_ids(ctx.run_dir))
    holes: Set[str] = set()
    for role in ("strong", "weak"):
        _, arm_holes = _run_arm_units(
            ctx, phase, plans=plans_by_role[role], store=ctx.store,
            arm=role, rule_name=rule_name, limit=limit)
        if arm_holes and ctx.run_settings.allow_infra_shortfall:
            # The durable record is the stage-1 path: load_baseline, the
            # report reader and the C control read it from there.
            shortfall.write_unit_ids(
                shortfall.stage1_shortfall_path(ctx.run_dir),
                stage1_holes | arm_holes)
            stage1_holes |= arm_holes
        else:
            holes |= arm_holes

    # The frozen report reader builds the rows over exactly this store
    # and record (baseline kind; the same status/cause mapping the
    # driver's own judging produced).
    all_rows = reports.acceptance_report(
        ctx.store, kind="baseline", benchmark=ctx.config.benchmark,
        phase_label=phase.label,
        n_attempts_strong=ctx.config.n_attempts_strong,
        n_attempts_weak=ctx.config.n_attempts_weak)
    _write_report(ctx, phase, all_rows)

    aux_metric, aux_scorer = aux_wiring(ctx.config.benchmark)
    arm_summaries: Dict[str, Dict[str, Any]] = {}
    for role in ("strong", "weak"):
        plans = plans_by_role[role]
        trajectories = [traj for traj in _arm_trajectories(ctx.store)
                        if traj.model_role == role
                        and traj.trajectory_id not in stage1_holes]
        expected = {plan.unit.task_ref: plan.n_attempts for plan in plans}
        metrics = metrics_block.compute_task_metrics(
            trajectories, expected=expected, aux_scorer=aux_scorer)
        arm_summaries[role] = metrics_block.summarize(
            metrics, depths[role], aux_metric=aux_metric)
    every_hole = set(stage1_holes) | holes
    summary = {
        "role": PHASE_ROLE_BASELINE,
        "strong": arm_summaries["strong"],
        "weak": arm_summaries["weak"],
        "infra_shortfalls": sorted(
            task_id for task_id, counts in metrics_block.compute_task_metrics(  # noqa: E501
                [traj for traj in _arm_trajectories(ctx.store)
                 if traj.model_role == "weak"
                 and traj.trajectory_id not in stage1_holes],
                expected={plan.unit.task_ref: plan.n_attempts
                          for plan in plans_by_role["weak"]},
                aux_scorer=aux_scorer).items()
            if counts["valid"] < ctx.config.n_attempts_weak),
        "infra_missing": sorted(every_hole),
        "infra_accepted": sorted(stage1_holes),
        "report_rows": len(all_rows),
        "complete": not every_hole,
    }
    summary["infra_shortfall_accepted"] = bool(stage1_holes)
    write_json(_phase_dir(ctx, phase) / SUMMARY_NAME, summary, indent=2)
    if holes:
        raise InfrastructureError(
            f"phase {phase.label!r}: {len(holes)} unit(s) exhausted their "
            "infra and redispatch budgets and stored nothing (listed in "
            "the summary under infra_missing); resume the run to retry "
            "them, or accept the shortfall with --allow-infra-shortfall")
    return summary


# ---------------------------------------------------------------------------
# The exam phase (A)
# ---------------------------------------------------------------------------

def _pending_bank_skills(bank: Sequence[Skill]) -> List[Skill]:
    """The bank members still under test (the A-phase universe is one
    exam per PENDING skill: accepted / merged / discarded members have
    their verdicts already)."""
    return sorted((skill for skill in bank if skill.status == "pending"),
                  key=lambda skill: skill.subtask_id)


def _run_exam(ctx: StageContext, phase: PhaseInstance,
              spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """One exam per pending bank skill: generate (at execution time, the
    R8 ruling), answer bare, audit for leaks, answer with the skill.

    An exam's control unit is its own bare answer, so the frozen order
    holds WITHIN each unit: the control runs and is judged first, then
    :func:`decision.control_perfect_skips_run` decides whether the
    with-skill answer is skipped, then the with-skill answer runs --
    after the D8 leak audit (boundaries doc C1: the audit checks before
    the with-skill call, so a leaked version never spends one).

    The rewrite loop is NOT here: a revise decision belongs to the next
    learn phase, which re-measures the new version in the following A
    phase (rule 1 reuses every unchanged unit). Attempts and the
    leak-verdict ledger live in the run-level shared stores (their ids
    are exam-anchored and never collide across phases). The invalid
    redispatch loop does not apply to the exam role: an answer run is
    either judged, or the pipeline's own repair budget died -- the
    terminal ``exam_generation_failed`` cause, never a re-dispatch."""
    _execution_params(spec)  # the exam execution has no knobs; loud check
    rule_name = spec.acceptance or verdicts.default_verdict_rule("exam")
    if rule_name != "exam_judge":
        raise ConfigError(
            "the exam phase's runs are model-judged attempts; its "
            f"acceptance rule must be 'exam_judge', got {rule_name!r}")
    delivery, delivery_params = _resolve_delivery(ctx, spec)
    bank = _bank_skills(ctx, phase)
    pending = _pending_bank_skills(bank)
    skill_by_exam = {ids.make_exam_id(skill.subtask_id): skill
                     for skill in pending}
    rows = delivery.resolve(bank, sorted(skill_by_exam), delivery_params)
    plans = _plan_units(rows, None, phase_label=phase.label,
                        benchmark=ctx.config.benchmark, role="weak",
                        n_attempts=1)
    _write_units(ctx, phase, [plan.unit for plan in plans])
    reused = _reused_rows(ctx, phase, [plan.unit for plan in plans])
    plugin = resolve_plugin(ctx.config.benchmark)
    recorder = UnitFailureRecorder(ctx.run_dir)
    failures_path = _phase_dir(ctx, phase) / GENERATION_FAILURES_NAME
    terminal_generation = {row["exam_id"]
                           for row in read_jsonl(failures_path)}
    compare_rule = _compare_rule(spec)

    def exam_unit(plan: _Plan) -> Tuple[str, str]:
        """One exam unit's disposition; an expected failure stays here,
        anything escaping is fatal to the batch (run_units' rule)."""
        exam_id = plan.unit.task_ref
        skill = skill_by_exam[exam_id]
        if plan.unit.unit_id in reused:
            return "reused", exam_id
        try:
            exam = _exam_or_generate(ctx, exam_id, plugin.view_rules,
                                     terminal=exam_id in
                                     terminal_generation)
            if exam is None:
                return "generation_failed", exam_id
            # -- the control first: the bare answer, judged ---------------
            bare = exam_once(
                store=ctx.store, exam=exam,
                answerer_handle=ctx.handle("answerer"),
                judge_handle=ctx.handle("judge"), skills_block="",
                purpose="bare", usage=ctx.usage, run_dir=ctx.run_dir)
            if decision.control_perfect_skips_run(
                    compare_rule=compare_rule, control_valid=1,
                    control_success=1 if bare.judge_verdict else 0):
                return "control_perfect", exam_id
            # -- the D8 leak audit BEFORE the with-skill call (C1) -------
            verdict = leak_verdict_for(
                store=ctx.store, skill_id=skill.skill_id,
                skill_version=skill.version, skill_content=skill.content,
                exam=exam, auditor_handle=ctx.handle("auditor"),
                usage=ctx.usage, run_dir=ctx.run_dir)
            if verdict["leaked"]:
                return "leaked", exam_id
            # -- the with-skill answer, judged --------------------------
            exam_once(
                store=ctx.store, exam=exam,
                answerer_handle=ctx.handle("answerer"),
                judge_handle=ctx.handle("judge"),
                skills_block=plan.unit.skills_block,
                purpose="acceptance", skill_id=skill.skill_id,
                skill_version=skill.version, usage=ctx.usage,
                run_dir=ctx.run_dir)
            return "measured", exam_id
        except ModelOutputError as exc:
            # The pipeline's own repair budget died (generation, answer or
            # judge): terminal within this run, first failure row wins.
            if exam_id not in terminal_generation:
                append_jsonl(failures_path, {
                    "exam_id": exam_id,
                    "error_class": type(exc).__name__,
                    "message": " ".join(str(exc).split())[:1000]})
                terminal_generation.add(exam_id)
            return "generation_failed", exam_id
        except InfrastructureError as exc:
            recorder.record(plan.unit.unit_id, phase.label, exc, exc)
            return "infra_failed", exam_id

    outcomes = run_units(exam_unit, plans, ctx.run_settings.workers)
    report = _exam_report(ctx, phase, pending, rule_name, reused)
    _write_report(ctx, phase, report)

    counts: Dict[str, List[str]] = {}
    for kind, exam_id in outcomes:
        counts.setdefault(kind, []).append(exam_id)
    summary = {
        "role": PHASE_ROLE_EXAM,
        "universe": [plan.unit.task_ref for plan in plans],
        "measured": sorted(counts.get("measured", [])),
        "reused": sorted(counts.get("reused", [])),
        "control_perfect": sorted(counts.get("control_perfect", [])),
        "leaked": sorted(counts.get("leaked", [])),
        "generation_failed": sorted(counts.get("generation_failed", [])),
        "infra_failed": sorted(counts.get("infra_failed", [])),
        "report_rows": len(report),
    }
    write_json(_phase_dir(ctx, phase) / SUMMARY_NAME, summary, indent=2)
    if summary["infra_failed"]:
        raise InfrastructureError(
            f"phase {phase.label!r}: {len(summary['infra_failed'])} exam "
            "unit(s) are pending on infrastructure errors, recorded in the "
            "unit-failure ledger; resume the run to retry them")
    write_json(_phase_dir(ctx, phase) / SUMMARY_NAME, summary, indent=2)
    return summary


def _exam_or_generate(ctx: StageContext, exam_id: str, view_rules: Any,
                      *, terminal: bool) -> Optional[Exam]:
    """The stored exam, or one G3 generation call at execution time (the
    R8 ruling: the call moved from the authoring phase to here). A
    generation whose repair budget already died is never re-offered --
    the unit's row is its ``exam_generation_failed`` cause."""
    if ctx.store.exists("exams", exam_id):
        return ctx.store.load("exams", exam_id, Exam)
    if terminal:
        return None
    subtask_id = exam_id[: -len("/exam")]
    subtask = ctx.store.load("subtasks", subtask_id, Subtask)
    trajectory = ctx.store.load("trajectories", subtask.trajectory_id,
                                Trajectory)
    start, end = subtask.step_range
    exam = gen_exam(subtask,
                    render_steps_view(trajectory.steps[start:end],
                                      view_rules),
                    ctx.handle("examiner"),
                    usage=ctx.usage, run_dir=ctx.run_dir)
    ctx.store.save(exam, "exams")
    return exam


def _exam_report(ctx: StageContext, phase: PhaseInstance,
                 pending: Sequence[Skill], rule_name: str,
                 reused: Dict[str, AcceptanceReportRow]
                 ) -> List[AcceptanceReportRow]:
    """The A phase's report rows from the shared stores' on-disk state:
    one bare control row per exam plus one row per measured (skill,
    version) -- the frozen reader's shape, over THIS phase's universe,
    with rule-1 reuse rows joined in."""
    rows: List[AcceptanceReportRow] = []
    for skill in pending:
        exam_id = ids.make_exam_id(skill.subtask_id)
        injected = [{"skill_id": skill.skill_id,
                     "version": skill.version}]
        unit_id = verdicts.make_unit_id(
            phase_label=phase.label, benchmark="exam", task_ref=exam_id,
            role="weak", skills_injected=injected)
        if not ctx.store.exists("exams", exam_id):
            rows.append(verdicts.summarize_unit(
                unit_id=verdicts.make_unit_id(
                    phase_label=phase.label, benchmark="exam",
                    task_ref=exam_id, role="weak", skills_injected=injected),
                phase_label=phase.label, role="weak",
                injected_skills=injected, runs=[],
                status=verdicts.STATUS_NOT_TESTABLE,
                cause=verdicts.CAUSE_EXAM_GENERATION_FAILED,
                artifacts={"skill_id": skill.skill_id}))
            continue
        attempts = _exam_attempts(ctx.store, exam_id)

        # -- the bare control row ----------------------------------------
        bare = _find_bare(attempts)
        bare_runs: List[Dict[str, Any]] = []
        if bare is not None and bare.judge_verdict is not None:
            verdict, basis = verdicts.verdict_from_run(
                _run_record_from_attempt(bare), rule_name=rule_name)
            bare_runs.append({"run": bare.attempt_id, "verdict": verdict,
                              "basis": basis})
        if bare_runs:
            bare_status, bare_cause = verdicts.STATUS_NORMAL, ""
        elif any(judge_budget_exhausted(attempt)
                 for attempt in attempts
                 if attempt.purpose == "bare"
                 and attempt.skill_id is None):
            bare_status = verdicts.STATUS_NOT_TESTABLE
            bare_cause = verdicts.CAUSE_EXAM_GENERATION_FAILED
        else:
            bare_status = verdicts.STATUS_NOT_TESTABLE
            bare_cause = verdicts.CAUSE_ZERO_EXECUTION
        rows.append(verdicts.summarize_unit(
            unit_id=verdicts.make_unit_id(
                phase_label=phase.label, benchmark="exam",
                task_ref=exam_id, role="weak", skills_injected=None),
            phase_label=phase.label, role="weak", injected_skills=None,
            runs=bare_runs, status=bare_status, cause=bare_cause,
            artifacts={"exam_id": exam_id,
                       "attempt_ids": [run["run"] for run in bare_runs]}))

        # -- the with-skill row: reuse, leak, skip, judged, or owed -------
        reused_row = reused.get(unit_id)
        if reused_row is not None:
            rows.append(_rebased_row(reused_row, unit_id=unit_id,
                                     phase_label=phase.label))
            continue
        leak = find_leak_verdict(ctx.store, skill.skill_id, skill.version)
        if leak is not None and leak.get("leaked"):
            rows.append(verdicts.summarize_unit(
                unit_id=unit_id, phase_label=phase.label, role="weak",
                injected_skills=injected, runs=[],
                status=verdicts.STATUS_NOT_TESTABLE,
                cause=verdicts.CAUSE_LEAK,
                artifacts={"exam_id": exam_id}))
            continue
        judged = sorted(
            (attempt for attempt in attempts
             if attempt.purpose == "acceptance"
             and attempt.skill_id == skill.skill_id
             and attempt.skill_version == skill.version
             and attempt.judge_verdict is not None),
            key=lambda attempt: ids.parse_attempt_id(attempt.attempt_id)[1])
        runs: List[Dict[str, Any]] = []
        for attempt in judged:
            verdict, basis = verdicts.verdict_from_run(
                _run_record_from_attempt(attempt), rule_name=rule_name)
            runs.append({"run": attempt.attempt_id, "verdict": verdict,
                         "basis": basis})
        if runs:
            rows.append(verdicts.summarize_unit(
                unit_id=unit_id, phase_label=phase.label, role="weak",
                injected_skills=injected, runs=runs,
                status=verdicts.STATUS_NORMAL, cause="",
                artifacts={"exam_id": exam_id,
                           "attempt_ids": [run["run"] for run in runs]}))
            continue
        if bare is not None and bare.judge_verdict:
            # The control was perfect, so the with-skill answer was
            # skipped (rule 3): zero executions, no headroom.
            rows.append(verdicts.summarize_unit(
                unit_id=unit_id, phase_label=phase.label, role="weak",
                injected_skills=injected, runs=[],
                status=verdicts.STATUS_NOT_RUN_CONTROL_PERFECT,
                cause=verdicts.CAUSE_WEAK_ALREADY_KNOWS,
                artifacts={"exam_id": exam_id}))
            continue
        status, cause = (
            (verdicts.STATUS_NOT_TESTABLE,
             verdicts.CAUSE_EXAM_GENERATION_FAILED)
            if any(judge_budget_exhausted(attempt)
                   for attempt in attempts
                   if attempt.purpose == "acceptance"
                   and attempt.skill_id == skill.skill_id
                   and attempt.skill_version == skill.version)
            else (verdicts.STATUS_NOT_TESTABLE,
                  verdicts.CAUSE_ZERO_EXECUTION))
        rows.append(verdicts.summarize_unit(
            unit_id=unit_id, phase_label=phase.label, role="weak",
            injected_skills=injected, runs=[], status=status, cause=cause,
            artifacts={"exam_id": exam_id}))
    return sorted(rows, key=lambda row: row.unit_id)


# ---------------------------------------------------------------------------
# The live phase (B)
# ---------------------------------------------------------------------------

def _live_phase_groups(ctx: StageContext, task_ids: Set[str]
                       ) -> Dict[str, List[Skill]]:
    """The live phase's group universe: the pending groups (the same
    store derivation the frozen audit line and the pending universe are
    built from), filtered to the phase's task set."""
    return {task_id: members
            for task_id, members in lm.pending_groups(ctx.store).items()
            if task_id in task_ids}


def _round_of(ctx: StageContext, phase: PhaseInstance) -> int:
    """The round THIS live phase measures: the k-th live phase of the
    sequence measures round ``k-1`` (the rounds are pinned to {0, 1},
    so a sequence carries at most two live phases -- the old two-round
    protocol, one phase per round now)."""
    earlier_live = sum(1 for record in _earlier_phases(ctx, phase)
                       if record.get("role") == PHASE_ROLE_LIVE)
    if earlier_live >= len(lm._ROUNDS):
        raise ConfigError(
            f"phase {phase.label!r} would measure live round "
            f"{earlier_live}, but the rounds are pinned to "
            f"{list(lm._ROUNDS)} (user ruling 8): a sequence carries at "
            "most two live phases")
    return earlier_live


def _round_complete(ctx: StageContext, task_id: str, round_: int,
                    n_attempts: int, skip: Sequence[str]) -> bool:
    """Ruling A10's completeness half, ported from the old driver: every
    expected unit is stored or an accepted hole (skip-aware -- the
    all-holes round is complete without a single trajectory)."""
    directory = lm.round_dir(ctx.store, round_)
    if not directory.exists():
        return not [unit for unit in _expected_live_ids(task_id,
                                                         n_attempts)
                    if unit not in set(skip)]
    round_store = Store(directory)
    stored = {unit for unit in round_store.ids("trajectories")
              if ids.parse_trajectory_id(unit)[0] == task_id}
    skipped = set(skip)
    return all(unit in stored or unit in skipped
               for unit in _expected_live_ids(task_id, n_attempts))


def _expected_live_ids(task_id: str, n_attempts: int) -> List[str]:
    return [ids.make_trajectory_id(task_id, "weak", attempt)
            for attempt in range(n_attempts)]


def _frozen_audit_row(store: Store, round_: int,
                      task_id: str) -> Optional[Dict[str, Any]]:
    """The group's frozen injection-audit row of one round, or None when
    the round was never dispatched for it (the old driver's lookup)."""
    for row in read_jsonl(lm.round_dir(store, round_)
                          / lm.INJECTION_AUDIT_NAME):
        if row.get("task_id") == task_id:
            return row
    return None


def _canonical_line(row: Dict[str, Any]) -> str:
    """The row as append_jsonl writes it: the frozen line and the
    re-derivation compare as these exact bytes (ruling A10)."""
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def _run_live(ctx: StageContext, phase: PhaseInstance,
              spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """One live round over every group that still owes it: the k-th
    live phase measures round ``k-1`` through the frozen verdicts-ledger
    protocol (``judge_round`` / ``finalize_group``, the coordinator's
    2026-10-16 ruling -- the ledger rows are the resume protocol and
    E6's pinned shape), with the round store, the injection-audit
    freeze and the shortfall records exactly where the old driver kept
    them. The attribution / revision pipeline is NOT here: a miss feeds
    the next learn phase (evidence -> author), and the live phase after
    that re-measures -- rule 1 reuses the unchanged groups (the no-op
    guard's phase-world shape) and re-runs only what the revision
    changed."""
    params = _execution_params(spec)
    n_attempts = (params.n_attempts if params.n_attempts is not None
                  else ctx.config.n_attempts_weak)
    if n_attempts > ctx.config.n_attempts_weak:
        raise ConfigError(
            f"phase {phase.label!r}: the live depth ({n_attempts}) exceeds "
            f"n_attempts_weak ({ctx.config.n_attempts_weak}); the baseline "
            "would not cover the live executions (the old cross-check, "
            "restated here because the config validation is bypassed "
            "when a spec is constructed directly)")
    round_ = _round_of(ctx, phase)
    rule_name = _compare_rule(spec)
    store = ctx.store
    _, aux_scorer = aux_wiring(ctx.config.benchmark)
    stage1_holes = shortfall.stage1_accepted_unit_ids(ctx.run_dir)

    # -- finalization sweep: finish any commit a crash left hanging -----
    for task_id in sorted(lm.pending_groups(store)):
        lv.finalize_group(store, task_id)

    benchmark = ctx.config.benchmark
    groups = _live_phase_groups(
        ctx, {ids.make_task_id(benchmark, raw) for raw in phase.task_ids})
    task_ids = sorted(groups)
    delivery, delivery_params = _resolve_delivery(ctx, spec)
    rows = delivery.resolve(lm.pending_groups(store), task_ids,
                           delivery_params)
    tasks = _prefixed_task_map(
        ctx, sorted(ids.raw_task_id(task_id, benchmark)
                    for task_id in task_ids))
    plans = _plan_units(rows, tasks, phase_label=phase.label,
                        benchmark=benchmark, role="weak",
                        n_attempts=n_attempts)
    _write_units(ctx, phase, [plan.unit for plan in plans])
    plan_by_task = {plan.unit.task_ref: plan for plan in plans}
    reused = _reused_rows(ctx, phase, [plan.unit for plan in plans])

    accepted = shortfall.read_unit_ids(
        lm.round_dir(store, round_) / shortfall.SHORTFALL_NAME)

    def skip_for(task_id: str) -> List[str]:
        return lm.skip_ids_for(stage1_holes, accepted, task_id)

    # -- entry guards and the dispatch set -------------------------------
    # Baselines load before a single execution is spent; the zero-
    # execution short-circuits write their terminal ledger rows (the
    # coordinator's ruling: judge_round + finalize_group, the old
    # driver's shape).
    dispatch: List[_Plan] = []
    baselines: Dict[str, Dict[str, int]] = {}
    infra_failed: Set[str] = set()
    for task_id in task_ids:
        plan = plan_by_task[task_id]
        if plan.unit.unit_id in reused:
            continue  # rule 1: the last verdict stands, nothing re-runs
        if _round_has_row(store, task_id, round_):
            # A resumed call already judged this group's round, and the
            # entry sweep finalized it; it owes this phase nothing.
            continue
        _, baseline_counts = lm.load_baseline(store, task_id, n_attempts,
                                              aux_scorer=aux_scorer)
        baselines[task_id] = baseline_counts
        if decision.control_perfect_skips_run(
                compare_rule=rule_name,
                control_valid=baseline_counts["valid"],
                control_success=baseline_counts["success"]):
            lv.judge_round(
                store=store, task_id=task_id, round_=round_,
                rule_name=rule_name, baseline_counts=baseline_counts,
                n_attempts=n_attempts, skip_ids=[],
                terminal=lv.WEAK_ALREADY_KNOWS,
                max_injection_tokens=params.max_injection_tokens)
            lv.finalize_group(store, task_id)
            continue
        if decision.live_injection_overflows(
                max_injection_tokens=params.max_injection_tokens,
                injection_tokens=estimate_tokens(plan.unit.skills_block)):
            # The overflow verdict must carry the frozen audit row's own
            # evidence: freeze it first (the old driver's order).
            _freeze_audit_row(store, task_id, round_)
            lv.judge_round(
                store=store, task_id=task_id, round_=round_,
                rule_name=rule_name, baseline_counts=baseline_counts,
                n_attempts=n_attempts, skip_ids=[],
                terminal=lv.LIVE_INJECTION_OVERFLOW,
                max_injection_tokens=params.max_injection_tokens)
            lv.finalize_group(store, task_id)
            continue
        if _round_complete(ctx, task_id, round_, n_attempts,
                           skip_for(task_id)):
            dispatch.append(plan)  # stored already; owes only judgment
            continue
        _freeze_audit_row(store, task_id, round_)
        dispatch.append(plan)

    # -- one K1 batch for the round (ruling A7) ---------------------------
    if dispatch:
        owing = [plan for plan in dispatch
                 if not _round_complete(ctx, plan.unit.task_ref, round_,
                                        n_attempts,
                                        skip_for(plan.unit.task_ref))]
        if owing:
            skip_unit_ids: Set[str] = set()
            for plan in owing:
                skip_unit_ids.update(skip_for(plan.unit.task_ref))
            by_task = {plan.task.task_id: (plan.unit.skills_injected,
                                           plan.unit.skills_block)
                       for plan in owing}

            def skills_for_task(task: Task) -> Tuple[Any, str]:
                return by_task[task.task_id]

            batch_execute(
                store=Store(lm.round_dir(store, round_)),
                benchmark=benchmark,
                tasks=[plan.task for plan in owing],
                role="weak",
                model_name=ctx.config.models["executor_weak"],
                handle=ctx.handle("executor_weak"),
                n_attempts=n_attempts,
                skills_for_task=skills_for_task,
                workdir_root=(Path(ctx.workdir_root)
                              / phase.label.replace(":", "_")
                              / f"round{round_:02d}"),
                usage=ctx.usage,
                run_dir=ctx.run_dir,
                workers=ctx.run_settings.workers,
                dispatch_stagger=ctx.run_settings.k1_stagger,
                skip_unit_ids=skip_unit_ids,
            )

    # -- shortfall first, then the judgment on the shrunken sets ---------
    if ctx.run_settings.allow_infra_shortfall:
        open_holes = set()
        for plan in dispatch:
            if _round_has_row(store, plan.unit.task_ref, round_):
                continue
            skip = set(skip_for(plan.unit.task_ref))
            open_holes.update(
                unit for unit in _expected_live_ids(plan.unit.task_ref,
                                                    n_attempts)
                if unit not in skip
                and not _round_stores(store, plan.unit.task_ref, round_))
        if open_holes:
            shortfall.write_unit_ids(
                lm.round_dir(store, round_) / shortfall.SHORTFALL_NAME,
                set(accepted) | open_holes)
    accepted = shortfall.read_unit_ids(
        lm.round_dir(store, round_) / shortfall.SHORTFALL_NAME)

    for plan in dispatch:
        task_id = plan.unit.task_ref
        if _round_has_row(store, task_id, round_):
            continue  # a terminal fired above, or a resume already judged
        if task_id not in baselines:
            _, baseline_counts = lm.load_baseline(
                store, task_id, n_attempts, aux_scorer=aux_scorer)
            baselines[task_id] = baseline_counts
        if _round_complete(ctx, task_id, round_, n_attempts,
                           skip_for(task_id)):
            lv.judge_round(
                store=store, task_id=task_id, round_=round_,
                rule_name=rule_name, baseline_counts=baselines[task_id],
                n_attempts=n_attempts, skip_ids=skip_for(task_id),
                aux_scorer=aux_scorer,
                max_injection_tokens=params.max_injection_tokens)
            lv.finalize_group(store, task_id)
        else:
            infra_failed.add(task_id)

    # -- the report: the frozen live reader over this round, plus the
    # rule-1 rows of the groups that never re-ran ------------------------
    # A zero-execution terminal is finalized before the reader runs, so
    # the reader's fallback (the pending members) sees an emptied group;
    # the delivery row is the truth about what the group was at the skip
    # decision, so its injected refs and id replace the reader's for the
    # groups this phase never dispatched.
    report = reports.acceptance_report(
        store, kind="live", benchmark=benchmark, phase_label=phase.label,
        round_=round_)
    by_id = {row.unit_id: row for row in report}
    for plan in plans:
        for row in report:
            if (decision._parse_unit_id(row.unit_id)[0]
                    == plan.unit.task_ref
                    and row.unit_id != plan.unit.unit_id):
                del by_id[row.unit_id]
                by_id[plan.unit.unit_id] = replace(
                    row, unit_id=plan.unit.unit_id,
                    injected_skills=plan.unit.skills_injected)
    for plan in plans:
        if plan.unit.unit_id in reused:
            by_id.setdefault(
                plan.unit.unit_id,
                _rebased_row(reused[plan.unit.unit_id],
                             unit_id=plan.unit.unit_id,
                             phase_label=phase.label))
    report = sorted(by_id.values(), key=lambda row: row.unit_id)
    _write_report(ctx, phase, report)

    summary = {
        "role": PHASE_ROLE_LIVE,
        "round": round_,
        "n_attempts": n_attempts,
        "improvement_rule": rule_name,
        "units": [plan.unit.unit_id for plan in plans],
        "dispatched": [plan.unit.task_ref for plan in dispatch],
        "skipped_reused": sorted(
            plan.unit.unit_id for plan in plans
            if plan.unit.unit_id in reused),
        "skipped_control_perfect": sorted(
            plan.unit.unit_id for plan in plans
            if _round_terminal(store, plan.unit.task_ref, round_)
            == lv.WEAK_ALREADY_KNOWS),
        "skipped_injection_overflow": sorted(
            plan.unit.unit_id for plan in plans
            if _round_terminal(store, plan.unit.task_ref, round_)
            == lv.LIVE_INJECTION_OVERFLOW),
        "infra_missing": sorted(infra_failed),
        "infra_accepted": sorted(accepted),
        "report_rows": len(report),
    }
    summary["complete"] = not summary["infra_missing"]
    summary["infra_shortfall_accepted"] = bool(accepted)
    write_json(_phase_dir(ctx, phase) / SUMMARY_NAME, summary, indent=2)
    if infra_failed:
        raise InfrastructureError(
            f"phase {phase.label!r}: {len(infra_failed)} live group(s) "
            "still have units pending on infrastructure errors "
            f"({', '.join(sorted(infra_failed))}); see the summary's "
            "infra_missing, then resume the run to retry them")
    return summary


def _round_has_row(store: Store, task_id: str, round_: int) -> bool:
    """Whether the group's round already carries a verdicts-ledger row."""
    return lm._first_row_from(
        lm._ledger_path(store, lm.VERDICTS_LEDGER), task_id,
        round_) is not None


def _round_terminal(store: Store, task_id: str,
                    round_: int) -> Optional[str]:
    """The group's round row terminal, if it wrote one."""
    row = lm._first_row_from(
        lm._ledger_path(store, lm.VERDICTS_LEDGER), task_id, round_)
    return row.get("verdict") if row is not None else None


def _round_stores(store: Store, task_id: str, round_: int) -> bool:
    """Whether the group's round store holds ANY trajectory of the task
    (False means nothing was stored for it at all)."""
    directory = lm.round_dir(store, round_)
    if not directory.exists():
        return False
    round_store = Store(directory)
    return any(ids.parse_trajectory_id(unit)[0] == task_id
               for unit in round_store.ids("trajectories"))


def _freeze_audit_row(store: Store, task_id: str, round_: int) -> None:
    """Ruling A10: freeze the audit row BEFORE the round's dispatch (the
    first write), or compare the re-derivation byte for byte against the
    frozen line (a resumed dispatch)."""
    row = lm.audit_row(store, task_id, round_)
    frozen = _frozen_audit_row(store, round_, task_id)
    if frozen is not None:
        if _canonical_line(frozen) != _canonical_line(row):
            raise CodeBugError(
                f"the frozen injection_audit row of task {task_id!r} "
                f"round {round_} differs from the re-derivation:\nstored: "
                f"{_canonical_line(frozen)}\nrecomputed: "
                f"{_canonical_line(row)}; the group's pending members "
                "changed under the frozen line -- refusing to re-dispatch")
    else:
        append_jsonl(lm.round_dir(store, round_)
                     / lm.INJECTION_AUDIT_NAME, row)


# ---------------------------------------------------------------------------
# The final-test phase (C)
# ---------------------------------------------------------------------------

def _expected_units(ctx: StageContext, tasks: Sequence[Task]
                    ) -> Dict[str, int]:
    """The arm-independent expected unit set: prefixed task id -> the
    frozen weak sampling depth (one denominator for both arms)."""
    return {ids.make_task_id(ctx.config.benchmark, task.task_id):
            ctx.config.n_attempts_weak for task in tasks}


def _missing_units(expected: Dict[str, int],
                   trajectories: Sequence[Trajectory]) -> List[str]:
    """Expected unit ids with no trajectory (sorted)."""
    stored = {traj.trajectory_id for traj in trajectories}
    return [ids.make_trajectory_id(task_id, "weak", attempt)
            for task_id in sorted(expected)
            for attempt in range(expected[task_id])
            if ids.make_trajectory_id(task_id, "weak", attempt)
            not in stored]


def _run_test(ctx: StageContext, phase: PhaseInstance,
              spec: ExecutePhaseSpec) -> Dict[str, Any]:
    """The with-bank arm over the test set against its control arm.

    Frozen C rules: no skip logic and no redispatch loop -- every unit
    re-runs on every call (only a crash resume inside the phase keeps
    its stored units, K1's own resume); no report is written (the C
    numbers go to the summary alone, boundary doc section 3 rule 9);
    the comparison is the final summary's own statistic
    (:func:`decision.final_test_delta`), never a decision input.

    The control is the baseline phase's stored weak bare trajectories
    (the run-level store, deterministic-id reuse -- full mode) or a
    fresh bare arm inside this phase (split mode). A missing control
    unit the baseline phase never accepted stops the phase BEFORE the
    with-bank arm spends anything: filling that hole can move the bank,
    so with-bank runs made now could be measured against the wrong
    bank."""
    _execution_params(spec)
    delivery, delivery_params = _resolve_delivery(ctx, spec)
    bank = _bank_skills(ctx, phase)
    benchmark = ctx.config.benchmark
    tasks = _task_objects(ctx, list(phase.task_ids))
    tasks_by_ref = _prefixed_task_map(ctx, [task.task_id for task in tasks])
    n_attempts = ctx.config.n_attempts_weak
    expected = _expected_units(ctx, tasks)
    reference = bank_reference(bank)
    aux_metric, aux_scorer = aux_wiring(benchmark)
    against = (spec.compare.against if spec.compare is not None
               else FRESH_BARE)
    control_source = decision.control_arm_source(
        reused_phase=None if against == FRESH_BARE else against)

    # -- the control first, and its completeness BEFORE any spend --------
    control_dir = _arm_dir(ctx, phase, "control")
    control_accepted: Set[str] = set()
    reused_control: Optional[List[Trajectory]] = None
    if against != FRESH_BARE:
        stage1_holes = set(shortfall.stage1_accepted_unit_ids(ctx.run_dir))
        reused_control = []
        missing_control = []
        for task_id in sorted(expected):
            for attempt in range(n_attempts):
                trajectory_id = ids.make_trajectory_id(task_id, "weak",
                                                      attempt)
                if trajectory_id in stage1_holes:
                    control_accepted.add(trajectory_id)
                    continue
                if ctx.store.exists("trajectories", trajectory_id):
                    reused_control.append(ctx.store.load(
                        "trajectories", trajectory_id, Trajectory))
                else:
                    missing_control.append(trajectory_id)
        if missing_control:
            raise InfrastructureError(
                f"phase {phase.label!r}: the reused control of {against!r} "
                f"is incomplete: {len(missing_control)} weak bare unit(s) "
                f"missing ({missing_control}); resume the run so the "
                "control phase retries them (a hole there can only be "
                "accepted in that phase)")

    # -- with-bank arm -----------------------------------------------------
    with_bank_dir = _arm_dir(ctx, phase, "with_bank")
    with_bank_store = Store(with_bank_dir / "store")
    with_bank_accepted = set(shortfall.read_unit_ids(
        with_bank_dir / shortfall.SHORTFALL_NAME))
    rows = delivery.resolve(bank, tasks, delivery_params)
    audit_rows = getattr(delivery, "audit_rows", None)
    if audit_rows is not None:
        # R5's side channel: the audit names the injection this dispatch is
        # about to render, written in full before dispatch (decision 15);
        # retrieval keeps the frozen stage-6 location the audit tooling
        # reads.
        delivery.write_audit(ctx.run_dir,
                             audit_rows(bank, tasks, delivery_params))
    plans = _plan_units(rows, tasks_by_ref, phase_label=phase.label,
                        benchmark=benchmark, role="weak",
                        n_attempts=n_attempts)
    _write_units(ctx, phase, [plan.unit for plan in plans])
    _dispatch_arm(ctx, phase, plans=plans, store=with_bank_store,
                  arm="with_bank", skip_unit_ids=with_bank_accepted)
    with_bank = [traj for traj in _arm_trajectories(with_bank_store)
                 if traj.trajectory_id not in with_bank_accepted]

    # -- control arm -------------------------------------------------------
    if reused_control is not None:
        control = [traj for traj in reused_control
                   if traj.trajectory_id not in control_accepted]
    else:
        control_accepted = set(shortfall.read_unit_ids(
            control_dir / shortfall.SHORTFALL_NAME))
        control_store = Store(control_dir / "store")
        bare_delivery, bare_params = method_registry.resolve_fixed(
            "delivery", "none")
        bare_rows = bare_delivery.resolve(None, tasks, bare_params)
        bare_plans = _plan_units(bare_rows, tasks_by_ref,
                                 phase_label=phase.label,
                                 benchmark=benchmark, role="weak",
                                 n_attempts=n_attempts)
        _dispatch_arm(ctx, phase, plans=bare_plans, store=control_store,
                      arm="control", skip_unit_ids=control_accepted)
        control = [traj for traj in _arm_trajectories(control_store)
                   if traj.trajectory_id not in control_accepted]

    missing = {"with_bank": _missing_units(expected, with_bank),
               "control": _missing_units(expected, control)}
    accepted_now = {"with_bank": with_bank_accepted,
                    "control": control_accepted}
    new_holes = {arm: [unit for unit in missing[arm]
                       if unit not in accepted_now[arm]]
                 for arm in ("with_bank", "control")}
    if any(new_holes.values()) and ctx.run_settings.allow_infra_shortfall:
        # Durable per arm, written BEFORE the summary reads the shrunken
        # sample (the stage-6 rule); the reused control's holes stay the
        # baseline phase's own record, never accepted here.
        for arm in ("with_bank", "control"):
            if new_holes[arm] and (reused_control is None
                                   or arm == "with_bank"):
                record = (with_bank_dir if arm == "with_bank"
                          else control_dir)
                shortfall.write_unit_ids(
                    record / shortfall.SHORTFALL_NAME,
                    accepted_now[arm] | set(new_holes[arm]))
        new_holes = {arm: [] for arm in ("with_bank", "control")}

    with_bank_metrics = metrics_block.compute_task_metrics(
        with_bank, expected=expected, aux_scorer=aux_scorer)
    control_metrics = metrics_block.compute_task_metrics(
        control, expected=expected, aux_scorer=aux_scorer)
    if set(with_bank_metrics) != set(control_metrics):
        raise CodeBugError(
            "the test phase's arms cover different task sets: with-bank "
            f"{sorted(with_bank_metrics)} vs control "
            f"{sorted(control_metrics)} (a delta over different task sets "
            "is exactly the bug this guard exists for)")
    with_bank_summary = metrics_block.summarize(
        with_bank_metrics, n_attempts, aux_metric=aux_metric)
    control_summary = metrics_block.summarize(
        control_metrics, n_attempts, aux_metric=aux_metric)
    n_missing = sum(len(unit_ids) for unit_ids in missing.values())
    n_new = sum(len(unit_ids) for unit_ids in new_holes.values())
    summary = {
        "role": PHASE_ROLE_TEST,
        "mode": ctx.config.mode,
        "bank_reference": reference,
        "control_source": control_source,
        "with_bank": with_bank_summary,
        "control": control_summary,
        "delta": decision.final_test_delta(with_bank_summary,
                                            control_summary),
        "infra_missing": missing,
        "infra_accepted": {arm: sorted(accepted_now[arm])
                           for arm in ("with_bank", "control")},
        "complete": not n_missing,
        "infra_shortfall_accepted": any(accepted_now.values()),
    }
    write_json(_phase_dir(ctx, phase) / SUMMARY_NAME, summary, indent=2)
    if n_new:
        raise InfrastructureError(
            f"phase {phase.label!r}: {n_new} unit(s) exhausted their infra "
            "retries and stored nothing (listed in the summary under "
            "infra_missing); resume the run to retry them, or accept the "
            "shortfall with --allow-infra-shortfall")
    return summary
