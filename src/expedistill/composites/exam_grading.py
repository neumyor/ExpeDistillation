"""Exam grading: the judge half of "take one exam" (layer 5) -- the
acceptance module's grading member since R1 (boundaries doc §3.4:
everything that needs a model to judge belongs to acceptance; the entry
point the execution-side wiring calls is
``composites.verdicts.grade_attempt``).

The grading part of the former composites/exam_once.py, split out
mechanically in R0 (docs/decisions/execute_learn_repair_plan_20261016.md):
G5 with a repair budget that survives a crash. The JUDGE budget is
persistent, so it does not reset across a crash: each failed judge try
is recorded on the stored, still-unjudged attempt by re-saving it with
``judge_reason = "judge_failed_tries=<n>; <error>"`` (``judge_verdict``
stays None, so K4/K5 still see the attempt as not done). A resumed call
continues with the remaining tries; if none remain it raises
ModelOutputError at once, without a model call. A successful judge
overwrites the marker with the verdict's own reason, so the final record
does not carry the failure history (as in an uninterrupted run). The
marker lives on the attempt itself rather than in a side ledger because
the attempt file is written atomically and is the unit of identity: the
count can never disagree with the attempt it belongs to, and deleting an
attempt (which frees its count-based serial for reuse) also deletes its
count, so a reused id never inherits a stale budget.

The answer half lives in composites/exam_answering.py; the wiring of
the two is exam_once itself.
"""

import re
from pathlib import Path
from typing import Any, Optional, Tuple, Union

from expedistill.blocks.exam import judge
from expedistill.composites.exam_answering import _BLOCK_REPAIR_ATTEMPTS
from expedistill.core.models import Attempt, Exam
from expedistill.infra.reliability import ModelOutputError
from expedistill.infra.store import Store
from expedistill.llm.call import ModelHandle

#: Prefix of the judge_reason an unjudged attempt carries after failed judge
#: tries (module docstring). Only meaningful while judge_verdict is None.
_JUDGE_FAILURES_PREFIX = "judge_failed_tries="
_JUDGE_FAILURES_RE = re.compile(
    re.escape(_JUDGE_FAILURES_PREFIX) + r"(\d+)(?:;|$)")


def _judge_failures(attempt: Attempt) -> int:
    """Failed judge tries recorded on an unjudged attempt (0 if none)."""
    if attempt.judge_verdict is not None:
        return 0
    match = _JUDGE_FAILURES_RE.match(attempt.judge_reason)
    return int(match.group(1)) if match else 0


def judge_budget_exhausted(attempt: Attempt) -> bool:
    """True when an unjudged attempt's persistent judge budget is spent
    (a judged attempt never is): the exam pipeline terminally failed to
    produce this run's measurement. The coverage layer maps such an
    attempt to ``not_testable``/``exam_generation_failed`` -- an unjudged
    attempt whose budget merely survived a crash (fewer stored tries) is
    in-flight work, ``zero_execution``, not a terminal failure."""
    return _judge_failures(attempt) >= _BLOCK_REPAIR_ATTEMPTS


def judge_with_persistent_budget(store: Store, exam: Exam, attempt: Attempt,
                                 judge_handle: ModelHandle, usage: Any,
                                 run_dir: Optional[Union[str, Path]]
                                 ) -> Tuple[bool, str]:
    """G5 with a repair budget that survives a crash (module docstring).

    Public since R1: acceptance's grading entry
    (``composites.verdicts.grade_attempt``) delegates here, and
    ``exam_once`` goes through that entry -- the judge is acceptance's,
    only the wiring is execution-side."""
    failures = _judge_failures(attempt)
    if judge_budget_exhausted(attempt):
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
