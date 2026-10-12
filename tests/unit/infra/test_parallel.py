"""B5 run_parallel + run_units: ordered results, clamping, stop-on-fatal."""

import threading
import time

import pytest

from expedistill.infra.parallel import (
    MAX_WORKERS, ParallelError, UnitSkipped, effective_workers, run_parallel,
    run_units,
)
from expedistill.infra.reliability import (
    CodeBugError, ConfigError, ProviderRefusalError,
)


def _double(x):
    # Module-level: must be importable in spawn worker processes.
    return 2 * x


def test_results_preserve_input_order_with_thread_pool():
    # Later items finish first; results must still come back in input order.
    def slow_reverse(item):
        index, value = item
        time.sleep((5 - index) * 0.01)
        return value

    items = [(i, i) for i in range(5)]
    assert run_parallel(slow_reverse, items, workers=3) == [0, 1, 2, 3, 4]


def test_single_worker_runs_serially():
    order = []

    def note(item):
        order.append(item)
        return item

    assert run_parallel(note, [1, 2, 3], workers=1) == [1, 2, 3]
    assert order == [1, 2, 3]


def test_empty_items_return_empty():
    assert run_parallel(_double, [], workers=4) == []


def test_process_pool_preserves_order():
    assert run_parallel(_double, [1, 2, 3, 4], workers=2, thread=False) == \
        [2, 4, 6, 8]


def test_failure_raises_parallel_error_with_all_items_run():
    calls = []

    def flaky(item):
        calls.append(item)
        if item == 1:
            raise ValueError(f"boom {item}")
        return item

    with pytest.raises(ParallelError) as excinfo:
        run_parallel(flaky, [0, 1, 2, 3], workers=4)
    error = excinfo.value
    # Every item got its chance to run despite the early failure.
    assert sorted(calls) == [0, 1, 2, 3]
    # The per-item failure is reachable by index.
    assert set(error.failures) == {1}
    assert isinstance(error.failures[1], ValueError)
    assert "boom 1" in str(error)


def test_parallel_error_is_reliability_error():
    from expedistill.infra.reliability import ReliabilityError
    assert issubclass(ParallelError, ReliabilityError)


def test_effective_workers_clamps():
    assert effective_workers(1000, 3) == 3
    assert effective_workers(4, 10) == 4
    assert effective_workers(0, 10) == 1
    assert effective_workers(-3, 10) == 1
    assert effective_workers(2, 0) == 1
    assert MAX_WORKERS == 256


# ---------------------------------------------------------------------------
# run_units: the shared stop-on-fatal unit pool (stages dispatch through it)
# ---------------------------------------------------------------------------

def test_run_units_serial_returns_outcomes_in_input_order():
    order = []

    def worker(unit):
        order.append(unit)
        return unit * 10

    outcomes = run_units(worker, [1, 2, 3, 4], workers=1)
    assert outcomes == [10, 20, 30, 40]
    # Serial means serial: one unit at a time, in input order, no skips.
    assert order == [1, 2, 3, 4]
    assert not any(outcome is UnitSkipped for outcome in outcomes)


def test_run_units_parallel_preserves_input_order_under_a_barrier():
    barrier = threading.Barrier(4, timeout=10)

    def worker(unit):
        # The barrier only opens when 4 units are really in flight at once,
        # so this also proves the pool runs the requested concurrency.
        barrier.wait()
        time.sleep(0.01)
        return unit * unit

    units = list(range(20))
    outcomes = run_units(worker, units, workers=4)
    assert outcomes == [unit * unit for unit in units]
    assert not any(outcome is UnitSkipped for outcome in outcomes)


def test_run_units_fatal_config_error_stops_scheduling_and_surfaces_unwrapped():
    three_started = threading.Event()
    two_raising = threading.Event()
    started = []
    lock = threading.Lock()

    def worker(unit):
        with lock:
            started.append(unit)
        if unit == 2:
            # Wait until unit 3 is in flight, then fail fatally.
            assert three_started.wait(5)
            two_raising.set()
            raise ConfigError("bad model role")
        if unit == 3:
            three_started.set()
            # Already in flight when unit 2 fails: runs to completion.
            assert two_raising.wait(5)
            time.sleep(0.2)
            return "outcome-3"
        return f"outcome-{unit}"

    with pytest.raises(ConfigError) as excinfo:
        run_units(worker, list(range(6)), workers=2)
    error = excinfo.value
    # Unwrapped, original message plus the batch note.
    assert type(error) is ConfigError
    assert "bad model role" in str(error)
    assert "1 unit(s) failed" in str(error)
    assert "first unit index 2" in str(error)
    assert isinstance(error.__cause__, ParallelError)
    # Units 0-3 ran (2 fatally, 3 to completion); 4 and 5 never started.
    assert sorted(started) == [0, 1, 2, 3]
    outcomes = error.outcomes
    assert outcomes[0] == "outcome-0"
    assert outcomes[1] == "outcome-1"
    assert outcomes[2] is UnitSkipped  # failed: no outcome
    assert outcomes[3] == "outcome-3"  # in-flight unit's outcome kept
    assert outcomes[4] is UnitSkipped
    assert outcomes[5] is UnitSkipped


@pytest.mark.parametrize("error_class", [CodeBugError, ValueError])
def test_run_units_fatal_bug_surfaces_unwrapped_with_failures_chained(
        error_class):
    started = []
    lock = threading.Lock()

    def worker(unit):
        with lock:
            started.append(unit)
        if unit == 1:
            raise error_class("broken unit")
        return f"outcome-{unit}"

    with pytest.raises(error_class) as excinfo:
        run_units(worker, [0, 1, 2, 3], workers=2)
    error = excinfo.value
    # A bug surfaces like any fatal: its own type, its message plus the
    # batch note -- never a ParallelError wrapper.
    assert type(error) is error_class
    assert "broken unit" in str(error)
    assert "1 unit(s) failed" in str(error)
    assert "first unit index 1" in str(error)
    # The ParallelError carrying every failure stays chained as the cause.
    cause = error.__cause__
    assert isinstance(cause, ParallelError)
    assert set(cause.failures) == {1}
    assert isinstance(cause.failures[1], error_class)
    assert "1 of 4 unit(s) failed" in str(cause)
    # Unit 1's failure stopped the batch before unit 2 was started.
    assert sorted(started) == [0, 1]
    assert error.outcomes == [
        "outcome-0", UnitSkipped, UnitSkipped, UnitSkipped]


def test_run_units_fatal_with_exotic_constructor_surfaces_the_original():
    class TwoArgError(Exception):
        # Cannot be rebuilt from one message, so the pool cannot attach the
        # batch note: the original object itself must surface.
        def __init__(self, code, message):
            super().__init__(f"{code}: {message}")

    original = TwoArgError(7, "needs two arguments")

    def worker(unit):
        if unit == 0:
            raise original
        return f"outcome-{unit}"

    with pytest.raises(TwoArgError) as excinfo:
        run_units(worker, [0, 1], workers=1)
    error = excinfo.value
    # Same identity, same message, still chained to the full-failure cause.
    assert error is original
    assert "needs two arguments" in str(error)
    cause = error.__cause__
    assert isinstance(cause, ParallelError)
    assert set(cause.failures) == {0}
    assert cause.failures[0] is original


def test_run_units_first_failure_by_unit_order_not_by_time():
    one_failed = threading.Event()
    started = []
    lock = threading.Lock()

    def worker(unit):
        with lock:
            started.append(unit)
        if unit == 0:
            # Started before unit 1 fails, raises after the stop event is
            # already set -- and still wins the re-raise (unit order).
            assert one_failed.wait(5)
            raise ProviderRefusalError("zero refused")
        if unit == 1:
            one_failed.set()
            raise ConfigError("one is misconfigured")
        return f"outcome-{unit}"

    with pytest.raises(ProviderRefusalError) as excinfo:
        run_units(worker, list(range(6)), workers=2)
    error = excinfo.value
    assert "zero refused" in str(error)
    assert "2 unit(s) failed" in str(error)
    assert "first unit index 0" in str(error)
    # Both in-flight raises were recorded with their unit index.
    cause = error.__cause__
    assert isinstance(cause, ParallelError)
    assert set(cause.failures) == {0, 1}
    assert isinstance(cause.failures[0], ProviderRefusalError)
    assert isinstance(cause.failures[1], ConfigError)
    assert sorted(started) == [0, 1]
    assert all(outcome is UnitSkipped for outcome in error.outcomes)


def test_run_units_mixed_config_and_bug_failures_surface_the_first():
    one_failed = threading.Event()

    def worker(unit):
        if unit == 0:
            assert one_failed.wait(5)
            raise ConfigError("late config problem")
        if unit == 1:
            one_failed.set()
            raise CodeBugError("early bug")
        return f"outcome-{unit}"

    with pytest.raises(ConfigError) as excinfo:
        run_units(worker, [0, 1, 2, 3], workers=2)
    error = excinfo.value
    # The first failure by unit order surfaces, bug among the failures or
    # not: the kind split is gone.
    assert type(error) is ConfigError
    assert "late config problem" in str(error)
    assert "2 unit(s) failed" in str(error)
    assert "first unit index 0" in str(error)
    # Every failure -- including the bug -- stays in the chained cause.
    cause = error.__cause__
    assert isinstance(cause, ParallelError)
    assert set(cause.failures) == {0, 1}
    assert isinstance(cause.failures[0], ConfigError)
    assert isinstance(cause.failures[1], CodeBugError)
    assert all(outcome is UnitSkipped for outcome in error.outcomes)


def test_run_units_serial_fatal_refusal_matches_parallel_semantics():
    started = []

    def worker(unit):
        started.append(unit)
        if unit == 1:
            raise ProviderRefusalError("endpoint rejects the model")
        return f"outcome-{unit}"

    with pytest.raises(ProviderRefusalError) as excinfo:
        run_units(worker, [0, 1, 2, 3], workers=1)
    error = excinfo.value
    assert "endpoint rejects the model" in str(error)
    assert "1 unit(s) failed" in str(error)
    assert "first unit index 1" in str(error)
    # Identical semantics on the serial path: later units are skipped,
    # earlier outcomes are kept, the failure surfaces unwrapped.
    assert started == [0, 1]
    assert error.outcomes == [
        "outcome-0", UnitSkipped, UnitSkipped, UnitSkipped]


def test_run_units_clamps_workers_like_run_parallel():
    # Negative and zero worker counts clamp to one (serial), as run_parallel.
    for workers in (0, -3):
        order = []

        def worker(unit):
            order.append(unit)
            return unit

        assert run_units(worker, [1, 2, 3], workers=workers) == [1, 2, 3]
        assert order == [1, 2, 3]
    # A huge count is capped by the unit count, not rejected.
    assert run_units(lambda unit: unit * 2, [1, 2, 3], workers=1000) == \
        [2, 4, 6]


def test_run_units_empty_returns_empty():
    assert run_units(lambda unit: unit, [], workers=8) == []
