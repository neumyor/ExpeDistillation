"""K5 crash-injection resume tests (README stage 5, AGENTS.md section 8).

One two-round scenario is run uninterrupted to get the reference outcome,
then re-run with a crash injected at one boundary and resumed with the
REMAINING model script. The resumed run must end in exactly the reference
state -- same bank rows, same stored skills (statuses, groups, merged-skill
ids), same regression attempts, same curation log -- must consume exactly the
remaining script (no unit redone, none lost), and must never overwrite a
kept merge.

Scenario (budget 600 tokens, four 1600-char skills s0..s3):
  round 1: G7 -> groups [s0, s1], [s2, s3]
           merge A = bank/merge/0000 from (s0, s1): regression passes -> kept
           merge B = bank/merge/0001 from (s2, s3): exam 3 fails -> reverted
           G9 over {0000, s2, s3}; still over budget
  round 2: G7 -> group [0000, s2]
           merge C = bank/merge/0002 from (0000, s2): merge-of-merge, so the
           regression covers the exams of s0, s1 AND s2 -> kept
           G9 over {0002, s3}; within budget -> bank written
"""

import json

import pytest

from expedistill.composites import curation
from expedistill.composites.curation import LOG_DIR, LOG_NAME, curate_bank
from expedistill.core import ids
from expedistill.core.models import Attempt, Exam, Skill
from expedistill.infra.reliability import ConfigError, InfrastructureError
from expedistill.infra.store import (
    Store,
    read_json,
    read_jsonl,
    write_json,
)
from expedistill.llm import json_script, make_fake_handle


class Crash(Exception):
    """Simulated process death: not a ReliabilityError, so nothing retries
    or catches it -- it unwinds K5 exactly like a kill would stop it."""


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

def _subtask(index):
    return f"traj/tb:t{index}/strong/00/seg00"


def _sid(index):
    return ids.make_skill_id(_subtask(index))


M0, M1, M2 = (ids.make_merged_skill_id(n) for n in range(3))
BUDGET = 600


def _correct():
    return json_script({"verdict": "correct", "error_description": ""})


def _incorrect():
    return json_script({"verdict": "incorrect", "error_description": "wrong"})


def _similarity(groups):
    return json_script({"similar_groups": groups, "conflicts": []})


def _grouping(mapping):
    return json_script({"groups": mapping})


def _scripts():
    curator = [
        _similarity([[_sid(0), _sid(1)], [_sid(2), _sid(3)]]),   # 0 G7 r1
        "y" * 1200,                                              # 1 G8 A
        "z" * 1500,                                              # 2 G8 B
        _grouping({M0: "g1", _sid(2): "g1", _sid(3): "g2"}),     # 3 G9 r1
        _similarity([[M0, _sid(2)]]),                            # 4 G7 r2
        "short merged skill",                                    # 5 G8 C
        _grouping({M2: "g3", _sid(3): "g2"}),                    # 6 G9 r2
    ]
    # A: exams 0, 1; B: exams 2, 3 (3 fails); C: exams 0, 1, 2.
    answerer = [f"answer {i}" for i in range(7)]
    judge = [_correct(), _correct(),
             _correct(), _incorrect(),
             _correct(), _correct(), _correct()]
    return {"curator": curator, "answerer": answerer, "judge": judge}


class GuardStore(Store):
    """Store that (a) refuses illegal skill rewrites and (b) can crash.

    (a) A stored skill's content / merged_from never change, and its status
    only moves pending->accepted, pending->discarded, accepted->merged (or
    stays). Any other rewrite -- e.g. a new merge written over a kept one --
    fails the test immediately.
    (b) ``crash`` is ``(when, predicate)``: raise Crash before or after the
    first save whose (obj, kind) matches.
    """

    ALLOWED = {("pending", "accepted"), ("pending", "discarded"),
               ("accepted", "merged")}

    def __init__(self, run_dir, crash=None):
        super().__init__(run_dir)
        self.crash = crash

    def save(self, obj, kind):
        if kind == "skills" and self.exists("skills", obj.skill_id):
            old = self.load("skills", obj.skill_id, Skill)
            assert old.content == obj.content, (
                f"content of {obj.skill_id} overwritten")
            assert old.merged_from == obj.merged_from, (
                f"merged_from of {obj.skill_id} overwritten")
            assert (old.status == obj.status
                    or (old.status, obj.status) in self.ALLOWED), (
                f"illegal status change {old.status}->{obj.status} "
                f"for {obj.skill_id}")
        crash = self.crash
        if crash is not None and crash[0] == "before" and crash[1](obj, kind):
            self.crash = None
            raise Crash(f"crash before saving {kind}")
        path = super().save(obj, kind)
        if crash is not None and crash[0] == "after" and crash[1](obj, kind):
            self.crash = None
            raise Crash(f"crash after saving {kind}")
        return path


def _seed(store):
    for index in range(4):
        store.save(Skill(skill_id=_sid(index), content=chr(97 + index) * 1600,
                         subtask_id=_subtask(index),
                         source_subtasks=[_subtask(index)],
                         status="accepted"),
                   "skills")
        store.save(Exam(exam_id=ids.make_exam_id(_subtask(index)),
                        question_text=f"question {index}",
                        reference_answer=f"reference {index}",
                        subtask_id=_subtask(index),
                        skill_id=_sid(index)), "exams")


def _handles(scripts):
    return {
        "curator_handle": make_fake_handle("curator", scripts["curator"]),
        "answerer_handle": make_fake_handle("weak", scripts["answerer"]),
        "judge_handle": make_fake_handle("judge", scripts["judge"]),
    }


def _run(store, handles):
    return curate_bank(
        store=store, weak_profile="weak", bank_token_budget=BUDGET,
        curation_rounds=3, run_dir=store.run_dir,
        **handles)


def _remaining(handles):
    return {"curator": handles["curator_handle"].client.script,
            "answerer": handles["answerer_handle"].client.script,
            "judge": handles["judge_handle"].client.script}


def _snapshot(store):
    return {
        "bank": store.bank(),
        "skills": {i: store.load("skills", i, Skill).to_dict()
                   for i in store.ids("skills")},
        "attempts": {i: store.load("attempts", i, Attempt).to_dict()
                     for i in store.ids("attempts")},
        "log": read_json(store.run_dir / LOG_DIR / LOG_NAME),
    }


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    store = GuardStore(tmp_path_factory.mktemp("ref") / "run")
    _seed(store)
    handles = _handles(_scripts())
    _run(store, handles)
    assert _remaining(handles) == {"curator": [], "answerer": [], "judge": []}
    return _snapshot(store)


def test_reference_scenario_shape(reference):
    """Pin the scenario itself, so a crash test cannot pass vacuously."""
    assert [row["skill_id"] for row in reference["bank"]] == [M2, _sid(3)]
    skills = reference["skills"]
    assert skills[M0]["status"] == "merged"
    assert skills[M1]["status"] == "discarded"
    assert skills[M2]["status"] == "accepted"
    assert skills[M2]["merged_from"] == [M0, _sid(2)]
    assert [skills[_sid(i)]["status"] for i in range(4)] == [
        "merged", "merged", "merged", "accepted"]
    log = reference["log"]
    assert log["rounds_executed"] == 2
    assert log["merges"] == {"attempted": 3, "kept": 2, "reverted": 1,
                             "skipped": 0}
    assert [r["skill_id"] for r in log["merge_records"]] == [M0, M1, M2]


def test_merge_of_merge_regression_covers_all_transitive_sources(reference):
    """README stage 5: the merged skill redoes EVERY source skill's exam --
    for a merge of a merge, that is every original (stage-3) skill's exam,
    not only the anchor's."""
    exams_for = {}
    for attempt in reference["attempts"].values():
        assert attempt["purpose"] == "regression"
        exams_for.setdefault(attempt["skill_id"], []).append(
            attempt["exam_id"])
    expected = sorted(ids.make_exam_id(_subtask(i)) for i in range(3))
    assert sorted(exams_for[M2]) == expected
    assert len(exams_for[M2]) == 3  # no exam examined twice


# ---------------------------------------------------------------------------
# Crash injection
# ---------------------------------------------------------------------------

def _is_skill(skill_id, status=None, group=None):
    def predicate(obj, kind):
        return (kind == "skills" and obj.skill_id == skill_id
                and (status is None or obj.status == status)
                and (group is None or obj.group == group))
    return predicate


def _is_unjudged(skill_id=None):
    def predicate(obj, kind):
        return (kind == "attempts" and obj.judge_verdict is None
                and (skill_id is None or obj.skill_id == skill_id))
    return predicate


#: name -> (handle to inject a Crash into, script index) or
#:         ("store", (when, predicate)) or ("log", n-th write to fail)
CRASH_POINTS = {
    # store boundaries inside merge A's commit
    "after_merged_skill_saved": ("store", ("after", _is_skill(M0, "pending"))),
    "between_source_status_saves": ("store", ("before",
                                              _is_skill(_sid(1), "merged"))),
    "before_merged_flipped_accepted": ("store", ("before",
                                                 _is_skill(M0, "accepted"))),
    "after_merged_flipped_accepted": ("store", ("after",
                                                _is_skill(M0, "accepted"))),
    "before_revert_saved": ("store", ("before", _is_skill(M1, "discarded"))),
    # inside round 1's G9 application
    "mid_grouping_apply": ("store", ("before", _is_skill(_sid(2),
                                                         group="g1"))),
    # K3 stores the answer (judge_verdict None) before judging: crashes
    # after that save must resume by judging the stored answer, not by
    # reading the unjudged attempt as a failed regression.
    "after_unjudged_attempt_saved": ("store", ("after", _is_unjudged())),
    "after_unjudged_merge_of_merge_attempt_saved": (
        "store", ("after", _is_unjudged(M2))),
    "first_regression_judge": ("judge", 0),
    "revert_merge_failing_judge": ("judge", 3),
    # model-call boundaries
    "first_regression_answer": ("answerer", 0),
    "before_second_merge": ("curator", 2),
    "after_round_grouping": ("curator", 4),
    "mid_second_round_merge": ("curator", 5),
    "mid_second_round_regression": ("answerer", 5),
    "before_second_round_grouping": ("curator", 6),
}


@pytest.mark.parametrize("name", sorted(CRASH_POINTS))
def test_crash_then_resume_equals_uninterrupted(tmp_path, reference, name):
    target, where = CRASH_POINTS[name]
    scripts = _scripts()
    crash = None
    if target == "store":
        crash = where
    else:
        scripts[target].insert(where, Crash(f"crash at {target}[{where}]"))
    store = GuardStore(tmp_path / "run", crash=crash)
    _seed(store)
    handles = _handles(scripts)

    with pytest.raises(Crash):
        _run(store, handles)
    assert store.bank() == []

    resumed = GuardStore(tmp_path / "run")
    remaining = _remaining(handles)
    resume_handles = _handles(remaining)
    _run(resumed, resume_handles)

    # Every unit done before the crash was reused, none redone or lost.
    assert _remaining(resume_handles) == {
        "curator": [], "answerer": [], "judge": []}
    assert _snapshot(resumed) == reference

    # A further resume of the finished bank: no model call, and no
    # legitimate post-crash state (merged sources, reverted or rolled-back
    # merges) trips the staleness guard.
    idle = _handles({"curator": [], "answerer": [], "judge": []})
    assert _run(GuardStore(tmp_path / "run"), idle) == reference["bank"]
    assert not any(h.client.calls for h in idle.values())


def test_crash_at_merge_commit_log_write_reuses_stored_attempts(
        tmp_path, reference, monkeypatch):
    """Crash on the log write that commits merge A's 'kept' decision: the
    regression attempts are already stored and must be reused on resume (no
    second answer / judge call), and the merge is then committed."""
    real_write = curation._write_log
    state = {"armed": True}

    def crashing_write(log, path):
        if state["armed"] and len(log.get("merge_records", [])) == 1:
            state["armed"] = False
            raise Crash("crash on the commit log write")
        real_write(log, path)

    monkeypatch.setattr(curation, "_write_log", crashing_write)
    store = GuardStore(tmp_path / "run")
    _seed(store)
    handles = _handles(_scripts())
    with pytest.raises(Crash):
        _run(store, handles)
    # At the crash, A is pending, its sources still accepted.
    assert store.load("skills", M0, Skill).status == "pending"
    assert store.load("skills", _sid(0), Skill).status == "accepted"

    resume_handles = _handles(_remaining(handles))
    _run(GuardStore(tmp_path / "run"), resume_handles)
    assert _remaining(resume_handles) == {
        "curator": [], "answerer": [], "judge": []}
    assert _snapshot(GuardStore(tmp_path / "run")) == reference


def test_two_successive_crashes_then_resume(tmp_path, reference):
    scripts = _scripts()
    scripts["answerer"].insert(0, Crash("first"))
    store = GuardStore(tmp_path / "run")
    _seed(store)
    handles = _handles(scripts)
    with pytest.raises(Crash):
        _run(store, handles)

    remaining = _remaining(handles)
    # Second crash mid second-round regression (answer index 5 overall, i.e.
    # index 5 of the remaining answer script since none were consumed).
    remaining["answerer"].insert(5, Crash("second"))
    handles = _handles(remaining)
    with pytest.raises(Crash):
        _run(GuardStore(tmp_path / "run"), handles)

    handles = _handles(_remaining(handles))
    _run(GuardStore(tmp_path / "run"), handles)
    assert _remaining(handles) == {"curator": [], "answerer": [], "judge": []}
    assert _snapshot(GuardStore(tmp_path / "run")) == reference


def test_resume_after_kept_merge_never_reuses_its_serial(tmp_path, reference):
    """The original bug: merge serials restarted at 0 on every call, so the
    first merge after a resume overwrote bank/merge/0000."""
    scripts = _scripts()
    scripts["curator"].insert(2, Crash("after merge A"))
    store = GuardStore(tmp_path / "run")
    _seed(store)
    handles = _handles(scripts)
    with pytest.raises(Crash):
        _run(store, handles)
    kept = store.load("skills", M0, Skill)
    assert kept.status == "accepted"
    first_log = read_json(store.run_dir / LOG_DIR / LOG_NAME)
    assert [r["skill_id"] for r in first_log["merge_records"]] == [M0]

    resumed = GuardStore(tmp_path / "run")
    _run(resumed, _handles(_remaining(handles)))
    assert resumed.load("skills", M0, Skill).content == kept.content
    assert resumed.load("skills", M1, Skill).content == "z" * 1500
    # The previous call's merge records are carried forward, not dropped.
    log = read_json(resumed.run_dir / LOG_DIR / LOG_NAME)
    assert log["merge_records"][0] == first_log["merge_records"][0]
    assert len(log["merge_records"]) == 3


# ---------------------------------------------------------------------------
# InfrastructureError: stop the stage, record, resume continues from disk
# ---------------------------------------------------------------------------

def test_infrastructure_error_propagates_is_recorded_and_resumes(
        tmp_path, reference):
    scripts = _scripts()
    scripts["curator"].insert(2, InfrastructureError("endpoint down"))
    store = GuardStore(tmp_path / "run")
    _seed(store)
    handles = _handles(scripts)

    with pytest.raises(InfrastructureError):
        _run(store, handles)
    failures = read_jsonl(store.run_dir / "failed_units.jsonl")
    assert len(failures) == 1
    assert failures[0]["stage"] == "stage5_curation"
    assert failures[0]["error_class"] == "InfrastructureError"
    # The merge was NOT recorded as skipped: bank content must not depend on
    # network luck.
    log = read_json(store.run_dir / LOG_DIR / LOG_NAME)
    assert [r["outcome"] for r in log["merge_records"]] == ["kept"]

    resume_handles = _handles(_remaining(handles))
    _run(GuardStore(tmp_path / "run"), resume_handles)
    assert _remaining(resume_handles) == {
        "curator": [], "answerer": [], "judge": []}
    assert _snapshot(GuardStore(tmp_path / "run")) == reference


# ---------------------------------------------------------------------------
# Staleness and corruption guards on the pre-bank resume path
# ---------------------------------------------------------------------------

class AppendCrashStore(GuardStore):
    """Crashes on the first bank append: every round is logged done, the
    final members are logged, but skill_bank.jsonl is still empty."""

    def append_bank(self, skill_dict):
        raise Crash("crash before the bank append")


def _crash_before_bank(tmp_path):
    store = AppendCrashStore(tmp_path / "run")
    _seed(store)
    handles = _handles(_scripts())
    with pytest.raises(Crash):
        _run(store, handles)
    assert store.bank() == []
    return GuardStore(tmp_path / "run")


def _idle():
    return _handles({"curator": [], "answerer": [], "judge": []})


def test_resume_before_bank_append_completes_without_model_calls(
        tmp_path, reference):
    store = _crash_before_bank(tmp_path)
    idle = _idle()
    _run(store, idle)
    assert not any(h.client.calls for h in idle.values())
    assert _snapshot(store) == reference


def _downgrade_run_to_v1(run_dir):
    """Rewrite the run's stored skill records and bank rows to the
    pre-v2 shape (schema_version 1, no source_subtasks): the state a run
    written by pre-v2 code is in on disk. Note this is a fixture shape,
    not a supported resume scenario: per AGENTS.md 6.1 a formal v1 run
    resumes on the old code of its own maintenance branch, never on this
    checkout (R6)."""
    for obj_id in Store(run_dir).ids("skills"):
        path = Store(run_dir).path_for("skills", obj_id)
        row = read_json(path)
        del row["source_subtasks"]
        row["schema_version"] = 1
        write_json(path, row, indent=2)
    bank_path = run_dir / "skill_bank.jsonl"
    rows = read_jsonl(bank_path)
    for row in rows:
        del row["source_subtasks"]
        row["schema_version"] = 1
    bank_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows), encoding="utf-8")


def test_resume_completes_a_v1_bank_prefix_in_the_v1_form(tmp_path):
    """The completion's promise is "the same dicts the interrupted append
    loop wrote" (I1): a v1-object run (records and bank rows written by
    pre-v2 code) resumed after a crash mid-append must gain rows in its
    own v1 form -- never the migrated v2 form (a mixed-version bank).
    What this pins is that store-as-stored invariant, which serves v2
    runs just as much; the v1-resume scenario itself is outside the
    supported scope (AGENTS.md 6.1 / R6: a formal v1 run resumes on its
    maintenance branch's old code, never on this checkout). The
    no-merge scenario on purpose: a store holding v1 merge products
    outside the bank is the from_dict loud-error boundary (v1 merges
    cannot load without their sibling records), pinned separately in
    tests/unit/core/test_models.py."""
    class SecondAppendCrashStore(GuardStore):
        # Crashes once the first bank row is on disk: the ledger holds a
        # prefix and the log already carries final_member_ids.
        appends = 0

        def append_bank(self, skill_dict):
            self.appends += 1
            if self.appends == 2:
                raise Crash("crash on the second bank append")
            return super().append_bank(skill_dict)

    store = SecondAppendCrashStore(tmp_path / "run")
    _seed_two(store)
    with pytest.raises(Crash):
        _run(store, _handles(_no_merge_scripts()))
    assert len(store.bank()) == 1
    _downgrade_run_to_v1(store.run_dir)
    bank_prefix = store.bank()

    resumed = GuardStore(tmp_path / "run")
    idle = _idle()
    _run(resumed, idle)

    assert not any(h.client.calls for h in idle.values())
    bank = resumed.bank()
    assert len(bank) == 2
    assert bank[0] == bank_prefix[0]
    completed = bank[1]
    assert completed["schema_version"] == 1
    assert "source_subtasks" not in completed
    # The completed row is the stored record in its own on-disk form.
    assert completed == read_json(
        resumed.path_for("skills", completed["skill_id"]))


def test_new_accepted_skill_before_bank_append_is_stale(tmp_path):
    """Reviewer repro: rounds all done, bank not yet appended, and a resumed
    stage 3/4 accepts a NEW skill -- it must not be appended uncurated."""
    store = _crash_before_bank(tmp_path)
    late = Skill(skill_id=_sid(9), content="accepted late",
                 subtask_id=_subtask(9), source_subtasks=[_subtask(9)],
                 status="accepted")
    store.save(late, "skills")
    idle = _idle()

    with pytest.raises(ConfigError, match="skill bank is stale") as excinfo:
        _run(store, idle)
    message = str(excinfo.value)
    assert late.skill_id in message
    assert "start a new run directory" in message
    assert "remove" not in message
    assert store.bank() == []
    assert not any(h.client.calls for h in idle.values())


def test_new_accepted_skill_mid_curation_is_stale(tmp_path):
    scripts = _scripts()
    scripts["curator"].insert(4, Crash("after round one"))
    store = GuardStore(tmp_path / "run")
    _seed(store)
    with pytest.raises(Crash):
        _run(store, _handles(scripts))
    store.save(Skill(skill_id=_sid(9), content="accepted late",
                     subtask_id=_subtask(9),
                     source_subtasks=[_subtask(9)], status="accepted"),
               "skills")
    with pytest.raises(ConfigError, match=_sid(9)):
        _run(GuardStore(tmp_path / "run"), _idle())


def _corrupt(tmp_path, mutate):
    scripts = _scripts()
    scripts["curator"].insert(4, Crash("after round one"))
    store = Store(tmp_path / "run")
    _seed(store)
    with pytest.raises(Crash):
        _run(store, _handles(scripts))
    mutate(store)
    idle = _idle()
    with pytest.raises(ConfigError) as excinfo:
        _run(Store(tmp_path / "run"), idle)
    assert not any(h.client.calls for h in idle.values())
    return str(excinfo.value)


def test_merged_skill_accepted_outside_k5_is_refused(tmp_path):
    # bank/merge/0001 was reverted (discarded); something flips it back.
    def mutate(store):
        skill = store.load("skills", M1, Skill)
        skill.status = "accepted"
        skill.discard_reason = None
        store.save(skill, "skills")
    message = _corrupt(tmp_path, mutate)
    assert M1 in message and "corrupted" in message


def test_merged_skill_with_bumped_version_is_refused(tmp_path):
    def mutate(store):
        skill = store.load("skills", M0, Skill)
        skill.version = 2
        store.save(skill, "skills")
    message = _corrupt(tmp_path, mutate)
    assert M0 in message and "corrupted" in message


def test_in_flight_merge_accepted_outside_k5_is_refused(tmp_path):
    """The cross-stage repro from K5's side: an in-flight (pending) merged
    skill accepted by someone else before its regression exams ran."""
    scripts = _scripts()
    scripts["answerer"].insert(0, Crash("first regression answer"))
    store = Store(tmp_path / "run")
    _seed(store)
    with pytest.raises(Crash):
        _run(store, _handles(scripts))
    skill = store.load("skills", M0, Skill)
    assert skill.status == "pending"
    skill.status = "accepted"
    store.save(skill, "skills")
    idle = _idle()
    with pytest.raises(ConfigError, match="corrupted run") as excinfo:
        _run(Store(tmp_path / "run"), idle)
    assert M0 in str(excinfo.value)
    assert not any(h.client.calls for h in idle.values())


# ---------------------------------------------------------------------------
# No-merge round: G9 runs (>= 2 members), is journaled, and replays on
# resume with zero extra model calls
# ---------------------------------------------------------------------------

def _no_merge_scripts():
    return {
        "curator": [
            _similarity([]),                                     # G7
            _grouping({_sid(0): "g1", _sid(1): "g2"}),          # G9
        ],
        "answerer": [],
        "judge": [],
    }


def _seed_two(store):
    for index in range(2):
        store.save(Skill(skill_id=_sid(index),
                         content=f"skill {index} advice",
                         subtask_id=_subtask(index),
                         source_subtasks=[_subtask(index)],
                         status="accepted"),
                   "skills")


@pytest.fixture(scope="module")
def no_merge_reference(tmp_path_factory):
    store = GuardStore(tmp_path_factory.mktemp("ref2") / "run")
    _seed_two(store)
    handles = _handles(_no_merge_scripts())
    _run(store, handles)
    assert _remaining(handles) == {"curator": [], "answerer": [], "judge": []}
    return _snapshot(store)


def test_no_merge_reference_groups_the_bank(no_merge_reference):
    assert [row["skill_id"] for row in no_merge_reference["bank"]] == [
        _sid(0), _sid(1)]
    assert [row["group"] for row in no_merge_reference["bank"]] == ["g1", "g2"]
    assert no_merge_reference["log"]["rounds_executed"] == 1
    assert no_merge_reference["log"]["rounds"][0]["grouping"] == {
        _sid(0): "g1", _sid(1): "g2"}


def test_crash_mid_group_apply_resumes_with_zero_model_calls(
        tmp_path, no_merge_reference):
    # Crash after the G9 output was journaled but before its effects were
    # fully applied: the resume replays the journaled grouping, so an idle
    # handle (empty script) suffices -- zero extra model calls.
    store = GuardStore(tmp_path / "run",
                       crash=("before", _is_skill(_sid(0), group="g1")))
    _seed_two(store)
    with pytest.raises(Crash):
        _run(store, _handles(_no_merge_scripts()))
    assert store.bank() == []

    resumed = GuardStore(tmp_path / "run")
    _run(resumed, _idle())
    assert _snapshot(resumed) == no_merge_reference


def test_crash_during_g9_call_resumes_and_redoes_only_g9(
        tmp_path, no_merge_reference):
    # Crash inside the G9 model call itself: the output was never
    # journaled, so the resume re-asks G9 once (the interrupted unit) and
    # nothing else.
    scripts = _no_merge_scripts()
    scripts["curator"].insert(1, Crash("crash during G9"))
    store = GuardStore(tmp_path / "run")
    _seed_two(store)
    handles = _handles(scripts)
    with pytest.raises(Crash):
        _run(store, handles)
    assert store.bank() == []

    resumed = GuardStore(tmp_path / "run")
    resume_handles = _handles(_remaining(handles))
    _run(resumed, resume_handles)
    assert _remaining(resume_handles) == {
        "curator": [], "answerer": [], "judge": []}
    assert _snapshot(resumed) == no_merge_reference
