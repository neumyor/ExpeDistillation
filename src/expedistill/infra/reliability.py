"""B6: failure categories, retry loop, and the unit-failure ledger.

Adapted from the SkillExpand repo's reliability/ package (ExpeToSkill-merged
@ TB-eval 4f436af), collapsed into one module and mapped onto this design's
four dispositions (AGENTS.md section 6 rule 6):

======================  ============================  =======================
old Category           new class                     disposition
======================  ============================  =======================
INFRASTRUCTURE         InfrastructureError           retry
RESPONSE               ModelOutputError              retry, bounded repairs
PERMANENT              ProviderRefusalError          stop the current phase
PERMANENT              ConfigError                   stop the current phase
BUG                    CodeBugError                  stop everything
======================  ============================  =======================

Not migrated: the library-exception translation registry (this design has
one LLM client, so translation happens once at the call boundary), the
guard/FailureCollector stage machinery (composites own their units), the
named RepairPolicy table (repair budgets are experiment parameters that
live in the config and manifest, not in code), and ``exit_now`` (supervisor
process management belongs to scripts/, not to a library module).
"""

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from expedistill.infra.store import append_jsonl, read_jsonl


class ReliabilityError(Exception):
    """Base of every failure the pipeline knows how to handle.

    ``retryable`` decides the disposition: retry the work, or stop the
    current phase (default). CodeBugError stops everything.
    """

    retryable = False


class InfrastructureError(ReliabilityError):
    """Sandbox, network or timeout failure: the work is valid, the machine
    or service was not. Retried without bound from the caller's side."""

    retryable = True


class ModelOutputError(ReliabilityError):
    """Model output broke its declared format contract. Retried, but only a
    bounded number of repair attempts per call (resampling forever would
    only burn tokens); the bound is an experiment parameter."""

    retryable = True


class ProviderRefusalError(ReliabilityError):
    """The provider rejected the request (auth, permission, unknown model,
    oversized request): repeating the same request cannot help."""

    retryable = False


class ConfigError(ReliabilityError):
    """The run cannot proceed as configured. Stops the current phase; a
    human changes the configuration or starts a new run."""

    retryable = False


class CodeBugError(ReliabilityError):
    """A programming error. Stops everything, including sibling jobs:
    continuing would persist corrupt results at full speed."""

    retryable = False


def with_retries(fn: Callable[[], Any],
                 *,
                 attempts: int,
                 retry_on: Union[type, Tuple[type, ...]],
                 backoff: Union[float, Callable[[int], float]] = 0.0,
                 on_give_up: Optional[Callable[[BaseException], None]] = None,
                 sleep: Optional[Callable[[float], None]] = None) -> Any:
    """Call ``fn`` up to ``attempts`` times, retrying matching exceptions.

    ``attempts`` is the total number of calls (>= 1), not the number of
    retries. ``retry_on`` is an exception class or tuple; anything else
    propagates immediately. ``backoff`` is a constant delay in seconds or a
    callable receiving the 0-based count of failures so far. ``on_give_up``
    runs once after the final failure, before the exception is re-raised, so
    the caller can record the unit without a try/except of its own.

    Adapted from SkillExpand's retry.retry_transient: the same
    "retry only the declared categories, propagate everything else" rule,
    with the policy object replaced by explicit parameters (policies are
    experiment config here, not library constants).
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    if not isinstance(retry_on, tuple):
        retry_on = (retry_on,)
    sleeper = sleep if sleep is not None else time.sleep
    failure: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            return fn()
        except retry_on as exc:
            failure = exc
            if attempt + 1 < attempts:
                delay = backoff(attempt) if callable(backoff) else backoff
                if delay:
                    sleeper(delay)
    if on_give_up is not None:
        on_give_up(failure)
    assert failure is not None  # the loop only exits here after a failure
    raise failure


class UnitFailureRecorder:
    """Append-only ledger of failed units in ``<run_dir>/failed_units.jsonl``.

    Composites record a unit the moment it fails and move on; the ledger is
    what a resume reads to retry exactly those units. A failure is recorded
    separately from any success output and never silently dropped (AGENTS.md
    section 6 rule 6). Appends go through store.append_jsonl, which locks
    the ledger itself, so B5 worker threads (and other processes) can record
    concurrently without losing lines.
    """

    SCHEMA = "unit-failure-v1"
    #: Truncated, not dropped: a full traceback belongs in logs, not in the
    #: ledger every resume re-reads.
    MESSAGE_LIMIT = 1000

    def __init__(self, run_dir: Path) -> None:
        self.path = Path(run_dir) / "failed_units.jsonl"

    def record(self, unit_id: Any, stage: str, error_class: Any,
               message: Any) -> None:
        """Append one failure record.

        ``error_class`` accepts a class, an exception instance or a string;
        only the name is stored, so the ledger stays plain data that crosses
        process boundaries.
        """
        if isinstance(error_class, BaseException):
            error_class = type(error_class).__name__
        elif isinstance(error_class, type):
            error_class = error_class.__name__
        else:
            error_class = str(error_class)
        text = " ".join(str(message).split())
        if len(text) > self.MESSAGE_LIMIT:
            text = text[:self.MESSAGE_LIMIT - 3] + "..."
        append_jsonl(self.path, {
            "schema": self.SCHEMA,
            "unit_id": str(unit_id),
            "stage": stage,
            "error_class": error_class,
            "message": text,
            "time": time.time(),
        })

    def list_failures(self) -> List[Dict[str, Any]]:
        return read_jsonl(self.path)
