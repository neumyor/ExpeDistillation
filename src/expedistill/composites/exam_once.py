"""K3 "take one exam" (composite, layer 5): answer one exam once, judge
it, record it -- D9 -> G4 -> G5 -> one Attempt.

The single most-reused action of the pipeline (README 1.5): the stage-4
bare answer, the stage-4 with-skill loop and the stage-5 regression
re-exam are all this function. Since R0
(docs/decisions/execute_learn_repair_plan_20261016.md) the two halves it
wires live in their own files, split out mechanically with no behavior
change: the answer half (the count-based serial, the unjudged
pending-attempt lookup, the same-exam lock, G4 under its repair budget)
in composites/exam_answering.py, the judge half (G5 with a crash-persistent
repair budget) in composites/exam_grading.py. The D9-formatted skills
block arrives as a parameter ("" for the bare control) because the
frozen signature hands it straight to G4 -- the caller (K4/K5) renders it
with ``inject.format_skills_block``, which keeps "acceptance-time
injection equals test-time injection" true by construction.

This composite owns the bookkeeping the halves must not do:

* answer-first persistence (crash semantics). The weak model's answer
  (G4) is stored as soon as it returns, as an Attempt with
  ``judge_verdict=None`` ("not judged yet"); the judge (G5) then fills in
  the verdict and the SAME attempt id is re-saved. On entry, before
  answering, K3 looks for an unjudged attempt of this exam with the
  identical (purpose, skill_id, skill_version, retry_round) -- the latest
  by serial -- and, if one exists, reuses its answer and only judges. So,
  by crash point:

  - crash during the answer call: nothing stored; the resumed call
    answers afresh under the same serial (the answer was never
    observed);
  - crash after the answer is stored (judge call or verdict save): the
    resumed call makes ONE judge call on the stored answer and completes
    the same attempt id -- the weak model is never re-asked, so the final
    record does not depend on where the crash happened (a resample at
    temperature > 0 would differ).

  Callers that resume by looking for stored attempts (K4, K5) must
  therefore only treat ``judge_verdict is not None`` as "done"; an
  unjudged attempt is in-flight work that the next K3 call with the same
  key completes.

* same-exam mutual exclusion (parallel-safety net). The serial is
  allocated count-then-save, a check-then-act over the store: two
  concurrent calls on the SAME exam would both count the same stored set,
  allocate the same attempt id, and the second save would silently
  overwrite the first attempt's record -- evidence destroyed, no error
  raised. A per-exam-id lock therefore serializes the WHOLE body of
  exam_once for one exam, the model calls included. That is a deliberate
  choice over locking only the pending-lookup/serial/save span with the
  answer call outside: same-exam concurrency is not a scenario any caller
  has today (stage-4 units are per-skill with per-skill exams; the
  wave-parallel K5 design makes overlapping units structurally
  impossible within a wave), so the lock only ever contends when a caller
  bug shares an exam -- and contention then degrades to plain sequential
  calls, which are always semantically correct, while the narrower lock
  would buy only a duplicated answer call that no real scenario needs.
  Simplicity beats micro-parallelism in a safety net. Different exams
  take different locks and never block each other. The lock is
  per-process: keeping two PROCESSES off one run directory is B7's job
  (infra.runlock), not K3's.

  Contract: callers must not rely on same-exam parallelism -- it is not
  provided, by design.

K3 is NOT idempotent: every call is a fresh attempt by design (a retry
round answers the same exam again on purpose). Callers check completion
-- i.e. decide whether a JUDGED attempt with this meaning already exists
-- before calling; the only stored state this function reuses is an
unjudged answer with the identical key (above), never a judged attempt.
"""

from pathlib import Path
from typing import Any, Optional, Union

from expedistill.composites.exam_answering import (
    _exam_lock,
    _pending_attempt,
    answer_exam,
)
from expedistill.composites.exam_grading import _judge_with_persistent_budget
from expedistill.core.models import ATTEMPT_PURPOSES, Attempt, Exam
from expedistill.infra.store import Store
from expedistill.llm.call import ModelHandle

__all__ = ["exam_once"]


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
    distinguishes "no skill" from "skill X version N". ``retry_round``
    records which stage-4 rewrite round the attempt belongs to (0 outside
    the loop).

    If an unjudged attempt with the identical key is stored (an earlier
    call crashed after answering), its answer is reused and only the
    judge runs. Both model calls get one block-level repair resample on
    ModelOutputError (see the two halves' docstrings); exhaustion
    propagates. Answer exhaustion stores nothing (and its budget is not
    persisted); the judge budget is persisted on the unjudged attempt, so
    a resumed call only gets the tries that remain, and raises
    ModelOutputError without a model call when none do.
    Returns the stored (judged) Attempt.

    The whole body runs under the per-exam lock (module docstring):
    callers must not rely on same-exam parallelism.
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
            attempt = answer_exam(
                store=store, exam=exam, answerer_handle=answerer_handle,
                skills_block=skills_block, purpose=purpose,
                skill_id=skill_id, skill_version=skill_version,
                retry_round=retry_round, usage=usage, run_dir=run_dir)

        verdict, error_description = _judge_with_persistent_budget(
            store, exam, attempt, judge_handle, usage, run_dir)
        attempt.judge_verdict = verdict
        attempt.judge_reason = error_description
        store.save(attempt, "attempts")
        return attempt
