"""K5 curation composite tests: FakeClient-driven G7/G8/G9 JSON + K3 verdicts.

Covers the stage-5 contract (README stage 5 / AGENTS.md section 8): dedup
merges kept, regression failures reverted, the conflict-merge path, G9
grouping, the token-budget round loop, the over-budget ConfigError stop,
bank-level resume (including a crash mid-append), the empty accepted set,
determinism, and the protocol-v2 marker checks (mixed-kind groups skipped
before any G8 call, structure-losing merges never stored, prose groups
exempt, reused pending merges never re-validated). No network; every
model call is scripted.
"""

import pytest

from expedistill.composites.curation import (
    LOG_DIR,
    LOG_NAME,
    LOG_SCHEMA,
    curate_bank,
)
from expedistill.core import ids
from expedistill.core.models import Attempt, Exam, Skill
from expedistill.infra.reliability import ConfigError, ModelOutputError
from expedistill.infra.store import Store, read_json, read_jsonl, write_json
from expedistill.inject import count_bank_tokens
from expedistill.llm import json_script, make_fake_handle


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _subtask(index: int) -> str:
    return f"traj/tb:t{index}/strong/00/seg00"


def _skill_id(index: int) -> str:
    return ids.make_skill_id(_subtask(index))


def _add_skill(store: Store, index: int, content: str,
               status: str = "accepted") -> Skill:
    skill = Skill(skill_id=_skill_id(index), content=content,
                  subtask_id=_subtask(index),
                  source_subtasks=[_subtask(index)], status=status)
    store.save(skill, "skills")
    return skill


def _add_exam(store: Store, index: int) -> Exam:
    exam = Exam(exam_id=ids.make_exam_id(_subtask(index)),
                question_text=f"question {index}",
                reference_answer=f"reference {index}",
                subtask_id=_subtask(index),
                skill_id=_skill_id(index))
    store.save(exam, "exams")
    return exam


def _handles(curator_script, answer_script, judge_script):
    return {
        "curator_handle": make_fake_handle("curator", curator_script),
        "answerer_handle": make_fake_handle("weak", answer_script),
        "judge_handle": make_fake_handle("judge", judge_script),
    }


def _curate(store: Store, handles, *, budget=10 ** 6, rounds=3,
            merge_check="exam"):
    return curate_bank(
        store=store,
        weak_profile="a weak model that needs short sentences",
        bank_token_budget=budget,
        curation_rounds=rounds,
        merge_check=merge_check,
        run_dir=store.run_dir,
        **handles,
    )


def _similarity(groups=(), conflicts=()):
    return json_script({
        "similar_groups": [list(group) for group in groups],
        "conflicts": [list(pair) for pair in conflicts],
    })


def _grouping(mapping):
    return json_script({"groups": mapping})


_CORRECT = lambda: json_script({"verdict": "correct", "error_description": ""})  # noqa: E731
_INCORRECT = lambda: json_script({"verdict": "incorrect", "error_description": "wrong"})  # noqa: E731


def _log_of(store):
    return read_json(store.run_dir / LOG_DIR / LOG_NAME)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "run")


# ---------------------------------------------------------------------------
# Dedup merge kept
# ---------------------------------------------------------------------------

def test_dedup_merge_kept(store):
    skills = [_add_skill(store, 0, "Check the log file first."),
              _add_skill(store, 1, "Look at the log file before anything else.")]
    for index in range(2):
        _add_exam(store, index)
    handles = _handles(
        curator_script=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                        "Read the log file before doing anything else.",
                        _grouping({"bank/merge/0000": "error handling"})],
        answer_script=["first answer", "second answer"],
        judge_script=[_CORRECT(), _CORRECT()],
    )

    bank = _curate(store, handles)

    merged_id = "bank/merge/0000"
    assert [row["skill_id"] for row in bank] == [merged_id]
    row = bank[0]
    assert row["content"] == "Read the log file before doing anything else."
    assert row["merged_from"] == sorted(s.skill_id for s in skills)
    assert row["group"] == "error handling"
    assert row["status"] == "accepted"

    # Sources merged away, content preserved in their stored records.
    for skill in skills:
        stored = store.load("skills", skill.skill_id, Skill)
        assert stored.status == "merged"
        assert stored.content == skill.content
    stored_merged = store.load("skills", merged_id, Skill)
    assert stored_merged.status == "accepted"
    assert stored_merged.group == "error handling"

    # Both regression attempts recorded against the merged skill.
    attempts = [store.load("attempts", aid, Attempt)
                for aid in store.ids("attempts")]
    assert len(attempts) == 2
    assert all(a.purpose == "regression" and a.skill_id == merged_id
               and a.judge_verdict is True for a in attempts)

    log = _log_of(store)
    assert log["rounds_executed"] == 1
    assert log["merges"] == {"attempted": 1, "kept": 1, "reverted": 0,
                             "skipped": 0}
    assert log["final_member_ids"] == [merged_id]
    assert log["bank_tokens"] == count_bank_tokens(
        [store.load("skills", merged_id, Skill)])


# ---------------------------------------------------------------------------
# merge_check: none -- merges skip the regression exams (M3)
# ---------------------------------------------------------------------------

def test_merge_check_none_merges_without_any_regression_call(store):
    # The answerer and judge scripts are EMPTY: any regression call would
    # exhaust the script and raise, so passing this test means the merge
    # happened with zero answerer/judge calls throughout, the product went
    # pending -> accepted, and the journal records the same kept outcome.
    skills = [_add_skill(store, 0, "Check the log file first."),
              _add_skill(store, 1, "Look at the log file before anything else.")]
    for index in range(2):
        _add_exam(store, index)
    handles = _handles(
        curator_script=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                        "Read the log file before doing anything else.",
                        _grouping({"bank/merge/0000": "error handling"})],
        answer_script=[],
        judge_script=[],
    )

    bank = _curate(store, handles, merge_check="none")

    merged_id = "bank/merge/0000"
    assert [row["skill_id"] for row in bank] == [merged_id]
    assert bank[0]["status"] == "accepted"
    assert store.load("skills", merged_id, Skill).status == "accepted"
    for skill in skills:
        assert store.load("skills", skill.skill_id, Skill).status == "merged"
    assert store.ids("attempts") == []
    assert handles["answerer_handle"].client.calls == []
    assert handles["judge_handle"].client.calls == []
    assert _log_of(store)["merges"] == {"attempted": 1, "kept": 1,
                                        "reverted": 0, "skipped": 0}


def test_merge_check_unknown_value_refused(store):
    handles = _handles(curator_script=[], answer_script=[], judge_script=[])
    with pytest.raises(ConfigError, match="merge_check"):
        _curate(store, handles, merge_check="regression")


# ---------------------------------------------------------------------------
# Regression failure reverts the merge
# ---------------------------------------------------------------------------

def test_regression_fail_reverts_merge(store):
    skills = [_add_skill(store, 0, "Use short sentences."),
              _add_skill(store, 1, "Prefer short sentences.")]
    for index in range(2):
        _add_exam(store, index)
    handles = _handles(
        curator_script=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                        "Whatever the merge produced.",
                        _grouping({_skill_id(0): "writing",
                                   _skill_id(1): "writing"})],
        answer_script=["a", "b"],
        judge_script=[_CORRECT(), _INCORRECT()],
    )

    bank = _curate(store, handles)

    # Sources stay accepted and ARE the bank; the merged skill never enters.
    assert [row["skill_id"] for row in bank] == sorted(
        s.skill_id for s in skills)
    for skill in skills:
        stored = store.load("skills", skill.skill_id, Skill)
        assert stored.status == "accepted"
        assert stored.group == "writing"
    merged = store.load("skills", "bank/merge/0000", Skill)
    assert merged.status == "discarded"
    assert merged.discard_reason == "regression_failed"
    assert merged.merged_from == sorted(s.skill_id for s in skills)

    log = _log_of(store)
    assert log["merges"]["attempted"] == 1
    assert log["merges"]["reverted"] == 1
    record = log["merge_records"][0]
    assert record["outcome"] == "reverted"
    assert "regression failed" in record["reason"]


# ---------------------------------------------------------------------------
# Conflict merge path
# ---------------------------------------------------------------------------

def test_conflict_pair_merged_with_conflict_mode(store):
    _add_skill(store, 0, "Restart the service on failure.")
    _add_skill(store, 1, "Never restart the service on failure.")
    for index in range(2):
        _add_exam(store, index)
    handles = _handles(
        curator_script=[_similarity(conflicts=[[_skill_id(0), _skill_id(1)]]),
                        "Restart only when the config check fails.",
                        _grouping({"bank/merge/0000": "recovery"})],
        answer_script=["a", "b"],
        judge_script=[_CORRECT(), _CORRECT()],
    )

    bank = _curate(store, handles)

    assert [row["skill_id"] for row in bank] == ["bank/merge/0000"]
    # The conflict mode must reach the MERGE prompt.
    merge_calls = [call for call in handles["curator_handle"].client.calls
                   if "MERGE MODE" in call["user"]]
    assert len(merge_calls) == 1
    assert "conflict" in merge_calls[0]["user"]
    log = _log_of(store)
    assert log["merge_records"][0]["mode"] == "conflict"


# ---------------------------------------------------------------------------
# Early break: nothing to merge and within budget -> done, no G9 call.
# README stage 5 step 4: G9 runs whenever the bank has >= 2 members, even
# with zero merge candidates; only a 1-member (or empty) bank skips it.
# ---------------------------------------------------------------------------

def test_nothing_mergeable_and_within_budget_breaks_early(store):
    skill = _add_skill(store, 0, "One skill.")
    _add_exam(store, 0)
    handles = _handles(
        curator_script=[_similarity()],
        answer_script=[],
        judge_script=[],
    )
    bank = _curate(store, handles)
    assert [row["skill_id"] for row in bank] == [skill.skill_id]
    # Only the G7 call happened: the round broke before any merge or grouping.
    assert len(handles["curator_handle"].client.calls) == 1
    assert bank[0]["group"] is None
    assert store.load("skills", skill.skill_id, Skill).group is None
    assert _log_of(store)["rounds_executed"] == 1


def test_no_merge_candidates_still_groups_the_bank(store):
    # Zero merge candidates, within budget, but a two-member bank: the
    # round must still run G9 (the bank's groups are part of its shape),
    # then stop. Before the fix this broke out before G9 and left every
    # member with group=None against README stage 5 step 4.
    for index in range(2):
        _add_skill(store, index, f"Skill number {index} with advice.")
        _add_exam(store, index)
    handles = _handles(
        curator_script=[_similarity(),
                        _grouping({_skill_id(0): "debugging",
                                   _skill_id(1): "tooling"})],
        answer_script=[],
        judge_script=[],
    )
    bank = _curate(store, handles)

    assert [row["skill_id"] for row in bank] == [_skill_id(0), _skill_id(1)]
    assert [row["group"] for row in bank] == ["debugging", "tooling"]
    assert store.load("skills", _skill_id(0), Skill).group == "debugging"
    # Exactly one G7 call and one G9 call; no merge, no regression exam.
    assert len(handles["curator_handle"].client.calls) == 2
    log = _log_of(store)
    assert log["rounds_executed"] == 1
    assert log["rounds"][0]["done"] is True
    assert log["rounds"][0]["stop"] is True
    assert log["rounds"][0]["grouping"] == {_skill_id(0): "debugging",
                                             _skill_id(1): "tooling"}
    assert log["merges"]["attempted"] == 0


# ---------------------------------------------------------------------------
# Budget: within budget after the first round -> done
# ---------------------------------------------------------------------------

def test_within_budget_first_round_done(store):
    for index in range(2):
        _add_skill(store, index, f"Skill number {index} with advice.")
        _add_exam(store, index)
    handles = _handles(
        curator_script=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                        "Merged advice.",
                        _grouping({"bank/merge/0000": "misc"})],
        answer_script=["a", "b"],
        judge_script=[_CORRECT(), _CORRECT()],
    )
    bank = _curate(store, handles)
    assert len(bank) == 1
    assert _log_of(store)["rounds_executed"] == 1
    # One round only: exactly SIMILARITY, MERGE, GROUP calls.
    assert len(handles["curator_handle"].client.calls) == 3


# ---------------------------------------------------------------------------
# Budget: over budget -> a second round merges more -> within -> done
# ---------------------------------------------------------------------------

def test_over_budget_second_round_merges_more(store):
    big = "x" * 1600
    for index in range(3):
        _add_skill(store, index, big)
        _add_exam(store, index)
    merged_round_one = "y" * 1200
    merged_round_two = "short merged skill"
    handles = _handles(
        curator_script=[
            _similarity(groups=[[_skill_id(0), _skill_id(1)]]),
            merged_round_one,
            _grouping({"bank/merge/0000": "misc", _skill_id(2): "misc"}),
            _similarity(groups=[["bank/merge/0000", _skill_id(2)]]),
            merged_round_two,
            _grouping({"bank/merge/0001": "misc"}),
        ],
        # Round two re-examines bank/merge/0000's sources transitively:
        # exams 0, 1 (via the round-one merge) and 2 -> 2 + 3 attempts.
        answer_script=["a", "b", "c", "d", "e"],
        judge_script=[_CORRECT() for _ in range(5)],
    )

    bank = _curate(store, handles, budget=600, rounds=3)

    assert [row["skill_id"] for row in bank] == ["bank/merge/0001"]
    row = bank[0]
    assert row["merged_from"] == ["bank/merge/0000", _skill_id(2)]
    # The round-one merge is itself merged away in round two.
    assert store.load("skills", "bank/merge/0000", Skill).status == "merged"
    assert store.load("skills", _skill_id(2), Skill).status == "merged"
    log = _log_of(store)
    assert log["rounds_executed"] == 2
    assert log["merges"]["kept"] == 2
    assert log["bank_tokens"] <= 600
    regression_exams = sorted(
        store.load("attempts", aid, Attempt).exam_id
        for aid in store.ids("attempts")
        if store.load("attempts", aid, Attempt).skill_id == "bank/merge/0001")
    assert regression_exams == sorted(
        ids.make_exam_id(_subtask(i)) for i in range(3))


# ---------------------------------------------------------------------------
# Budget: over budget after curation_rounds -> ConfigError, no bank written
# ---------------------------------------------------------------------------

def test_over_budget_after_rounds_raises_and_writes_no_bank(store):
    for index in range(2):
        _add_skill(store, index, "x" * 1600)
        _add_exam(store, index)
    handles = _handles(
        curator_script=[
            _similarity(),
            _grouping({_skill_id(0): "misc", _skill_id(1): "misc"}),
            _similarity(),
            _grouping({_skill_id(0): "misc", _skill_id(1): "misc"}),
        ],
        answer_script=[],
        judge_script=[],
    )

    with pytest.raises(ConfigError) as excinfo:
        _curate(store, handles, budget=10, rounds=2)

    message = str(excinfo.value)
    assert "exceeds bank_token_budget after 2 curation rounds" in message
    assert store.bank() == []
    assert not (store.run_dir / "skill_bank.jsonl").exists()
    log = _log_of(store)
    assert log["rounds_executed"] == 2
    assert log["final_member_ids"] == sorted(_skill_id(i) for i in range(2))


# ---------------------------------------------------------------------------
# Resume: existing bank returned, zero model calls
# ---------------------------------------------------------------------------

def test_resume_returns_existing_bank_without_model_calls(store):
    skill = _add_skill(store, 0, "A stored skill.")
    expected = [skill.to_dict()]
    store.append_bank(skill.to_dict())
    write_json(store.run_dir / LOG_DIR / LOG_NAME,
               {"final_member_ids": [skill.skill_id]})

    handles = _handles(curator_script=[], answer_script=[], judge_script=[])
    # Any model call would exhaust the empty FakeClient script and fail.
    bank = _curate(store, handles)

    assert bank == expected
    assert not handles["curator_handle"].client.calls
    assert not handles["answerer_handle"].client.calls
    assert not handles["judge_handle"].client.calls


def test_resume_completes_bank_interrupted_mid_append(store):
    skills = [_add_skill(store, 0, "First skill."),
              _add_skill(store, 1, "Second skill.")]
    for index in range(2):
        _add_exam(store, index)
    # Simulate a crash after the first append: log written, bank partial.
    store.append_bank(skills[0].to_dict())
    write_json(store.run_dir / LOG_DIR / LOG_NAME,
               {"final_member_ids": sorted(s.skill_id for s in skills)})

    handles = _handles(curator_script=[], answer_script=[], judge_script=[])
    bank = _curate(store, handles)

    assert [row["skill_id"] for row in bank] == sorted(
        s.skill_id for s in skills)
    assert bank[1] == skills[1].to_dict()
    assert not handles["curator_handle"].client.calls


# ---------------------------------------------------------------------------
# Empty accepted set -> empty bank
# ---------------------------------------------------------------------------

def test_empty_accepted_set_yields_empty_bank(store):
    _add_skill(store, 0, "Discarded skill.", status="discarded")
    handles = _handles(curator_script=[], answer_script=[], judge_script=[])
    assert _curate(store, handles) == []
    assert not handles["curator_handle"].client.calls
    assert store.bank() == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_same_inputs_same_member_ids_and_order(tmp_path):
    def run_once(tag):
        store = Store(tmp_path / f"run_{tag}")
        for index in range(2):
            _add_skill(store, index, f"Skill {index} content.")
            _add_exam(store, index)
        handles = _handles(
            curator_script=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                            "Merged content.",
                            _grouping({"bank/merge/0000": "misc"})],
            answer_script=["a", "b"],
            judge_script=[_CORRECT(), _CORRECT()],
        )
        bank = _curate(store, handles)
        log = _log_of(store)
        return bank, log, store

    bank_a, log_a, store_a = run_once("a")
    bank_b, log_b, _ = run_once("b")
    assert bank_a == bank_b
    assert log_a == log_b
    assert store_a.bank() == bank_a


# ---------------------------------------------------------------------------
# Unverifiable merge: a source has no stored exam
# ---------------------------------------------------------------------------

def test_merge_skipped_when_source_exam_missing(store):
    skills = [_add_skill(store, 0, "Skill zero."),
              _add_skill(store, 1, "Skill one.")]
    _add_exam(store, 0)  # only ONE exam: skill 1 is unverifiable
    handles = _handles(
        curator_script=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                        _grouping({_skill_id(0): "misc",
                                   _skill_id(1): "misc"})],
        answer_script=[],
        judge_script=[],
    )

    bank = _curate(store, handles)

    # No MERGE call happened and no merged skill was stored.
    merge_calls = [call for call in handles["curator_handle"].client.calls
                   if "MERGE MODE" in call["user"]]
    assert merge_calls == []
    assert "bank/merge/0000" not in store.ids("skills")
    assert [row["skill_id"] for row in bank] == sorted(
        s.skill_id for s in skills)
    log = _log_of(store)
    assert log["merges"]["skipped"] == 1
    assert "no stored exam" in log["merge_records"][0]["reason"]


# ---------------------------------------------------------------------------
# Merge-of-merge: regression covers every ORIGINAL source's exam
# ---------------------------------------------------------------------------

def test_merge_of_merge_of_merge_resolves_sources_transitively(store):
    for index in range(3):
        _add_skill(store, index, f"original skill {index}")
        _add_exam(store, index)
    # A merge whose own source is a merge: bank/merge/0001 <- (0000, s2),
    # bank/merge/0000 <- (s0, s1). Its exams are those of s0, s1, s2.
    for serial, sources in ((0, [_skill_id(0), _skill_id(1)]),
                            (1, ["bank/merge/0000", _skill_id(2)])):
        store.save(Skill(skill_id=f"bank/merge/{serial:04d}",
                         content=f"merged {serial}",
                         subtask_id=_subtask(0),
                         source_subtasks=[_subtask(0), _subtask(1)]
                         if serial == 0
                         else [_subtask(0), _subtask(1), _subtask(2)],
                         status="accepted",
                         merged_from=sources), "skills")
        for source in sources:
            skill = store.load("skills", source, Skill)
            skill.status = "merged"
            store.save(skill, "skills")
    _add_skill(store, 3, "original skill 3")
    _add_exam(store, 3)
    handles = _handles(
        curator_script=[_similarity(groups=[["bank/merge/0001", _skill_id(3)]]),
                        "merged twice",
                        _grouping({"bank/merge/0002": "misc"})],
        answer_script=["a", "b", "c", "d"],
        judge_script=[_CORRECT() for _ in range(4)],
    )

    bank = _curate(store, handles)

    assert [row["skill_id"] for row in bank] == ["bank/merge/0002"]
    exams = [store.load("attempts", aid, Attempt).exam_id
             for aid in store.ids("attempts")]
    assert sorted(exams) == sorted(
        ids.make_exam_id(_subtask(i)) for i in range(4))


# ---------------------------------------------------------------------------
# Unit-level failure disposition (ModelOutputError)
# ---------------------------------------------------------------------------

def _failures(store):
    return read_jsonl(store.run_dir / "failed_units.jsonl")


def test_merge_model_output_error_skips_that_merge_and_continues(store):
    for index in range(4):
        _add_skill(store, index, f"skill {index}")
        _add_exam(store, index)
    handles = _handles(
        curator_script=[
            _similarity(groups=[[_skill_id(0), _skill_id(1)],
                                [_skill_id(2), _skill_id(3)]]),
            ModelOutputError("merge text unusable"),
            "merged two and three",
            _grouping({_skill_id(0): "a", _skill_id(1): "a",
                       "bank/merge/0001": "b"})],
        answer_script=["a", "b"],
        judge_script=[_CORRECT(), _CORRECT()],
    )

    bank = _curate(store, handles)

    # Serials are allocated at dispatch, so the failed G8 BURNS serial 0
    # (v3 semantic change, recorded in its skipped record); the next merge
    # is bank/merge/0001.
    assert [row["skill_id"] for row in bank] == sorted(
        ["bank/merge/0001", _skill_id(0), _skill_id(1)])
    assert store.load("skills", "bank/merge/0001", Skill).merged_from == [
        _skill_id(2), _skill_id(3)]
    log = _log_of(store)
    assert log["merges"] == {"attempted": 2, "kept": 1, "reverted": 0,
                             "skipped": 1}
    skipped = log["merge_records"][0]
    assert skipped["outcome"] == "skipped"
    assert skipped["serial"] == 0
    assert "ModelOutputError" in skipped["reason"]
    failures = _failures(store)
    assert len(failures) == 1
    assert failures[0]["stage"] == "stage5_curation"
    assert failures[0]["error_class"] == "ModelOutputError"


def test_regression_model_output_error_reverts_that_merge(store):
    for index in range(2):
        _add_skill(store, index, f"skill {index}")
        _add_exam(store, index)
    handles = _handles(
        curator_script=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                        "merged",
                        _grouping({_skill_id(0): "a", _skill_id(1): "a"})],
        answer_script=["a"],
        # K3 resamples the judge once on ModelOutputError, then gives up.
        judge_script=[ModelOutputError("bad verdict"),
                      ModelOutputError("bad verdict")],
    )

    bank = _curate(store, handles)

    assert [row["skill_id"] for row in bank] == [_skill_id(0), _skill_id(1)]
    merged = store.load("skills", "bank/merge/0000", Skill)
    assert merged.status == "discarded"
    assert merged.discard_reason == "regression_error"
    record = _log_of(store)["merge_records"][0]
    assert record["outcome"] == "reverted"
    assert "ModelOutputError" in record["reason"]
    assert [f["error_class"] for f in _failures(store)] == ["ModelOutputError"]


@pytest.mark.parametrize("call", ["similarity", "grouping"])
def test_whole_bank_model_output_error_propagates(store, call):
    for index in range(2):
        _add_skill(store, index, f"skill {index}")
        _add_exam(store, index)
    if call == "similarity":
        curator = [ModelOutputError("bad G7")]
    else:
        curator = [_similarity(), ModelOutputError("bad G9")]
    handles = _handles(curator_script=curator, answer_script=[],
                       judge_script=[])

    with pytest.raises(ModelOutputError):
        _curate(store, handles, budget=1)

    assert store.bank() == []
    failures = _failures(store)
    assert len(failures) == 1
    assert failures[0]["stage"] == "stage5_curation"
    assert call in failures[0]["unit_id"]


# ---------------------------------------------------------------------------
# Resume edge cases: foreign journal, orphaned in-flight merge
# ---------------------------------------------------------------------------

def test_unversioned_log_is_refused_instead_of_misread(store):
    _add_skill(store, 0, "Skill zero.")
    write_json(store.run_dir / LOG_DIR / LOG_NAME,
               {"rounds_executed": 1, "merge_records": []})
    handles = _handles(curator_script=[], answer_script=[], judge_script=[])
    with pytest.raises(ConfigError, match="journal"):
        _curate(store, handles)
    assert not handles["curator_handle"].client.calls


def test_orphaned_pending_merge_is_rolled_back_and_its_serial_not_reused(
        store):
    for index in range(3):
        _add_skill(store, index, f"skill {index}")
        _add_exam(store, index)
    orphan = Skill(skill_id="bank/merge/0000", content="orphan text",
                   subtask_id=_subtask(0),
                   source_subtasks=[_subtask(0), _subtask(1)],
                   status="pending",
                   merged_from=[_skill_id(0), _skill_id(1)])
    store.save(orphan, "skills")
    handles = _handles(
        curator_script=[_similarity(groups=[[_skill_id(1), _skill_id(2)]]),
                        "merged one and two",
                        _grouping({_skill_id(0): "a",
                                   "bank/merge/0001": "a"})],
        answer_script=["a", "b"],
        judge_script=[_CORRECT(), _CORRECT()],
    )

    bank = _curate(store, handles)

    assert [row["skill_id"] for row in bank] == [
        "bank/merge/0001", _skill_id(0)]
    rolled_back = store.load("skills", "bank/merge/0000", Skill)
    assert rolled_back.status == "discarded"
    assert rolled_back.discard_reason == "interrupted_merge"
    assert rolled_back.content == "orphan text"


def test_pending_merge_with_merged_sources_is_rolled_forward(store):
    # A kept merge whose log was lost: sources are only ever marked merged
    # after a kept decision, so the merge is finalised, not discarded.
    for index in range(2):
        _add_exam(store, index)
    _add_skill(store, 0, "skill 0", status="merged")
    _add_skill(store, 1, "skill 1")
    store.save(Skill(skill_id="bank/merge/0000", content="merged",
                     subtask_id=_subtask(0),
                     source_subtasks=[_subtask(0), _subtask(1)],
                     status="pending",
                     merged_from=[_skill_id(0), _skill_id(1)]), "skills")
    handles = _handles(curator_script=[_similarity()], answer_script=[],
                       judge_script=[])

    bank = _curate(store, handles)

    assert [row["skill_id"] for row in bank] == ["bank/merge/0000"]
    assert store.load("skills", _skill_id(1), Skill).status == "merged"


# ---------------------------------------------------------------------------
# Cross-stage staleness guard on the finished-bank resume path
# ---------------------------------------------------------------------------

def _finish_one_merge_run(store):
    for index in range(2):
        _add_skill(store, index, f"skill {index}")
        _add_exam(store, index)
    handles = _handles(
        curator_script=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                        "merged", _grouping({"bank/merge/0000": "a"})],
        answer_script=["a", "b"], judge_script=[_CORRECT(), _CORRECT()])
    return _curate(store, handles)


def test_resume_refuses_stale_bank_with_new_accepted_skills(store):
    bank = _finish_one_merge_run(store)
    # A later stage-4 resume accepted a skill after the bank was written.
    late = _add_skill(store, 7, "accepted after curation finished")

    handles = _handles(curator_script=[], answer_script=[], judge_script=[])
    with pytest.raises(ConfigError, match="skill bank is stale") as excinfo:
        _curate(store, handles)
    assert late.skill_id in str(excinfo.value)
    assert "start a new run directory" in str(excinfo.value)
    assert "remove" not in str(excinfo.value)
    # Never auto-merged into the finished bank, no model call.
    assert store.bank() == bank
    assert not handles["curator_handle"].client.calls


def test_stale_guard_runs_after_completing_a_truncated_append(store):
    skills = [_add_skill(store, 0, "First skill."),
              _add_skill(store, 1, "Second skill.")]
    store.append_bank(skills[0].to_dict())
    write_json(store.run_dir / LOG_DIR / LOG_NAME,
               {"final_member_ids": sorted(s.skill_id for s in skills)})
    # Skill 1 is a member (completed from the log), skill 2 is not.
    _add_skill(store, 2, "late skill")
    handles = _handles(curator_script=[], answer_script=[], judge_script=[])
    with pytest.raises(ConfigError, match=r"\['" + _skill_id(2)):
        _curate(store, handles)
    assert [row["skill_id"] for row in store.bank()] == sorted(
        s.skill_id for s in skills)


def test_stale_guard_ignores_merged_discarded_and_pending_skills(store):
    bank = _finish_one_merge_run(store)
    # Non-accepted records (a stage-4 skill still pending, a discarded
    # version, a pending merged skill) are not bank candidates.
    _add_skill(store, 5, "still being accepted", status="pending")
    _add_skill(store, 6, "rejected", status="discarded")
    store.save(Skill(skill_id="bank/merge/0009", content="in flight",
                     subtask_id=_subtask(0),
                     source_subtasks=[_subtask(0), _subtask(1)],
                     status="pending",
                     merged_from=[_skill_id(0), _skill_id(1)]), "skills")
    handles = _handles(curator_script=[], answer_script=[], judge_script=[])
    assert _curate(store, handles) == bank


# ---------------------------------------------------------------------------
# Protocol v2 K5 marker checks: pre-check, post-check, prose tolerance,
# and the reuse branch's no-re-validation rule.
# ---------------------------------------------------------------------------

PROCEDURE_CONTENT = "\n".join([
    "[When to use] You need to restart a stuck service.",
    "[Steps]",
    "1. Check the service status.",
    "2. Restart the service.",
    "[Verify] The status command reports running.",
    "[Do not use when] The service already reports running.",
])

FAILURE_REFERENCE_CONTENT = "\n".join([
    "[Trigger] The service rejects connections after a config edit.",
    "[Why it fails] The config edit was not followed by a restart.",
    "[Do instead]",
    "1. Re-check the config file for syntax errors.",
    "2. Restart the service.",
    "[Do not use when] The service never accepted connections.",
])


@pytest.mark.parametrize("other_content", [
    FAILURE_REFERENCE_CONTENT,  # procedure mixed with failure reference
    "Check the log file first.",  # marked mixed with unmarked prose
])
def test_mixed_kind_group_skips_the_merge_before_any_g8_call(
        store, other_content):
    _add_skill(store, 0, PROCEDURE_CONTENT)
    _add_skill(store, 1, other_content)
    for index in range(2):
        _add_exam(store, index)
    handles = _handles(
        curator_script=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                        _grouping({_skill_id(0): "a", _skill_id(1): "a"})],
        answer_script=[],
        judge_script=[],
    )

    bank = _curate(store, handles)

    # The pre-check fired with ZERO G8 calls; nothing was stored and both
    # sources stay accepted bank members.
    merge_calls = [call for call in handles["curator_handle"].client.calls
                   if "MERGE MODE" in call["user"]]
    assert merge_calls == []
    assert "bank/merge/0000" not in store.ids("skills")
    assert [row["skill_id"] for row in bank] == [_skill_id(0), _skill_id(1)]
    log = _log_of(store)
    assert log["merges"]["skipped"] == 1
    record = log["merge_records"][0]
    assert record["outcome"] == "skipped"
    # The skip record carries the pre-check's own reason: G8 never ran.
    assert "merge group mixes skill kinds" in record["reason"]
    assert "G8 merge failed" not in record["reason"]
    failures = _failures(store)
    assert len(failures) == 1
    assert failures[0]["error_class"] == "ModelOutputError"


def test_merge_output_missing_sections_is_skipped_and_never_stored(store):
    _add_skill(store, 0, PROCEDURE_CONTENT)
    _add_skill(store, 1, PROCEDURE_CONTENT)
    for index in range(2):
        _add_exam(store, index)
    # A G8 output that dropped [Verify]: skill_markers no longer reads the
    # sources' procedure set.
    broken_output = "\n".join([
        "[When to use] You need to restart a stuck service.",
        "[Steps]",
        "1. Check the service status.",
        "2. Restart the service.",
        "[Do not use when] The service already reports running.",
    ])
    handles = _handles(
        curator_script=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                        broken_output,
                        _grouping({_skill_id(0): "a", _skill_id(1): "a"})],
        answer_script=[],
        judge_script=[],
    )

    bank = _curate(store, handles)

    # The G8 call DID happen (its output failed the post-check), but the
    # merged skill never reached the store -- commit step 1 never ran.
    merge_calls = [call for call in handles["curator_handle"].client.calls
                   if "MERGE MODE" in call["user"]]
    assert len(merge_calls) == 1
    assert "bank/merge/0000" not in store.ids("skills")
    assert [row["skill_id"] for row in bank] == [_skill_id(0), _skill_id(1)]
    log = _log_of(store)
    assert log["merges"]["skipped"] == 1
    record = log["merge_records"][0]
    assert record["outcome"] == "skipped"
    # G8 ran and produced an unusable output: the existing G8-failure
    # skip path and its reason prefix apply.
    assert "G8 merge failed" in record["reason"]
    assert "four bracket sections" in record["reason"]
    assert len(_failures(store)) == 1


def test_all_prose_group_skips_both_marker_checks(store):
    # Prose sources (every pre-v2 fixture's shape, and the v1 TB bank
    # under v2 code): both marker checks are skipped, so even a G8 output
    # that carries a full section structure is kept -- no marker equality
    # is ever asserted against all-unmarked sources.
    _add_skill(store, 0, "Check the log file first.")
    _add_skill(store, 1, "Look at the log file before anything else.")
    for index in range(2):
        _add_exam(store, index)
    handles = _handles(
        curator_script=[_similarity(groups=[[_skill_id(0), _skill_id(1)]]),
                        PROCEDURE_CONTENT,
                        _grouping({"bank/merge/0000": "error handling"})],
        answer_script=["a", "b"],
        judge_script=[_CORRECT(), _CORRECT()],
    )

    bank = _curate(store, handles)

    assert [row["skill_id"] for row in bank] == ["bank/merge/0000"]
    assert bank[0]["content"] == PROCEDURE_CONTENT
    assert _log_of(store)["merges"]["kept"] == 1


def test_resume_reuse_branch_does_not_revalidate_the_pending_merge(store):
    for index in range(2):
        _add_skill(store, index, PROCEDURE_CONTENT)
        _add_exam(store, index)
    # A crash after commit step 1 left a journaled in-flight merge whose
    # content would FAIL the post-check. A pending merge was validated
    # before it was ever saved, so the reuse branch takes it without any
    # re-check; the regression exams and the keep decision proceed.
    store.save(Skill(skill_id="bank/merge/0000",
                     content="prose that lost its markers",
                     subtask_id=_subtask(0),
                     source_subtasks=[_subtask(0), _subtask(1)],
                     status="pending",
                     merged_from=sorted([_skill_id(0), _skill_id(1)])),
               "skills")
    write_json(store.run_dir / LOG_DIR / LOG_NAME, {
        "schema": LOG_SCHEMA,
        "rounds_executed": 1,
        "merges": {"attempted": 0, "kept": 0, "reverted": 0, "skipped": 0},
        "merge_records": [],
        "rounds": [{
            "round": 1,
            "similar_groups": [[_skill_id(0), _skill_id(1)]],
            "conflicts": [],
            "grouping": None,
            "dispatch": [{"unit": 0, "mode": "dedup",
                          "sources": sorted([_skill_id(0), _skill_id(1)]),
                          "serial": 0}],
            "done": False,
            "stop": False,
        }],
        "final_member_ids": [],
        "bank_tokens": 0,
        "input_skill_ids": sorted([_skill_id(0), _skill_id(1)]),
    })
    handles = _handles(
        curator_script=[_grouping({"bank/merge/0000": "misc"})],
        answer_script=["a", "b"],
        judge_script=[_CORRECT(), _CORRECT()],
    )

    bank = _curate(store, handles)

    # No G8 call and no marker veto: the reused merge is kept as stored.
    merge_calls = [call for call in handles["curator_handle"].client.calls
                   if "MERGE MODE" in call["user"]]
    assert merge_calls == []
    assert [row["skill_id"] for row in bank] == ["bank/merge/0000"]
    assert bank[0]["content"] == "prose that lost its markers"
    assert _log_of(store)["merges"]["kept"] == 1
