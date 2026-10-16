"""K6 crash-injection resume tests: resume == uninterrupted run, everywhere.

The methodology of test_acceptance_resume.py, applied to the live-run
phase functions: a flow driver (the same calls stage4_live_run.py will
make, in the same order) first runs uninterrupted to record the
reference -- every stored skill, the three ledgers, the frozen audit
lines, failed_units -- then the same scenario is crashed at EVERY
boundary (each model call, and each disk write before and after) and
resumed with the rest of the script. The resumed run must leave exactly
the reference state on disk, and the only model calls it may repeat are
the ones whose result was still in flight (not yet persisted) at the
crash: a G10 plan lost before its ledger append, a G11 rewrite lost
before its save, and a failed repair try lost before its failure line.

The driver stands in for the stage's K1 dispatch the same way: freezing
the audit line, then seeding the round store with the scenario's
trajectories. A crash between the freeze and the seeding leaves the
round store incomplete, so the resumed driver re-enters the dispatch
step, re-derives the audit row and compares it with the frozen line byte
for byte (the A10 first-write-freeze contract) instead of rewriting it.
"""

import json
from pathlib import Path

import pytest

from expedistill.composites import (
    live_attribution as lat,
    live_measurement as lm,
    live_verdicts as lv,
)
from expedistill.composites.live_attribution import (
    apply_revisions,
    attribute_group,
)
from expedistill.composites.live_measurement import (
    audit_row,
    load_baseline,
    survivors_and_block,
)
from expedistill.composites.live_verdicts import (
    finalize_group,
    judge_round,
    no_op_guard,
)
from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill, Step, Trajectory
from expedistill.infra.reliability import ModelOutputError
from expedistill.infra.store import Store, read_jsonl
from expedistill.llm import json_script, make_fake_handle

BENCHMARK = "tb"
TASK_ID = make_task_id(BENCHMARK, "t1")
N_ATTEMPTS = 3
RULE = "strict_rate_gain"
WEAK_PROFILE = "A small model; known to skip intermediate steps."
TASK_INSTRUCTION = "Fix the stuck service on host <host>."
ROLES = ("author", "rewriter")

MARKED_A = "\n".join([
    "[When to use] The service rejects connections and needs a restart.",
    "[Steps]",
    "1. Check the service status with the status command.",
    "2. Restart the service with the restart command.",
    "[Verify] The status command reports the service as running.",
    "[Do not use when] The status command already reports running.",
])
MARKED_A_REVISED = "\n".join([
    "[When to use] The service rejects connections and a restart is due.",
    "[Steps]",
    "1. Run the status command to see the service state.",
    "2. Restart the service with its restart command.",
    "3. Re-run the status command to confirm.",
    "[Verify] The second status command reports the service as running.",
    "[Do not use when] The status command already reports running.",
])
MARKED_B_REVISED = "\n".join([
    "[When to use] A config change needs a service reload first.",
    "[Steps]",
    "1. Edit the configuration file.",
    "2. Reload the service.",
    "[Verify] The reload command reports the new configuration.",
    "[Do not use when] The configuration is unchanged.",
])


class Crash(Exception):
    """Injected process death: not a ReliabilityError, so nothing retries."""


class MOE:
    """Scripted ModelOutputError (a fresh exception per raise)."""

    def __init__(self, message):
        self.message = message


def plan(actions, diagnosis="the injected skills did not change anything"):
    return json_script({
        "diagnosis": diagnosis,
        "skill_actions": [
            {"skill_id": skill_id, "action": action, "problem": problem}
            for skill_id, action, problem in actions
        ],
    })


def revision(text, category="missing_step",
             reason="spelled out the verification step"):
    return json_script({"skill_text": text, "failure_category": category,
                        "revision_reason": reason})


def make_group_skill(segment, content):
    subtask_id = make_subtask_id(make_trajectory_id(TASK_ID, "strong", 0),
                                segment)
    return Skill(skill_id=make_skill_id(subtask_id), content=content,
                 subtask_id=subtask_id, source_subtasks=[subtask_id])


A_ID = make_group_skill(0, MARKED_A).skill_id
B_ID = make_group_skill(1, MARKED_A).skill_id
C_ID = make_group_skill(2, MARKED_A).skill_id
GROUP = [make_group_skill(0, MARKED_A), make_group_skill(1, MARKED_A),
         make_group_skill(2, MARKED_A)]

# The two sweep scenarios. Both revise two skills and delete the third,
# so the write matrix covers every ledger and every kind of skill save;
# they differ only in round 1's outcome (the accept path vs the
# live_no_improvement path).
SCENARIOS = {
    "revise_round1_pass": {
        "baseline": (False, False, True),
        "round0": (False, False, False),
        "round1": (True, True, False),
        "scripts": {
            "author": [
                MOE("LIVE_RUN_ATTRIBUTION: bad json"),
                plan([(A_ID, "revise", "trigger too vague"),
                      (B_ID, "revise", "the verify step is missing"),
                      (C_ID, "delete", "harmful for this group")]),
            ],
            "rewriter": [
                MOE("LIVE_SKILL_REVISION: bad json"),
                revision(MARKED_A_REVISED),
                revision(MARKED_B_REVISED),
            ],
        },
    },
    "revise_round1_miss": {
        "baseline": (False, False, True),
        "round0": (False, False, False),
        "round1": (False, False, False),
        "scripts": {
            "author": [
                plan([(A_ID, "revise", "trigger too vague"),
                      (B_ID, "revise", "the verify step is missing"),
                      (C_ID, "delete", "harmful for this group")]),
            ],
            "rewriter": [
                revision(MARKED_A_REVISED),
                revision(MARKED_B_REVISED),
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def make_traj(attempt, correctness):
    return Trajectory(
        trajectory_id=make_trajectory_id(TASK_ID, "weak", attempt),
        task_id=TASK_ID, benchmark=BENCHMARK, model_role="weak",
        model_name="weak-model", attempt_index=attempt,
        steps=[Step(action=f"do x {attempt}",
                    observation=f"obs x {attempt}")],
        correctness=correctness,
    )


def unit_ids():
    return [make_trajectory_id(TASK_ID, "weak", attempt)
            for attempt in range(N_ATTEMPTS)]


def round_dir(store, round_):
    return store.run_dir / "stage4_acceptance" / "live_run" / f"round{round_:02d}"


def round_store(store, round_):
    return Store(round_dir(store, round_))


def seed_baseline(store, statuses):
    for attempt, correctness in enumerate(statuses):
        store.save(make_traj(attempt, correctness), "trajectories")


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


# ---------------------------------------------------------------------------
# The flow driver: what stage4_live_run.py will do with one group
# ---------------------------------------------------------------------------

def _round_complete(store, round_):
    if not round_dir(store, round_).exists():
        return False
    stored = set(round_store(store, round_).ids("trajectories"))
    return all(unit in stored for unit in unit_ids())


def _dispatch_round(store, round_, statuses):
    """The stage's per-round dispatch: freeze the audit line (or compare
    a re-derivation against it byte for byte when re-dispatching), then
    let K1 run -- which the fixture stands in for by seeding the round
    store with the scenario's trajectories."""
    path = round_dir(store, round_) / lm.INJECTION_AUDIT_NAME
    row = audit_row(store, TASK_ID, round_)
    if path.exists():
        wanted = json.dumps(row, ensure_ascii=False, sort_keys=True)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            if json.loads(line).get("task_id") == TASK_ID:
                if line != wanted:
                    raise Crash(f"audit drift at round {round_}")
                break
    else:
        # Written through live_verdicts' name on purpose: the
        # crash tests wrap append_jsonl on the verdicts and
        # attribution modules, and the audit freeze must be
        # counted with them.
        lv.append_jsonl(path, row)
    rs = round_store(store, round_)
    for attempt, correctness in enumerate(statuses):
        if not rs.exists("trajectories",
                         make_trajectory_id(TASK_ID, "weak", attempt)):
            rs.save(make_traj(attempt, correctness), "trajectories")


def _flow(store, handles, scenario):
    """Drive one group through the live phases; raises Crash when a
    crash injection fires."""
    finalize_group(store, TASK_ID)               # the finalization sweep
    if not lm._pending_group(store, TASK_ID):
        return                                   # already terminal
    _, counts = load_baseline(store, TASK_ID, N_ATTEMPTS)
    if (counts["success"] == counts["valid"]
            and RULE in lv._ALL_SUCCESS_SAFE_RULES):
        judge_round(store=store, task_id=TASK_ID, round_=0, rule_name=RULE,
                    baseline_counts=counts, n_attempts=N_ATTEMPTS,
                    skip_ids=[], terminal=lv.WEAK_ALREADY_KNOWS)
        finalize_group(store, TASK_ID)
        return
    if not _round_complete(store, 0):
        _dispatch_round(store, 0, scenario["round0"])
    verdict = judge_round(store=store, task_id=TASK_ID, round_=0,
                          rule_name=RULE, baseline_counts=counts,
                          n_attempts=N_ATTEMPTS, skip_ids=[])
    if verdict.get("verdict") is not None or verdict["better"]:
        finalize_group(store, TASK_ID)
        return
    attribution = attribute_group(
        store=store, task_id=TASK_ID, round_=0, n_attempts=N_ATTEMPTS,
        task_instruction=TASK_INSTRUCTION, weak_profile=WEAK_PROFILE,
        author_handle=handles["author"], view_rules=None,
        run_dir=store.run_dir)
    if attribution is None:
        return                                    # budget exhausted: terminal
    apply_revisions(
        store=store, task_id=TASK_ID, round_=0,
        rewriter_handle=handles["rewriter"], weak_profile=WEAK_PROFILE,
        view_rules=None, run_dir=store.run_dir)
    if no_op_guard(store, TASK_ID, 0):
        return                                    # byte-identical: no round 1
    survivors, _ = survivors_and_block(store, TASK_ID)
    if not survivors:
        return
    if not _round_complete(store, 1):
        _dispatch_round(store, 1, scenario["round1"])
    judge_round(store=store, task_id=TASK_ID, round_=1, rule_name=RULE,
                baseline_counts=counts, n_attempts=N_ATTEMPTS, skip_ids=[])
    finalize_group(store, TASK_ID)


def run_flow(store, scenario, scripts):
    """Run the flow with fresh handles; return (handles, call log,
    completed)."""
    log = []
    handles = {role: make_fake_handle(role, scripted(role, scripts[role], log))
               for role in ROLES}
    try:
        _flow(store, handles, scenario)
    except Crash:
        return handles, log, False
    return handles, log, True


def fresh_run(tmp_path, scenario):
    store = Store(tmp_path / "run")
    seed_baseline(store, scenario["baseline"])
    for skill in GROUP:
        store.save(skill, "skills")
    return store


def prompts(handles):
    return {role: [call["user"] for call in handles[role].client.calls]
            for role in ROLES}


def call_counts(handles):
    return {role: len(handles[role].client.calls) for role in ROLES}


def snapshot(store):
    live = store.run_dir / "stage4_acceptance" / "live_run"
    return {
        "skills": {skill_id: store.load("skills", skill_id, Skill).to_dict()
                   for skill_id in store.ids("skills")},
        "verdicts": read_jsonl(live / lm.VERDICTS_LEDGER),
        "attributions": read_jsonl(live / lm.ATTRIBUTIONS_LEDGER),
        "attribution_failures": read_jsonl(
            live / lm.ATTRIBUTION_FAILURES_LEDGER),
        "revision_failures": read_jsonl(live / lm.REVISION_FAILURES_LEDGER),
        "audit_round0": read_jsonl(
            live / "round00" / lm.INJECTION_AUDIT_NAME),
        "audit_round1": read_jsonl(
            live / "round01" / lm.INJECTION_AUDIT_NAME),
        "unit_failures": read_jsonl(store.run_dir / "failed_units.jsonl"),
    }


def reference_run(tmp_path, name):
    scenario = SCENARIOS[name]
    store = fresh_run(tmp_path, scenario)
    handles, log, completed = run_flow(store, scenario, scenario["scripts"])
    assert completed
    return snapshot(store), prompts(handles), log


def resume_and_compare(store, scenario, crash_prompts, repeats, reference,
                       reference_prompts):
    """Resume with the rest of each script and compare with the reference.

    ``repeats[role]`` is how many of the crash run's calls of that role
    were still in flight and must be asked again."""
    full = scenario["scripts"]
    rest = {}
    for role in ROLES:
        made = len(crash_prompts[role])
        assert crash_prompts[role] == reference_prompts[role][:made], role
        rest[role] = list(full[role][made - repeats.get(role, 0):])
    handles, _, completed = run_flow(store, scenario, rest)
    assert completed
    resumed = prompts(handles)
    for role in ROLES:
        made = len(crash_prompts[role])
        kept = made - repeats.get(role, 0)
        assert resumed[role] == reference_prompts[role][kept:], role
    assert snapshot(store) == reference


# ---------------------------------------------------------------------------
# The reference runs are sane
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_reference_runs_are_sane(tmp_path, name):
    reference, reference_prompts, _ = reference_run(tmp_path, name)
    skills = reference["skills"]
    assert set(skills) == {A_ID, B_ID, C_ID}
    if name == "revise_round1_pass":
        assert (skills[A_ID]["status"], skills[A_ID]["version"]) == (
            "accepted", 2)
        assert (skills[B_ID]["status"], skills[B_ID]["version"]) == (
            "accepted", 2)
        assert skills[C_ID]["status"] == "discarded"
        assert skills[C_ID]["discard_reason"] == lv.LIVE_REVISION_DELETED
        assert [row["better"] for row in reference["verdicts"]] == [
            False, True]
    else:
        for skill_id in (A_ID, B_ID):
            assert (skills[skill_id]["status"],
                    skills[skill_id]["discard_reason"]) == (
                "discarded", lv.LIVE_NO_IMPROVEMENT)
        assert skills[C_ID]["discard_reason"] == lv.LIVE_REVISION_DELETED
        assert [row["better"] for row in reference["verdicts"]] == [
            False, False]
    # One revision try failed (the attribution MOE only exists in the
    # pass scenario; both rewriter budgets started clean for B).
    assert len(reference["attributions"]) == 1
    assert len(reference["unit_failures"]) == 0
    assert reference["audit_round0"] and reference["audit_round1"]


# ---------------------------------------------------------------------------
# Crash at every model call
# ---------------------------------------------------------------------------

def _model_call_points():
    points = []
    for name, scenario in sorted(SCENARIOS.items()):
        total = sum(len(items) for items in scenario["scripts"].values())
        points.extend((name, index) for index in range(total))
    return points


@pytest.mark.parametrize("name,crash_at", _model_call_points())
def test_crash_at_every_model_call_resumes_identically(tmp_path, name,
                                                       crash_at):
    scenario = SCENARIOS[name]
    reference, reference_prompts, log = reference_run(tmp_path / "ref", name)
    assert crash_at < len(log)
    role = log[crash_at]
    index = log[:crash_at].count(role)

    scripts = {r: list(items) for r, items in scenario["scripts"].items()}
    scripts[role] = scripts[role][:index] + [Crash(f"crash at {crash_at}")]
    store = fresh_run(tmp_path / "crashed", scenario)
    handles, _, completed = run_flow(store, scenario, scripts)
    assert not completed

    # The crashed call's result was never persisted, so it is asked
    # again; everything earlier was persisted and is not.
    resume_and_compare(store, scenario, prompts(handles), {role: 1},
                       reference, reference_prompts)


# ---------------------------------------------------------------------------
# Crash at every disk write
# ---------------------------------------------------------------------------

class _IOCrasher:
    """Counts the flow's writes (skill saves + ledger appends); raises
    Crash at one of them. ``in_flight`` says which model call's result
    the crash trapped (and must therefore be asked again on resume)."""

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
                # A pending skill whose version is one above the stored
                # one is a revision about to land: losing it loses the
                # G11 call that produced it. Every other save (a discard
                # or an accept) is re-derivable with no model call.
                if (kind == "skills" and obj.status == "pending"
                        and self.store.exists("skills", obj.skill_id)):
                    stored = self.store.load("skills", obj.skill_id, Skill)
                    if obj.version == stored.version + 1:
                        return {"rewriter": 1}
                return {}
            return self._event(describe, lambda: original(obj, kind))
        return wrapped

    def append(self, original):
        def wrapped(path, record):
            def describe():
                name = Path(path).name
                if name in (lm.ATTRIBUTIONS_LEDGER,
                            lm.ATTRIBUTION_FAILURES_LEDGER):
                    # An attribution plan (or its failed try) that never
                    # landed is asked again.
                    return {"author": 1}
                if name == lm.REVISION_FAILURES_LEDGER:
                    return {"rewriter": 1}
                # Verdict rows and audit lines are pure derivations.
                return {}
            return self._event(describe, lambda: original(path, record))
        return wrapped


def _count_writes(tmp_path, monkeypatch, name):
    scenario = SCENARIOS[name]
    store = fresh_run(tmp_path, scenario)
    crasher = _IOCrasher(store, target=-1, when="before")
    store.save = crasher.save(store.save)
    with monkeypatch.context() as patch:
        wrapped = crasher.append(lv.append_jsonl)
        patch.setattr(lv, "append_jsonl", wrapped)
        patch.setattr(lat, "append_jsonl", wrapped)
        _, _, completed = run_flow(store, scenario, scenario["scripts"])
    assert completed
    return crasher.count


# Per scenario: audit r0 + verdict r0 + attribution rows + per-skill
# revision/delete saves + audit r1 + verdict r1 + the final commits.
_WRITE_COUNTS = {"revise_round1_pass": 12, "revise_round1_miss": 10}


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
    reference, reference_prompts, _ = reference_run(tmp_path / "ref", name)

    store = fresh_run(tmp_path / "crashed", scenario)
    crasher = _IOCrasher(store, target=write_at, when=when)
    original_save = store.save
    store.save = crasher.save(original_save)
    with monkeypatch.context() as patch:
        wrapped = crasher.append(lv.append_jsonl)
        patch.setattr(lv, "append_jsonl", wrapped)
        patch.setattr(lat, "append_jsonl", wrapped)
        handles, _, completed = run_flow(store, scenario,
                                         scenario["scripts"])
    store.save = original_save
    assert not completed     # the crash fired

    resume_and_compare(store, scenario, prompts(handles), crasher.in_flight,
                       reference, reference_prompts)


# ---------------------------------------------------------------------------
# The concrete cases the decision names, spelled out
# ---------------------------------------------------------------------------

def test_attribution_crash_before_persist_reasks(tmp_path):
    """Crash at the G10 call itself: no attribution row, so the plan is
    asked again -- the standing unpersisted-call exemption."""
    name = "revise_round1_miss"
    scenario = SCENARIOS[name]
    reference, reference_prompts, _ = reference_run(tmp_path / "ref", name)
    scripts = {r: list(items) for r, items in scenario["scripts"].items()}
    scripts["author"] = [Crash("author died mid-call")]
    store = fresh_run(tmp_path / "crashed", scenario)
    handles, _, completed = run_flow(store, scenario, scripts)
    assert not completed
    assert len(handles["author"].client.calls) == 1

    resume_and_compare(store, scenario, prompts(handles), {"author": 1},
                       reference, reference_prompts)


def test_attribution_crash_after_persist_never_reasks(tmp_path, monkeypatch):
    """Crash right after the attribution line lands: first judgment
    sticks, the resumed run reads the row and never asks G10 again."""
    name = "revise_round1_miss"
    scenario = SCENARIOS[name]
    reference, _, _ = reference_run(tmp_path / "ref", name)
    store = fresh_run(tmp_path / "crashed", scenario)
    state = {"done": False}
    original_append = lat.append_jsonl

    def append(path, record):
        out = original_append(path, record)
        if (Path(path).name == lm.ATTRIBUTIONS_LEDGER
                and not state["done"]):
            state["done"] = True
            raise Crash("crash after the attribution line")
        return out

    monkeypatch.setattr(lat, "append_jsonl", append)
    handles, _, completed = run_flow(store, scenario, scenario["scripts"])
    monkeypatch.undo()
    assert not completed
    assert len(handles["author"].client.calls) == 1

    # Resume: G10 is never re-asked; the rewriter does the rest.
    rest = {"author": [], "rewriter": list(scenario["scripts"]["rewriter"])}
    handles, _, completed = run_flow(store, scenario, rest)
    assert completed
    assert len(handles["author"].client.calls) == 0
    assert snapshot(store) == reference


def test_crash_mid_revision_keeps_the_landed_sibling(tmp_path, monkeypatch):
    """Crash after skill A's revision saved but before B's: resume must
    skip A (the version bump is the completion proof) and revise only
    B, without re-asking A's rewrite."""
    name = "revise_round1_miss"
    scenario = SCENARIOS[name]
    reference, reference_prompts, _ = reference_run(tmp_path / "ref", name)
    store = fresh_run(tmp_path / "crashed", scenario)
    original_save = store.save
    state = {"crashed": False}

    def save(obj, kind):
        if (kind == "skills" and obj.skill_id == A_ID
                and obj.status == "pending" and obj.version == 2
                and not state["crashed"]):
            original_save(obj, kind)
            state["crashed"] = True
            raise Crash("crash right after A's revision landed")
        return original_save(obj, kind)

    store.save = save
    handles, _, completed = run_flow(store, scenario, scenario["scripts"])
    store.save = original_save
    assert not completed
    # A landed at v2 and is still pending; B and C are untouched.
    stored_a = store.load("skills", A_ID, Skill)
    assert (stored_a.status, stored_a.version) == ("pending", 2)
    assert store.load("skills", B_ID, Skill).version == 1
    assert len(handles["rewriter"].client.calls) == 1   # only A's was asked

    resume_and_compare(store, scenario, prompts(handles), {},
                       reference, reference_prompts)


def test_crash_between_verdict_and_commit_sweep_completes(tmp_path):
    """Round 0 passed: the verdict row landed, the crash ate the accept
    commits. The resumed flow's finalization sweep commits them with no
    model call at all."""
    scenario = {
        "baseline": (False, False, True),
        "round0": (True, True, False),
        "round1": (),
        "scripts": {"author": [], "rewriter": []},
    }
    store = fresh_run(tmp_path, scenario)
    original_save = store.save
    state = {"crashed": False}

    def save(obj, kind):
        if kind == "skills" and obj.status == "accepted" and not state[
                "crashed"]:
            state["crashed"] = True
            raise Crash("crash before the accepted status is saved")
        return original_save(obj, kind)

    store.save = save
    handles, _, completed = run_flow(store, scenario, scenario["scripts"])
    store.save = original_save
    assert not completed
    assert call_counts(handles) == {"author": 0, "rewriter": 0}
    assert all(store.load("skills", s.skill_id, Skill).status == "pending"
               for s in GROUP)

    # Resume with nothing scripted: the sweep alone finishes the commits.
    handles, _, completed = run_flow(
        store, scenario, {"author": [], "rewriter": []})
    assert completed
    assert call_counts(handles) == {"author": 0, "rewriter": 0}
    assert all(store.load("skills", s.skill_id, Skill).status == "accepted"
               for s in GROUP)
    verdicts = read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                          / lm.VERDICTS_LEDGER)
    assert len(verdicts) == 1 and verdicts[0]["better"] is True


def test_audit_first_write_survives_crash_resume_only_reads(tmp_path,
                                                            monkeypatch):
    """Crash right after the round-0 audit line lands: the resumed
    dispatch re-derives the row, finds the frozen line byte-equal and
    never rewrites it."""
    name = "revise_round1_miss"
    scenario = SCENARIOS[name]
    reference, reference_prompts, _ = reference_run(tmp_path / "ref", name)
    store = fresh_run(tmp_path / "crashed", scenario)
    audit_path = round_dir(store, 0) / lm.INJECTION_AUDIT_NAME
    state = {"done": False}
    original_append = lv.append_jsonl

    def append(path, record):
        out = original_append(path, record)
        if Path(path) == audit_path and not state["done"]:
            state["done"] = True
            raise Crash("crash right after the audit freeze")
        return out

    monkeypatch.setattr(lv, "append_jsonl", append)
    handles, _, completed = run_flow(store, scenario, scenario["scripts"])
    monkeypatch.undo()
    assert not completed
    frozen = audit_path.read_text(encoding="utf-8")

    # Resume: the audit line is untouched -- the re-derived row equals
    # the frozen line and the dispatch step must not write it again.
    audit_writes = []
    guarded_append = lv.append_jsonl

    def no_audit_rewrites(path, record):
        if Path(path) == audit_path:
            audit_writes.append(record)
        return guarded_append(path, record)

    monkeypatch.setattr(lv, "append_jsonl", no_audit_rewrites)
    resume_and_compare(store, scenario, prompts(handles), {},
                       reference, reference_prompts)
    monkeypatch.undo()
    assert audit_writes == []
    assert audit_path.read_text(encoding="utf-8") == frozen


def test_attribution_budget_persists_across_crash(tmp_path, monkeypatch):
    """One failed G10 try is ledgered, the run dies: the resumed call
    gets the REMAINING budget (one more ask), and the failure ledger
    keeps exactly the one row."""
    name = "revise_round1_pass"     # its author script is [MOE, plan]
    scenario = SCENARIOS[name]
    reference, reference_prompts, _ = reference_run(tmp_path / "ref", name)
    store = fresh_run(tmp_path / "crashed", scenario)
    state = {"done": False}
    original_append = lat.append_jsonl

    def append(path, record):
        out = original_append(path, record)
        if (Path(path).name == lm.ATTRIBUTION_FAILURES_LEDGER
                and not state["done"]):
            state["done"] = True
            raise Crash("crash after the failed try's ledger line")
        return out

    monkeypatch.setattr(lat, "append_jsonl", append)
    handles, _, completed = run_flow(store, scenario, scenario["scripts"])
    monkeypatch.undo()
    assert not completed
    assert len(handles["author"].client.calls) == 1   # the failed try

    resume_and_compare(store, scenario, prompts(handles), {},
                       reference, reference_prompts)
    failures = read_jsonl(store.run_dir / "stage4_acceptance" / "live_run"
                          / lm.ATTRIBUTION_FAILURES_LEDGER)
    assert [(row["try_index"], row["exhausted"]) for row in failures] == [
        (0, False)]
