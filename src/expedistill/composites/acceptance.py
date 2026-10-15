"""K4 acceptance: prove one skill actually teaches the weak model (layer 5).

README stage 4 / composite row K4: one bare answer first; only a skill whose
bare answer is WRONG and whose with-skill answer is CORRECT is accepted. The
loop is [with-skill exam (K3) -> rewrite (G6) -> leak audit (D8)] with at most
``retry_limit`` rewrites after the first attempt. The leak audit is a model
judge (D8, ``blocks.audit.audit_leak``) made through the caller's auditor
handle. A version the judge marks as leaked is voided and its round is
consumed (README: 该版作废，本轮算用掉一次): the rewrite happens without a
with-skill attempt for the voided version, and the judge's reason travels
into the rewrite's error description. This composite owns all disk IO and
resume for the flow; the pieces it wires (K3 via ``exam_once``, G6, D8, D9)
stay single-purpose.

Leak-verdict ledger ``stage4_acceptance/leak_verdicts.jsonl``: one JSON row
``{"skill_id": str, "skill_version": int, "leaked": bool, "reason": str}``
per (skill, version), the FIRST verdict the judge gave for that version.
The row is appended BEFORE the round acts on the verdict. The judge is
nondeterministic, so first verdict sticks: a resumed run looks the verdict
up and never asks the judge again for the same (skill, version) -- resume
must equal an uninterrupted run, which a re-ask could not guarantee.

Round accounting: rounds are numbered 0..retry_limit inclusive, so a run makes
at most retry_limit+1 with-skill attempts and retry_limit rewrites. Every
round except the last ends in exactly one rewrite OUTCOME: either a new
version (stored right away, with its history entry) or a G6 repair budget
exhausted on the unchanged version (one line per failed try in
``REWRITE_FAILURE_LEDGER``, the last try marked ``exhausted``). Hence

    completed rounds = (version - 1) + exhausted rewrite rounds

and that count is the next round. It is derived from records that are
written anyway, so the Skill object needs no round field (and no schema
bump); see docs/decisions/acceptance_resume_20261011.md.

Persistence order is what makes resume equal to an uninterrupted run at
every crash point:

* the skill is stored right after each rewrite, BEFORE the new version is
  audited or examined, so a stored attempt for (skill_id, version) was
  always taken on the stored content of that version;
* every leak verdict is appended to ``LEAK_VERDICTS_LEDGER`` exactly once
  per (skill, version) before it is acted on -- a resumed round finds the
  row and does not re-ask the judge (first verdict sticks);
* each failed G6 try is appended before the next one, so a resumed round
  continues the same repair budget instead of starting a fresh one;
* a terminal status is stored last; until then the verdict that decides it
  is already on disk and is found again.

Resume model (every check runs BEFORE any model call; stored work is reused,
never re-asked):

* a stored skill in a terminal status is returned unchanged;
* a stored acceptance attempt with a True verdict ON THE STORED VERSION
  accepts the stored skill (an attempt on any other version proves nothing
  about the stored content);
* a stored bare attempt with a True verdict discards it (weak_already_knows);
* a stored bare attempt with a False verdict is reused instead of re-asked;
* a stored with-skill attempt for (skill_id, version, retry_round) with a
  verdict is reused instead of re-asked;
* a stored leak verdict for the working version is reused: leaked=true
  voids the round exactly as before the crash, leaked=false proceeds to the
  exam.

What resume cannot save is a model call whose result was not yet persisted
(the call in flight, a K3 answer or verdict whose attempt save had not
finished, a rewrite whose skill save had not finished, a leak verdict whose
ledger line had not finished appending): those are asked again. K3 stores
the answer as an unjudged attempt before judging, so every lookup above
requires a verdict (``judge_verdict is not None``); an unjudged attempt is
completed by the next K3 call with the same key, not decided on. Failures
the wired blocks raise after their own repair budget (ModelOutputError,
InfrastructureError -- from K3, G6 after ``_REWRITE_ATTEMPTS``, or the D8
judge after call_model's budget) propagate unchanged; deciding what a failed
unit means for the stage is the caller's job (stage 4 records it, see
``composites.skill_status.discard_skill``).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from expedistill.blocks.audit import audit_leak
from expedistill.blocks.author import rewrite_skill
from expedistill.composites.exam_once import exam_once
from expedistill.core import ids
from expedistill.core.models import Attempt, Exam, Skill
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.infra.store import Store, append_jsonl, read_jsonl
from expedistill.inject.skills import format_skills_block
from expedistill.llm.call import ModelHandle

__all__ = ["LEAK_VERDICTS_LEDGER", "REWRITE_FAILURE_LEDGER", "accept_skill"]

#: Ledger of leak-audit verdicts, written into stage4_acceptance/ next to
#: the attempts: one row per (skill, version), the first verdict the judge
#: gave for it (schema in the module docstring). It doubles as the discard
#: bookkeeping: every leaked=true row is a voided version.
LEAK_VERDICTS_LEDGER = "leak_verdicts.jsonl"

#: Ledger of failed G6 tries, next to LEAK_VERDICTS_LEDGER. One line per try
#: that raised ModelOutputError; the try that exhausts the budget carries
#: ``exhausted: true`` and is what marks its round as consumed.
REWRITE_FAILURE_LEDGER = "rewrite_failures.jsonl"

#: Repair budget for one rewrite call. call_model already repairs malformed
#: JSON per request; this is the composite-level budget for the whole G6 call.
#: Exhausting it consumes the round like a failed attempt (see
#: _rewrite_round).
_REWRITE_ATTEMPTS = 2

#: Prefix of the error description synthesized for an audit-voided rewrite.
_VOID_NOTE = "The previous skill version was voided by the leak audit: "


def _ledger_path(store: Store, name: str = LEAK_VERDICTS_LEDGER) -> Path:
    # stage4_acceptance/ is the parent of the attempts directory.
    return Path(store.dir_for("attempts")).parent / name


def _skill_records(store: Store, name: str, skill_id: str
                   ) -> List[Dict[str, Any]]:
    """This skill's lines of one stage-4 ledger, in append order."""
    return [record for record in read_jsonl(_ledger_path(store, name))
            if record.get("skill_id") == skill_id]


def _find_leak_verdict(store: Store, skill_id: str,
                       skill_version: int) -> Optional[Dict[str, Any]]:
    """The stored judge verdict for one (skill, version), if any.

    The first matching row wins; only one row per (skill, version) is ever
    appended, but reading the first keeps the lookup honest even if a
    hand-edited ledger ever holds duplicates.
    """
    for record in _skill_records(store, LEAK_VERDICTS_LEDGER, skill_id):
        if record.get("skill_version") == skill_version:
            return record
    return None


def _serial(attempt: Attempt) -> int:
    # The numeric serial, never the id string: serials >= 100 widen the id,
    # and "att100" sorts before "att99" as text.
    return ids.parse_attempt_id(attempt.attempt_id)[1]


def _exam_attempts(store: Store, exam_id: str) -> List[Attempt]:
    """All stored attempts of one exam, in serial order."""
    attempts = []
    for attempt_id in store.ids("attempts"):
        attempt = store.load("attempts", attempt_id, Attempt)
        if attempt.exam_id == exam_id:
            attempts.append(attempt)
    return sorted(attempts, key=_serial)


def _find_bare(attempts: List[Attempt]) -> Optional[Attempt]:
    """The judged bare attempt of the exam, if an interrupted run left one."""
    for attempt in attempts:
        if (attempt.purpose == "bare" and attempt.skill_id is None
                and attempt.judge_verdict is not None):
            return attempt
    return None


def _find_accepted(attempts: List[Attempt], skill_id: str,
                   skill_version: int) -> Optional[Attempt]:
    """A judged acceptance attempt that passed ON this version.

    The version must match: a passing attempt on another version says
    nothing about the stored content, and accepting on it would let text
    that was never examined into the bank.
    """
    for attempt in attempts:
        if (attempt.purpose == "acceptance" and attempt.skill_id == skill_id
                and attempt.skill_version == skill_version
                and attempt.judge_verdict is True):
            return attempt
    return None


def _find_round_attempt(attempts: List[Attempt], skill_id: str,
                        skill_version: int, retry_round: int
                        ) -> Optional[Attempt]:
    """The judged with-skill attempt for one (version, round) pair, if an
    interrupted run stored it between the exam and the rewrite."""
    for attempt in attempts:
        if (attempt.purpose == "acceptance" and attempt.skill_id == skill_id
                and attempt.skill_version == skill_version
                and attempt.retry_round == retry_round
                and attempt.judge_verdict is not None):
            return attempt
    return None


def _last_with_skill(attempts: List[Attempt], skill_id: str,
                     before_round: int) -> Optional[Attempt]:
    """The most recent judged with-skill attempt of an already completed
    round (rewrite context on resume).

    Only rounds before ``before_round`` count: the resumed round's own
    attempt, if stored, is found again by the loop. Round is the primary
    order; the attempt serial breaks ties.
    """
    judged = [a for a in attempts
              if a.purpose == "acceptance" and a.skill_id == skill_id
              and a.retry_round < before_round
              and a.judge_verdict is not None]
    if not judged:
        return None
    return max(judged, key=lambda a: (a.retry_round, _serial(a)))


def _void_error_description(source_attempt: Attempt, report: str) -> str:
    """Deterministic error description for an audit-voided rewrite.

    The rewrite prompt needs a weak answer and an error description. For a
    voided version the failure being repaired is the leak, so the audit
    report is always carried. The source attempt's judge reason (why the weak
    model's answer was wrong) is kept as extra context when present: with no
    with-skill attempt yet (round 0) the source is the bare attempt, whose
    judge reason still says how the weak model misunderstood the question.
    """
    note = _VOID_NOTE + report
    reason = (source_attempt.judge_reason or "").strip()
    return f"{reason} {note}" if reason else note


def _rewrite_round(store: Store, skill: Skill, exam: Exam, retry_round: int,
                   weak_answer: str, error_description: str,
                   weak_profile: str, rewriter_handle: ModelHandle,
                   usage: Any, run_dir: Optional[Union[str, Path]]) -> bool:
    """Close one round with a rewrite outcome; True when a new version exists.

    The G6 call gets a repair budget of ``_REWRITE_ATTEMPTS`` on
    ModelOutputError. Each failed try is appended to
    ``REWRITE_FAILURE_LEDGER`` before the next one, so a resumed round
    continues the same budget. When the budget is exhausted the round is
    consumed like a failed with-skill attempt: no rewrite, no version bump,
    the loop moves on to the next round -- retrying immediately would only
    burn tokens, and the next round re-audits the unchanged content and
    tries the rewrite again. A successful rewrite is stored before this
    returns, so the next version is never examined unsaved.
    """
    failed_tries = [
        record for record in _skill_records(
            store, REWRITE_FAILURE_LEDGER, skill.skill_id)
        if record.get("retry_round") == retry_round
        and record.get("skill_version") == skill.version]
    for try_index in range(len(failed_tries), _REWRITE_ATTEMPTS):
        try:
            result = rewrite_skill(
                skill, exam.question_text, weak_answer, error_description,
                weak_profile, rewriter_handle, usage=usage, run_dir=run_dir)
        except ModelOutputError as exc:
            append_jsonl(_ledger_path(store, REWRITE_FAILURE_LEDGER), {
                "schema": "rewrite-failure-v1",
                "skill_id": skill.skill_id,
                "skill_version": skill.version,
                "exam_id": exam.exam_id,
                "retry_round": retry_round,
                "try_index": try_index,
                "exhausted": try_index + 1 >= _REWRITE_ATTEMPTS,
                "message": " ".join(str(exc).split()),
            })
            continue
        skill.rewrite_history.append({
            "content": skill.content,
            "error_category": result["failure_category"],
            "reason": result["rewrite_reason"],
        })
        skill.content = result["content"]
        skill.version += 1
        store.save(skill, "skills")
        return True
    return False


def _next_round(store: Store, skill: Skill) -> int:
    """Rounds already closed by a rewrite outcome (module docstring)."""
    exhausted = [record for record in _skill_records(
                     store, REWRITE_FAILURE_LEDGER, skill.skill_id)
                 if record.get("exhausted")]
    return (skill.version - 1) + len(exhausted)


def accept_skill(
    *,
    store: Store,
    skill: Skill,
    exam: Exam,
    weak_profile: str,
    answerer_handle: ModelHandle,
    judge_handle: ModelHandle,
    rewriter_handle: ModelHandle,
    auditor_handle: ModelHandle,
    retry_limit: int,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Skill:
    """Run the K4 acceptance loop for one (skill, exam) pair; return the
    skill with its final status, stored.

    The stored skill is the resume source of truth: rewrites bump the version
    in place, so the object passed in may be older than what is on disk, and
    resume must always read the latest version. Makes no model call when the
    verdicts are already on disk (a stored leak verdict for the working
    version included: the judge is never asked twice).
    """
    if retry_limit < 0:
        raise ValueError(f"retry_limit must be >= 0, got {retry_limit}")
    if skill.skill_id != ids.make_skill_id(skill.subtask_id):
        # Only stage-3 candidates are accepted on their own subtask's exam.
        # A merged skill (stage 5) spans several subtasks and is proven by
        # K5's regression exams; accepting it here on the anchor's exam alone
        # would put an unregressed skill into the bank.
        raise CodeBugError(
            f"accept_skill got {skill.skill_id!r}, which is not the stage-3 "
            f"skill of subtask {skill.subtask_id!r}")

    if store.exists("skills", skill.skill_id):
        working = store.load("skills", skill.skill_id, Skill)
    else:
        working = skill
        store.save(working, "skills")

    if working.status != "pending":
        # accepted / discarded / merged are all terminal here: re-entering
        # the loop would re-ask questions already answered on disk.
        return working

    attempts = _exam_attempts(store, exam.exam_id)

    accepted = _find_accepted(attempts, working.skill_id, working.version)
    if accepted is not None:
        working.status = "accepted"
        store.save(working, "skills")
        return working

    bare = _find_bare(attempts)
    if bare is None:
        bare = exam_once(
            store=store, exam=exam, answerer_handle=answerer_handle,
            judge_handle=judge_handle, skills_block="", purpose="bare",
            usage=usage, run_dir=run_dir)
    if bare.judge_verdict:
        # The weak model already answers this exam without any skill, so the
        # skill cannot teach it anything (README stage 4 step 1).
        working.status = "discarded"
        working.discard_reason = "weak_already_knows"
        store.save(working, "skills")
        return working

    start_round = _next_round(store, working)
    last_with_skill = _last_with_skill(attempts, working.skill_id, start_round)

    # The range can be empty when retry_limit shrank between runs; the
    # fall-through discard below then ends a run that had already used up
    # every allowed round.
    for retry_round in range(start_round, retry_limit + 1):
        last_round = retry_round == retry_limit

        # The audit is one judge call per (skill, version), persisted before
        # the round acts on it. A stored verdict (an interrupted run, or a
        # later round on an unchanged version) is reused as is: first
        # verdict sticks.
        verdict = _find_leak_verdict(store, working.skill_id,
                                     working.version)
        if verdict is None:
            leaked, reason = audit_leak(
                working.content, exam.question_text, exam.reference_answer,
                auditor_handle, usage=usage, run_dir=run_dir)
            verdict = {"skill_id": working.skill_id,
                       "skill_version": working.version,
                       "leaked": leaked, "reason": reason}
            append_jsonl(_ledger_path(store, LEAK_VERDICTS_LEDGER), verdict)
        if verdict["leaked"]:
            if last_round:
                working.status = "discarded"
                working.discard_reason = "leak_audit"
                store.save(working, "skills")
                return working
            # The voided version is never examined: rewrite straight from the
            # last with-skill attempt if one exists, else the bare attempt.
            source = last_with_skill if last_with_skill is not None else bare
            _rewrite_round(
                store, working, exam, retry_round, source.weak_answer,
                _void_error_description(source, verdict["reason"]),
                weak_profile, rewriter_handle, usage, run_dir)
            continue

        attempt = _find_round_attempt(
            attempts, working.skill_id, working.version, retry_round)
        if attempt is None:
            attempt = exam_once(
                store=store, exam=exam, answerer_handle=answerer_handle,
                judge_handle=judge_handle,
                skills_block=format_skills_block([working]),
                purpose="acceptance", skill_id=working.skill_id,
                skill_version=working.version, retry_round=retry_round,
                usage=usage, run_dir=run_dir)
        if attempt.judge_verdict:
            working.status = "accepted"
            store.save(working, "skills")
            return working

        last_with_skill = attempt
        if last_round:
            working.status = "discarded"
            working.discard_reason = "retry_exhausted"
            store.save(working, "skills")
            return working
        _rewrite_round(
            store, working, exam, retry_round, attempt.weak_answer,
            attempt.judge_reason, weak_profile, rewriter_handle, usage,
            run_dir)

    working.status = "discarded"
    working.discard_reason = "retry_exhausted"
    store.save(working, "skills")
    return working
