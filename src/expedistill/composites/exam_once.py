"""K3 "take one exam" (composite, layer 5): D9 -> G4 -> G5 -> one Attempt.

Wires three functional blocks into the single most-reused action of the
pipeline (README 1.5): the stage-4 bare answer, the stage-4 with-skill loop
and the stage-5 regression re-exam are all this function. The D9-formatted
skills block arrives as a parameter ("" for the bare control) because the
frozen signature hands it straight to G4 -- the caller (K4/K5) renders it
with ``inject.format_skills_block``, which keeps "acceptance-time injection
equals test-time injection" true by construction.

This composite owns the bookkeeping the blocks must not do:

* the attempt serial. It is *count-based*: serial = number of attempts already
  stored for this exam, so replaying an identical flow over the same stored
  set yields identical IDs. An unjudged attempt (see below) is stored, so it
  occupies its serial like any other.

* answer-first persistence (crash semantics). The weak model's answer (G4) is
  stored as soon as it returns, as an Attempt with ``judge_verdict=None``
  ("not judged yet"); the judge (G5) then fills in the verdict and the SAME
  attempt id is re-saved. On entry, before answering, K3 looks for an
  unjudged attempt of this exam with the identical (purpose, skill_id,
  skill_version, retry_round) -- the latest by serial -- and, if one exists,
  reuses its answer and only judges. So, by crash point:

  - crash during the answer call: nothing stored; the resumed call answers
    afresh under the same serial (the answer was never observed);
  - crash after the answer is stored (judge call or verdict save): the
    resumed call makes ONE judge call on the stored answer and completes the
    same attempt id -- the weak model is never re-asked, so the final record
    does not depend on where the crash happened (a resample at
    temperature > 0 would differ).

  Callers that resume by looking for stored attempts (K4, K5) must therefore
  only treat ``judge_verdict is not None`` as "done"; an unjudged attempt is
  in-flight work that the next K3 call with the same key completes.

* the composite-level repair budget. ``call_model`` already repairs JSON
  *format* failures internally; K3 adds a second line of defence of
  ``_BLOCK_REPAIR_ATTEMPTS`` (= 2) calls per block for contract violations
  that survive parsing (e.g. a judge verdict outside the vocabulary), as
  decided in design review. After those calls the error propagates:
  resampling forever would only burn tokens, and the caller (K4/K5) records
  the unit failure.

  The JUDGE budget is persistent, so it does not reset across a crash: each
  failed judge try is recorded on the stored, still-unjudged attempt by
  re-saving it with ``judge_reason = "judge_failed_tries=<n>; <error>"``
  (``judge_verdict`` stays None, so K4/K5 still see the attempt as not done).
  A resumed call continues with the remaining tries; if none remain it raises
  ModelOutputError at once, without a model call. A successful judge
  overwrites the marker with the verdict's own reason, so the final record
  does not carry the failure history (as in an uninterrupted run). The
  marker lives on the attempt itself rather than in a side ledger because the
  attempt file is written atomically and is the unit of identity: the count
  can never disagree with the attempt it belongs to, and deleting an attempt
  (which frees its count-based serial for reuse) also deletes its count, so
  a reused id never inherits a stale budget.

  The ANSWER budget is NOT persistent: until an answer succeeds nothing is
  stored, so a crash between two answer tries (or after answer exhaustion
  before the caller records it) gives the resumed call a fresh answer
  budget. Accepted as a reproducibility caveat: a failed answer is never
  observed downstream, so it cannot leak into any record.

* same-exam mutual exclusion (parallel-safety net). The serial above is
  allocated count-then-save, a check-then-act over the store: two
  concurrent calls on the SAME exam would both count the same stored set,
  allocate the same attempt id, and the second save would silently
  overwrite the first attempt's record -- evidence destroyed, no error
  raised. A per-exam-id lock therefore serializes the WHOLE body of
  exam_once for one exam, the model calls included. That is a deliberate
  choice over locking only the pending-lookup/serial/save span with the
  answer call outside: same-exam concurrency is not a scenario any caller
  has today (stage-4 units are per-skill with per-skill exams; the
  wave-parallel K5 design makes overlapping units structurally impossible
  within a wave), so the lock only ever contends when a caller bug shares
  an exam -- and contention then degrades to plain sequential calls, which
  are always semantically correct, while the narrower lock would buy only
  a duplicated answer call that no real scenario needs. Simplicity beats
  micro-parallelism in a safety net. Different exams take different locks
  and never block each other. The lock is per-process: keeping two
  PROCESSES off one run directory is B7's job (infra.runlock), not K3's.

  Contract: callers must not rely on same-exam parallelism -- it is not
  provided, by design.

K3 is NOT idempotent: every call is a fresh attempt by design (a retry round
answers the same exam again on purpose). Callers check completion -- i.e.
decide whether a JUDGED attempt with this meaning already exists -- before
calling; the only stored state this function reuses is an unjudged answer
with the identical key (above), never a judged attempt.
"""

import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from expedistill.blocks.exam import answer, judge
from expedistill.core import ids
from expedistill.core.models import ATTEMPT_PURPOSES, Attempt, Exam
from expedistill.infra.reliability import ModelOutputError, with_retries
from expedistill.infra.store import Store
from expedistill.llm.call import ModelHandle

__all__ = ["exam_once"]

#: Total calls per block when the block-level contract is broken (design
#: review): the first call plus one repair resample. Not configurable here --
#: the repair budget is an experiment-level decision, not a library knob.
_BLOCK_REPAIR_ATTEMPTS = 2

#: Prefix of the judge_reason an unjudged attempt carries after failed judge
#: tries (module docstring). Only meaningful while judge_verdict is None.
_JUDGE_FAILURES_PREFIX = "judge_failed_tries="
_JUDGE_FAILURES_RE = re.compile(
    re.escape(_JUDGE_FAILURES_PREFIX) + r"(\d+)(?:;|$)")

#: One threading.Lock per exam id: the serial is count-then-act, so calls
#: on the SAME exam must not interleave (module docstring). Keyed by exam
#: id, not by store, so two Store instances over one directory in one
#: process are covered too. Growth is bounded by the exams of the run
#: (themselves bounded by the frozen candidate set).
_EXAM_LOCKS: Dict[str, threading.Lock] = {}
_EXAM_LOCKS_GUARD = threading.Lock()


def _reset_exam_locks_in_child() -> None:
    # A fork copies locks held by threads that do not exist in the child;
    # start the child with fresh, unheld ones (same rationale as
    # infra.store's per-ledger registry -- B5 also offers a process pool).
    global _EXAM_LOCKS_GUARD
    _EXAM_LOCKS_GUARD = threading.Lock()
    _EXAM_LOCKS.clear()


os.register_at_fork(after_in_child=_reset_exam_locks_in_child)


def _exam_lock(exam_id: str) -> threading.Lock:
    """The lock of one exam, created lazily under the registry guard."""
    with _EXAM_LOCKS_GUARD:
        return _EXAM_LOCKS.setdefault(exam_id, threading.Lock())


def _next_serial(store: Store, exam_id: str) -> int:
    """Serial = attempts already stored for this exam (count-based).

    Counting (not max-of-serials) is what makes replayed flows reproduce IDs:
    delete a stored attempt and its serial is handed to the next call. Only
    IDs that parse as attempt IDs of *this* exam count, so other exams stored
    in the same run directory never shift the numbering.
    """
    serial = 0
    for stored_id in store.ids("attempts"):
        if ids.parse_attempt_id(stored_id)[0] == exam_id:
            serial += 1
    return serial


def _pending_attempt(store: Store, exam_id: str, purpose: str,
                     skill_id: Optional[str], skill_version: Optional[int],
                     retry_round: int) -> Optional[Attempt]:
    """The latest (by serial) unjudged attempt of this exam with this key.

    Such an attempt is an answer a previous call stored before its judge call
    completed (module docstring); judged attempts are never returned.
    """
    latest: Optional[Attempt] = None
    latest_serial = -1
    for stored_id in store.ids("attempts"):
        stored_exam, serial = ids.parse_attempt_id(stored_id)
        if stored_exam != exam_id or serial <= latest_serial:
            continue
        attempt = store.load("attempts", stored_id, Attempt)
        if (attempt.judge_verdict is None
                and attempt.purpose == purpose
                and attempt.skill_id == skill_id
                and attempt.skill_version == skill_version
                and attempt.retry_round == retry_round):
            latest, latest_serial = attempt, serial
    return latest


def _judge_failures(attempt: Attempt) -> int:
    """Failed judge tries recorded on an unjudged attempt (0 if none)."""
    if attempt.judge_verdict is not None:
        return 0
    match = _JUDGE_FAILURES_RE.match(attempt.judge_reason)
    return int(match.group(1)) if match else 0


def _judge_with_persistent_budget(store: Store, exam: Exam, attempt: Attempt,
                                  judge_handle: ModelHandle, usage: Any,
                                  run_dir: Optional[Union[str, Path]]
                                  ) -> Tuple[bool, str]:
    """G5 with a repair budget that survives a crash (module docstring)."""
    failures = _judge_failures(attempt)
    if failures >= _BLOCK_REPAIR_ATTEMPTS:
        raise ModelOutputError(
            f"JUDGE: repair budget already exhausted for {attempt.attempt_id} "
            f"({failures} failed tries stored)")
    while True:
        try:
            return judge(
                exam.question_text,
                exam.reference_answer,
                attempt.weak_answer,
                judge_handle,
                usage=usage,
                run_dir=run_dir,
            )
        except ModelOutputError as exc:
            failures += 1
            attempt.judge_reason = f"{_JUDGE_FAILURES_PREFIX}{failures}; {exc}"
            store.save(attempt, "attempts")
            if failures >= _BLOCK_REPAIR_ATTEMPTS:
                raise


def exam_once(
    *,
    store: Store,
    exam: Exam,
    answerer_handle: ModelHandle,
    judge_handle: ModelHandle,
    skills_block: str,
    purpose: str,
    skill_id: Optional[str] = None,
    skill_version: Optional[int] = None,
    retry_round: int = 0,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Attempt:
    """Answer one exam once (bare or with skills), judge it, record it.

    ``skills_block`` is the already-formatted D9 block ("" = bare control);
    ``purpose`` is one of ``core.models.ATTEMPT_PURPOSES``. ``skill_id`` /
    ``skill_version`` stay None for bare answers, so every stored Attempt
    distinguishes "no skill" from "skill X version N". ``retry_round`` records
    which stage-4 rewrite round the attempt belongs to (0 outside the loop).

    If an unjudged attempt with the identical key is stored (an earlier call
    crashed after answering), its answer is reused and only the judge runs.
    Both model calls get one block-level repair resample on ModelOutputError
    (see module docstring); exhaustion propagates. Answer exhaustion stores
    nothing (and its budget is not persisted); the judge budget is persisted
    on the unjudged attempt, so a resumed call only gets the tries that
    remain, and raises ModelOutputError without a model call when none do.
    Returns the stored (judged) Attempt.

    The whole body runs under the per-exam lock (module docstring): callers
    must not rely on same-exam parallelism.
    """
    if purpose not in ATTEMPT_PURPOSES:
        raise ValueError(
            f"purpose must be one of {ATTEMPT_PURPOSES}, got {purpose!r}")

    # Whole body under the per-exam lock: the serial allocation is
    # count-then-act over the store, so same-exam calls must not interleave
    # (module docstring). The model calls are inside on purpose -- the lock
    # only ever contends on a caller bug, and simplicity beats
    # micro-parallelism in a safety net.
    with _exam_lock(exam.exam_id):
        attempt = _pending_attempt(store, exam.exam_id, purpose, skill_id,
                                   skill_version, retry_round)
        if attempt is None:
            weak_answer = with_retries(
                lambda: answer(
                    exam.question_text,
                    skills_block,
                    answerer_handle,
                    usage=usage,
                    run_dir=run_dir,
                ),
                attempts=_BLOCK_REPAIR_ATTEMPTS,
                retry_on=ModelOutputError,
            )
            serial = _next_serial(store, exam.exam_id)
            attempt = Attempt(
                attempt_id=ids.make_attempt_id(exam.exam_id, serial),
                exam_id=exam.exam_id,
                skill_id=skill_id,
                skill_version=skill_version,
                purpose=purpose,
                weak_answer=weak_answer,
                judge_verdict=None,
                retry_round=retry_round,
            )
            # Persist the answer before judging: a crash in the judge call
            # must not cost (and later resample) the weak model's answer.
            store.save(attempt, "attempts")

        verdict, error_description = _judge_with_persistent_budget(
            store, exam, attempt, judge_handle, usage, run_dir)
        attempt.judge_verdict = verdict
        attempt.judge_reason = error_description
        store.save(attempt, "attempts")
        return attempt
