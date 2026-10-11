"""K4 crash-injection resume tests: resume == uninterrupted run, everywhere.

Each scenario is first run uninterrupted to record the reference: the final
stored skill, every stored attempt, every stage-4 ledger and the prompt each
handle received. Then the same scenario is crashed at EVERY boundary -- each
model call (the scripted response is replaced by a ``Crash``) and each disk
write (before and after ``store.save`` / the ledger append) -- and resumed
with the rest of the script. The resumed run must leave exactly the
reference state on disk, and the only model calls it may repeat are the ones
whose result was still in flight (not yet persisted) at the crash:

* a crashed call itself;
* a K3 answer whose unjudged attempt was not yet saved, or a K3 verdict
  whose re-save had not finished (K3 stores the answer first, then the same
  attempt id with its verdict, see exam_once);
* a G6 rewrite whose result was not yet saved (skill save or the
  failed-try ledger line).
"""

import pytest

import expedistill.composites.acceptance as acceptance_mod
from expedistill.composites.acceptance import accept_skill
from expedistill.core.ids import (
    make_attempt_id,
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Attempt, Exam, Skill
from expedistill.infra.reliability import ModelOutputError
from expedistill.infra.store import Store, read_jsonl
from expedistill.llm import json_script, make_fake_handle

WEAK_PROFILE = "A small model; known to skip intermediate steps."

QUESTION = "What should you do when the service rejects connections?"
REFERENCE = "Restart the service after updating its configuration."
LEAK_QUESTION = 'The service prints "svc-token-9" and stops. What next?'
LEAK_REFERENCE = 'Rotate the credential "svc-token-9" and restart the service.'

LEAKY_V1 = 'Use "svc-token-9" to authenticate before restarting.'
LEAKY_V2 = 'Re-run with "svc-token-9" after restarting the service.'
CLEAN_V1 = "Restart the service after editing its configuration."
CLEAN_V2 = "Rotate the printed credential, then restart the service."
CLEAN_V3 = ("First rotate the printed credential, then restart the service "
            "and check that it stays up.")

ROLES = ("answerer", "judge", "rewriter")


class Crash(Exception):
    """Injected process death: not a ReliabilityError, so nothing retries."""


class MOE:
    """Scripted ModelOutputError (a fresh exception per raise)."""

    def __init__(self, message):
        self.message = message


def incorrect(reason):
    return json_script({"verdict": "incorrect", "error_description": reason})


def correct():
    return json_script({"verdict": "correct", "error_description": ""})


def rewrite(text, category):
    return json_script({"skill_text": text, "failure_category": category,
                        "rewrite_reason": f"rewrote for {category}"})


# Every scenario: (skill text, question, reference, retry_limit, scripts).
SCENARIOS = {
    # leak-void r0 -> rewrite v2; r1 fails, rewrite budget exhausted;
    # r2 fails on the unchanged v2 -> rewrite v3; r3 passes.
    "void_exhaust_accept": (LEAKY_V1, LEAK_QUESTION, LEAK_REFERENCE, 3, {
        "answerer": ["bare answer", "r1 answer", "r2 answer", "r3 answer"],
        "judge": [incorrect("bare skips the rotation"),
                  incorrect("r1 misses the rotation"),
                  incorrect("r2 restarts too early"), correct()],
        "rewriter": [rewrite(CLEAN_V2, "misread"), MOE("bad json one"),
                     MOE("bad json two"), rewrite(CLEAN_V3, "missing_step")],
    }),
    # Same, but the last round fails too.
    "void_exhaust_retry_exhausted": (
        LEAKY_V1, LEAK_QUESTION, LEAK_REFERENCE, 3, {
            "answerer": ["bare answer", "r1 answer", "r2 answer",
                         "r3 answer"],
            "judge": [incorrect("bare skips the rotation"),
                      incorrect("r1 misses the rotation"),
                      incorrect("r2 restarts too early"),
                      incorrect("r3 still restarts too early")],
            "rewriter": [rewrite(CLEAN_V2, "misread"), MOE("bad json one"),
                         MOE("bad json two"),
                         rewrite(CLEAN_V3, "missing_step")],
        }),
    # Voided rounds only: r0 void + exhausted rewrite, r1 void + leaky
    # rewrite, r2 (last) void -> leak_audit discard.
    "void_exhaust_leak_discard": (
        LEAKY_V1, LEAK_QUESTION, LEAK_REFERENCE, 2, {
            "answerer": ["bare answer"],
            "judge": [incorrect("bare skips the rotation")],
            "rewriter": [MOE("bad json one"), MOE("bad json two"),
                         rewrite(LEAKY_V2, "misread")],
        }),
    # Plain loop: r0 fails -> rewrite, r1 passes.
    "plain_accept": (CLEAN_V1, QUESTION, REFERENCE, 1, {
        "answerer": ["bare answer", "r0 answer", "r1 answer"],
        "judge": [incorrect("bare skips the restart"),
                  incorrect("r0 skips the restart"), correct()],
        "rewriter": [rewrite(CLEAN_V2, "misread")],
    }),
    "weak_already_knows": (CLEAN_V1, QUESTION, REFERENCE, 1, {
        "answerer": ["bare answer"],
        "judge": [correct()],
        "rewriter": [],
    }),
}


def make_pair(content, question, reference):
    subtask_id = make_subtask_id(
        make_trajectory_id(make_task_id("tb", "t1"), "strong", 0), 1)
    skill = Skill(skill_id=make_skill_id(subtask_id), content=content,
                  subtask_id=subtask_id)
    exam = Exam(exam_id=make_exam_id(subtask_id), question_text=question,
                reference_answer=reference, subtask_id=subtask_id)
    return skill, exam


def scripted(role, items, log):
    items = list(items)

    def respond(system, user):
        log.append(role)
        if not items:
            raise AssertionError(f"{role} script exhausted")
        item = items.pop(0)
        if isinstance(item, MOE):
            raise ModelOutputError(item.message)
        if isinstance(item, BaseException):
            raise item
        return item

    return respond


def run_once(store, scenario, scripts):
    """Run K4 with fresh handles; return (result or None on Crash, handles)."""
    content, question, reference, retry_limit, _ = scenario
    skill, exam = make_pair(content, question, reference)
    log = []
    handles = {role: make_fake_handle(role, scripted(role, scripts[role], log))
               for role in ROLES}
    try:
        result = accept_skill(
            store=store, skill=skill, exam=exam, weak_profile=WEAK_PROFILE,
            answerer_handle=handles["answerer"],
            judge_handle=handles["judge"],
            rewriter_handle=handles["rewriter"], retry_limit=retry_limit,
            leak_threshold=0.0, run_dir=store.run_dir)
    except Crash:
        result = None
    return result, handles, log


def prompts(handles):
    return {role: [call["user"] for call in handles[role].client.calls]
            for role in ROLES}


def snapshot(store):
    skill_ids = store.ids("skills")
    assert len(skill_ids) == 1
    stage_dir = store.run_dir / "stage4_acceptance"
    return {
        "skill": store.load("skills", skill_ids[0], Skill).to_dict(),
        "attempts": [store.load("attempts", a, Attempt).to_dict()
                     for a in store.ids("attempts")],
        "ledgers": {path.name: read_jsonl(path)
                    for path in sorted(stage_dir.glob("*.jsonl"))},
        "unit_failures": read_jsonl(store.run_dir / "failed_units.jsonl"),
    }


def reference_run(tmp_path, name):
    scenario = SCENARIOS[name]
    store = Store(tmp_path / "reference")
    result, handles, log = run_once(store, scenario, scenario[4])
    assert result is not None
    state = snapshot(store)
    assert result.to_dict() == state["skill"]
    return state, prompts(handles), log


def resume_and_compare(store, scenario, crash_prompts, repeats, reference,
                       reference_prompts):
    """Resume with the rest of each script and compare with the reference.

    ``repeats[role]`` is how many of that role's crash-run calls were still
    in flight and must be asked again.
    """
    full = scenario[4]
    rest = {}
    for role in ROLES:
        made = len(crash_prompts[role])
        # The crash run asked exactly a prefix of the reference prompts.
        assert crash_prompts[role] == reference_prompts[role][:made], role
        rest[role] = full[role][made - repeats.get(role, 0):]
    result, handles, _ = run_once(store, scenario, rest)
    assert result is not None
    resumed = prompts(handles)
    for role in ROLES:
        made = len(crash_prompts[role])
        kept = made - repeats.get(role, 0)
        # Nothing persisted is asked again; the remainder is asked exactly
        # as in the uninterrupted run.
        assert resumed[role] == reference_prompts[role][kept:], role
    state = snapshot(store)
    assert state == reference
    assert result.to_dict() == state["skill"]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_reference_runs_are_sane(tmp_path, name):
    state, _, _ = reference_run(tmp_path, name)
    skill = state["skill"]
    expected = {
        "void_exhaust_accept": ("accepted", None, 3, CLEAN_V3),
        "void_exhaust_retry_exhausted": (
            "discarded", "retry_exhausted", 3, CLEAN_V3),
        "void_exhaust_leak_discard": ("discarded", "leak_audit", 2, LEAKY_V2),
        "plain_accept": ("accepted", None, 2, CLEAN_V2),
        "weak_already_knows": (
            "discarded", "weak_already_knows", 1, CLEAN_V1),
    }[name]
    assert (skill["status"], skill["discard_reason"], skill["version"],
            skill["content"]) == expected
    assert len(skill["rewrite_history"]) == skill["version"] - 1


def _model_call_points():
    points = []
    for name, scenario in sorted(SCENARIOS.items()):
        total = sum(len(items) for items in scenario[4].values())
        points.extend((name, index) for index in range(total))
    return points


@pytest.mark.parametrize("name,crash_at", _model_call_points())
def test_crash_at_every_model_call_resumes_identically(tmp_path, name,
                                                       crash_at):
    scenario = SCENARIOS[name]
    reference, reference_prompts, log = reference_run(tmp_path, name)
    role = log[crash_at]
    index = log[:crash_at].count(role)
    scripts = {r: list(items) for r, items in scenario[4].items()}
    scripts[role] = scripts[role][:index] + [Crash(f"crash at {crash_at}")]

    store = Store(tmp_path / "crashed")
    result, handles, _ = run_once(store, scenario, scripts)
    assert result is None
    crash_prompts = prompts(handles)

    # K3 stores the answer before judging, so only the crashed call itself
    # is asked again (a judge crash never re-asks the answerer).
    repeats = {role: 1}
    resume_and_compare(store, scenario, crash_prompts, repeats, reference,
                       reference_prompts)


class _IOCrasher:
    """Counts disk writes of the K4 flow; raises Crash at one of them."""

    def __init__(self, store, target, when):
        self.store = store
        self.target = target
        self.when = when
        self.count = 0
        self.in_flight = {}

    def _event(self, describe, write):
        index = self.count
        self.count += 1
        if index == self.target and self.when == "before":
            self.in_flight = describe()
            raise Crash(f"crash before write {index}")
        out = write()
        if index == self.target and self.when == "after":
            raise Crash(f"crash after write {index}")
        return out

    def save(self, original):
        def wrapped(obj, kind):
            def describe():
                if kind == "attempts":
                    # K3 writes each attempt twice: the answer (unjudged),
                    # then the same id with its verdict.
                    if obj.judge_verdict is None:
                        return {"answerer": 1}
                    return {"judge": 1}
                if (kind == "skills" and obj.status == "pending"
                        and self.store.exists("skills", obj.skill_id)):
                    return {"rewriter": 1}   # the save after a rewrite
                return {}
            return self._event(describe, lambda: original(obj, kind))
        return wrapped

    def append(self, original):
        def wrapped(path, record):
            def describe():
                if "rewrite" in str(record.get("schema", "")):
                    return {"rewriter": 1}   # a failed rewrite try
                return {}
            return self._event(describe, lambda: original(path, record))
        return wrapped


def _count_writes(tmp_path, monkeypatch, name):
    scenario = SCENARIOS[name]
    store = Store(tmp_path / "count")
    crasher = _IOCrasher(store, target=-1, when="before")
    store.save = crasher.save(Store.save.__get__(store))
    with monkeypatch.context() as patch:
        patch.setattr(acceptance_mod, "append_jsonl",
                      crasher.append(acceptance_mod.append_jsonl))
        result, _, _ = run_once(store, scenario, scenario[4])
    assert result is not None
    return crasher.count


# Per scenario: initial skill save + 2 writes per K3 attempt (answer, then
# verdict) + one per leak line, failed rewrite try and stored rewrite + the
# terminal save.
_WRITE_COUNTS = {"void_exhaust_accept": 15,
                 "void_exhaust_retry_exhausted": 15,
                 "void_exhaust_leak_discard": 10,
                 "plain_accept": 9,
                 "weak_already_knows": 4}


def test_write_counts_are_current(tmp_path, monkeypatch):
    # Guards the sweep below: if the flow's writes change, the
    # parametrization must cover the new ones.
    counts = {name: _count_writes(tmp_path / name, monkeypatch, name)
              for name in SCENARIOS}
    assert counts == _WRITE_COUNTS


def _write_points():
    return [(name, index, when)
            for name, count in sorted(_WRITE_COUNTS.items())
            for index in range(count) for when in ("before", "after")]


@pytest.mark.parametrize("name,write_at,when", _write_points())
def test_crash_at_every_write_resumes_identically(tmp_path, monkeypatch,
                                                  name, write_at, when):
    scenario = SCENARIOS[name]
    reference, reference_prompts, _ = reference_run(tmp_path, name)

    store = Store(tmp_path / "crashed")
    crasher = _IOCrasher(store, target=write_at, when=when)
    original_save = store.save
    store.save = crasher.save(original_save)
    with monkeypatch.context() as patch:
        patch.setattr(acceptance_mod, "append_jsonl",
                      crasher.append(acceptance_mod.append_jsonl))
        result, handles, _ = run_once(store, scenario, scenario[4])
    store.save = original_save
    assert result is None   # the crash fired
    crash_prompts = prompts(handles)

    resume_and_compare(store, scenario, crash_prompts, crasher.in_flight,
                       reference, reference_prompts)


# ---------------------------------------------------------------------------
# The two concrete failures from the review, spelled out
# ---------------------------------------------------------------------------

def test_passing_attempt_on_rewritten_version_accepts_that_version(tmp_path,
                                                                   monkeypatch):
    """Crash between "with-skill attempt on v2 passes (stored)" and the
    status save: resume must accept v2, never the never-examined v1."""
    name = "plain_accept"
    scenario = SCENARIOS[name]
    store = Store(tmp_path / "crashed")
    original_save = store.save

    def save(obj, kind):
        if kind == "skills" and obj.status == "accepted":
            raise Crash("crash before the accepted status is saved")
        return original_save(obj, kind)

    store.save = save
    result, _, _ = run_once(store, scenario, scenario[4])
    assert result is None
    store.save = original_save
    stored = store.load("skills", store.ids("skills")[0], Skill)
    # The rewrite was persisted before v2 was examined.
    assert (stored.version, stored.content) == (2, CLEAN_V2)

    result, handles, _ = run_once(
        store, scenario, {role: [] for role in ROLES})
    assert (result.status, result.version, result.content) == (
        "accepted", 2, CLEAN_V2)
    assert all(not handles[r].client.calls for r in ROLES)


def test_passing_attempt_of_another_version_does_not_accept(tmp_path):
    """A passing attempt whose skill_version is not the stored version
    proves nothing about the stored content."""
    skill, exam = make_pair(CLEAN_V1, QUESTION, REFERENCE)
    store = Store(tmp_path / "run")
    store.save(skill, "skills")
    store.save(Attempt(
        attempt_id=make_attempt_id(exam.exam_id, 0), exam_id=exam.exam_id,
        skill_id=None, skill_version=None, purpose="bare",
        weak_answer="bare", judge_verdict=False, judge_reason="skips"),
        "attempts")
    store.save(Attempt(
        attempt_id=make_attempt_id(exam.exam_id, 1), exam_id=exam.exam_id,
        skill_id=skill.skill_id, skill_version=2, purpose="acceptance",
        weak_answer="lost", judge_verdict=True, judge_reason="",
        retry_round=0), "attempts")
    answerer = make_fake_handle("weak", ["v1 answer"])
    judge = make_fake_handle("judge", [incorrect("still skips")])
    rewriter = make_fake_handle("strong", [])

    result = accept_skill(
        store=store, skill=skill, exam=exam, weak_profile=WEAK_PROFILE,
        answerer_handle=answerer, judge_handle=judge,
        rewriter_handle=rewriter, retry_limit=0, leak_threshold=0.0,
        run_dir=store.run_dir)

    # v1 was examined for real and failed.
    assert (result.status, result.discard_reason) == (
        "discarded", "retry_exhausted")
    assert len(answerer.client.calls) == 1


def test_crash_between_unjudged_save_and_judge_reuses_the_answer(tmp_path):
    """The with-skill answer is stored unjudged, then the judge call dies:
    every K4 lookup must treat that attempt as not done (no accept, no
    discard, no round consumed), and resume must judge the stored answer
    without asking the weak model again."""
    name = "plain_accept"
    scenario = SCENARIOS[name]
    reference, reference_prompts, _ = reference_run(tmp_path, name)
    scripts = {r: list(items) for r, items in scenario[4].items()}
    # judge call 1 is round 0's verdict (call 0 judged the bare answer).
    scripts["judge"] = scripts["judge"][:1] + [Crash("judge died")]
    store = Store(tmp_path / "crashed")
    result, handles, _ = run_once(store, scenario, scripts)
    assert result is None
    stored = [store.load("attempts", a, Attempt) for a in store.ids("attempts")]
    assert [(a.purpose, a.judge_verdict) for a in stored] == [
        ("bare", False), ("acceptance", None)]
    skill = store.load("skills", store.ids("skills")[0], Skill)
    assert (skill.status, skill.version) == ("pending", 1)

    resume_and_compare(store, scenario, prompts(handles), {"judge": 1},
                       reference, reference_prompts)


def test_unjudged_attempts_never_decide_anything(tmp_path):
    """Unjudged attempts left on disk are not a bare verdict, not a passing
    or failing round: K4 completes them through K3 and decides on the
    verdicts only."""
    skill, exam = make_pair(CLEAN_V1, QUESTION, REFERENCE)
    store = Store(tmp_path / "run")
    store.save(skill, "skills")
    store.save(Attempt(
        attempt_id=make_attempt_id(exam.exam_id, 0), exam_id=exam.exam_id,
        skill_id=None, skill_version=None, purpose="bare",
        weak_answer="stored bare answer", judge_verdict=None), "attempts")
    answerer = make_fake_handle("weak", ["r0 answer"])
    judge = make_fake_handle("judge", [incorrect("bare skips the restart"),
                                       correct()])
    rewriter = make_fake_handle("strong", [])

    result = accept_skill(
        store=store, skill=skill, exam=exam, weak_profile=WEAK_PROFILE,
        answerer_handle=answerer, judge_handle=judge,
        rewriter_handle=rewriter, retry_limit=0, leak_threshold=0.0,
        run_dir=store.run_dir)

    assert result.status == "accepted"
    # the stored bare answer was judged, not re-asked
    assert "stored bare answer" in judge.client.calls[0]["user"]
    assert len(answerer.client.calls) == 1
    stored = [store.load("attempts", a, Attempt) for a in store.ids("attempts")]
    assert [(a.purpose, a.judge_verdict) for a in stored] == [
        ("bare", False), ("acceptance", True)]
