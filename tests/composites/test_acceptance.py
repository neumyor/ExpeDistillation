"""K4 acceptance composite tests (llm.fake + tmp run dir, no network).

Verdict sequences are driven by FakeClient scripts on the answerer / judge /
auditor / rewriter handles. Every resume case is built by pre-storing exactly
the artifacts an interrupted run would have left, then asserting the flow
continues from the latest stored state without re-asking anything already on
disk (AGENTS.md section 8 rule 2). The leak audit is a model call now, so
resume equality additionally rests on the persisted verdict ledger: the first
verdict per (skill, version) sticks and the judge is never asked twice.
"""

from expedistill.composites.acceptance import (
    LEAK_VERDICTS_LEDGER,
    REWRITE_FAILURE_LEDGER,
    accept_skill,
    discard_skill,
)
from expedistill.composites import acceptance as acceptance_mod
from expedistill.core.ids import (
    make_attempt_id,
    make_exam_id,
    make_merged_skill_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Attempt, Exam, Skill
import pytest

from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.infra.store import Store, append_jsonl, read_jsonl
from expedistill.llm import json_script, make_fake_handle

WEAK_PROFILE = "A small model; known to skip intermediate steps."

QUESTION = "What should you do when the service rejects connections?"
REFERENCE = "Restart the service after updating its configuration."
SKILL_V1 = "Restart the service after editing its configuration."
REWRITE_V2 = "Stop the service, edit the configuration, then start it again."
REWRITE_V3 = ("First stop the service, then edit the configuration file, "
              "then start the service again.")

LEAK_QUESTION = 'The service prints "svc-token-9" and stops. What next?'
LEAK_REFERENCE = 'Rotate the credential "svc-token-9" and restart the service.'
LEAKY_SKILL_V1 = 'Use "svc-token-9" to authenticate before restarting.'
CLEAN_V2 = "Rotate the printed credential, then restart the service."

BARE_REASON = "the answer skips the restart step"
LEAK_REASON = "the skill quotes the credential from the exam situation"


def make_pair(content=SKILL_V1, question=QUESTION, reference=REFERENCE):
    """A (skill, exam) pair hanging on one subtask, stage-3 style."""
    subtask_id = make_subtask_id(
        make_trajectory_id(make_task_id("tb", "t1"), "strong", 0), 1)
    skill = Skill(skill_id=make_skill_id(subtask_id), content=content,
                  subtask_id=subtask_id)
    exam = Exam(exam_id=make_exam_id(subtask_id), question_text=question,
                reference_answer=reference, subtask_id=subtask_id)
    return skill, exam


def incorrect(reason=BARE_REASON):
    return json_script(
        {"verdict": "incorrect", "error_description": reason})


def correct():
    return json_script({"verdict": "correct", "error_description": ""})


def leak_verdict(leaked, reason="generic method advice only"):
    return json_script({"leaked": leaked, "reason": reason})


def rewrite_result(text, category="misread",
                   reason="spelled out the intermediate steps"):
    return json_script({"skill_text": text, "failure_category": category,
                        "rewrite_reason": reason})


def run_accept(store, skill, exam, answerer, judge, rewriter, auditor,
               retry_limit=1):
    return accept_skill(
        store=store, skill=skill, exam=exam, weak_profile=WEAK_PROFILE,
        answerer_handle=answerer, judge_handle=judge,
        rewriter_handle=rewriter, auditor_handle=auditor,
        retry_limit=retry_limit, run_dir=store.run_dir)


def make_attempt(exam, serial, *, purpose, skill_id=None, skill_version=None,
                 weak_answer="some answer", judge_verdict=False,
                 judge_reason=BARE_REASON, retry_round=0):
    return Attempt(
        attempt_id=make_attempt_id(exam.exam_id, serial),
        exam_id=exam.exam_id,
        skill_id=skill_id,
        skill_version=skill_version,
        purpose=purpose,
        weak_answer=weak_answer,
        judge_verdict=judge_verdict,
        judge_reason=judge_reason,
        retry_round=retry_round,
    )


def assert_no_calls(*handles):
    for handle in handles:
        assert handle.client.calls == []


# ---------------------------------------------------------------------------
# Fresh runs
# ---------------------------------------------------------------------------

def test_accept_on_first_round(tmp_path):
    skill, exam = make_pair()
    store = Store(tmp_path / "run")
    answerer = make_fake_handle("weak", ["bare answer", "skilled answer"])
    judge = make_fake_handle("judge", [incorrect(), correct()])
    rewriter = make_fake_handle("strong", [])
    auditor = make_fake_handle("auditor", [leak_verdict(False)])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor)

    assert result.status == "accepted"
    assert result.version == 1
    assert result.rewrite_history == []
    assert store.load("skills", skill.skill_id, Skill).status == "accepted"
    # one bare + one acceptance attempt
    assert len(store.ids("attempts")) == 2
    assert_no_calls(rewriter)
    # the bare prompt carries no skills block, the acceptance prompt carries
    # the skill content injected through D9
    assert "## Skill rules" not in answerer.client.calls[0]["user"]
    skilled = answerer.client.calls[1]["user"]
    assert "## Skill rules" in skilled
    assert SKILL_V1 in skilled
    # the acceptance attempt records which skill version was examined
    acceptance = [store.load("attempts", a, Attempt)
                  for a in store.ids("attempts")]
    acceptance = [a for a in acceptance if a.purpose == "acceptance"]
    assert len(acceptance) == 1
    assert acceptance[0].skill_id == skill.skill_id
    assert acceptance[0].skill_version == 1
    assert acceptance[0].retry_round == 0
    # the passing verdict is on disk, one row for this version
    verdicts = read_jsonl(
        store.run_dir / "stage4_acceptance" / LEAK_VERDICTS_LEDGER)
    assert verdicts == [{
        "skill_id": skill.skill_id, "skill_version": 1,
        "leaked": False, "reason": "generic method advice only",
    }]


def test_discard_weak_already_knows(tmp_path):
    skill, exam = make_pair()
    store = Store(tmp_path / "run")
    answerer = make_fake_handle("weak", ["bare answer"])
    judge = make_fake_handle("judge", [correct()])
    rewriter = make_fake_handle("strong", [])
    auditor = make_fake_handle("auditor", [])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor)

    assert result.status == "discarded"
    assert result.discard_reason == "weak_already_knows"
    assert len(answerer.client.calls) == 1
    assert len(store.ids("attempts")) == 1
    # the bare control passed before any audit happened
    assert_no_calls(rewriter, auditor)


def test_retry_exhausted_after_full_loop(tmp_path):
    skill, exam = make_pair()
    store = Store(tmp_path / "run")
    answerer = make_fake_handle("weak", ["bare", "v1", "v2", "v3"])
    judge = make_fake_handle("judge", [incorrect()] * 4)
    rewriter = make_fake_handle("strong", [
        rewrite_result(REWRITE_V2),
        rewrite_result(REWRITE_V3, category="missing_step",
                      reason="named the configuration file explicitly"),
    ])
    auditor = make_fake_handle("auditor", [leak_verdict(False)] * 3)

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor, retry_limit=2)

    assert result.status == "discarded"
    assert result.discard_reason == "retry_exhausted"
    assert result.version == 3
    # every rewrite-history entry carries the PREVIOUS version's text
    assert [entry["content"] for entry in result.rewrite_history] == [
        SKILL_V1, REWRITE_V2]
    assert [entry["error_category"] for entry in result.rewrite_history] == [
        "misread", "missing_step"]
    assert result.rewrite_history[1]["reason"] == (
        "named the configuration file explicitly")
    # 1 bare + 3 with-skill attempts; the returned skill is the stored one
    assert len(store.ids("attempts")) == 4
    assert store.load("skills", skill.skill_id, Skill).to_dict() == (
        result.to_dict())
    # one judge verdict per version: v1, v2, v3
    verdicts = read_jsonl(
        store.run_dir / "stage4_acceptance" / LEAK_VERDICTS_LEDGER)
    assert [row["skill_version"] for row in verdicts] == [1, 2, 3]


def test_leak_audit_void_path(tmp_path):
    skill, exam = make_pair(LEAKY_SKILL_V1, LEAK_QUESTION, LEAK_REFERENCE)
    store = Store(tmp_path / "run")
    answerer = make_fake_handle("weak", ["bare answer", "clean answer"])
    judge = make_fake_handle("judge", [incorrect(), correct()])
    rewriter = make_fake_handle("strong", [rewrite_result(CLEAN_V2)])
    auditor = make_fake_handle(
        "auditor", [leak_verdict(True, LEAK_REASON),
                    leak_verdict(False)])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor)

    assert result.status == "accepted"
    assert result.version == 2
    assert result.content == CLEAN_V2
    # the voided version was never examined: no acceptance attempt for v1
    acceptance = [store.load("attempts", a, Attempt)
                  for a in store.ids("attempts")]
    assert [a.skill_version for a in acceptance
            if a.purpose == "acceptance"] == [2]
    # the history entry keeps the voided text as the previous version
    assert result.rewrite_history[0]["content"] == LEAKY_SKILL_V1
    # the judge verdicts are recorded, one row per version
    verdicts = read_jsonl(
        store.run_dir / "stage4_acceptance" / LEAK_VERDICTS_LEDGER)
    assert verdicts == [
        {"skill_id": skill.skill_id, "skill_version": 1, "leaked": True,
         "reason": LEAK_REASON},
        {"skill_id": skill.skill_id, "skill_version": 2, "leaked": False,
         "reason": "generic method advice only"},
    ]
    # the rewrite prompt carried the audit note (with the judge's reason)
    # plus the bare attempt's context (round 0: no with-skill attempt yet)
    prompt = rewriter.client.calls[0]["user"]
    assert "voided by the leak audit" in prompt
    assert LEAK_REASON in prompt
    assert BARE_REASON in prompt
    assert "bare answer" in prompt


def test_terminal_leak_discard_keeps_leak_audit_reason(tmp_path):
    skill, exam = make_pair(LEAKY_SKILL_V1, LEAK_QUESTION, LEAK_REFERENCE)
    store = Store(tmp_path / "run")
    answerer = make_fake_handle("weak", ["bare answer"])
    judge = make_fake_handle("judge", [incorrect()])
    rewriter = make_fake_handle("strong", [])
    auditor = make_fake_handle("auditor", [leak_verdict(True, LEAK_REASON)])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor, retry_limit=0)

    assert result.status == "discarded"
    assert result.discard_reason == "leak_audit"
    assert result.version == 1
    assert_no_calls(rewriter)


# ---------------------------------------------------------------------------
# Verdict persistence: first verdict sticks, the judge is never re-asked
# ---------------------------------------------------------------------------

def test_verdict_is_asked_once_per_version_across_rounds(tmp_path):
    """A rewrite that exhausts its budget consumes the round without a
    version bump; the next round re-audits the SAME version and must reuse
    the stored verdict instead of asking the judge again."""
    skill, exam = make_pair(LEAKY_SKILL_V1, LEAK_QUESTION, LEAK_REFERENCE)
    store = Store(tmp_path / "run")
    answerer = make_fake_handle("weak", ["bare answer"])
    judge = make_fake_handle("judge", [incorrect()])
    rewriter = make_fake_handle("strong", [
        ModelOutputError("REWRITE: bad"), ModelOutputError("REWRITE: bad 2")])
    # r0 voids v1 and the rewrite exhausts its budget; r1 re-audits v1.
    # The single scripted verdict must serve both rounds.
    auditor = make_fake_handle("auditor", [leak_verdict(True, LEAK_REASON)])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor, retry_limit=1)

    assert result.status == "discarded"
    assert result.discard_reason == "leak_audit"
    assert len(auditor.client.calls) == 1
    verdicts = read_jsonl(
        store.run_dir / "stage4_acceptance" / LEAK_VERDICTS_LEDGER)
    assert len(verdicts) == 1
    assert verdicts[0]["skill_version"] == 1


def test_resume_reuses_stored_clean_verdict(tmp_path):
    skill, exam = make_pair()
    store = Store(tmp_path / "run")
    store.save(skill, "skills")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")
    # An interrupted run already asked the judge about v1 and stored its
    # clean verdict; the resumed run must not ask again.
    append_jsonl(store.run_dir / "stage4_acceptance" / LEAK_VERDICTS_LEDGER,
                  {"skill_id": skill.skill_id, "skill_version": 1,
                   "leaked": False, "reason": "generic method advice only"})
    answerer = make_fake_handle("weak", ["skilled answer"])
    judge = make_fake_handle("judge", [correct()])
    rewriter = make_fake_handle("strong", [])
    auditor = make_fake_handle("auditor", [])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor)

    assert result.status == "accepted"
    assert_no_calls(auditor)


def test_first_verdict_sticks_on_resume(tmp_path):
    """The judge is nondeterministic; a stored leaked=true verdict must void
    the version on resume even though a fresh ask would have said clean."""
    skill, exam = make_pair()
    store = Store(tmp_path / "run")
    store.save(skill, "skills")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")
    append_jsonl(store.run_dir / "stage4_acceptance" / LEAK_VERDICTS_LEDGER,
                  {"skill_id": skill.skill_id, "skill_version": 1,
                   "leaked": True, "reason": LEAK_REASON})
    answerer = make_fake_handle("weak", [])
    judge = make_fake_handle("judge", [])
    # If the resumed run asked the judge again, this clean verdict (and the
    # acceptance attempt below) would turn the leak into an accept.
    rewriter = make_fake_handle("strong", [rewrite_result(CLEAN_V2)])
    auditor = make_fake_handle("auditor", [leak_verdict(False)])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor, retry_limit=0)

    # last round + leaked verdict -> terminal leak discard, no second ask
    assert result.status == "discarded"
    assert result.discard_reason == "leak_audit"
    assert_no_calls(auditor, answerer, judge)


def test_auditor_model_output_error_propagates(tmp_path):
    """The judge broke its JSON contract after call_model's repair budget:
    the error propagates to the caller (stage 4's unit disposition), the
    skill stays pending and no verdict row is written."""
    skill, exam = make_pair()
    store = Store(tmp_path / "run")
    answerer = make_fake_handle("weak", ["bare answer"])
    judge = make_fake_handle("judge", [incorrect()])
    rewriter = make_fake_handle("strong", [])
    auditor = make_fake_handle("strong", [
        ModelOutputError("LEAK_AUDIT_JUDGE: leaked must be true or false")])

    with pytest.raises(ModelOutputError, match="LEAK_AUDIT_JUDGE"):
        run_accept(store, skill, exam, answerer, judge, rewriter, auditor)

    stored = store.load("skills", skill.skill_id, Skill)
    assert stored.status == "pending"
    assert not (store.run_dir / "stage4_acceptance"
                / LEAK_VERDICTS_LEDGER).exists()
    assert_no_calls(rewriter)


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def test_resume_returns_stored_accepted_skill(tmp_path):
    skill, exam = make_pair()
    skill.status = "accepted"
    store = Store(tmp_path / "run")
    store.save(skill, "skills")
    answerer = make_fake_handle("weak", [])
    judge = make_fake_handle("judge", [])
    rewriter = make_fake_handle("strong", [])
    auditor = make_fake_handle("auditor", [])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor)

    assert result.to_dict() == skill.to_dict()
    assert_no_calls(answerer, judge, rewriter, auditor)


def test_resume_recognizes_stored_accepted_attempt(tmp_path):
    skill, exam = make_pair()
    store = Store(tmp_path / "run")
    store.save(skill, "skills")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")
    store.save(make_attempt(
        exam, 5, purpose="acceptance", skill_id=skill.skill_id,
        skill_version=1, judge_verdict=True), "attempts")
    answerer = make_fake_handle("weak", [])
    judge = make_fake_handle("judge", [])
    rewriter = make_fake_handle("strong", [])
    auditor = make_fake_handle("auditor", [])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor)

    assert result.status == "accepted"
    assert store.load("skills", skill.skill_id, Skill).status == "accepted"
    assert_no_calls(answerer, judge, rewriter, auditor)


def test_resume_mid_rewrite_continues_from_stored_version(tmp_path):
    skill, exam = make_pair(REWRITE_V2)
    skill.version = 2
    skill.rewrite_history = [{
        "content": SKILL_V1, "error_category": "misread",
        "reason": "spelled out the intermediate steps",
    }]
    store = Store(tmp_path / "run")
    store.save(skill, "skills")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")
    store.save(make_attempt(
        exam, 5, purpose="acceptance", skill_id=skill.skill_id,
        skill_version=1, judge_verdict=False), "attempts")
    # stored version 2 means round 1 is next; retry_limit=1 makes it the last
    answerer = make_fake_handle("weak", ["v2 answer"])
    judge = make_fake_handle("judge", [correct()])
    rewriter = make_fake_handle("strong", [])
    auditor = make_fake_handle("auditor", [leak_verdict(False)])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor, retry_limit=1)

    assert result.status == "accepted"
    assert result.version == 2
    # the history was not rewritten and nothing was re-asked
    assert result.rewrite_history == [{
        "content": SKILL_V1, "error_category": "misread",
        "reason": "spelled out the intermediate steps",
    }]
    assert_no_calls(rewriter)
    assert len(answerer.client.calls) == 1
    # bare and round-0 attempts were reused: only round 1 was added
    assert len(store.ids("attempts")) == 3
    # the resumed round audited the stored v2 (its verdict was not on disk)
    verdicts = read_jsonl(
        store.run_dir / "stage4_acceptance" / LEAK_VERDICTS_LEDGER)
    assert [row["skill_version"] for row in verdicts] == [2]


def test_resume_reuses_wrong_bare_attempt(tmp_path):
    skill, exam = make_pair()
    store = Store(tmp_path / "run")
    store.save(skill, "skills")
    store.save(make_attempt(exam, 0, purpose="bare", judge_verdict=False),
               "attempts")
    answerer = make_fake_handle("weak", ["skilled answer"])
    judge = make_fake_handle("judge", [correct()])
    rewriter = make_fake_handle("strong", [])
    auditor = make_fake_handle("auditor", [leak_verdict(False)])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor)

    assert result.status == "accepted"
    # the only model answer is the with-skill one
    assert len(answerer.client.calls) == 1
    assert "## Skill rules" in answerer.client.calls[0]["user"]
    assert len(store.ids("attempts")) == 2


# ---------------------------------------------------------------------------
# Rewrite repair budget
# ---------------------------------------------------------------------------

def test_rewrite_repaired_after_model_output_error(tmp_path):
    skill, exam = make_pair()
    store = Store(tmp_path / "run")
    answerer = make_fake_handle("weak", ["bare", "v1", "v2"])
    judge = make_fake_handle("judge", [incorrect(), incorrect(), correct()])
    rewriter = make_fake_handle("strong", [
        ModelOutputError("REWRITE: response was not valid JSON"),
        rewrite_result(REWRITE_V2),
    ])
    auditor = make_fake_handle("auditor", [leak_verdict(False)] * 2)

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor, retry_limit=1)

    assert result.status == "accepted"
    assert result.version == 2
    assert result.content == REWRITE_V2
    assert len(rewriter.client.calls) == 2


def test_rewrite_budget_exhaustion_consumes_the_round(tmp_path):
    skill, exam = make_pair()
    store = Store(tmp_path / "run")
    answerer = make_fake_handle("weak", ["bare", "round0", "round1"])
    judge = make_fake_handle("judge", [incorrect(), incorrect(), incorrect()])
    rewriter = make_fake_handle("strong", [
        ModelOutputError("REWRITE: bad"),
        ModelOutputError("REWRITE: still bad"),
    ])
    # v1 is audited once in round 0 and again (same version) in round 1.
    auditor = make_fake_handle("auditor", [leak_verdict(False)])

    result = run_accept(store, skill, exam, answerer, judge, rewriter,
                        auditor, retry_limit=1)

    # the failed rewrite consumed round 0; round 1 re-audited the unchanged
    # version (verdict reused) and its failed attempt ended the run
    assert result.status == "discarded"
    assert result.discard_reason == "retry_exhausted"
    assert result.version == 1
    assert result.rewrite_history == []
    assert len(rewriter.client.calls) == 2
    assert len(auditor.client.calls) == 1
    # bare + one with-skill attempt per round, same version in both rounds
    acceptance = [store.load("attempts", a, Attempt)
                  for a in store.ids("attempts")]
    assert [a.retry_round for a in acceptance
            if a.purpose == "acceptance"] == [0, 1]
    assert all(a.skill_version == 1
               for a in acceptance if a.purpose == "acceptance")
    # both failed tries are on record; the last one closed round 0
    failures = read_jsonl(
        store.run_dir / "stage4_acceptance" / REWRITE_FAILURE_LEDGER)
    assert [(f["retry_round"], f["try_index"], f["exhausted"])
            for f in failures] == [(0, 0, False), (0, 1, True)]


def test_discard_skill_discards_the_stored_version(tmp_path):
    skill, _ = make_pair()
    store = Store(tmp_path / "run")
    stored = Skill.from_dict(skill.to_dict())
    stored.version = 2
    stored.content = REWRITE_V2
    store.save(stored, "skills")

    result = discard_skill(store, skill, "model_output_error")

    assert (result.version, result.content) == (2, REWRITE_V2)
    assert store.load("skills", skill.skill_id, Skill).to_dict() == (
        result.to_dict())
    assert result.discard_reason == "model_output_error"


def test_refuses_a_skill_that_is_not_a_stage3_candidate(tmp_path):
    skill, exam = make_pair()
    merged = Skill(skill_id=make_merged_skill_id(0), content=SKILL_V1,
                   subtask_id=skill.subtask_id, merged_from=[skill.skill_id])
    store = Store(tmp_path / "run")
    answerer = make_fake_handle("weak", [])
    judge = make_fake_handle("judge", [])
    rewriter = make_fake_handle("strong", [])
    auditor = make_fake_handle("auditor", [])

    with pytest.raises(CodeBugError, match="bank/merge/0000"):
        run_accept(store, merged, exam, answerer, judge, rewriter, auditor)

    assert_no_calls(answerer, judge, rewriter, auditor)
    assert store.ids("skills") == [] and store.ids("attempts") == []


def test_attempts_are_ordered_by_numeric_serial(tmp_path):
    """Serials >= 100 widen the id ("att100" sorts before "att99" as a
    string); every K4 ordering must use the parsed serial."""
    skill, exam = make_pair()
    store = Store(tmp_path / "run")
    for serial in (99, 100):
        store.save(make_attempt(
            exam, serial, purpose="acceptance", skill_id=skill.skill_id,
            skill_version=1, weak_answer=f"answer {serial}",
            judge_verdict=False, retry_round=0), "attempts")

    attempts = acceptance_mod._exam_attempts(store, exam.exam_id)

    assert [a.weak_answer for a in attempts] == ["answer 99", "answer 100"]
    latest = acceptance_mod._last_with_skill(attempts, skill.skill_id, 1)
    assert latest.weak_answer == "answer 100"
