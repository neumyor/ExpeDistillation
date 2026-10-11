"""K3 exam_once tests: wiring, bookkeeping and repair budget (FakeClient only).

Both handles share one FakeClient so the ANSWER-then-JUDGE call order is a
single list assertion. Every test gets its own temporary run directory: the
Store, the request dumps (run_dir) and the usage ledger all land in tmp_path,
never in runs/ (AGENTS.md section 8).
"""

import pytest

from expedistill.composites import exam_once
from expedistill.core.models import Attempt, Exam
from expedistill.infra.reliability import ModelOutputError
from expedistill.infra.store import Store, read_jsonl
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.call import ModelHandle
from expedistill.llm.fake import FakeClient, json_script
from expedistill.prompts import get as get_prompt

EXAM_ID = "traj/tb:demo/weak/00/seg00/exam"
SUBTASK_ID = "traj/tb:demo/weak/00/seg00"
QUESTION = "The build fails; what next?"
REFERENCE = "Run the tests first, then read the first failing case."
ANSWER_TEXT = "Run the tests first."
CORRECT = {"verdict": "correct", "error_description": ""}


class Crash(Exception):
    """Non-retryable injected failure (neither ModelOutputError nor infra)."""


def bad_verdict():
    """A judge reply that parses but breaks the verdict vocabulary."""
    return json_script({"verdict": "maybe", "error_description": ""})


def make_exam(exam_id: str = EXAM_ID) -> Exam:
    return Exam(
        exam_id=exam_id,
        question_text=QUESTION,
        reference_answer=REFERENCE,
        subtask_id=SUBTASK_ID,
    )


def make_handles(script):
    """Answerer and judge wired to ONE shared FakeClient (call-order proof)."""
    client = FakeClient(script)
    answerer = ModelHandle(name="weak-model", role="weak", client=client)
    judge = ModelHandle(name="strong-model", role="judge", client=client)
    return client, answerer, judge


def call_once(store, script, **overrides):
    """One exam_once call with the defaults every test shares."""
    client, answerer, judge = make_handles(script)
    kwargs = dict(
        store=store,
        exam=make_exam(),
        answerer_handle=answerer,
        judge_handle=judge,
        skills_block="",
        purpose="bare",
    )
    kwargs.update(overrides)
    return exam_once(**kwargs), client


def test_bare_calls_answer_then_judge_in_order(tmp_path):
    attempt, client = call_once(Store(tmp_path),
                                [ANSWER_TEXT, json_script(CORRECT)])
    assert [call["user"] for call in client.calls] == [
        get_prompt("ANSWER").format(question_text=QUESTION, skills_block=""),
        get_prompt("JUDGE").format(
            question_text=QUESTION,
            reference_answer=REFERENCE,
            weak_answer=ANSWER_TEXT,
        ),
    ]
    assert attempt.attempt_id == f"{EXAM_ID}/att00"
    assert attempt.exam_id == EXAM_ID
    assert attempt.purpose == "bare"
    assert attempt.skill_id is None
    assert attempt.skill_version is None
    assert attempt.retry_round == 0
    assert attempt.weak_answer == ANSWER_TEXT
    assert attempt.judge_verdict is True
    assert attempt.judge_reason == ""


def test_with_skills_records_skill_fields_and_block(tmp_path):
    block = "## Skill rules\n- Check the lock file first."
    skill_id = f"{SUBTASK_ID}/skill"
    attempt, client = call_once(
        Store(tmp_path),
        [ANSWER_TEXT, json_script(
            {"verdict": "incorrect", "error_description": "Skips the lock file."})],
        skills_block=block,
        purpose="acceptance",
        skill_id=skill_id,
        skill_version=3,
        retry_round=2,
    )
    assert block in client.calls[0]["user"]
    assert attempt.purpose == "acceptance"
    assert attempt.skill_id == skill_id
    assert attempt.skill_version == 3
    assert attempt.retry_round == 2
    assert attempt.judge_verdict is False
    assert attempt.judge_reason == "Skips the lock file."


def test_serial_increments_and_is_reused_after_delete(tmp_path):
    store = Store(tmp_path)
    first, _ = call_once(store, [ANSWER_TEXT, json_script(CORRECT)])
    second, _ = call_once(store, [ANSWER_TEXT, json_script(CORRECT)])
    third, _ = call_once(store, [ANSWER_TEXT, json_script(CORRECT)])
    assert [first.attempt_id, second.attempt_id, third.attempt_id] == [
        f"{EXAM_ID}/att00", f"{EXAM_ID}/att01", f"{EXAM_ID}/att02",
    ]
    # Count-based serial: deleting a stored attempt frees its serial again,
    # so a replayed identical flow reproduces the same IDs.
    store.path_for("attempts", second.attempt_id).unlink()
    replayed, _ = call_once(store, [ANSWER_TEXT, json_script(CORRECT)])
    assert replayed.attempt_id == f"{EXAM_ID}/att02"


def test_serial_counts_only_this_exam(tmp_path):
    store = Store(tmp_path)
    other = Attempt(
        attempt_id=f"{SUBTASK_ID[:-2]}01/exam/att07",
        exam_id=f"{SUBTASK_ID[:-2]}01/exam",
        purpose="regression",
    )
    store.save(other, "attempts")
    attempt, _ = call_once(store, [ANSWER_TEXT, json_script(CORRECT)])
    assert attempt.attempt_id == f"{EXAM_ID}/att00"


def test_invalid_purpose_rejects_before_any_model_call(tmp_path):
    # Empty script: the FakeClient raises AssertionError if anything calls it,
    # so this doubles as the "no model call happens" canary.
    _, answerer, judge = make_handles([])
    with pytest.raises(ValueError, match="purpose"):
        exam_once(
            store=Store(tmp_path),
            exam=make_exam(),
            answerer_handle=answerer,
            judge_handle=judge,
            skills_block="",
            purpose="smoke",
        )


def test_judge_repair_resamples_once_and_still_records(tmp_path):
    # First judge reply parses as JSON but breaks the verdict contract, so
    # the block raises ModelOutputError; exam_once resamples once and wins.
    attempt, client = call_once(
        Store(tmp_path),
        [ANSWER_TEXT,
         json_script({"verdict": "maybe", "error_description": ""}),
         json_script(CORRECT)],
    )
    assert attempt.judge_verdict is True
    assert len(client.calls) == 3  # answer + two judge calls
    assert attempt.attempt_id == f"{EXAM_ID}/att00"


def test_answer_repair_resamples_once(tmp_path):
    # An int response is neither a string nor has .text, so B1 raises
    # ModelOutputError on unpack; the resample returns a usable answer.
    attempt, client = call_once(
        Store(tmp_path),
        [123, ANSWER_TEXT, json_script(CORRECT)],
    )
    assert attempt.weak_answer == ANSWER_TEXT
    assert len(client.calls) == 3  # two answer calls + judge


def test_answer_repair_exhaustion_propagates_and_stores_nothing(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ModelOutputError):
        call_once(store, [123, 456])
    assert store.ids("attempts") == []


def test_judge_repair_exhaustion_propagates_and_keeps_answer(tmp_path):
    # The answer was persisted before judging, so exhaustion of the judge
    # repair budget leaves it stored and unjudged (K4/K5 lookups keep
    # treating it as not done), with the spent budget recorded on it.
    store = Store(tmp_path)
    with pytest.raises(ModelOutputError):
        call_once(store, [ANSWER_TEXT, bad_verdict(), bad_verdict()])
    assert store.ids("attempts") == [f"{EXAM_ID}/att00"]
    stored = store.load("attempts", f"{EXAM_ID}/att00", Attempt)
    assert (stored.weak_answer, stored.judge_verdict) == (ANSWER_TEXT, None)
    assert stored.judge_reason.startswith("judge_failed_tries=2")


def test_exhausted_judge_budget_raises_on_resume_without_model_call(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ModelOutputError):
        call_once(store, [ANSWER_TEXT, bad_verdict(), bad_verdict()])
    # Empty script: any model call would raise AssertionError instead.
    with pytest.raises(ModelOutputError, match="budget"):
        call_once(store, [])
    stored = store.load("attempts", f"{EXAM_ID}/att00", Attempt)
    assert stored.judge_verdict is None
    assert store.ids("attempts") == [f"{EXAM_ID}/att00"]


def test_judge_budget_is_not_reset_by_a_crash(tmp_path):
    # Uninterrupted: two bad verdicts exhaust the budget.
    with pytest.raises(ModelOutputError):
        call_once(Store(tmp_path / "ref"),
                  [ANSWER_TEXT, bad_verdict(), bad_verdict()])
    # Crashed mid second judge try: the resume has ONE try left, so the same
    # second bad verdict exhausts it -- a fresh budget would have resampled
    # and recorded a verdict the uninterrupted run never reached.
    store = Store(tmp_path / "run")
    with pytest.raises(Crash):
        call_once(store, [ANSWER_TEXT, bad_verdict(), Crash("mid judge 2")])
    with pytest.raises(ModelOutputError):
        call_once(store, [bad_verdict(), json_script(CORRECT)])
    stored = store.load("attempts", f"{EXAM_ID}/att00", Attempt)
    assert stored.judge_verdict is None
    assert stored.judge_reason.startswith("judge_failed_tries=2")


def test_judge_resume_with_budget_left_completes_identically(tmp_path):
    reference, _ = call_once(Store(tmp_path / "ref"),
                             [ANSWER_TEXT, bad_verdict(), json_script(CORRECT)])
    store = Store(tmp_path / "run")
    with pytest.raises(Crash):
        call_once(store, [ANSWER_TEXT, bad_verdict(), Crash("mid judge 2")])
    stored = store.load("attempts", f"{EXAM_ID}/att00", Attempt)
    assert stored.judge_verdict is None
    assert stored.judge_reason.startswith("judge_failed_tries=1")
    resumed, client = call_once(store, [json_script(CORRECT)])
    assert len(client.calls) == 1
    # The failure marker is replaced by the real verdict's reason.
    assert resumed.to_dict() == reference.to_dict()
    assert reference.judge_reason == ""


def test_attempt_round_trips_through_store(tmp_path):
    store = Store(tmp_path)
    attempt, _ = call_once(store, [ANSWER_TEXT, json_script(CORRECT)])
    assert store.exists("attempts", attempt.attempt_id)
    loaded = store.load("attempts", attempt.attempt_id, Attempt)
    assert loaded.to_dict() == attempt.to_dict()


def test_usage_rows_recorded_per_call(tmp_path):
    store = Store(tmp_path)
    usage = UsageRecorder(tmp_path / "llm_requests" / "usage.jsonl")
    call_once(store, [ANSWER_TEXT, json_script(CORRECT)], usage=usage,
              run_dir=tmp_path)
    rows = read_jsonl(tmp_path / "llm_requests" / "usage.jsonl")
    assert [row["prompt"] for row in rows] == ["ANSWER", "JUDGE"]
    assert [row["role"] for row in rows] == ["weak", "judge"]
    assert all(row["status"] == "ok" for row in rows)
    # FakeClient returns plain strings, so token counts are unknown: recorded
    # as null, never as 0 (AGENTS.md section 6 rule 5).
    assert all(row["in_tokens"] is None for row in rows)
    totals = usage.totals()
    assert totals["overall"]["request_count"] == 2
    assert totals["by_role"]["weak"]["request_count"] == 1
    assert totals["by_role"]["judge"]["request_count"] == 1


# ---------------------------------------------------------------------------
# Answer-first persistence: a crash in the judge call keeps the answer
# ---------------------------------------------------------------------------

def test_judge_crash_keeps_answer_and_resume_only_judges(tmp_path):
    reference, _ = call_once(Store(tmp_path / "ref"),
                             [ANSWER_TEXT, json_script(CORRECT)])

    store = Store(tmp_path / "run")
    with pytest.raises(Crash):
        call_once(store, [ANSWER_TEXT, Crash("judge crashed")])
    # The answer is on disk, marked "not judged yet", occupying serial 00.
    assert store.ids("attempts") == [f"{EXAM_ID}/att00"]
    stored = store.load("attempts", f"{EXAM_ID}/att00", Attempt)
    assert stored.weak_answer == ANSWER_TEXT
    assert stored.judge_verdict is None

    # Resume: the weak model is NOT asked again (a resample could differ);
    # exactly one judge call completes the same attempt id.
    resumed, client = call_once(store, [json_script(CORRECT)])
    assert len(client.calls) == 1
    assert client.calls[0]["user"] == get_prompt("JUDGE").format(
        question_text=QUESTION,
        reference_answer=REFERENCE,
        weak_answer=ANSWER_TEXT,
    )
    assert resumed.to_dict() == reference.to_dict()
    assert store.ids("attempts") == [f"{EXAM_ID}/att00"]
    assert (store.load("attempts", resumed.attempt_id, Attempt).to_dict()
            == reference.to_dict())


def test_judge_crash_resume_uses_latest_matching_unjudged(tmp_path):
    store = Store(tmp_path)
    for serial, text in enumerate(["older answer", "newer answer"]):
        store.save(Attempt(attempt_id=f"{EXAM_ID}/att{serial:02d}",
                           exam_id=EXAM_ID, purpose="bare",
                           weak_answer=text), "attempts")
    attempt, client = call_once(store, [json_script(CORRECT)])
    assert attempt.attempt_id == f"{EXAM_ID}/att01"
    assert attempt.weak_answer == "newer answer"
    assert len(client.calls) == 1
    # The older unjudged attempt is left untouched.
    assert (store.load("attempts", f"{EXAM_ID}/att00", Attempt).judge_verdict
            is None)


def test_unjudged_attempt_of_other_version_is_not_reused(tmp_path):
    store = Store(tmp_path)
    skill_id = f"{SUBTASK_ID}/skill"
    store.save(Attempt(attempt_id=f"{EXAM_ID}/att00", exam_id=EXAM_ID,
                       purpose="acceptance", skill_id=skill_id,
                       skill_version=1, weak_answer="answer on v1"),
               "attempts")
    attempt, client = call_once(
        store, ["answer on v2", json_script(CORRECT)],
        skills_block="## Skill rules\n- v2", purpose="acceptance",
        skill_id=skill_id, skill_version=2)
    assert len(client.calls) == 2  # answered afresh, then judged
    # The unjudged v1 attempt keeps serial 00; the new one takes 01.
    assert attempt.attempt_id == f"{EXAM_ID}/att01"
    assert attempt.weak_answer == "answer on v2"
    assert attempt.skill_version == 2
    old = store.load("attempts", f"{EXAM_ID}/att00", Attempt)
    assert (old.weak_answer, old.judge_verdict) == ("answer on v1", None)


@pytest.mark.parametrize("field,value", [
    ("purpose", "regression"), ("skill_id", "other/skill"),
    ("retry_round", 1)])
def test_unjudged_attempt_with_other_key_is_not_reused(tmp_path, field,
                                                       value):
    store = Store(tmp_path)
    fields = dict(attempt_id=f"{EXAM_ID}/att00", exam_id=EXAM_ID,
                  purpose="acceptance", skill_id=f"{SUBTASK_ID}/skill",
                  skill_version=1, weak_answer="stale", retry_round=0)
    fields[field] = value
    store.save(Attempt(**fields), "attempts")
    attempt, client = call_once(
        store, [ANSWER_TEXT, json_script(CORRECT)], purpose="acceptance",
        skill_id=f"{SUBTASK_ID}/skill", skill_version=1, retry_round=0)
    assert len(client.calls) == 2
    assert attempt.attempt_id == f"{EXAM_ID}/att01"


def test_judged_attempt_is_never_reused(tmp_path):
    # K3 is still not idempotent: a completed attempt does not short-circuit
    # a new call (callers check completion themselves).
    store = Store(tmp_path)
    call_once(store, [ANSWER_TEXT, json_script(CORRECT)])
    again, client = call_once(store, [ANSWER_TEXT, json_script(CORRECT)])
    assert len(client.calls) == 2
    assert again.attempt_id == f"{EXAM_ID}/att01"
