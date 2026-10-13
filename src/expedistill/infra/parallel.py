"""B5: business-agnostic bounded parallelism, results in input order.

Adapted from the SkillExpand repo's runtime/parallel.py (ExpeToSkill-merged
@ TB-eval 4f436af). Kept from there: the MAX_WORKERS clamp (a typo cannot ask
for 640 workers), the serial shortcut for a single worker, the spawn process
context on every platform (macOS defaults to it, other platforms may not),
and "cancel nothing, gather everything" so a slow first item cannot lose the
results of completed ones. Not migrated: benchmark-aware worker routing,
per-process config caching and the per-result worker timeout -- those are
owned by callers here, which wrap their ``fn`` if they need a deadline.

``run_units`` is the unit pool the stages share: one implementation of the
stop-on-fatal batch semantics instead of one per stage. Its fatal-error
rule: the first failure by unit order is re-raised unwrapped whatever its
kind, with a ParallelError carrying every failure chained as ``__cause__``.
(K1 batch_execute still has its own ``_reraise`` with the old
config/refusal-vs-bug split; unifying it onto run_units' rule is future
work.) Its results are in input order, but with ``workers > 1`` the order
in which workers append to LEDGER or audit files varies run to run:
callers must sort their summary lists or derive them from the store, never
from append order.
"""

import multiprocessing
import threading
from concurrent.futures import (
    BrokenExecutor, Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor,
)
from typing import (
    Any, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar, Union,
)

from expedistill.infra.reliability import (
    InfrastructureError, ReliabilityError,
)

#: Hard ceiling, kept explicit so a config typo cannot ask for 640 workers.
MAX_WORKERS = 256

_UnitType = TypeVar("_UnitType")
_OutcomeType = TypeVar("_OutcomeType")


class ParallelError(ReliabilityError):
    """One or more items failed; ``failures`` maps item index to exception.

    All items get their chance to run (gather-then-raise), so a caller that
    records successes as they arrive has lost nothing when this is raised.
    ``outcomes`` is the input-ordered result list ``run_units`` attaches
    (None from run_parallel): every slot holds its unit's outcome, or the
    UnitSkipped sentinel where the unit produced none.
    """

    def __init__(self, message: str,
                 failures: Dict[int, BaseException],
                 outcomes: Optional[List[Any]] = None) -> None:
        super().__init__(message)
        self.failures = failures
        self.outcomes = outcomes


class _UnitSkipped:
    """Type of the UnitSkipped sentinel; compare with ``is``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UnitSkipped"


#: Outcome of a unit that produced none: it was never started (the batch had
#: already stopped) or it raised. Such units stay pending and are re-offered
#: on resume; the store or ledger decides what "pending" means.
UnitSkipped = _UnitSkipped()


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


class _UnitPoolState:
    """Shared stop state of one run_units call (thread-safe).

    ``stop``: a unit raised a fatal error -> start nothing new. ``failures``
    maps unit index to the fatal exception; every in-flight unit that raises
    is recorded, even after the stop event is already set.
    """

    def __init__(self) -> None:
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self.failures: Dict[int, BaseException] = {}

    def record_failure(self, index: int, exc: BaseException) -> None:
        self.stop.set()
        with self._lock:
            self.failures[index] = exc


def run_units(worker: Callable[[_UnitType], _OutcomeType],
              units: Sequence[_UnitType],
              workers: int = 1) -> List[Union[_OutcomeType, _UnitSkipped]]:
    """Run ``worker(unit)`` over ``units``, stopping the batch at the first
    fatal error.

    The unit pool the stages share: one implementation of the stop/failure
    semantics (K1 batch_execute has its own stop flag now; the surfacing
    rule below is run_units' own, every caller gets it identically)
    instead of one per stage.

    - ``worker`` runs ONE unit and returns its outcome, whatever the caller
      wants collected. Expected-failure dispositions (ModelOutputError,
      InfrastructureError, ...) are handled INSIDE the worker: any exception
      escaping it is fatal by definition.
    - Units run on a bounded thread pool of ``workers`` threads (clamped by
      effective_workers; ``workers <= 1`` takes a serial path with identical
      semantics). A stop event internal to this call is checked before a
      unit starts: once a fatal error has set it, queued units are never
      started -- their result slot is the ``UnitSkipped`` sentinel -- while
      units already in flight run to completion and keep their outcome.
      There is no cross-call state.
    - Each fatal exception is recorded with its unit index; in-flight units
      that raise after the stop event is set are recorded too (several can
      race). After the pool drains, the FIRST failure by unit order -- not
      by wall-clock time -- is re-raised UNWRAPPED whatever its kind: a
      fresh instance of the original class whose message gained a "[N
      unit(s) failed, first unit index <i>; no further units were
      started]" note (the original object itself when its class cannot be
      built from one message). The ParallelError carrying EVERY failure --
      and the outcomes -- stays chained as ``__cause__``: the first fatal
      is the actionable error regardless of kind, and the full-failure
      context belongs in the cause chain, not on top of it. (K1
      batch_execute's ``_reraise`` still splits config/refusal from bugs;
      unifying it onto this rule is future work.)
    - Whatever is raised carries the outcomes in ``.outcomes`` (read it
      with ``getattr(exc, "outcomes", None)``): the input-ordered list,
      with UnitSkipped in every slot whose unit produced no outcome --
      never started, or failed. Those units stay pending and are re-offered
      on resume.
    - With no fatal error the outcomes are simply returned, in input
      order; the caller distinguishes skipped units by
      ``outcome is UnitSkipped``.

    Result order is input order, so aggregation is deterministic. The order
    in which workers append to LEDGER or audit files varies with
    ``workers``: sort summary lists or derive them from the store, never
    from append order. Thread-safety of caller resources (ledgers, the
    store) is the caller's business.
    """
    items = list(units)
    if not items:
        return []
    state = _UnitPoolState()

    def wrapped(pair: Tuple[int, _UnitType]) -> Any:
        index, unit = pair
        if state.stop.is_set():
            return UnitSkipped
        try:
            return worker(unit)
        except Exception as exc:  # noqa: BLE001 - fatal by contract, surfaced
            state.record_failure(index, exc)
            return UnitSkipped

    outcomes = run_parallel(wrapped, list(enumerate(items)), workers,
                            thread=True)
    _surface_failures(state.failures, outcomes, len(items))
    return outcomes


def _surface_failures(failures: Dict[int, BaseException],
                      outcomes: List[Any], n_units: int) -> None:
    """Re-raise the first fatal failure by unit order, unwrapped.

    Whatever its kind, the first fatal surfaces as its original type with
    its message plus the batch note (a fresh instance of the original
    class; the original object itself when the class cannot be built from
    one message): the first fatal is the actionable error regardless of
    kind. The ParallelError carrying EVERY failure stays chained as
    ``__cause__``, so no failure context is lost.
    """
    if not failures:
        return
    first_index = min(failures)
    first = failures[first_index]
    error = ParallelError(
        f"{len(failures)} of {n_units} unit(s) failed "
        f"(first: unit {first_index}: {type(first).__name__}: {first})",
        failures, outcomes)
    note = (f"{first} [B5: {len(failures)} unit(s) failed, first unit "
            f"index {first_index}; no further units were started]")
    try:
        surfaced = type(first)(note)
        surfaced.outcomes = outcomes
    except Exception:  # noqa: BLE001 - exotic class: surface the original
        surfaced = first
    raise surfaced from error
