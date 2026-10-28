"""K3 exam answering: the answer half of "take one exam" (layer 5).

The answering part of the former composites/exam_once.py, split out
mechanically in R0 (docs/decisions/execute_learn_repair_plan_20261016.md):
the per-exam bookkeeping (the count-based attempt serial, the unjudged
pending-attempt lookup, the same-exam lock) and the answer step --
``answer_exam`` runs G4 under its repair budget and persists the answer
as an unjudged Attempt BEFORE any judge call (answer-first persistence;
see composites/exam_once.py for the crash semantics that rest on it).
The judge half lives in composites/exam_grading.py; the wiring of the
two is exam_once itself.
"""

import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from expedistill.blocks.exam import choose_skills, write_plan
from expedistill.core import ids
from expedistill.core.models import Attempt, Exam, Skill, Step
from expedistill.infra.store import Store
from expedistill.llm.call import ModelHandle

__all__ = ["answer_exam"]

#: Total calls per block when the block-level contract is broken (design
#: review): the first call plus one repair resample. Not configurable here --
#: the repair budget is an experiment-level decision, not a library knob.
_BLOCK_REPAIR_ATTEMPTS = 2

#: One threading.Lock per exam id: the serial is count-then-act, so calls
#: on the SAME exam must not interleave (see composites/exam_once.py).
#: Keyed by exam id, not by store, so two Store instances over one
#: directory in one process are covered too. Growth is bounded by the
#: exams of the run (themselves bounded by the frozen candidate set).
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
    completed (see composites/exam_once.py); judged attempts are never
    returned.
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


def _aliases(folder: Mapping[str, Any]) -> Dict[str, str]:
    """Map every model-visible skill name to its canonical skill id."""
    aliases: Dict[str, str] = {}
    for entry in folder.get("files") or []:
        if not isinstance(entry, Mapping):
            continue
        skill_id = entry.get("skill_id")
        filename = entry.get("filename")
        if not isinstance(skill_id, str) or not isinstance(filename, str):
            continue
        dirname = filename.split("/", 1)[0]
        for name in (skill_id, filename, dirname, f"{dirname}-SKILL.md"):
            aliases.setdefault(name, skill_id)
    return aliases


def _skill_contents(store: Store, skill_ids: Sequence[str]) -> List[str]:
    """The opened skills' bodies, in the model's order."""
    out: List[str] = []
    for skill_id in skill_ids:
        skill = store.load("skills", skill_id, Skill)
        out.append(skill.content)
    return out


def _cat_step(folder: Mapping[str, Any], skill_ids: Sequence[str],
              bodies: Sequence[str]) -> Step:
    """The synthetic skill-read step: the cat commands and their output."""
    by_id = {
        entry.get("skill_id"): entry.get("filename")
        for entry in folder.get("files") or []
        if isinstance(entry, Mapping)
    }
    commands = [f"cat {by_id[skill_id]}" for skill_id in skill_ids
                if by_id.get(skill_id)]
    action = "\n".join(commands) if commands else "# no skill opened"
    return Step(action=action, observation="\n\n".join(bodies))


def answer_exam(
    *,
    store: Store,
    exam: Exam,
    answerer_handle: ModelHandle,
    skill_folder: Optional[Mapping[str, Any]] = None,
    skills_block: Optional[str] = None,
    purpose: str,
    skill_id: Optional[str] = None,
    skill_version: Optional[int] = None,
    retry_round: int = 0,
    max_skill_reads: int = 3,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Attempt:
    """Answer one exam once and store the answer as an unjudged Attempt.

    The generalization exam asks two questions: which skills to open, then
    what plan follows.  A bare control has no catalog and skips the first
    question.  ``skills_block`` is the regression-exam fallback (the merged
    skill's body): it skips catalog selection too and answers from that one
    body.  The answer is persisted before judging, so a crash in the judge
    never costs the model's plan.
    """
    if purpose not in ("bare", "acceptance", "regression"):
        raise ValueError(f"unexpected purpose {purpose!r}")
    if skills_block is not None:
        selected_ids: List[str] = []
        selection_reason = "the regression exam supplies one fixed skill body"
        opened = skills_block
        read_step = Step(action="# fixed skill body", observation=opened)
    elif skill_folder is None:
        selected_ids = []
        selection_reason = ""
        opened = ""
        read_step = Step(action="# no skill catalog", observation="")
    else:
        catalog = str(skill_folder.get("catalog_text") or "")
        aliases = _aliases(skill_folder)
        selected, selection_reason = choose_skills(
            exam.scene_text, catalog, list(aliases), max_skill_reads,
            answerer_handle, usage=usage, run_dir=run_dir)
        selected_ids = [aliases[name] for name in selected]
        bodies = _skill_contents(store, selected_ids)
        opened = "\n\n".join(bodies)
        read_step = _cat_step(skill_folder, selected_ids, bodies)
    plan = write_plan(
        exam.scene_text, opened, selection_reason, answerer_handle,
        usage=usage, run_dir=run_dir)
    serial = _next_serial(store, exam.exam_id)
    attempt = Attempt(
        attempt_id=ids.make_attempt_id(exam.exam_id, serial),
        exam_id=exam.exam_id,
        skill_id=skill_id,
        skill_version=skill_version,
        purpose=purpose,
        weak_answer=plan,
        steps=[read_step, Step(action=plan, observation="")],
        judge_verdict=None,
        retry_round=retry_round,
    )
    store.save(attempt, "attempts")
    return attempt
