"""K5 wave-parallel curation tests: equivalence, waves, dispatch, crashes.

The 2026-10-12 redesign in one sentence: parallel COMPUTE, ordered COMMIT,
bit-identical output to serial (see curation.py's module docstring). These
tests pin its four load-bearing claims:

* the wave partitioner's invariants (member-disjoint waves, overlapping
  pairs keep serial order -- the first-wins-preserving greedy);
* workers=1 and workers=8 produce identical stores, banks and journals on
  the same scripted inputs (content-routed fakes: every response is a pure
  function of the request, never of call order);
* dispatch-time serial pre-allocation (burned serials, duplicate units
  deduplicated at dispatch);
* the rebuilt crash protocol: several in-flight merges of ONE wave are
  identified by the dispatch map, and a resume at the same worker count
  ends exactly where the uninterrupted run ends.

The serial behaviour itself (workers=1 vs the pre-parallel code) is pinned
by the existing suites: test_curation.py, test_curation_resume.py,
test_curation_stage4_interleave.py and tests/unit/stages/test_stage5.py
run the serial path end to end.
"""

import re
import threading

import pytest

from expedistill.composites import curation
from expedistill.composites.curation import (
    LOG_DIR,
    LOG_NAME,
    _partition_waves,
    curate_bank,
)
from expedistill.core import ids
from expedistill.core.models import Attempt, Exam, Skill
from expedistill.infra.reliability import ConfigError, ModelOutputError
from expedistill.infra.store import Store, read_json, write_json
from expedistill.llm.call import ModelHandle
from expedistill.llm.fake import json_script


class Crash(Exception):
    """Simulated process death inside a worker or the main loop; nothing
    retries or catches it (same contract as test_curation_resume.Crash)."""


# ---------------------------------------------------------------------------
# Content-routed fake (parallel-safe: responses depend on content, not order)
# ---------------------------------------------------------------------------

_MERGE_HEADER_RE = re.compile(r"^### (\S+)$", re.M)
_QUESTION_RE = re.compile(r"question (\d+)")


class RoutedClient:
    """Thread-safe client whose responses are pure functions of the request.

    ``resolve(user)`` returns the response (an exception instance is raised
    instead). Calls are recorded (user text only) for counting assertions.
    Unlike llm.fake's scripted lists nothing here consumes state by call
    ORDER, so a wave's workers can interleave however the pool schedules
    them and a resumed call replays the same world.
    """

    def __init__(self, resolve):
        self.resolve = resolve
        self.calls = []
        self._lock = threading.Lock()

    def complete(self, model, system, user, *, temperature=0.0,
                 max_tokens=None, extra=None):
        with self._lock:
            self.calls.append(user)
        result = self.resolve(user)
        if isinstance(result, BaseException):
            raise result
        return result


def _routed_handle(role, resolve):
    return ModelHandle(name="fake-model", role=role,
                       client=RoutedClient(resolve))


class World:
    """Declarative, content-routed curation script for one scenario.

    ``similarity`` / ``grouping`` are keyed by the frozenset of member ids
    present in the G7/G9 listing; ``merges`` by (mode, frozenset of the
    ``### id`` headers in the MERGE prompt). The answerer keys on the exam
    question index and the merge marker (the first 6 chars of the merge
    text, embedded in the skills block and the weak answer); the judge
    grades (question, marker) incorrect iff it is in ``fail``. ``*_faults``
    are one-shot exceptions keyed the same way (crash injection).
    """

    def __init__(self, n_skills, contents=None):
        self.skill_contents = {
            index: (contents or {}).get(index, chr(97 + index % 26) * 1600)
            for index in range(n_skills)}
        self.known = {skill_id(index) for index in range(n_skills)} | {
            f"bank/merge/{serial:04d}" for serial in range(16)}
        self.merges = {}       # (mode, frozenset(ids)) -> merged text
        self.similarity = {}   # frozenset(member ids) -> G7 payload
        self.grouping = {}      # frozenset(member ids) -> {id: group}
        self.fail = set()      # (exam index, marker) judged incorrect
        self.merge_faults = {}   # (mode, frozenset) -> one-shot exception
        self.answer_faults = {}  # (exam index, marker) -> one-shot exception
        self.judge_faults = {}   # (exam index, marker) -> one-shot exception

    def markers(self):
        return {text[:6] for text in self.merges.values()}

    def _marker(self, user):
        return next(marker for marker in self.markers() if marker in user)

    def _members(self, user):
        return frozenset(id_ for id_ in self.known if id_ in user)

    def curator_resolve(self, user):
        if "MERGE MODE" in user:
            sources = frozenset(_MERGE_HEADER_RE.findall(user))
            mode = "conflict" if "MERGE MODE: conflict" in user else "dedup"
            fault = self.merge_faults.pop((mode, sources), None)
            if fault is not None:
                raise fault
            return self.merges[(mode, sources)]
        members = self._members(user)
        if "- similar_groups:" in user:
            return json_script(self.similarity[members])
        return json_script({"groups": self.grouping[members]})

    def answerer_resolve(self, user):
        question = int(_QUESTION_RE.search(user).group(1))
        marker = self._marker(user)
        fault = self.answer_faults.pop((question, marker), None)
        if fault is not None:
            raise fault
        return f"ans q{question} with {marker}"

    def judge_resolve(self, user):
        question = int(_QUESTION_RE.search(user).group(1))
        marker = self._marker(user)
        fault = self.judge_faults.pop((question, marker), None)
        if fault is not None:
            raise fault
        if (question, marker) in self.fail:
            return json_script({"verdict": "incorrect",
                                "error_description": "wrong"})
        return json_script({"verdict": "correct", "error_description": ""})


def subtask_id(index):
    return f"traj/tb:t{index}/strong/00/seg00"


def skill_id(index):
    return ids.make_skill_id(subtask_id(index))


def exam_id(index):
    return ids.make_exam_id(subtask_id(index))


def merge_id(serial):
    return ids.make_merged_skill_id(serial)


def seed(store, world):
    for index, content in world.skill_contents.items():
        store.save(Skill(skill_id=skill_id(index), content=content,
                         subtask_id=subtask_id(index), status="accepted"),
                  "skills")
        store.save(Exam(exam_id=exam_id(index),
                        question_text=f"question {index}",
                        reference_answer=f"reference {index}",
                        subtask_id=subtask_id(index)), "exams")


def run(store, world, workers=1, budget=600, rounds=3):
    handles = {
        "curator_handle": _routed_handle("curator", world.curator_resolve),
        "answerer_handle": _routed_handle("weak", world.answerer_resolve),
        "judge_handle": _routed_handle("judge", world.judge_resolve),
    }
    bank = curate_bank(store=store, weak_profile="weak",
                       bank_token_budget=budget, curation_rounds=rounds,
                       run_dir=store.run_dir, workers=workers, **handles)
    return bank, handles


def snapshot(store):
    return {
        "bank": store.bank(),
        "skills": {id_: store.load("skills", id_, Skill).to_dict()
                   for id_ in store.ids("skills")},
        "attempts": {id_: store.load("attempts", id_, Attempt).to_dict()
                     for id_ in store.ids("attempts")},
        "log": read_json(store.run_dir / LOG_DIR / LOG_NAME),
    }


def log_path(store):
    return store.run_dir / LOG_DIR / LOG_NAME


# ---------------------------------------------------------------------------
# Wave partitioner
# ---------------------------------------------------------------------------

def test_waves_empty_round_is_no_waves():
    assert _partition_waves([]) == []


def test_waves_disjoint_units_share_one_wave():
    units = [("dedup", ["a", "b"]), ("dedup", ["c", "d"]),
             ("conflict", ["e", "f"])]
    assert _partition_waves(units) == [[0, 1, 2]]


def test_waves_overlap_chain_staircases_in_serial_order():
    units = [("dedup", ["a", "b"]), ("dedup", ["b", "c"]),
             ("dedup", ["c", "d"])]
    assert _partition_waves(units) == [[0], [1], [2]]


def test_waves_mixed_overlap_keeps_disjoint_units_in_wave_zero():
    units = [("dedup", ["a", "b"]), ("dedup", ["b", "c"]),
             ("dedup", ["d", "e"])]
    assert _partition_waves(units) == [[0, 2], [1]]


def test_waves_duplicate_groups_overlap_like_any_other_pair():
    units = [("dedup", ["a", "b"]), ("dedup", ["b", "a"])]
    assert _partition_waves(units) == [[0], [1]]


def test_waves_preserve_first_wins_for_every_overlapping_pair():
    # The property the literal "earliest wave with no shared member" rule
    # violates: whenever units i < j share a member, i must commit first.
    units = [("dedup", ["a", "b"]), ("dedup", ["b", "c"]),
             ("dedup", ["c", "d"]), ("dedup", ["a", "x"]),
             ("dedup", ["x", "y"]), ("dedup", ["d", "e"]),
             ("dedup", ["y", "b"])]
    waves = _partition_waves(units)
    position = {unit: wave
                for wave, index in enumerate(waves) for unit in index}
    member_sets = [{str(item) for item in group} for _, group in units]
    for later in range(len(units)):
        for earlier in range(later):
            if member_sets[later] & member_sets[earlier]:
                assert position[later] > position[earlier], (
                    f"units {earlier} and {later} reordered")
    for wave in waves:  # and every wave stays member-disjoint
        for i in wave:
            for j in wave:
                if i < j:
                    assert not (member_sets[i] & member_sets[j])


def test_waves_are_a_pure_function_of_the_units():
    units = [("dedup", ["c", "d"]), ("dedup", ["a", "b"])]
    assert _partition_waves(units) == _partition_waves(units)


# ---------------------------------------------------------------------------
# Scenario worlds
# ---------------------------------------------------------------------------

def two_round_world():
    """The test_curation_resume scenario, routed: round 1 merges (s0, s1)
    kept and (s2, s3) reverted (fails s3's exam); round 2 merges the
    round-one product with s2 (a merge of a merge: exams 0, 1, 2)."""
    world = World(4)
    world.similarity[frozenset(skill_id(i) for i in range(4))] = {
        "similar_groups": [[skill_id(0), skill_id(1)],
                           [skill_id(2), skill_id(3)]],
        "conflicts": []}
    world.merges[("dedup", frozenset([skill_id(0), skill_id(1)]))] = (
        "mergeA" + "y" * 1194)
    world.merges[("dedup", frozenset([skill_id(2), skill_id(3)]))] = (
        "mergeB" + "z" * 1493)
    world.fail.add((3, "mergeB"))
    world.grouping[frozenset([merge_id(0), skill_id(2), skill_id(3)])] = {
        merge_id(0): "g1", skill_id(2): "g1", skill_id(3): "g2"}
    world.similarity[frozenset([merge_id(0), skill_id(2), skill_id(3)])] = {
        "similar_groups": [[merge_id(0), skill_id(2)]], "conflicts": []}
    world.merges[("dedup", frozenset([merge_id(0), skill_id(2)]))] = (
        "mergeC short merged skill")
    world.grouping[frozenset([merge_id(2), skill_id(3)])] = {
        merge_id(2): "g3", skill_id(3): "g2"}
    return world


def three_pair_world():
    """Three member-disjoint pairs -> ONE wave of three units (the
    multi-in-flight crash tests need >= 2 in flight in a single wave)."""
    world = World(6)
    members = frozenset(skill_id(i) for i in range(6))
    world.similarity[members] = {
        "similar_groups": [[skill_id(0), skill_id(1)],
                           [skill_id(2), skill_id(3)],
                           [skill_id(4), skill_id(5)]],
        "conflicts": []}
    for pair, letter in (((0, 1), "X"), ((2, 3), "Y"), ((4, 5), "Z")):
        world.merges[("dedup", frozenset(skill_id(i) for i in pair))] = (
            f"merge{letter} merged advice")
    world.grouping[frozenset(merge_id(n) for n in range(3))] = {
        merge_id(0): "gx", merge_id(1): "gy", merge_id(2): "gz"}
    return world


def overlap_world():
    """Unit 1 (=[s1, s4], which sorts before [s2, s3]) shares s1 with
    unit 0: wave 1, and by then s1 is merged away -> skipped with 'fewer
    than two current members' (the overlap scenario from the design
    reviews). Units 0 and 2 stay in wave 0."""
    world = World(5)
    world.similarity[frozenset(skill_id(i) for i in range(5))] = {
        "similar_groups": [[skill_id(0), skill_id(1)],
                           [skill_id(2), skill_id(3)],
                           [skill_id(1), skill_id(4)]],
        "conflicts": []}
    world.merges[("dedup", frozenset([skill_id(0), skill_id(1)]))] = (
        "mergeA merged advice")
    world.merges[("dedup", frozenset([skill_id(2), skill_id(3)]))] = (
        "mergeB merged advice")
    world.grouping[frozenset([merge_id(0), merge_id(2), skill_id(4)])] = {
        merge_id(0): "g1", merge_id(2): "g1", skill_id(4): "g2"}
    return world


def first_wins_world():
    """Units (s0,s1) -> reverted, (s1,s2) -> kept, (s2,s3) -> would merge.
    Serial keeps (s1,s2) and skips (s2,s3); the wave order must not let
    (s2,s3) commit first (the counterexample to the unconstrained greedy)."""
    world = World(4)
    world.similarity[frozenset(skill_id(i) for i in range(4))] = {
        "similar_groups": [[skill_id(0), skill_id(1)],
                           [skill_id(1), skill_id(2)],
                           [skill_id(2), skill_id(3)]],
        "conflicts": []}
    world.merges[("dedup", frozenset([skill_id(0), skill_id(1)]))] = (
        "mergeA merged advice")
    world.merges[("dedup", frozenset([skill_id(1), skill_id(2)]))] = (
        "mergeB merged advice")
    world.merges[("dedup", frozenset([skill_id(2), skill_id(3)]))] = (
        "mergeC merged advice")
    world.fail.add((0, "mergeA"))  # the first merge fails regression
    world.grouping[frozenset([skill_id(0), merge_id(1), skill_id(3)])] = {
        skill_id(0): "g1", merge_id(1): "g2", skill_id(3): "g1"}
    return world


def duplicate_world():
    """G7 emits the same pair twice: the later duplicate is deduplicated at
    dispatch (skipped, no serial) and never runs."""
    world = World(2)
    world.similarity[frozenset(skill_id(i) for i in range(2))] = {
        "similar_groups": [[skill_id(0), skill_id(1)],
                           [skill_id(1), skill_id(0)]],
        "conflicts": []}
    world.merges[("dedup", frozenset([skill_id(0), skill_id(1)]))] = (
        "mergeA merged advice")
    world.grouping[frozenset([merge_id(0)])] = {merge_id(0): "g1"}
    return world


# ---------------------------------------------------------------------------
# Equivalence: same scripted inputs, workers=1 vs workers=8
# ---------------------------------------------------------------------------

def _run_both(tmp_path, world, budget=600, rounds=3):
    results = {}
    for tag, workers in (("serial", 1), ("parallel", 8)):
        store = Store(tmp_path / tag / "run")
        seed(store, world)
        bank, _ = run(store, world, workers=workers, budget=budget,
                      rounds=rounds)
        results[tag] = (snapshot(store), bank)
    return results["serial"], results["parallel"]


@pytest.mark.parametrize("make_world,budget", [
    (two_round_world, 600),
    (three_pair_world, 10 ** 6),
    (overlap_world, 10 ** 6),
    (first_wins_world, 10 ** 6),
    (duplicate_world, 10 ** 6),
], ids=["two-round", "three-pair", "overlap", "first-wins", "duplicates"])
def test_parallel_output_is_bit_identical_to_serial(tmp_path, make_world,
                                                     budget):
    (serial_state, serial_bank), (parallel_state, parallel_bank) = _run_both(
        tmp_path, make_world(), budget=budget)
    assert serial_bank == parallel_bank
    assert serial_state == parallel_state


def test_two_round_scenario_shape_and_dispatch_map(tmp_path):
    store = Store(tmp_path / "run")
    world = two_round_world()
    seed(store, world)
    bank, _ = run(store, world, workers=8)

    assert [row["skill_id"] for row in bank] == [merge_id(2), skill_id(3)]
    log = read_json(log_path(store))
    assert log["merges"] == {"attempted": 3, "kept": 2, "reverted": 1,
                             "skipped": 0}
    assert [record["skill_id"] for record in log["merge_records"]] == [
        merge_id(0), merge_id(1), merge_id(2)]
    # Serials are pre-allocated for the whole round, in unit order, before
    # any wave runs; the dispatch map is journalled inside the round record.
    assert log["rounds"][0]["dispatch"] == [
        {"unit": 0, "mode": "dedup",
         "sources": [skill_id(0), skill_id(1)], "serial": 0},
        {"unit": 1, "mode": "dedup",
         "sources": [skill_id(2), skill_id(3)], "serial": 1}]
    assert log["rounds"][1]["dispatch"] == [
        {"unit": 0, "mode": "dedup",
         "sources": [merge_id(0), skill_id(2)], "serial": 2}]
    # The round-one units really ran as ONE wave (the parallel path is
    # exercised, not vacuously serial).
    units = curation._round_units(log["rounds"][0])
    assert _partition_waves(units) == [[0, 1]]


def test_overlap_unit_skips_exactly_like_serial(tmp_path):
    store = Store(tmp_path / "run")
    world = overlap_world()
    seed(store, world)
    bank, _ = run(store, world, workers=8)

    assert [row["skill_id"] for row in bank] == [
        merge_id(0), merge_id(2), skill_id(4)]
    log = read_json(log_path(store))
    skipped = log["merge_records"][1]
    assert skipped["unit"] == 1
    assert skipped["outcome"] == "skipped"
    assert skipped["reason"].startswith(
        "group has fewer than two current members")
    # Its dispatch serial is burned with it (recorded, never stored).
    assert skipped["serial"] == 1
    assert merge_id(1) not in store.ids("skills")


def test_first_wins_not_lost_to_wave_reordering(tmp_path):
    """The counterexample that constrains the greedy: unit 0 reverts, so in
    serial unit 1 keeps (s1, s2) and unit 2 skips. A wave rule that put
    unit 2 next to unit 0 would keep (s2, s3) instead -- a different bank."""
    store = Store(tmp_path / "run")
    world = first_wins_world()
    seed(store, world)
    bank, _ = run(store, world, workers=8, budget=10 ** 6)

    assert [row["skill_id"] for row in bank] == [
        merge_id(1), skill_id(0), skill_id(3)]
    assert store.load("skills", merge_id(1), Skill).merged_from == [
        skill_id(1), skill_id(2)]
    log = read_json(log_path(store))
    assert [record["outcome"] for record in log["merge_records"]] == [
        "reverted", "kept", "skipped"]


def test_duplicate_units_dedup_at_dispatch(tmp_path):
    store = Store(tmp_path / "run")
    world = duplicate_world()
    seed(store, world)
    bank, _ = run(store, world, workers=8)

    assert [row["skill_id"] for row in bank] == [merge_id(0)]
    log = read_json(log_path(store))
    assert log["merges"] == {"attempted": 2, "kept": 1, "reverted": 0,
                             "skipped": 1}
    duplicate = log["merge_records"][1]
    assert duplicate["unit"] == 1
    assert duplicate["outcome"] == "skipped"
    assert duplicate["serial"] is None
    assert duplicate["reason"] == "duplicate source set of unit 000"
    assert log["rounds"][0]["dispatch"][1]["serial"] is None


def test_g8_model_output_error_burns_its_serial_in_parallel(tmp_path):
    for workers in (1, 8):
        store = Store(tmp_path / f"w{workers}" / "run")
        world = World(4)
        world.similarity[frozenset(skill_id(i) for i in range(4))] = {
            "similar_groups": [[skill_id(0), skill_id(1)],
                               [skill_id(2), skill_id(3)]],
            "conflicts": []}
        world.merge_faults[("dedup",
                            frozenset([skill_id(0), skill_id(1)]))] = (
            ModelOutputError("merge text unusable"))
        world.merges[("dedup",
                      frozenset([skill_id(2), skill_id(3)]))] = (
            "mergeB merged advice")
        world.grouping[frozenset(
            [skill_id(0), skill_id(1), merge_id(1)])] = {
            skill_id(0): "a", skill_id(1): "a", merge_id(1): "b"}
        seed(store, world)
        bank, _ = run(store, world, workers=workers, budget=10 ** 6)

        # Serial 0 was allocated at dispatch and burned by the failed G8;
        # the surviving merge is bank/merge/0001 in BOTH worker counts.
        assert [row["skill_id"] for row in bank] == [
            merge_id(1), skill_id(0), skill_id(1)]
        log = read_json(log_path(store))
        assert log["merge_records"][0]["serial"] == 0
        assert log["merge_records"][0]["outcome"] == "skipped"
        assert "ModelOutputError" in log["merge_records"][0]["reason"]
        assert log["merge_records"][1]["skill_id"] == merge_id(1)


# ---------------------------------------------------------------------------
# Crash protocol: several in-flight merges of ONE wave, identified by the map
# ---------------------------------------------------------------------------

def _reference(tmp_path, world, workers, budget=600, rounds=3):
    store = Store(tmp_path / "reference" / f"w{workers}" / "run")
    seed(store, world)
    run(store, world, workers=workers, budget=budget, rounds=rounds)
    return snapshot(store)


@pytest.fixture
def crash_write_log(monkeypatch):
    """One-shot crash at a chosen ``curation._write_log`` write.

    ``install(trigger, when)`` picks the write (first match only) and
    whether the crash hits BEFORE the write (the write is lost -- a kill
    inside it) or AFTER it (the write is durable -- a kill between two
    journal writes).
    """
    real = curation._write_log
    state = {"armed": True}

    def install(trigger, when):
        def crashing(log, path):
            if state["armed"] and when == "before" and trigger(log):
                state["armed"] = False
                raise Crash("crash before the log write")
            result = real(log, path)
            if state["armed"] and when == "after" and trigger(log):
                state["armed"] = False
                raise Crash("crash right after the log write")
            return result
        monkeypatch.setattr(curation, "_write_log", crashing)

    return install


def test_crash_after_dispatch_write_before_any_wave(tmp_path, crash_write_log):
    world = two_round_world()
    reference = _reference(tmp_path, two_round_world(), 1)
    crash_write_log(lambda log: (
        bool(log["rounds"]) and log["rounds"][-1].get("dispatch") is not None
        and not log["merge_records"]), "after")

    store = Store(tmp_path / "run")
    seed(store, world)
    with pytest.raises(Crash):
        run(store, world, workers=8)
    # The plan is durable but nothing has run: no merge record, no merge.
    log = read_json(log_path(store))
    assert log["rounds"][0]["dispatch"][0]["serial"] == 0
    assert log["merge_records"] == []

    bank, handles = run(store, world, workers=8)
    assert snapshot(store) == reference
    # The round-1 G7 output was journaled before the crash: the resume never
    # re-asks it (its curator calls are round 1's two G8s and G9, then
    # round 2's G7, G8 and G9 -- a full run would need one more G7).
    curator_calls = handles["curator_handle"].client.calls
    assert len(curator_calls) == 6
    assert sum("- similar_groups:" in call for call in curator_calls) == 1


def test_crash_inside_one_units_g8_with_two_in_flight(tmp_path):
    world = two_round_world()
    reference = _reference(tmp_path, two_round_world(), 8)
    world.merge_faults[("dedup", frozenset([skill_id(0), skill_id(1)]))] = (
        Crash("crash inside unit 0's G8"))

    store = Store(tmp_path / "run")
    seed(store, world)
    with pytest.raises(Crash):
        run(store, world, workers=8)

    run(store, world, workers=8)
    assert snapshot(store) == reference


def test_crash_between_regression_exams_with_two_in_flight(tmp_path):
    world = two_round_world()
    reference = _reference(tmp_path, two_round_world(), 8)
    # Unit 0 passes exam 0, dies on exam 1's ANSWER call; unit 1 runs to
    # completion (in-flight units are never cancelled) and is committed by
    # the main thread before the exception surfaces.
    world.answer_faults[(1, "mergeA")] = Crash("crash between exams")

    store = Store(tmp_path / "run")
    seed(store, world)
    with pytest.raises(Crash):
        run(store, world, workers=8)

    bank, handles = run(store, world, workers=8)
    assert snapshot(store) == reference
    # Unit 0's exam 0 was already judged before the crash: its answer is
    # never re-asked (exactly one ANSWER call for question 0, pre-crash).
    answers = handles["answerer_handle"].client.calls
    assert sum("question 0" in call and "mergeA" in call
               for call in answers) == 0


def test_crash_after_outcomes_before_commit_reuses_everything(tmp_path,
                                                              monkeypatch):
    world = two_round_world()
    reference = _reference(tmp_path, two_round_world(), 8)
    real_run_units = curation.run_units
    state = {"armed": True}

    def crashing_run_units(worker, units, workers=1):
        outcomes = real_run_units(worker, units, workers)
        if state["armed"]:
            state["armed"] = False
            raise Crash("crash after the wave's outcomes")
        return outcomes

    monkeypatch.setattr(curation, "run_units", crashing_run_units)
    store = Store(tmp_path / "run")
    seed(store, world)
    with pytest.raises(Crash):
        run(store, world, workers=8)
    # Both merges of the wave are pending with no decision: the state the
    # v2 code called corrupted ("more than one undecided merged skill").
    assert store.load("skills", merge_id(0), Skill).status == "pending"
    assert store.load("skills", merge_id(1), Skill).status == "pending"
    assert read_json(log_path(store))["merge_records"] == []
    monkeypatch.undo()

    bank, handles = run(store, world, workers=8)
    assert snapshot(store) == reference
    # The whole round-1 wave was reused: the resume makes no G8, answer or
    # judge call for mergeA or mergeB -- only round 1's G9, then round 2's
    # G7, G8, three answers/judgments for mergeC, and round 2's G9.
    curator_calls = handles["curator_handle"].client.calls
    assert len(curator_calls) == 4
    assert sum("MERGE MODE" in call for call in curator_calls) == 1
    answers = handles["answerer_handle"].client.calls
    assert len(answers) == 3
    assert all("mergeC" in call for call in answers)
    assert len(handles["judge_handle"].client.calls) == 3


def test_crash_after_first_commit_of_a_three_unit_wave(tmp_path,
                                                       crash_write_log):
    world = three_pair_world()
    reference = _reference(tmp_path, three_pair_world(), 8, budget=10 ** 6)
    # Die BEFORE the write that commits unit 1's record (the second of the
    # round): unit 0 is fully committed, units 1 and 2 are computed but
    # undecided.
    crash_write_log(lambda log: (
        len([record for record in log["merge_records"]
             if record["round"] == 1]) == 2), "before")

    store = Store(tmp_path / "run")
    seed(store, world)
    with pytest.raises(Crash):
        run(store, world, workers=8, budget=10 ** 6)
    log = read_json(log_path(store))
    assert [record["unit"] for record in log["merge_records"]] == [0]
    assert store.load("skills", merge_id(0), Skill).status == "accepted"
    assert store.load("skills", merge_id(1), Skill).status == "pending"
    assert store.load("skills", merge_id(2), Skill).status == "pending"

    bank, handles = run(store, world, workers=8, budget=10 ** 6)
    assert snapshot(store) == reference
    # Units 1 and 2 re-ran on their stored merges and judged attempts: the
    # resume only pays for G9.
    assert len(handles["curator_handle"].client.calls) == 1
    assert not handles["answerer_handle"].client.calls
    assert not handles["judge_handle"].client.calls


def test_crash_after_a_records_write_finalises_from_the_log(tmp_path,
                                                            crash_write_log):
    """Record durable, effects missing: the crash lands right AFTER unit
    1's record write but before its apply, so its merged skill is pending
    WITH a decision -- the resume finalises it from the log instead of
    re-running it."""
    world = three_pair_world()
    reference = _reference(tmp_path, three_pair_world(), 8, budget=10 ** 6)
    crash_write_log(lambda log: (
        len([record for record in log["merge_records"]
             if record["round"] == 1]) == 2), "after")

    store = Store(tmp_path / "run")
    seed(store, world)
    with pytest.raises(Crash):
        run(store, world, workers=8, budget=10 ** 6)
    log = read_json(log_path(store))
    assert [record["unit"] for record in log["merge_records"]] == [0, 1]
    assert store.load("skills", merge_id(1), Skill).status == "pending"

    bank, handles = run(store, world, workers=8, budget=10 ** 6)
    assert snapshot(store) == reference
    # Units 1 (finalised from the log) and 2 (reused via the map) needed no
    # model call; only G9 remains.
    assert len(handles["curator_handle"].client.calls) == 1
    assert not handles["answerer_handle"].client.calls
    assert not handles["judge_handle"].client.calls


def test_foreign_pending_merge_is_rolled_back_by_the_map(tmp_path,
                                                          monkeypatch):
    """A pending merge whose serial no dispatch entry claims (a leftover
    from another run) is rolled back as an audit record while the wave's
    own in-flight merges are reused. Single-round scenario so the foreign
    serial shifts nothing else (a stored serial always advances the
    continuation -- ids are never reused)."""
    world = three_pair_world()
    reference = _reference(tmp_path, three_pair_world(), 8, budget=10 ** 6)
    real_run_units = curation.run_units
    state = {"armed": True}

    def crashing_run_units(worker, units, workers=1):
        outcomes = real_run_units(worker, units, workers)
        if state["armed"]:
            state["armed"] = False
            raise Crash("crash after the wave's outcomes")
        return outcomes

    monkeypatch.setattr(curation, "run_units", crashing_run_units)
    store = Store(tmp_path / "run")
    seed(store, world)
    with pytest.raises(Crash):
        run(store, world, workers=8, budget=10 ** 6)
    monkeypatch.undo()
    assert read_json(log_path(store))["merge_records"] == []
    store.save(Skill(skill_id=merge_id(9), content="foreign merge",
                     subtask_id=subtask_id(0), status="pending",
                     merged_from=[skill_id(0), skill_id(1)]), "skills")

    bank, _ = run(store, world, workers=8, budget=10 ** 6)
    foreign = store.load("skills", merge_id(9), Skill)
    assert foreign.status == "discarded"
    assert foreign.discard_reason == "interrupted_merge"
    # ... and the wave's own three in-flight merges were reused, not rolled
    # back: the run still ends in the reference bank and journal.
    assert bank == reference["bank"]
    assert read_json(log_path(store)) == reference["log"]


def test_crash_between_dispatch_write_and_duplicate_records(tmp_path,
                                                             crash_write_log):
    """The dispatch map write and the duplicate-unit skip records are two
    writes; a crash between them must not let the duplicate run as a normal
    unit on resume (the duplicate set is re-derived from the round)."""
    world = duplicate_world()
    reference = _reference(tmp_path, duplicate_world(), 8, budget=10 ** 6)
    crash_write_log(lambda log: (
        bool(log["rounds"]) and log["rounds"][-1].get("dispatch") is not None
        and not log["merge_records"]), "after")

    store = Store(tmp_path / "run")
    seed(store, world)
    with pytest.raises(Crash):
        run(store, world, workers=8, budget=10 ** 6)
    log = read_json(log_path(store))
    assert log["rounds"][0]["dispatch"][1]["serial"] is None
    assert log["merge_records"] == []

    run(store, world, workers=8, budget=10 ** 6)
    assert snapshot(store) == reference


# ---------------------------------------------------------------------------
# Stage plumbing: the run setting reaches K5's wave pool
# ---------------------------------------------------------------------------

def test_stage5_threads_workers_into_the_wave_pool(tmp_path):
    from expedistill.core.config import ExperimentConfig
    from expedistill.stages import stage5_curation
    from expedistill.stages.context import RunSettings, StageContext

    summaries = {}
    for tag, workers in (("serial", 1), ("parallel", 8)):
        run_dir = tmp_path / tag / "run"
        ctx = StageContext(
            run_dir=run_dir, store=Store(run_dir),
            config=ExperimentConfig(
                benchmark="tb", mode="full",
                models={"executor_strong": "strong-model",
                        "executor_weak": "weak-model"},
                weak_profile="weak",
                methods={"acceptance": {"name": "exam", "retry_limit": 1},
                         "curation": {"token_budget": 10 ** 6, "rounds": 3}}),
            usage=None,
            handles={
                "answerer": _routed_handle("weak",
                                           overlap_world().answerer_resolve),
                "judge": _routed_handle("judge",
                                        overlap_world().judge_resolve),
                "curator": _routed_handle("curator",
                                          overlap_world().curator_resolve),
            },
            workdir_root=run_dir / "work",
            run_settings=RunSettings(workers=workers))
        seed(ctx.store, overlap_world())
        summaries[tag] = stage5_curation.run(ctx)
    assert summaries["serial"] == summaries["parallel"]
    assert summaries["parallel"]["members"] == [
        merge_id(0), merge_id(2), skill_id(4)]


def test_journal_less_pending_merge_is_reused_by_merged_from(tmp_path):
    """v2 behaviour kept for runs whose journal was lost: a pending merge
    matching the first undecided unit's sources is reused, not re-merged.
    Consequence of dispatch-time allocation: the reusing unit's own serial
    burns (the reused merge keeps its stored id), so later merges sit
    higher than the lost run would have placed them."""
    def make_world():
        world = World(4)
        world.similarity[frozenset(skill_id(i) for i in range(4))] = {
            "similar_groups": [[skill_id(0), skill_id(1)],
                               [skill_id(2), skill_id(3)]],
            "conflicts": []}
        world.merges[("dedup", frozenset([skill_id(0), skill_id(1)]))] = (
            "mergeA" + "y" * 1194)
        world.merges[("dedup", frozenset([skill_id(2), skill_id(3)]))] = (
            "mergeB merged advice")
        world.grouping[frozenset([merge_id(0), merge_id(2)])] = {
            merge_id(0): "g1", merge_id(2): "g2"}
        return world

    for tag, workers in (("serial", 1), ("parallel", 8)):
        store = Store(tmp_path / tag / "run")
        world = make_world()
        seed(store, world)
        store.save(Skill(skill_id=merge_id(0), content="mergeA" + "y" * 1194,
                         subtask_id=subtask_id(0), status="pending",
                         merged_from=[skill_id(0), skill_id(1)]), "skills")

        bank, handles = run(store, world, workers=workers, budget=10 ** 6)

        # The pending merge was reused for unit 0 (no G8 for it) and unit 1
        # merged at its own dispatch serial 2 (serial 1 burned by the reuse).
        assert [row["skill_id"] for row in bank] == [merge_id(0), merge_id(2)]
        merge_calls = [call for call in handles["curator_handle"].client.calls
                       if "MERGE MODE" in call]
        assert len(merge_calls) == 1
        assert skill_id(2) in merge_calls[0]
        log = read_json(log_path(store))
        assert log["merge_records"][0] == {
            "round": 1, "unit": 0, "mode": "dedup", "serial": 1,
            "sources": [skill_id(0), skill_id(1)],
            "skill_id": merge_id(0), "outcome": "kept", "reason": ""}


# ---------------------------------------------------------------------------
# Journal compatibility
# ---------------------------------------------------------------------------

def test_v2_journal_is_refused_with_the_clear_error(tmp_path):
    store = Store(tmp_path / "run")
    seed(store, two_round_world())
    write_json(log_path(store), {
        "schema": "curation-log-v2",
        "input_skill_ids": sorted(skill_id(i) for i in range(4)),
        "rounds_executed": 0, "merge_records": [], "rounds": [],
        "final_member_ids": [], "bank_tokens": 0,
        "merges": {"attempted": 0, "kept": 0, "reverted": 0, "skipped": 0}})
    with pytest.raises(ConfigError) as excinfo:
        run(store, two_round_world())
    assert "curation-log-v3" in str(excinfo.value)
    assert "start a new run" in str(excinfo.value)
