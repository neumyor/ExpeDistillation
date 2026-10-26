"""Trial-level coverage records (layer 5, read-only).

Coverage answers "was this expected trial present, missing, or terminal and
why".  It is deliberately separate from grading: this module never decides
success/failure/invalid and never aggregates counts.  Its only output is a
list of ``CoverageRecord`` objects, one per expected trial (or one per
expected attempt of a terminal unit).

The phase drivers own the facts they observe (a control-perfect skip, an
injection overflow, a leaked exam version, a judge-budget exhaustion); they
pass them in as explicit sets/maps.  This module owns the generic
expected-vs-stored derivation.
"""

from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from expedistill.composites import acceptance_skill_status as status
from expedistill.composites import exam_grading, live_measurement as lm
from expedistill.core import ids, vocabulary
from expedistill.core.answer_sheet import CoverageRecord, UnitSpec
from expedistill.infra.store import Store

__all__ = [
    "baseline_coverage",
    "exam_coverage",
    "live_coverage",
    "test_coverage",
]


def _present(unit_id: str, attempt_index: int,
             cause: str = "") -> CoverageRecord:
    return CoverageRecord(
        unit_id=unit_id, attempt_index=attempt_index, present=True,
        status=vocabulary.COVERAGE_PRESENT, cause=cause)


def _gap(unit_id: str, attempt_index: int, cause: str) -> CoverageRecord:
    return CoverageRecord(
        unit_id=unit_id, attempt_index=attempt_index, present=False,
        status=(vocabulary.COVERAGE_MISSING
                if cause in (vocabulary.CAUSE_MISSING,
                             vocabulary.CAUSE_ACCEPTED_HOLE)
                else vocabulary.COVERAGE_TERMINAL),
        cause=cause)


def baseline_coverage(
        store: Store, specs: Sequence[UnitSpec],
        accepted_holes: Iterable[str]) -> List[CoverageRecord]:
    """One record per expected trajectory of the baseline's arms."""
    accepted = set(accepted_holes)
    out: List[CoverageRecord] = []
    for spec in specs:
        for attempt in range(spec.expected_attempts):
            trajectory_id = ids.make_trajectory_id(
                spec.task_ref, spec.role, attempt)
            if trajectory_id in accepted:
                out.append(_gap(spec.unit_id, attempt,
                                vocabulary.CAUSE_ACCEPTED_HOLE))
            elif store.exists("trajectories", trajectory_id):
                out.append(_present(spec.unit_id, attempt))
            else:
                out.append(_gap(spec.unit_id, attempt,
                                vocabulary.CAUSE_MISSING))
    return out


def exam_coverage(
        store: Store, specs: Sequence[UnitSpec], *,
        control_perfect_units: Set[str],
        leak_versions: Set[Tuple[str, int]],
        generation_failed_units: Set[str],
        terminal_overrides: Optional[Dict[str, str]] = None
        ) -> List[CoverageRecord]:
    """Coverage for one exam's bare control and each measured skill version.

    The phase driver supplies the terminal facts it observed.  An unjudged
    attempt still inside its judge budget is ``zero_execution``; a spent
    budget is ``exam_generation_failed``.
    """
    overrides = terminal_overrides or {}
    out: List[CoverageRecord] = []
    for spec in specs:
        if spec.unit_id in overrides:
            out.append(_gap(spec.unit_id, 0, overrides[spec.unit_id]))
            continue
        if spec.unit_id in control_perfect_units:
            out.append(_gap(spec.unit_id, 0,
                            vocabulary.CAUSE_WEAK_ALREADY_KNOWS))
            continue
        if spec.unit_id in generation_failed_units:
            out.append(_gap(spec.unit_id, 0,
                            vocabulary.CAUSE_EXAM_GENERATION_FAILED))
            continue
        injected = spec.injected_skills
        if isinstance(injected, list) and any(
                isinstance(entry, dict)
                and (entry.get("skill_id"), entry.get("version"))
                in leak_versions
                for entry in injected):
            out.append(_gap(spec.unit_id, 0, vocabulary.CAUSE_LEAK))
            continue

        attempts = status.exam_attempts(store, spec.task_ref)
        if not injected:
            judged = status.find_bare(attempts)
            if judged is not None and judged.judge_verdict is not None:
                out.append(_present(
                    spec.unit_id,
                    ids.parse_attempt_id(judged.attempt_id)[1]))
            elif any(exam_grading.judge_budget_exhausted(attempt)
                     for attempt in attempts
                     if attempt.purpose == "bare"
                     and attempt.skill_id is None):
                # find_bare() returns only judged attempts; a spent judge
                # budget lives on an unjudged bare attempt, so scan those.
                out.append(_gap(spec.unit_id, 0,
                                vocabulary.CAUSE_EXAM_GENERATION_FAILED))
            else:
                out.append(_gap(spec.unit_id, 0,
                                vocabulary.CAUSE_ZERO_EXECUTION))
            continue

        entry = injected[0] if isinstance(injected, list) else {}
        skill_id = entry.get("skill_id")
        version = entry.get("version")
        judged = [attempt for attempt in attempts
                  if attempt.purpose == "acceptance"
                  and attempt.skill_id == skill_id
                  and attempt.skill_version == version
                  and attempt.judge_verdict is not None]
        if judged:
            for attempt in judged:
                out.append(_present(
                    spec.unit_id,
                    ids.parse_attempt_id(attempt.attempt_id)[1]))
        elif any(exam_grading.judge_budget_exhausted(attempt)
                 for attempt in attempts
                 if attempt.purpose == "acceptance"
                 and attempt.skill_id == skill_id
                 and attempt.skill_version == version):
            out.append(_gap(spec.unit_id, 0,
                            vocabulary.CAUSE_EXAM_GENERATION_FAILED))
        else:
            out.append(_gap(spec.unit_id, 0,
                            vocabulary.CAUSE_ZERO_EXECUTION))
    return out


def live_coverage(
        store: Store, specs: Sequence[UnitSpec], *, round_: int,
        terminal_overrides: Dict[str, str],
        zero_execution_units: Iterable[str] = (),
        accepted_holes: Iterable[str] = ()) -> List[CoverageRecord]:
    """Coverage for one live round's task groups.

    ``terminal_overrides`` maps a unit id to a coverage cause (control
    perfect, injection overflow, undetermined, leak).  A group whose
    dispatch never completed is ``zero_execution``.  Everything else is
    present/missing per expected trajectory.
    """
    accepted = set(accepted_holes)
    zero = set(zero_execution_units)
    round_store = Store(lm.round_dir(store, round_))
    out: List[CoverageRecord] = []
    for spec in specs:
        cause = terminal_overrides.get(spec.unit_id)
        if cause is None and spec.unit_id in zero:
            cause = vocabulary.CAUSE_ZERO_EXECUTION
        for attempt in range(spec.expected_attempts):
            trajectory_id = ids.make_trajectory_id(
                spec.task_ref, spec.role, attempt)
            # Accepted holes keep their own fact; a group terminal applies
            # only to an attempt with no stored trajectory.  A stored but
            # unscored attempt stays present (it is a run) and carries the
            # terminal cause so the unit summary can still report all_holes
            # instead of inventing zero_execution.
            if trajectory_id in accepted:
                out.append(_gap(spec.unit_id, attempt,
                                vocabulary.CAUSE_ACCEPTED_HOLE))
            elif round_store.exists("trajectories", trajectory_id):
                out.append(_present(spec.unit_id, attempt,
                                    cause or ""))
            elif cause is not None:
                out.append(_gap(spec.unit_id, attempt, cause))
            else:
                out.append(_gap(spec.unit_id, attempt,
                                vocabulary.CAUSE_MISSING))
    return out


def test_coverage(
        stores: Dict[str, Store], specs: Sequence[UnitSpec], *,
        accepted_by_arm: Dict[str, Iterable[str]],
        external_rewards: Optional[Dict[str, Dict[str, Sequence[float]]]] = None
        ) -> List[CoverageRecord]:
    """Coverage for the final test's arms (local and external).

    ``stores`` maps ``with_bank`` / ``control`` to the arm store; an external
    control arm is named in ``external_rewards`` and produces one present
    record per supplied reward.
    """
    accepted_by_arm = {arm: set(ids_) for arm, ids_ in accepted_by_arm.items()}
    external_rewards = external_rewards or {}
    out: List[CoverageRecord] = []
    for spec in specs:
        arm = spec.arm or ""
        rewards = external_rewards.get(arm, {}).get(spec.task_ref)
        if rewards is not None:
            for index in range(len(rewards)):
                out.append(_present(spec.unit_id, index))
            continue
        store = stores.get(arm)
        if store is None:
            for attempt in range(spec.expected_attempts):
                out.append(_gap(spec.unit_id, attempt,
                                vocabulary.CAUSE_MISSING))
            continue
        accepted = accepted_by_arm.get(arm, set())
        for attempt in range(spec.expected_attempts):
            trajectory_id = ids.make_trajectory_id(
                spec.task_ref, spec.role, attempt)
            if trajectory_id in accepted:
                out.append(_gap(spec.unit_id, attempt,
                                vocabulary.CAUSE_ACCEPTED_HOLE))
            elif store.exists("trajectories", trajectory_id):
                out.append(_present(spec.unit_id, attempt))
            else:
                out.append(_gap(spec.unit_id, attempt,
                                vocabulary.CAUSE_MISSING))
    return out
