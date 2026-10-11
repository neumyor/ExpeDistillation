"""B5: business-agnostic bounded parallelism, results in input order.

Adapted from the SkillExpand repo's runtime/parallel.py (ExpeToSkill-merged
@ TB-eval 4f436af). Kept from there: the MAX_WORKERS clamp (a typo cannot ask
for 640 workers), the serial shortcut for a single worker, the spawn process
context on every platform (macOS defaults to it, other platforms may not),
and "cancel nothing, gather everything" so a slow first item cannot lose the
results of completed ones. Not migrated: benchmark-aware worker routing,
per-process config caching and the per-result worker timeout -- those are
owned by callers here, which wrap their ``fn`` if they need a deadline.
"""

import multiprocessing
from concurrent.futures import (
    BrokenExecutor, Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor,
)
from typing import Any, Callable, Dict, List, Sequence

from expedistill.infra.reliability import InfrastructureError, ReliabilityError

#: Hard ceiling, kept explicit so a config typo cannot ask for 640 workers.
MAX_WORKERS = 256


class ParallelError(ReliabilityError):
    """One or more items failed; ``failures`` maps item index to exception.

    All items get their chance to run (gather-then-raise), so a caller that
    records successes as they arrive has lost nothing when this is raised.
    """

    def __init__(self, message: str,
                 failures: Dict[int, BaseException]) -> None:
        super().__init__(message)
        self.failures = failures


def effective_workers(requested: int, n_items: int) -> int:
    """Clamp a worker count to something sane for the batch."""
    return max(1, min(int(requested), MAX_WORKERS, n_items))


def run_parallel(fn: Callable[[Any], Any], items: Sequence[Any],
                 workers: int = 1, *, thread: bool = True) -> List[Any]:
    """Apply ``fn`` to every item with a bounded pool, in input order.

    Results are returned in input order regardless of completion order; a
    resumed run that persists in submission order is therefore reproducible
    (the ordering guarantee SkillExpand's map_units gave its collectors).

    Per-item exceptions are gathered, not racing: if any item fails, every
    item still runs and a ParallelError is raised afterwards carrying all
    failures by index. A broken pool (a worker process died) is reported as
    an InfrastructureError: it is retryable by resuming, not a code bug.

    With ``thread=False`` the pool spawns processes, so ``fn`` and the items
    must be picklable module-level objects (closures cannot cross the
    boundary); prefer threads unless the unit holds the GIL.
    """
    items = list(items)
    if not items:
        return []
    n = effective_workers(workers, len(items))
    if n == 1 and thread:
        # Serial shortcut: same order, same error handling, no pool overhead.
        return _serial(fn, items)

    if thread:
        executor: Executor = ThreadPoolExecutor(
            max_workers=n, thread_name_prefix="run-parallel")
    else:
        executor = ProcessPoolExecutor(
            max_workers=n, mp_context=multiprocessing.get_context("spawn"))
    with executor:
        futures = [executor.submit(fn, item) for item in items]
        return _collect(futures, items)


def _serial(fn: Callable[[Any], Any],
            items: List[Any]) -> List[Any]:
    results: List[Any] = []
    failures: Dict[int, BaseException] = {}
    for index, item in enumerate(items):
        try:
            results.append(fn(item))
        except Exception as exc:  # noqa: BLE001 - reported per item below
            failures[index] = exc
    _raise_if_failed(failures, len(items))
    return results


def _collect(futures: List[Future], items: List[Any]) -> List[Any]:
    results: List[Any] = []
    failures: Dict[int, BaseException] = {}
    for index, future in enumerate(futures):
        try:
            results.append(future.result())
        except BrokenExecutor as exc:
            error = InfrastructureError(
                f"worker pool broke before item {index + 1}/{len(items)} "
                "completed; resume retries unfinished units")
            error.__cause__ = exc
            failures[index] = error
        except Exception as exc:  # noqa: BLE001 - reported per item below
            failures[index] = exc
    _raise_if_failed(failures, len(items))
    return results


def _raise_if_failed(failures: Dict[int, BaseException],
                     n_items: int) -> None:
    if not failures:
        return
    first_index = min(failures)
    first = failures[first_index]
    raise ParallelError(
        f"{len(failures)} of {n_items} item(s) failed "
        f"(first: item {first_index}: {type(first).__name__}: {first})",
        failures) from first
