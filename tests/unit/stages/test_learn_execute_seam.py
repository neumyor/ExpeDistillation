"""The learn <-> execute seam over the REAL drivers (the Wave 2 review C's
hard requirement): a real learn phase (decision -> add -> pending skill ->
artifact) feeding a real exam phase (pending universe out of the ARTIFACT ->
exam generated -> answered -> judged), the next learn phase accepting or
discarding the skill off the exam phase's report, and the two-round exam
skeleton (D L A L A L A L C) as one smoke.

Every phase here is the production driver -- no seeded report files, no
seeded artifacts, no hand-written acceptance rows, and since Wave 3-S no
seeded SUBTASK either: the add path segments the baseline phase's strong
trajectory itself (methods.evidence.segmentation through learn_add / learn_apply),
so the chain from the baseline-phase trajectories to the new skill is real end
to end.
"""

import importlib
import json
import importlib.abc
import importlib.machinery
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
from expedistill.composites import phase_bank, verdicts
from expedistill.composites.phase_bank import load_phase_bank
from expedistill.core import ids
from expedistill.core.config import ExperimentConfig
from expedistill.core.models import Skill
from expedistill.infra.store import Store
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.fake import json_script, make_fake_handle
from expedistill.stages import learn_ledger, phase_execute, phase_learn
from expedistill.stages.context import RunSettings, StageContext

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"
TASK_IDS = ("t1", "t2", "t3")
BIG = 10 ** 6

MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "2. Restart the service.",
    "[Verify] The status command reports running.",
    "[Do not use when] The status command already reports running.",
])

REVISED = "\n".join([
    "[When to use] The service rejects connections and a restart is due.",
    "[Steps]",
    "1. Run the status command to see the service state.",
    "2. Restart the service with its restart command.",
    "[Verify] The second status command reports running.",
    "[Do not use when] The status command already reports running.",
])


class PhasePlugin(BenchmarkPlugin):
    """Strong succeeds everywhere; the weak model fails t1 (the task the
    learn phases distil a skill for) and passes the rest."""

    def __init__(self):
        self.calls = []

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        if tasks_spec is None:
            ids_ = list(TASK_IDS)
        else:
            unknown = [i for i in tasks_spec if i not in TASK_IDS]
            if unknown:
                raise ValueError(f"unknown task ids: {unknown}")
            ids_ = list(tasks_spec)
        return [Task(task_id=i, instruction=f"do {i}") for i in ids_]

    def run(self, task, handle, skills_block, workdir):
        self.calls.append({"task_id": task.task_id, "handle": handle,
                           "skills_block": skills_block})
        handle.client.complete(handle.name, "system", task.instruction)
        if handle.role == "strong":
            return _outcome(task.task_id, True)
        return _outcome(task.task_id, task.task_id != "t1")

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


def _outcome(task_id, correctness):
    return RunOutcome(
        steps=[{"action": f"exec {task_id}", "observation": "ok"}],
        correctness=correctness,
    )


def _install_plugin(monkeypatch, plugin):
    fullname = f"expedistill.benchmarks.{plugin.name}"

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
def clean_registry():
    _reset_registry()
    verdicts.BENCHMARK_VERDICT_RULES.setdefault(_FAKE, "scorer")
    yield
    sys.modules.pop(_MODULE, None)
    _reset_registry()
    verdicts.BENCHMARK_VERDICT_RULES.pop(_FAKE, None)


# ---------------------------------------------------------------------------
# The config, the sequence, the phase instances
# ---------------------------------------------------------------------------

def _phases(retry_rounds=3):
    """The v4 phase definitions of the exam method (upgrade_v3_config's
    image): [D, L] + [A, L] * retry_rounds + [C]."""
    return {
        "baseline": {"kind": "execute", "role": "baseline",
              "tasks": "train", "delivery": {"name": "none"},
              "execution": {"name": "real",
                            "arms": ["executor_strong", "executor_weak"]}},
        "learn": {"kind": "learn",
              "evidence": {"name": "segment", "K": 5,
                           "write_lessons": False},
              "author": {"name": "distill_diagnose"},
              "curate": {"name": "none", "token_budget": BIG}},
        "exam": {"kind": "execute", "role": "exam", "tasks": "train",
              "delivery": {"name": "per_skill"},
              "execution": {"name": "exam"}},
        "test": {"kind": "execute", "role": "test", "tasks": "test",
              "delivery": {"name": "whole_bank"},
              "execution": {"name": "real"},
              "compare": {"against": "baseline"}},
    }


def _sequence(retry_rounds=3):
    return ((["baseline", "learn"] + ["exam", "learn"] * retry_rounds)
            + ["test"])


def _config(retry_rounds=3):
    return ExperimentConfig(
        benchmark=_FAKE, mode="full",
        models={"executor_strong": "big-model", "executor_weak": "weak-7b"},
        weak_profile="a weak model",
        n_attempts_strong=1, n_attempts_weak=1,
        sequence=_sequence(retry_rounds),
        phases=_phases(retry_rounds))


def _handles(examiner=(), answerer=(), judge=(), auditor=(),
             author=(), rewriter=(), segmenter=()):
    handles = {
        "executor_strong": make_fake_handle("strong"),
        "executor_weak": make_fake_handle("weak"),
        "examiner": make_fake_handle("examiner", list(examiner)),
        "answerer": make_fake_handle("weak", list(answerer)),
        "judge": make_fake_handle("judge", list(judge)),
        "auditor": make_fake_handle("auditor", list(auditor)),
        "author": make_fake_handle("author", list(author)),
        "rewriter": make_fake_handle("rewriter", list(rewriter)),
        "segmenter": make_fake_handle("segmenter", list(segmenter)),
    }
    return handles


def _ctx(tmp_path, config, handles):
    run_dir = tmp_path / "run"
    return StageContext(
        run_dir=run_dir, store=Store(run_dir), config=config,
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles=handles, workdir_root=run_dir / "work",
        run_settings=RunSettings())


def _phase(config, index, task_ids=None):
    name = config.sequence[index]
    spec = config.phases[name]
    label = f"p{index + 1:02d}:{name}"
    ids_ = tuple(task_ids if task_ids is not None else TASK_IDS)
    from expedistill.core.config import PhaseInstance
    return PhaseInstance(index=index, name=name, label=label, spec=spec,
                         task_ids=ids_)


def _run(ctx, config, index):
    spec = config.phases[config.sequence[index]]
    driver = phase_execute if spec.kind == "execute" else phase_learn
    return driver.run(ctx, _phase(config, index))


# ---------------------------------------------------------------------------
# The scripted models
# ---------------------------------------------------------------------------

def _exam_generation():
    return json_script({
        "question_text": "The service rejects connections. What next?",
        "reference_answer": "Restart the service."})


def _verdict(correct):
    return json_script({"verdict": "correct" if correct else "incorrect",
                        "error_description": ""})


def _audit():
    return json_script({"leaked": False, "reason": "clean"})


def _distill():
    return json_script({
        "when_to_use": "When the service rejects connections.",
        "steps": ["Run the status command on the service.",
                  "Restart the service with its restart command."],
        "verify": "The status command reports the service is running.",
        "not_applicable_when": "The service already reports running.",
        "summary": "restore a service that rejects connections",
    })


def _segmentation():
    # The SEGMENT prompt's output contract over t1's 1-step strong
    # trajectory: one segment covering [0, 1) exactly (D7 demands full
    # coverage), kept by the default segment_select rule because its
    # own goal succeeded.
    return json_script([{"start": 0, "end": 1, "label": "restart",
                         "description": "restart the stuck service",
                         "success": True}])


def _revision():
    # The REWRITE prompt's output contract (same keys the exam revise
    # path's tests use): the rewritten text, the failure category, the
    # rewrite reason and the fresh one-line summary (schema v3).
    return json_script({"skill_text": REVISED,
                        "failure_category": "missing_step",
                        "rewrite_reason": "spelled out verification",
                        "summary": "a rewritten one-liner"})


def _t1_subtask_id():
    """The add path's real segment id: the first cut of the baseline phase's
    strong attempt 0 on t1 (the deterministic chain the segmentation
    wiring produces inside L1)."""
    task_id = ids.make_task_id(_FAKE, "t1")
    return ids.make_subtask_id(ids.make_trajectory_id(task_id, "strong", 0),
                               0)


def _load_skill(ctx, skill_id):
    return ctx.store.load("skills", skill_id, Skill)


# ---------------------------------------------------------------------------
# The seam: a pending skill rides the artifact into the exam phase and back
# ---------------------------------------------------------------------------

def test_a_real_learn_add_feeds_a_real_exam_phase(tmp_path, monkeypatch):
    plugin = _install_plugin(monkeypatch, PhasePlugin())
    config = _config(retry_rounds=1)
    handles = _handles(
        examiner=[_exam_generation()],
        answerer=["bare answer", "skilled answer"],
        judge=[_verdict(False), _verdict(True)],
        auditor=[_audit()],
        author=[_distill()],
        segmenter=[_segmentation()])
    ctx = _ctx(tmp_path, config, handles)

    # D: the baseline (strong all-success, weak fails t1).
    _run(ctx, config, 0)
    # L1: the baseline-gap learn -- decide (add for t1), segment the D
    # phase's strong trajectory, author, land.
    subtask_id = _t1_subtask_id()
    first = _run(ctx, config, 1)

    skill_id = ids.make_skill_id(subtask_id)
    skill = _load_skill(ctx, skill_id)
    assert skill.status == "pending" and skill.version == 1
    assert first.summary["task_list"] == {"add": 1, "superseded_rows": 0}
    # THE seam: the intermediate learn phase's artifact carries the
    # PENDING member -- without it the exam phase's universe is empty and
    # the whole exam method silently measures nothing.
    artifact = load_phase_bank(ctx.run_dir, "p02:learn")
    assert [row["skill_id"] for row in artifact] == [skill_id]
    assert artifact[0]["status"] == "pending"

    # A: a real exam phase over that artifact's pending member.
    _run(ctx, config, 2)
    rows = _report_rows(ctx, "p03:exam")
    with_skill = next(row for row in rows if row.injected_skills)
    assert with_skill.status == verdicts.STATUS_NORMAL
    assert with_skill.success_count == 1

    # L2: the learn phase over the A report accepts the skill.
    third = _run(ctx, config, 3)

    skill = _load_skill(ctx, skill_id)
    assert skill.status == "accepted"
    assert third.summary["task_list"] == {"keep": 1, "superseded_rows": 0}
    # The last learn phase's artifact is the bank the test phase injects.
    artifact = load_phase_bank(ctx.run_dir, "p04:learn")
    assert [row["status"] for row in artifact] == ["accepted"]
    # Nothing ran on the executor beyond D's two arms.
    assert all(call["handle"].role in ("strong", "weak") for call in
               plugin.calls)


def test_a_still_failing_skill_is_discarded_at_the_last_check(
        tmp_path, monkeypatch):
    _install_plugin(monkeypatch, PhasePlugin())
    # One A pair: [D, L, A, L, C] -- the last learn phase has no
    # check left, so the table deletes a still-failing skill.
    config = _config(retry_rounds=1)
    handles = _handles(
        examiner=[_exam_generation()],
        answerer=["bare answer", "skilled answer"],
        judge=[_verdict(False), _verdict(False)],
        auditor=[_audit()],
        author=[_distill()],
        segmenter=[_segmentation()])
    ctx = _ctx(tmp_path, config, handles)

    _run(ctx, config, 0)
    subtask_id = _t1_subtask_id()
    _run(ctx, config, 1)
    _run(ctx, config, 2)

    result = _run(ctx, config, 3)

    skill = _load_skill(ctx, ids.make_skill_id(subtask_id))
    assert skill.status == "discarded"
    assert skill.discard_reason == "retry_exhausted"
    assert result.summary["task_list"] == {"delete": 1,
                                           "superseded_rows": 0}
    # The discarded member leaves the bank: the last learn phase's
    # artifact is empty and the test phase would inject nothing.
    assert load_phase_bank(ctx.run_dir, "p04:learn") == []
    # ... and C runs against an empty bank without raising.
    final = _run(ctx, config, 4)
    assert final.summary["role"] == "test"
    assert final.summary["with_bank"]["tasks"] == 3


def test_a_perfect_bare_control_reaches_the_decision_as_no_headroom(
        tmp_path, monkeypatch):
    """The exam phase skips the with-skill answer when the BARE control
    is perfect; that skip leaves no attempt in any store, so the
    recomputed report alone would call it zero_execution (work still
    owed) and decide nothing. The phase's minimal verdict file carries
    the fact, the learn phase merges it into the report, and the
    decision table deletes the skill as weak_already_knows."""
    _install_plugin(monkeypatch, PhasePlugin())
    config = _config(retry_rounds=1)   # [D, L, A, L, C]
    handles = _handles(
        examiner=[_exam_generation()],
        answerer=["bare answer"],       # the with-skill answer never runs
        judge=[_verdict(True)],         # the bare control is perfect
        auditor=[],                     # the leak audit never runs
        author=[_distill()],
        segmenter=[_segmentation()])
    ctx = _ctx(tmp_path, config, handles)

    _run(ctx, config, 0)
    subtask_id = _t1_subtask_id()
    _run(ctx, config, 1)
    _run(ctx, config, 2)

    result = _run(ctx, config, 3)

    skill = _load_skill(ctx, ids.make_skill_id(subtask_id))
    assert skill.status == "discarded"
    assert skill.discard_reason == "weak_already_knows"
    assert result.summary["task_list"] == {"delete": 1,
                                           "superseded_rows": 0}
    assert load_phase_bank(ctx.run_dir, "p04:learn") == []


def _report_rows(ctx, label):
    """The phase's acceptance report, RECOMPUTED from the store (the
    execute phases persist only minimal per-unit verdicts now)."""
    from expedistill.composites.reports import acceptance_report
    name = label.split(":", 1)[1]
    benchmark = ctx.config.benchmark
    if name == "baseline":
        return acceptance_report(
            ctx.store, kind="baseline", benchmark=benchmark,
            phase_label=label,
            n_attempts_strong=ctx.config.n_attempts_strong,
            n_attempts_weak=ctx.config.n_attempts_weak)
    if name == "exam":
        return acceptance_report(ctx.store, kind="exam",
                                 benchmark=benchmark, phase_label=label)
    if name == "live":
        index = int(label[1:3])
        earlier = 0
        for entry in (ctx.run_dir / "phases").iterdir():
            record_path = entry / "phase.json"
            if not record_path.exists():
                continue
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if (isinstance(record.get("index"), int)
                    and record["index"] < index
                    and record.get("role") == "live"):
                earlier += 1
        return acceptance_report(ctx.store, kind="live",
                                 benchmark=benchmark, phase_label=label,
                                 round_=earlier)
    raise AssertionError(f"no report kind for {label!r}")


# ---------------------------------------------------------------------------
# The two-round exam skeleton as one smoke: D L A L A L A L C
# ---------------------------------------------------------------------------

def test_the_two_round_exam_skeleton_runs_end_to_end(tmp_path, monkeypatch):
    plugin = _install_plugin(monkeypatch, PhasePlugin())
    config = _config(retry_rounds=3)   # [D, L, A, L, A, L, A, L, C]
    handles = _handles(
        examiner=[_exam_generation()],           # generated once, reused
        answerer=["bare v1", "skilled v1",       # A1
                  "bare v2", "skilled v2"],      # A2
        judge=[_verdict(False), _verdict(False),  # A1: bare, with-skill
               _verdict(False), _verdict(True)],  # A2: bare, with-skill
        auditor=[_audit(), _audit()],            # A1 (v1), A2 (v2)
        author=[_distill()],                    # L1's add
        rewriter=[_revision()],                  # L2's revise
        segmenter=[_segmentation()])             # L1's cut of t1
    ctx = _ctx(tmp_path, config, handles)

    # D + L1: the weak model fails t1, the learn phase segments the D
    # phase's strong trajectory and adds one skill from its cut.
    _run(ctx, config, 0)
    subtask_id = _t1_subtask_id()
    skill_id = ids.make_skill_id(subtask_id)
    _run(ctx, config, 1)
    assert _load_skill(ctx, skill_id).status == "pending"

    # A1 measures version 1: the skill does not help yet.
    _run(ctx, config, 2)

    # L2 revises (a check remains): version 2, still pending.
    _run(ctx, config, 3)
    revised = _load_skill(ctx, skill_id)
    assert revised.version == 2 and revised.status == "pending"
    assert revised.content == REVISED

    # A2 re-measures the NEW version (the block changed: no rule-1
    # reuse) on the SAME stored exam, and this time it passes.
    _run(ctx, config, 4)
    measured = [row for row in _report_rows(ctx, "p05:exam")
                if row.injected_skills]
    assert measured and measured[0].status == verdicts.STATUS_NORMAL

    # L3 accepts version 2.
    _run(ctx, config, 5)
    assert _load_skill(ctx, skill_id).status == "accepted"

    # A3: nothing is pending any more -- the universe is empty and the
    # phase measures nothing (the exam method's quiet round).
    _run(ctx, config, 6)
    from expedistill.infra.store import read_jsonl
    units = read_jsonl(phase_bank.phase_bank_dir(ctx.run_dir, "p07:exam")
                       / "units.jsonl")
    assert units == []

    # L4 (the last learn phase) re-curates and C injects the accepted
    # bank over the test set against D's weak arm.
    _run(ctx, config, 7)
    assert load_phase_bank(ctx.run_dir, "p08:learn")[0]["status"] == "accepted"
    final = _run(ctx, config, 8)

    assert final.summary["role"] == "test"
    assert final.summary["control_source"] == "baseline_reuse"
    assert final.summary["with_bank"]["tasks"] == 3
    # The with-bank arm really carried the skill's block.
    with_bank_calls = [call for call in plugin.calls
                       if call["skills_block"]]
    assert with_bank_calls and all(
        "Restart the service with its restart command" in call["skills_block"]
        for call in with_bank_calls)
    # The learn ledger's receipts, end to end: added, revised, accepted
    # (+ the last learn phase's no-op over the already-accepted skill).
    outcomes = []
    from expedistill.infra.store import read_jsonl
    for index in (1, 3, 5, 7):
        path = (phase_bank.phase_bank_dir(ctx.run_dir,
                                          f"p{index + 1:02d}:learn")
                 / learn_ledger.OUTCOMES_NAME)
        outcomes.extend(row["outcome"] for row in read_jsonl(path))
    assert outcomes == ["added", "revised", "accepted", "already_accepted"]
