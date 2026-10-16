"""K4 acceptance: prove one skill actually teaches the weak model (layer 5).

The loop-and-commit part of the former composites/acceptance.py, split
out mechanically in R0 (docs/decisions/execute_learn_repair_plan_20261016.md):
the K4 acceptance loop ``accept_skill`` and the helpers that close each of
its rounds with an outcome -- the G6 rewrite (with its crash-persistent
repair budget) and the deterministic error description for an audit-voided
rewrite. The leak verdicts it consults are
composites/acceptance_leak.py's; the stored-record state lookups are
composites/acceptance_skill_status.py's.

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
stay single-purpose. The per-round verdict rules (the bare-answer, leak and
wrong-answer decisions and their last-round splits) are named rules in
``composites.decision`` since M3; this module keeps every read and write.

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
from typing import Any, Optional, Union

from expedistill.blocks.author import rewrite_skill
from expedistill.composites import decision
from expedistill.composites.acceptance_leak import leak_verdict_for
from expedistill.composites.acceptance_skill_status import (
    REWRITE_FAILURE_LEDGER,
    _exam_attempts,
    _find_accepted,
    _find_bare,
    _find_round_attempt,
    _last_with_skill,
    _ledger_path,
    _next_round,
    _skill_records,
)
from expedistill.composites.exam_once import exam_once
from expedistill.core import ids
from expedistill.core.models import Attempt, Exam, Skill
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.infra.store import Store, append_jsonl
from expedistill.inject.skills import format_skills_block
from expedistill.llm.call import ModelHandle

__all__ = ["accept_skill"]

#: Repair budget for one rewrite call. call_model already repairs malformed
#: JSON per request; this is the composite-level budget for the whole G6 call.
#: Exhausting it consumes the round like a failed attempt (see
#: _rewrite_round).
_REWRITE_ATTEMPTS = 2

#: Prefix of the error description synthesized for an audit-voided rewrite.
_VOID_NOTE = "The previous skill version was voided by the leak audit: "


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
    if decision.exam_stored_pass_action(accepted is not None) is not None:
        working.status = "accepted"
        store.save(working, "skills")
        return working

    bare = _find_bare(attempts)
    if bare is None:
        bare = exam_once(
            store=store, exam=exam, answerer_handle=answerer_handle,
            judge_handle=judge_handle, skills_block="", purpose="bare",
            usage=usage, run_dir=run_dir)
    bare_action = decision.exam_bare_answer_action(bare.judge_verdict)
    if bare_action is not None:
        # The weak model already answers this exam without any skill, so the
        # skill cannot teach it anything (README stage 4 step 1).
        working.status = "discarded"
        working.discard_reason = bare_action.reason
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
        verdict = leak_verdict_for(
            store=store, skill_id=working.skill_id,
            skill_version=working.version, skill_content=working.content,
            exam=exam, auditor_handle=auditor_handle, usage=usage,
            run_dir=run_dir)
        if verdict["leaked"]:
            action = decision.exam_leak_action(last_round=last_round)
            if action.kind == decision.DELETE:
                working.status = "discarded"
                working.discard_reason = action.reason
                store.save(working, "skills")
                return working
            # The voided version is never examined: rewrite straight from the
            # last with-skill attempt if one exists, else the bare attempt.
            source = decision.exam_voided_rewrite_source(
                last_with_skill=last_with_skill, bare=bare)
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
        action = decision.exam_answer_action(
            verdict=attempt.judge_verdict, last_round=last_round)
        if action.kind == decision.KEEP:
            working.status = "accepted"
            store.save(working, "skills")
            return working

        last_with_skill = attempt
        if action.kind == decision.DELETE:
            working.status = "discarded"
            working.discard_reason = action.reason
            store.save(working, "skills")
            return working
        _rewrite_round(
            store, working, exam, retry_round, attempt.weak_answer,
            attempt.judge_reason, weak_profile, rewriter_handle, usage,
            run_dir)

    working.status = "discarded"
    working.discard_reason = decision.RETRY_EXHAUSTED
    store.save(working, "skills")
    return working
