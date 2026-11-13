"""Trial-level coverage derivation tests (composites/coverage.py).

Coverage is read-only: it turns the phase's explicit terminal facts plus
the store's expected-vs-stored derivation into one CoverageRecord per
expected trial.  Each phase's rules are pinned against a real
``Store(tmp_path)`` with small saved Trajectory records.
"""

from expedistill.composites import coverage
from expedistill.core import ids
from expedistill.core.answer_sheet import UnitSpec
from expedistill.core.models import Step, Trajectory
from expedistill.infra.store import Store

TASK = "tb:task-1"
PHASE = "p03:practice"


def _trajectory(task_ref=TASK, role="weak", attempt=0, phase=PHASE,
                correctness=True, skills_injected=None):
    return Trajectory(
        trajectory_id=ids.make_trajectory_id(phase, task_ref, role, attempt),
        task_id=task_ref,
        benchmark="tb",
        model_role=role,
        model_name="model-a",
        attempt_index=attempt,
        skills_injected=skills_injected,
        steps=[Step(action="ls", observation="a.txt")],
        correctness=correctness,
    )


def _spec(unit_id, *, task_ref=TASK, role="weak", arm=None, phase=PHASE,
          expected_attempts=2, injected_skills=None):
    return UnitSpec(
        unit_id=unit_id,
        phase_label=phase,
        task_ref=task_ref,
        role=role,
        arm=arm,
        expected_attempts=expected_attempts,
        injected_skills=injected_skills,
    )


# ---------------------------------------------------------------------------
# One shared store (baseline, practice)
# ---------------------------------------------------------------------------

def test_store_coverage_present_missing_and_accepted_hole(tmp_path):
    store = Store(tmp_path)
    store.save(_trajectory(attempt=0), "trajectories")
    spec = _spec("unit:practice", expected_attempts=3)
    accepted = {ids.make_trajectory_id(PHASE, TASK, "weak", 1)}

    records = coverage.store_coverage(store, [spec], accepted)

    assert [(record.attempt_index, record.present, record.status,
             record.cause) for record in records] == [
        (0, True, "present", ""),
        (1, False, "missing", "accepted_hole"),
        (2, False, "missing", "missing"),
    ]


def test_store_coverage_reads_the_named_role_and_task(tmp_path):
    store = Store(tmp_path)
    store.save(_trajectory(attempt=0, role="strong", phase="p01:baseline"),
               "trajectories")
    spec = _spec("unit:baseline", role="strong", phase="p01:baseline",
                 expected_attempts=1)

    records = coverage.store_coverage(store, [spec], ())

    assert len(records) == 1
    assert records[0].present is True


def test_store_coverage_counts_only_the_specs_own_phase(tmp_path):
    # The run-level store holds the baseline's and every practice's runs
    # of the same task; a unit is covered by its own phase's run only.
    store = Store(tmp_path)
    store.save(_trajectory(phase="p01:baseline"), "trajectories")
    store.save(_trajectory(phase="p03:practice"), "trajectories")
    spec = _spec("unit:p05", phase="p05:practice", expected_attempts=1)

    records = coverage.store_coverage(store, [spec], ())

    assert [(record.present, record.cause) for record in records] == [
        (False, "missing")]


# ---------------------------------------------------------------------------
# Test phase (local arms and external rewards)
# ---------------------------------------------------------------------------

def _by_key(records):
    return {(record.unit_id, record.attempt_index): record
            for record in records}


def test_test_coverage_local_arms_and_external_rewards(tmp_path):
    with_bank = Store(tmp_path / "with_bank")
    with_bank.save(_trajectory("tb:t1", phase="p03:test"), "trajectories")
    with_bank.save(_trajectory("tb:t2", phase="p03:test"), "trajectories")
    specs = [
        _spec("unit:present", task_ref="tb:t1", arm="with_bank",
              phase="p03:test", expected_attempts=1),
        _spec("unit:hole", task_ref="tb:t2", arm="with_bank",
              phase="p03:test", expected_attempts=1),
        _spec("unit:no-store", task_ref="tb:t3", arm="control",
              phase="p03:test", expected_attempts=1),
        _spec("unit:external", task_ref="tb:t4", arm="external_control",
              phase="p03:test", expected_attempts=1),
    ]

    records = coverage.test_coverage(
        {"with_bank": with_bank}, specs,
        accepted_by_arm={
            "with_bank": {ids.make_trajectory_id(
                "p03:test", "tb:t2", "weak", 0)}},
        external_rewards={
            "external_control": {"tb:t4": [1.0, 0.0]}})

    by_key = _by_key(records)
    assert set(by_key) == {
        ("unit:present", 0),
        ("unit:hole", 0),
        ("unit:no-store", 0),
        ("unit:external", 0),
        ("unit:external", 1),
    }
    assert (by_key[("unit:present", 0)].present,
            by_key[("unit:present", 0)].status) == (True, "present")
    assert (by_key[("unit:hole", 0)].present,
            by_key[("unit:hole", 0)].status,
            by_key[("unit:hole", 0)].cause) == (
                False, "missing", "accepted_hole")
    assert (by_key[("unit:no-store", 0)].present,
            by_key[("unit:no-store", 0)].status,
            by_key[("unit:no-store", 0)].cause) == (
                False, "missing", "missing")
    # One present record per supplied external reward, indexed from zero.
    assert (by_key[("unit:external", 0)].present,
            by_key[("unit:external", 1)].present) == (True, True)
    assert all(by_key[("unit:external", index)].cause == ""
               for index in (0, 1))
