"""B5 run_parallel: ordered results, clamping, per-item error gathering."""

import time

import pytest

from expedistill.infra.parallel import (
    MAX_WORKERS, ParallelError, effective_workers, run_parallel,
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
