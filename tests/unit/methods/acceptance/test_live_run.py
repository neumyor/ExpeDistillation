"""Driver-level tests for stage4_live_run (fake plugin + scripted handles).

The fixture builds the store the way the pipeline would have left it for
stage 4 (stage-1 weak bare trajectories, pending candidate groups), the
benchmark plugin answers every execution from a per-test ``behave`` callable
(no network, no model on the execution path), and the G10/G11 model side is
FakeClient scripts dispatching on prompt content. The hand-built states
(a round store plus a frozen audit row, a half-applied no-op discard, a
pre-accepted hole) are exactly the states a crashed call would have left
(same discipline as the K6 tests), so the resume regressions need no crash
injection.
"""

import importlib
import importlib.abc
import importlib.machinery
import json
import random
import sys
import types

import pytest

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.registry import _reset_registry, register_plugin
from expedistill.composites import (
    live_measurement as lm,
    live_verdicts as lv,
)
from expedistill.composites.skill_status import discard_skill
from expedistill.core.config import ExperimentConfig
from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill, Step, Trajectory
from expedistill.infra.reliability import InfrastructureError
from expedistill.infra.store import Store, append_jsonl, read_json, read_jsonl
from expedistill.infra.store import write_json
from expedistill.llm import json_script, make_fake_handle
from expedistill.prompts import PROMPTS, PromptSpec
# The driver moved to methods/ (P1a); the alias keeps the test's
# monkeypatch targets and call sites reading as before.
from expedistill.methods.acceptance import live_run as stage4_live_run
from expedistill.methods import resolve_params


def run_live(ctx):
    """The driver takes (ctx, params); the tests always resolve the
    config's acceptance slot."""
    return stage4_live_run.run(ctx, resolve_params("acceptance", ctx.config))
from dataclasses import replace
from expedistill.stages.context import RunSettings, StageContext

BENCHMARK = "livebench"
TASK1 = make_task_id(BENCHMARK, "t1")
TASK2 = make_task_id(BENCHMARK, "t2")
N_ATTEMPTS = 2
WEAK_PROFILE = "A small model; known to skip intermediate steps."

LIVE_ACCEPTANCE = {"name": "live_run", "n_attempts": N_ATTEMPTS,
                   "improvement": "strict_rate_gain"}

# Protocol-v2 contents: four bracket sections at line starts, so
# blocks.author.skill_markers reads the procedure set off them (a G11
# revision may append lines but must keep the headers).
MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "2. Restart the service.",
    "[Verify] The status command reports the service as running.",
    "[Do not use when] The status command already reports running.",
])


class LivePlugin(BenchmarkPlugin):
    """Two tasks; every execution answered by ``behave(task, skills_block)``
    returning the scorer verdict (or raising InfrastructureError); every
    call logged (the log is how the tests prove a
    group was or was not dispatched -- A10's regression)."""

    def __init__(self, behave):
        self.behave = behave
        self.calls = []

    @property
    def name(self):
        return BENCHMARK

    def load_tasks(self, tasks_spec):
        known = ("t1", "t2")
        ids = list(known) if tasks_spec is None else list(tasks_spec)
        unknown = [task_id for task_id in ids if task_id not in known]
        if unknown:
            from expedistill.infra.reliability import ConfigError
            raise ConfigError(f"unknown task ids: {unknown}")
        return [Task(task_id=task_id, instruction=f"do {task_id}")
                for task_id in ids]

    def run(self, task, handle, skills_block, workdir):
        self.calls.append({"task_id": task.task_id,
                           "token": task.meta.get("attempt_token"),
                           "block": skills_block})
        correctness = self.behave(task, skills_block)
        return RunOutcome(
            steps=[{"action": "run", "observation": "the state"}],
            correctness=correctness,
        )

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "LIVE_TASK"


def install(monkeypatch, plugin):
    """Serve the plugin's package through the real import machinery (the
    pattern of tests/unit/stages/test_pipeline.py)."""
    fullname = f"expedistill.benchmarks.{BENCHMARK}"

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            register_plugin(plugin)

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == fullname:
                return importlib.machinery.ModuleSpec(name, Loader())
            return None

    sys.modules.pop(fullname, None)
    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, Finder()])
    return plugin


@pytest.fixture(autouse=True)
def live_registry(monkeypatch):
    _reset_registry()
    # The task prompt the stage renders into the G10 input must be a real
    # registered prompt (real plugins register theirs at import).
    monkeypatch.setitem(PROMPTS, "LIVE_TASK", PromptSpec(
        name="LIVE_TASK",
        template="Solve the task: {instruction}\n{skills_block}\n{history}",
        description="driver-test task execution prompt"))
    yield
    sys.modules.pop(f"expedistill.benchmarks.{BENCHMARK}", None)
    _reset_registry()


# ---------------------------------------------------------------------------
# Store seeding: the state the pipeline would have handed the stage
# ---------------------------------------------------------------------------

def make_traj(attempt, correctness, task_id=TASK1, role="weak"):
    return Trajectory(
        trajectory_id=make_trajectory_id(task_id, role, attempt),
        task_id=task_id, benchmark=BENCHMARK, model_role=role,
        model_name="weak-model", attempt_index=attempt,
        steps=[Step(action=f"step {attempt}", observation="obs")],
        correctness=correctness,
    )


def seed_baseline(store, statuses, task_id=TASK1):
    for attempt, correctness in enumerate(statuses):
        store.save(make_traj(attempt, correctness, task_id), "trajectories")


def seed_group(store, contents, task_id=TASK1):
    """Pending candidate skills hanging on the task's strong trajectory
    (the ID chain carries the task; no Subtask record is ever read)."""
    trajectory_id = make_trajectory_id(task_id, "strong", 0)
    skills = []
    for segment, content in enumerate(contents):
        subtask_id = make_subtask_id(trajectory_id, segment)
        skill = Skill(skill_id=make_skill_id(subtask_id), content=content,
                      subtask_id=subtask_id,
                      source_subtasks=[subtask_id])
        store.save(skill, "skills")
        skills.append(skill)
    return skills


def make_ctx(tmp_path, *, author=None, rewriter=None, acceptance=None,
             allow_infra_shortfall=False, run_dir=None):
    run_dir = run_dir or tmp_path / "run"
    ctx = StageContext(
        run_dir=run_dir,
        store=Store(run_dir),
        config=ExperimentConfig(
            benchmark=BENCHMARK,
            mode="full",
            models={"executor_strong": "strong-model",
                    "executor_weak": "weak-model"},
            weak_profile=WEAK_PROFILE,
            n_attempts_weak=3,
            methods={"acceptance": dict(acceptance or LIVE_ACCEPTANCE)},
        ),
        usage=None,
        handles={"executor_weak": make_fake_handle("executor_weak", []),
                 "author": author or make_fake_handle("author", []),
                 "rewriter": rewriter or make_fake_handle("rewriter", [])},
        workdir_root=tmp_path / "work",
        run_settings=RunSettings(
            allow_infra_shortfall=allow_infra_shortfall),
    )
    return ctx


def build_env(tmp_path, monkeypatch, *, behave, author=None, rewriter=None,
              acceptance=None, allow_infra_shortfall=False,
              baseline=(False, False), contents=(MARKED,),
              task_id=TASK1):
    """One task's complete entry state: baseline, group, plugin, context."""
    plugin = install(monkeypatch, LivePlugin(behave))
    ctx = make_ctx(tmp_path, author=author, rewriter=rewriter,
                   acceptance=acceptance,
                   allow_infra_shortfall=allow_infra_shortfall)
    seed_baseline(ctx.store, baseline, task_id)
    skills = seed_group(ctx.store, contents, task_id)
    return ctx, ctx.store, plugin, skills


# ---------------------------------------------------------------------------
# Scripted G10 / G11 handles (content-dispatching, like test_stage4's)
# ---------------------------------------------------------------------------

def author_plan(action_for, diagnosis="the injected skill did not help"):
    """G10 handle: one action per listed skill, chosen by ``action_for``."""

    def respond(system, user):
        assert user.startswith("You are the attribution step"), user[:80]
        ids = [line.split("skill_id: ", 1)[1]
               for line in user.splitlines()
               if line.startswith("skill_id: ")]
        assert ids, "the G10 prompt listed no skills"
        actions = []
        for skill_id in ids:
            action = action_for(skill_id)
            actions.append({"skill_id": skill_id, "action": action,
                            "problem": "" if action == "keep"
                            else "the trigger is too vague to recognize"})
        return json_script({"diagnosis": diagnosis,
                            "skill_actions": actions})

    return make_fake_handle("author", respond)


def rewriter_append(marker=" Revised."):
    """G11 handle: rewrite the skill as its own text plus a marker line
    (markers preserved, bytes changed -- what makes round 1 a different
    injection)."""

    def respond(system, user):
        original = user.split("SKILL (current version):\n", 1)[1].split(
            "\n\nATTRIBUTION REPORT", 1)[0]
        return json_script({
            "failure_category": "missing_step",
            "skill_text": original + marker,
            "revision_reason": "spelled out the trigger condition",
        })

    return make_fake_handle("rewriter", respond)


# ---------------------------------------------------------------------------
# Readers the assertions share
# ---------------------------------------------------------------------------

def live_dir(store):
    return store.run_dir / "stage4_acceptance" / "live_run"


def verdicts(store):
    return read_jsonl(live_dir(store) / lm.VERDICTS_LEDGER)


def verdict_of(store, task_id, round_):
    for row in verdicts(store):
        if row["task_id"] == task_id and row["round"] == round_:
            return row
    return None


def unit(task_id, attempt):
    return make_trajectory_id(task_id, "weak", attempt)


def stored_skill(store, skill):
    return store.load("skills", skill.skill_id, Skill)


# ---------------------------------------------------------------------------
# Round 0 passes: accepted, zero attribution calls
# ---------------------------------------------------------------------------

def test_round0_pass_accepts_with_zero_attribution_calls(tmp_path, monkeypatch):
    ctx, store, plugin, skills = build_env(
        tmp_path, monkeypatch,
        behave=lambda task, block: True,
        author=author_plan(lambda skill_id: "revise"))

    summary = run_live(ctx)

    assert summary["accepted"] == 1
    assert summary["discarded"] == {}
    assert stored_skill(store, skills[0]).status == "accepted"
    assert len(plugin.calls) == N_ATTEMPTS      # the round-0 batch only
    # Zero model calls: a passing group never reaches G10/G11.
    assert ctx.handle("author").client.calls == []
    assert ctx.handle("rewriter").client.calls == []
    verdict = verdict_of(store, TASK1, 0)
    assert verdict["better"] is True
    assert verdict_of(store, TASK1, 1) is None
    # No round-1 artifacts at all: the group was terminal before the seam.
    assert not (live_dir(store) / "round01").exists()


# ---------------------------------------------------------------------------
# Both rounds fail: the group is discarded, live_no_improvement
# ---------------------------------------------------------------------------

def test_two_failed_rounds_discard_the_group(tmp_path, monkeypatch):
    ctx, store, plugin, skills = build_env(
        tmp_path, monkeypatch,
        behave=lambda task, block: False,
        author=author_plan(lambda skill_id: "revise"),
        rewriter=rewriter_append())

    summary = run_live(ctx)

    assert summary["accepted"] == 0
    assert summary["discarded"] == {"live_no_improvement": 1}
    skill = stored_skill(store, skills[0])
    assert (skill.status, skill.discard_reason) == (
        "discarded", "live_no_improvement")
    assert (skill.version, len(skill.rewrite_history)) == (2, 1)
    # Both rounds really executed (the round field tells the two
    # live_no_improvement meanings apart -- this one is the round-1 miss).
    assert len(plugin.calls) == 2 * N_ATTEMPTS
    assert verdict_of(store, TASK1, 0)["better"] is False
    assert verdict_of(store, TASK1, 1)["better"] is False


# ---------------------------------------------------------------------------
# Round 0 misses, the revision lands, round 1 passes: the full happy path
# ---------------------------------------------------------------------------

def test_revision_then_round1_pass_accepts(tmp_path, monkeypatch):
    def behave(task, block):
        return True if "Revised." in block else False

    ctx, store, plugin, skills = build_env(
        tmp_path, monkeypatch, behave=behave,
        author=author_plan(lambda skill_id: "revise"),
        rewriter=rewriter_append())

    summary = run_live(ctx)

    skill = stored_skill(store, skills[0])
    assert skill.status == "accepted"
    assert (skill.version, len(skill.rewrite_history)) == (2, 1)
    # Round 0 ran the original block and failed; round 1 ran the revised
    # block and passed.
    blocks = {call["block"] for call in plugin.calls}
    assert any("Revised." in block for block in blocks)
    assert any("Revised." not in block and block
               for block in blocks)
    assert len(plugin.calls) == 2 * N_ATTEMPTS
    assert verdict_of(store, TASK1, 1)["better"] is True

    # -- the summary's full shape, pinned once ---------------------------
    audit0 = read_jsonl(live_dir(store) / "round00"
                        / lm.INJECTION_AUDIT_NAME)[0]
    audit1 = read_jsonl(live_dir(store) / "round01"
                        / lm.INJECTION_AUDIT_NAME)[0]
    assert summary == {
        "accepted": 1,
        "discarded": {},
        "mode": "live_run",
        "improvement_rule": "strict_rate_gain",
        "infra_failed": [],
        "processed_this_call": 1,
        "group_details": {TASK1: {
            "round0": {"baseline": {"valid": 2, "success": 0},
                       "live": {"valid": 2, "success": 0},
                       "better": False, "verdict": None, "aux": None,
                       "injection_tokens": audit0["injection_tokens"],
                       "judged_with_shortfall": False},
            "round1": {"baseline": {"valid": 2, "success": 0},
                       "live": {"valid": 2, "success": 2},
                       "better": True, "verdict": None, "aux": None,
                       "injection_tokens": audit1["injection_tokens"],
                       "judged_with_shortfall": False},
            "outcome": "accepted",
            "discard_reasons": {},
        }},
        "infra_missing": {"round00": [], "round01": []},
        "infra_accepted": {"round00": [], "round01": []},
        "infra_shortfall_accepted": False,
    }
    assert read_json(ctx.run_dir / "stage4_acceptance"
                    / "stage4_summary.json") == summary


# ---------------------------------------------------------------------------
# Mixed terminal: G10 deletes one member, the revision passes round 1 --
# a finished group must not be reported as "pending"
# ---------------------------------------------------------------------------

def test_delete_plus_round1_pass_reports_mixed_outcome(
        tmp_path, monkeypatch):
    def behave(task, block):
        return True if "Revised." in block else False

    ctx, store, plugin, skills = build_env(
        tmp_path, monkeypatch, behave=behave,
        author=author_plan(lambda skill_id: "delete"
                           if skill_id == skills[0].skill_id
                           else "revise"),
        rewriter=rewriter_append(), contents=(MARKED, MARKED))

    summary = run_live(ctx)

    deleted = stored_skill(store, skills[0])
    survivor = stored_skill(store, skills[1])
    assert (deleted.status, deleted.discard_reason) == (
        "discarded", "live_revision_deleted")
    assert survivor.status == "accepted"
    # The group is fully terminal with both fates: "pending" (the old
    # report for this shape) would tell the operator work is still owed.
    detail = summary["group_details"][TASK1]
    assert detail["outcome"] == "mixed"
    assert detail["discard_reasons"] == {"live_revision_deleted": 1}


# ---------------------------------------------------------------------------
# All keep: the no-op guard discards without a second sample
# ---------------------------------------------------------------------------

def test_all_keep_fires_the_noop_guard_and_never_runs_round1(
        tmp_path, monkeypatch):
    ctx, store, plugin, skills = build_env(
        tmp_path, monkeypatch,
        behave=lambda task, block: False,
        author=author_plan(lambda skill_id: "keep"))

    summary = run_live(ctx)

    assert summary["discarded"] == {"live_no_improvement": 1}
    assert stored_skill(store, skills[0]).discard_reason == \
        "live_no_improvement"
    # A4: the guard exists to prevent re-sampling until a lucky pass -- the
    # group's executions stop at round 0.
    assert len(plugin.calls) == N_ATTEMPTS
    assert verdict_of(store, TASK1, 1) is None
    assert not (live_dir(store) / "round01").exists()


# ---------------------------------------------------------------------------
# Zero-execution terminals: weak_already_knows, overflow (A10 revision 3)
# ---------------------------------------------------------------------------

def test_zero_execution_terminals_are_never_dispatched_or_redispatched(
        tmp_path, monkeypatch):
    plugin = install(monkeypatch, LivePlugin(lambda task, block: True))
    ctx = make_ctx(tmp_path)
    # t1: the baseline is all-success -> weak_already_knows, zero executions.
    seed_baseline(ctx.store, (True, True), TASK1)
    seed_group(ctx.store, [MARKED], TASK1)
    # t2: a normal round-0 pass.
    seed_baseline(ctx.store, (False, False), TASK2)
    seed_group(ctx.store, [MARKED], TASK2)

    summary = run_live(ctx)

    assert [call["task_id"] for call in plugin.calls] == ["t2", "t2"]
    for skill_id in ctx.store.ids("skills"):
        skill = ctx.store.load("skills", skill_id, Skill)
        if skill.subtask_id.startswith(f"traj/{TASK1}"):
            assert (skill.status, skill.discard_reason) == (
                "discarded", "weak_already_knows")
        else:
            assert skill.status == "accepted"
    assert verdict_of(ctx.store, TASK1, 0)["verdict"] == "weak_already_knows"
    assert summary["processed_this_call"] == 2
    assert summary["discarded"] == {"weak_already_knows": 1}

    # -- resume: nothing pending anywhere, so nothing is executed, asked
    #    or dispatched -- the zero-execution group included (Major-1).
    plugin.calls.clear()
    author = make_fake_handle("author", [])
    ctx2 = make_ctx(tmp_path, author=author, run_dir=ctx.run_dir)
    summary2 = run_live(ctx2)

    assert plugin.calls == []
    assert author.client.calls == []
    assert summary2["processed_this_call"] == 0
    assert summary2["accepted"] == 1
    assert summary2["discarded"] == {"weak_already_knows": 1}


def test_injection_overflow_terminalizes_before_any_execution(
        tmp_path, monkeypatch):
    acceptance = dict(LIVE_ACCEPTANCE, max_injection_tokens=1)
    ctx, store, plugin, skills = build_env(
        tmp_path, monkeypatch, behave=lambda task, block: True,
        acceptance=acceptance)

    summary = run_live(ctx)

    assert plugin.calls == []          # the overflow never reaches K1
    skill = stored_skill(store, skills[0])
    assert (skill.status, skill.discard_reason) == (
        "discarded", "live_injection_overflow")
    verdict = verdict_of(store, TASK1, 0)
    assert verdict["verdict"] == "live_injection_overflow"
    assert verdict["injection_tokens"] > 1   # the audit row's own evidence
    assert summary["discarded"] == {"live_injection_overflow": 1}

    # Resume: the terminal group is never re-derived into a dispatch set.
    plugin.calls.clear()
    ctx2 = make_ctx(tmp_path, run_dir=ctx.run_dir)
    run_live(ctx2)
    assert plugin.calls == []
    assert len(verdicts(store)) == 1   # append-once across the resume


# ---------------------------------------------------------------------------
# Infra holes: stage-end raise, then the resume completes the round
# ---------------------------------------------------------------------------

def test_infra_holes_raise_at_stage_end_and_a_resume_completes(
        tmp_path, monkeypatch):
    monkeypatch.setattr(random, "uniform", lambda a, b: 0.0)
    broken = {"a00": True}

    def behave(task, block):
        token = task.meta.get("attempt_token", "")
        if broken["a00"] and token.startswith("a00"):
            raise InfrastructureError("sandbox down")
        return True

    ctx, store, plugin, skills = build_env(
        tmp_path, monkeypatch, behave=behave,
        author=author_plan(lambda skill_id: "revise"))

    with pytest.raises(InfrastructureError, match="resume"):
        run_live(ctx)

    # The group is untouched and pending; the summary names the hole and
    # the stage wrote it before raising (exit-3 semantics).
    assert stored_skill(store, skills[0]).status == "pending"
    summary = read_json(ctx.run_dir / "stage4_acceptance"
                        / "stage4_summary.json")
    assert summary["infra_failed"] == [TASK1]
    assert summary["infra_missing"]["round00"] == [unit(TASK1, 0)]
    assert summary["infra_shortfall_accepted"] is False

    # The resume: the sandbox heals, the hole executes, the round judges.
    broken["a00"] = False
    plugin.calls.clear()
    summary2 = run_live(ctx)

    assert [call["token"] for call in plugin.calls] == ["a00r3"]
    assert summary2["accepted"] == 1
    assert summary2["infra_failed"] == []
    assert stored_skill(store, skills[0]).status == "accepted"


# ---------------------------------------------------------------------------
# Shortfall acceptance: judged_with_shortfall, live_undetermined
# ---------------------------------------------------------------------------

def test_shortfall_acceptance_judges_holes_and_undetermines_all_hole_groups(
        tmp_path, monkeypatch):
    monkeypatch.setattr(random, "uniform", lambda a, b: 0.0)

    def behave(task, block):
        if task.task_id == "t1":
            raise InfrastructureError(   # every unit of t1 is a hole
                "sandbox down")
        token = task.meta.get("attempt_token", "")
        if token.startswith("a00"):
            raise InfrastructureError("sandbox down")
        return True

    install(monkeypatch, LivePlugin(behave))
    ctx = make_ctx(tmp_path, allow_infra_shortfall=True)
    for task_id in (TASK1, TASK2):
        seed_baseline(ctx.store, (False, False), task_id)
        seed_group(ctx.store, [MARKED], task_id)

    summary = run_live(ctx)    # no raise under the flag

    # t1: nothing was executed, so there is no live denominator.
    t1_verdict = verdict_of(ctx.store, TASK1, 0)
    assert t1_verdict["verdict"] == lv.LIVE_UNDETERMINED
    assert t1_verdict["judged_with_shortfall"] is True
    # t2: judged on the remaining valid sample, flagged with the shortfall.
    t2_verdict = verdict_of(ctx.store, TASK2, 0)
    assert t2_verdict["live"] == {"valid": 1, "success": 1}
    assert t2_verdict["better"] is True
    assert t2_verdict["judged_with_shortfall"] is True
    for skill_id in ctx.store.ids("skills"):
        skill = ctx.store.load("skills", skill_id, Skill)
        if skill.subtask_id.startswith(f"traj/{TASK1}"):
            assert (skill.status, skill.discard_reason) == (
                "discarded", lv.LIVE_UNDETERMINED)
        else:
            assert skill.status == "accepted"
    # The durable per-round record, written before the judgments.
    record = read_json(live_dir(ctx.store) / "round00"
                       / stage4_live_run.SHORTFALL_NAME)
    assert record["unit_ids"] == sorted([
        unit(TASK1, 0), unit(TASK1, 1), unit(TASK2, 0)])
    assert summary["infra_shortfall_accepted"] is True
    assert summary["infra_accepted"]["round00"] == record["unit_ids"]
    assert summary["infra_missing"] == {"round00": [], "round01": []}
    assert summary["infra_failed"] == []


def test_a_previously_accepted_hole_is_skipped_and_judges_with_shortfall(
        tmp_path, monkeypatch):
    """The durable-record half of the shortfall chain: a hole accepted by an
    earlier call is never re-executed, and the round it leaves behind is
    judged complete -- the state a crash between acceptance and judgment
    would have left, hand-built the way the K6 fixtures build theirs."""
    ctx, store, plugin, skills = build_env(
        tmp_path, monkeypatch, behave=lambda task, block: True)

    round_store = Store(live_dir(store) / "round00")
    round_store.save(make_traj(0, True), "trajectories")
    write_json(live_dir(store) / "round00"
               / stage4_live_run.SHORTFALL_NAME,
               {"unit_ids": [unit(TASK1, 1)]}, indent=2)
    append_jsonl(live_dir(store) / "round00" / lm.INJECTION_AUDIT_NAME,
                 lm.audit_row(store, TASK1, 0))

    summary = run_live(ctx)

    # Nothing was executed: the stored unit completed the round together
    # with the accepted hole, so the group never entered a dispatch set.
    assert plugin.calls == []
    verdict = verdict_of(store, TASK1, 0)
    assert verdict["live"] == {"valid": 1, "success": 1}
    assert verdict["better"] is True
    assert verdict["judged_with_shortfall"] is True
    assert stored_skill(store, skills[0]).status == "accepted"
    assert summary["infra_shortfall_accepted"] is True
    assert summary["infra_accepted"]["round00"] == [unit(TASK1, 1)]


# ---------------------------------------------------------------------------
# The no-op guard's crash window: half-applied discards are completed,
# never dispatched to round 1
# ---------------------------------------------------------------------------

def test_a_crash_mid_noop_discard_is_completed_not_redispatched(
        tmp_path, monkeypatch):
    ctx, store, plugin, skills = build_env(
        tmp_path, monkeypatch, behave=lambda task, block: False,
        contents=(MARKED, MARKED))
    # Round 0 executed, judged not better, attributed all-keep, and the
    # guard fired -- a crash then ate half of its discards. The remaining
    # survivor set re-renders a SMALLER block, so re-running the guard
    # alone would miss the frozen line and wrongly dispatch round 1.
    round_store = Store(live_dir(store) / "round00")
    for attempt in range(N_ATTEMPTS):
        round_store.save(make_traj(attempt, False), "trajectories")
    audit = lm.audit_row(store, TASK1, 0)
    append_jsonl(live_dir(store) / "round00" / lm.INJECTION_AUDIT_NAME,
                 audit)
    _, baseline_counts = lm.load_baseline(store, TASK1, N_ATTEMPTS)
    lv.judge_round(store=store, task_id=TASK1, round_=0,
                   rule_name="strict_rate_gain",
                   baseline_counts=baseline_counts,
                   n_attempts=N_ATTEMPTS, skip_ids=[])
    append_jsonl(live_dir(store) / lm.ATTRIBUTIONS_LEDGER, {
        "schema": "live-attribution-v1", "task_id": TASK1, "round": 0,
        "diagnosis": "the group did not help",
        "skill_actions": [
            {"skill_id": skill.skill_id, "version": 1, "action": "keep",
             "problem": ""} for skill in skills],
        "injection_block_sha256": audit["injection_block_sha256"],
    })
    discard_skill(store, skills[1], lv.LIVE_NO_IMPROVEMENT)

    summary = run_live(ctx)

    assert plugin.calls == []          # round 1 never ran
    for skill in skills:
        stored = stored_skill(store, skill)
        assert (stored.status, stored.discard_reason) == (
            "discarded", "live_no_improvement")
    assert verdict_of(store, TASK1, 1) is None
    assert not (live_dir(store) / "round01").exists()
    assert summary["discarded"] == {"live_no_improvement": 2}


# ---------------------------------------------------------------------------
# Entry guards: a missing baseline unit or a valid==0 baseline stops the
# stage before any execution is spent
# ---------------------------------------------------------------------------

def test_a_missing_baseline_unit_stops_the_stage_before_any_execution(
        tmp_path, monkeypatch):
    plugin = install(monkeypatch, LivePlugin(lambda task, block: True))
    ctx = make_ctx(tmp_path)
    seed_baseline(ctx.store, (False,), TASK1)   # one unit never ran
    seed_group(ctx.store, [MARKED], TASK1)

    with pytest.raises(InfrastructureError, match="stage 1"):
        run_live(ctx)

    assert plugin.calls == []


def test_an_all_invalid_baseline_is_a_config_error(tmp_path, monkeypatch):
    plugin = install(monkeypatch, LivePlugin(lambda task, block: True))
    ctx = make_ctx(tmp_path)
    write_json(ctx.run_dir / "stage1_baseline"
               / "accepted_infra_shortfall.json",
               {"unit_ids": [unit(TASK1, 0), unit(TASK1, 1)]}, indent=2)
    seed_group(ctx.store, [MARKED], TASK1)

    with pytest.raises(Exception, match="no valid attempt"):
        run_live(ctx)

    assert plugin.calls == []


# ---------------------------------------------------------------------------
# A9: stage 1's accepted weak-arm holes are inherited as this round's skips
# ---------------------------------------------------------------------------

def test_stage1_weak_arm_holes_are_inherited_as_round_skips(
        tmp_path, monkeypatch):
    """The stage reads stage 1's durable record and inherits ITS task's
    weak-arm holes into the round-0 skip set (both arms are in that
    record; only the weak arm of this task may skip here)."""
    ctx, store, plugin, skills = build_env(
        tmp_path, monkeypatch, behave=lambda task, block: True,
        baseline=(False, False))
    # The record carries a strong-arm unit and another task's unit too:
    # neither belongs in t1's round-0 skip set.
    write_json(ctx.run_dir / "stage1_baseline"
               / "accepted_infra_shortfall.json",
               {"unit_ids": sorted([
                   unit(TASK1, 0),
                   make_trajectory_id(TASK1, "strong", 0),
                   unit(TASK2, 0),
               ])}, indent=2)

    summary = run_live(ctx)

    # The inherited hole was never executed; the other unit was.
    assert [call["token"] for call in plugin.calls] == ["a01"]
    verdict = verdict_of(store, TASK1, 0)
    assert verdict["live"] == {"valid": 1, "success": 1}
    assert verdict["judged_with_shortfall"] is True
    assert verdict["better"] is True      # 1/1 beats the 0/1 baseline
    assert stored_skill(store, skills[0]).status == "accepted"
    # The inherited hole is NOT this stage's acceptance: the live arm
    # accepted nothing of its own.
    assert summary["infra_shortfall_accepted"] is False
    assert summary["infra_accepted"] == {"round00": [], "round01": []}


# ---------------------------------------------------------------------------
# Worker-count equivalence: the same inputs at any parallelism land on the
# same store, ledgers and summary
# ---------------------------------------------------------------------------

def test_worker_counts_produce_identical_outcomes(tmp_path, monkeypatch):
    # One plugin instance serves both runs (install registers one finder;
    # the second run simply clears the call log).
    plugin = install(monkeypatch, LivePlugin(
        lambda task, block: True if "Revised." in block
        else False))

    def run_once(tag, workers):
        run_dir = tmp_path / f"run-{tag}"
        ctx = make_ctx(tmp_path, run_dir=run_dir,
                       author=author_plan(lambda skill_id: "revise"),
                       rewriter=rewriter_append())
        for task_id in (TASK1, TASK2):
            seed_baseline(ctx.store, (False, False), task_id)
            seed_group(ctx.store, [MARKED, MARKED], task_id)
        ctx = replace(ctx, run_settings=RunSettings(workers=workers))
        plugin.calls.clear()
        summary = run_live(ctx)
        skills = {skill_id: ctx.store.load("skills", skill_id, Skill)
                  for skill_id in ctx.store.ids("skills")}
        return (summary, skills,
                sorted(json.dumps(row, sort_keys=True) for row in
                        verdicts(ctx.store)),
                len(plugin.calls))

    serial = run_once("serial", 1)
    parallel = run_once("w4", 4)

    assert serial == parallel
    assert serial[0]["accepted"] == 4
    assert serial[3] == 8        # two rounds over two tasks x two attempts
