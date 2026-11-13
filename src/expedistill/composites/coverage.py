"""Trial-level coverage records (layer 5, read-only).

Coverage answers "was this expected trial present, missing, or terminal and
why".  It is deliberately separate from grading: this module never decides
success/failure/invalid and never aggregates counts.  Its only output is a
list of ``CoverageRecord`` objects, one per expected trial (or one per
expected attempt of a terminal unit).

The phase drivers own the facts they observe (the accepted holes); they
pass them in as explicit sets.  This module owns the generic
expected-vs-stored derivation.
"""

from typing import Dict, Iterable, List, Optional, Sequence

from expedistill.core import ids, vocabulary
from expedistill.core.answer_sheet import CoverageRecord, UnitSpec
from expedistill.infra.store import Store

__all__ = [
    "store_coverage",
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


def store_coverage(
        store: Store, specs: Sequence[UnitSpec],
        accepted_holes: Iterable[str]) -> List[CoverageRecord]:
    """One record per expected trajectory of the arms that share one
    store (the baseline's two arms, a practice phase's executor)."""
    accepted = set(accepted_holes)
    out: List[CoverageRecord] = []
    for spec in specs:
        for attempt in range(spec.expected_attempts):
            trajectory_id = ids.make_trajectory_id(
                spec.phase_label, spec.task_ref, spec.role, attempt)
            if trajectory_id in accepted:
                out.append(_gap(spec.unit_id, attempt,
                                vocabulary.CAUSE_ACCEPTED_HOLE))
            elif store.exists("trajectories", trajectory_id):
                out.append(_present(spec.unit_id, attempt))
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
                spec.phase_label, spec.task_ref, spec.role, attempt)
            if trajectory_id in accepted:
                out.append(_gap(spec.unit_id, attempt,
                                vocabulary.CAUSE_ACCEPTED_HOLE))
            elif store.exists("trajectories", trajectory_id):
                out.append(_present(spec.unit_id, attempt))
            else:
                out.append(_gap(spec.unit_id, attempt,
                                vocabulary.CAUSE_MISSING))
    return out
