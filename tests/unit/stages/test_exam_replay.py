"""W3-D: the exam phase's replay protection -- a judged answer is a
finished measurement.

Three crash points, three resumes (the exam phase's own replay rules,
completing the picture the baseline phase draws with unit-id skips and the B
phase with its append-once verdicts ledger):

* a COMPLETED phase replayed whole: every unit's attempts are already
  judged, so the replay makes ZERO model calls (the probe is a handle
  whose every call explodes) and leaves every stored artifact
  byte-identical -- the exams, the attempts, the leak ledger, the report
  and the summary;
* a PARTIAL phase replayed: the units that already measured reuse their
  judged attempts (any model call for them explodes), only the
  unanswered ones spend calls;
* a crash BETWEEN the answer and the judge: the unjudged pending attempt
  is completed by the resumed exam_once call -- the pre-existing
  ``_pending_attempt`` path, which the guard must leave untouched;
* a LATER exam phase over a revised version: a fresh measurement round, not
  a replay -- the bare control is re-answered and the new version is
  audited again (the guard's scope, pinning the W2-D skeleton's shape).
"""

import importlib
import importlib.abc
import importlib.machinery
import json
import sys
import types
import urllib.parse
from pathlib import Path

import pytest

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.registry import _reset_registry, register_plugin
from expedistill.composites import phase_bank, verdicts
from expedistill.core.config import ExecutePhaseSpec, ExperimentConfig
from expedistill.core.ids import (
    make_attempt_id,
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Attempt, Skill, Step, Subtask, Trajectory
from expedistill.infra.reliability import InfrastructureError
from expedistill.infra.store import Store
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.fake import json_script, make_fake_handle
from expedistill.stages import phase_execute
from expedistill.stages.context import RunSettings, StageContext

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"
TASK_IDS = ("t1", "t2", "t3")

SKILL_TEXT = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "2. Restart the service.",
])

#: One distinct exam question per task, so the scripted judges and
#: auditors can tell the units apart by their prompts.
_QUESTIONS = {
    "t1": "The billing service rejects connections. What should be done?",
    "t2": "The cache node rejects connections. What should be done?",
    "t3": "The search index rejects connections. What should be done?",
}


class PhasePlugin(BenchmarkPlugin):
    """The fixed task set; the exam phase never dispatches it."""

    def __init__(self):
        self.calls = []

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        ids_ = list(TASK_IDS if tasks_spec is None else tasks_spec)
        return [Task(task_id=i, instruction=f"do {i}") for i in ids_]

    def run(self, task, handle, skills_block, workdir):
        self.calls.append({"task_id": task.task_id})
        handle.client.complete(handle.name, "system", task.instruction)
        return _outcome(task.task_id, True)

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
    yield
    sys.modules.pop(_MODULE, None)
    _reset_registry()


def _config():
    # A legal v4 method (the parse_phases checklist runs at construction);
    # the phase the tests RUN is constructed directly below.
    return ExperimentConfig(
        benchmark=_FAKE, mode="full",
        models={"executor_strong": "big-model", "executor_weak": "weak-7b"},
        weak_profile="a weak model",
        n_attempts_strong=1, n_attempts_weak=1,
        sequence=["baseline"],
        phases={"baseline": {"kind": "execute", "role": "baseline",
                     "tasks": "train", "delivery": {"name": "none"},
                     "execution": {"name": "real",
                                   "arms": ["executor_strong",
                                            "executor_weak"]}}})


def _exam_spec():
    return ExecutePhaseSpec(role="exam", tasks="train",
                            delivery={"name": "per_skill"},
                            execution={"name": "exam"})


def _ctx(tmp_path, handles):
    run_dir = tmp_path / "run"
    return StageContext(
        run_dir=run_dir, store=Store(run_dir), config=_config(),
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles=handles, workdir_root=run_dir / "workdirs",
        run_settings=RunSettings())


def _phase(index=2, name="exam"):
    from expedistill.core.config import PhaseInstance
    return PhaseInstance(index=index, name=name,
                         label=f"p{index + 1:02d}:{name}", spec=_exam_spec(),
                         task_ids=TASK_IDS)


def _skill_on(ctx, raw_task):
    """One pending skill on ``raw_task`` with its subtask and trajectory
    stored, the exam-phase universe's deterministic chain."""
    task_id = make_task_id(_FAKE, raw_task)
    trajectory = Trajectory(
        trajectory_id=make_trajectory_id(task_id, "strong", 0),
        task_id=task_id, benchmark=_FAKE, model_role="strong",
        model_name="big-model", attempt_index=0,
        steps=[Step(action="check", observation="down"),
               Step(action="restart", observation="ok")],
        correctness=True)
    ctx.store.save(trajectory, "trajectories")
    subtask = Subtask(
        subtask_id=make_subtask_id(trajectory.trajectory_id, 0),
        task_id=task_id, trajectory_id=trajectory.trajectory_id,
        source="strong", trajectory_success=True, segment_index=0,
        step_range=(0, 2), label=f"fix-{raw_task}",
        description=f"fix the {raw_task} service", success=True)
    ctx.store.save(subtask, "subtasks")
    return Skill(skill_id=make_skill_id(subtask.subtask_id),
                 content=SKILL_TEXT, subtask_id=subtask.subtask_id,
                 source_subtasks=[subtask.subtask_id], version=1,
                 status="pending")


def _write_bank(ctx, label, skills):
    directory = phase_bank.phase_bank_dir(ctx.run_dir, label)
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / phase_bank.BANK_NAME, "w", encoding="utf-8") as f:
        for skill in skills:
            f.write(json.dumps(skill.to_dict(), ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# The scripted models: callables, so nothing depends on unit order. Every
# callable serves ONLY the tasks in ``allowed`` -- a model call for any
# replayed (already judged) unit explodes, which is the probe.
# ---------------------------------------------------------------------------

def _examiner(allowed):
    def script(system, user):
        for raw in allowed:
            if f"SUBTASK LABEL: fix-{raw}" in user:
                return json_script({
                    "question_text": _QUESTIONS[raw],
                    "reference_answer": "Restart it and say why."})
        raise AssertionError("examiner called for a replayed unit")
    return make_fake_handle("examiner", script)


def _answerer():
    def script(system, user):
        return "skilled answer" if "[When to use]" in user else "bare answer"
    return make_fake_handle("answerer", script)


def _judge(bare_correct=()):
    def script(system, user):
        correct = ("skilled answer" in user
                   or any(_QUESTIONS[raw] in user for raw in bare_correct))
        return json_script({
            "verdict": "correct" if correct else "incorrect",
            "error_description": "" if correct else "vague"})
    return make_fake_handle("judge", script)


def _auditor(leaked=()):
    def script(system, user):
        is_leaked = any(_QUESTIONS[raw] in user for raw in leaked)
        return json_script({
            "leaked": is_leaked,
            "reason": "reveals the answer" if is_leaked else "clean"})
    return make_fake_handle("auditor", script)


def _handles(allowed, bare_correct=(), leaked=()):
    return {"examiner": _examiner(allowed), "answerer": _answerer(),
            "judge": _judge(bare_correct), "auditor": _auditor(leaked)}


def _tree(root):
    """Every file under ``root`` as ``{relative path: bytes}``."""
    root = Path(root)
    return {str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()}


def _attempts_dir(ctx):
    return ctx.run_dir / "stage4_acceptance" / "attempts"


def _verdicts(ctx, phase):
    """The phase's minimal per-unit verdicts, in file order."""
    from expedistill.stages.phase_execute.phase_disk import VERDICTS_NAME
    from expedistill.composites import phase_bank
    from expedistill.infra.store import read_jsonl
    path = phase_bank.phase_bank_dir(ctx.run_dir, phase.label) / VERDICTS_NAME
    return list(read_jsonl(path))


def _leak_ledger(ctx):
    return ctx.run_dir / "stage4_acceptance" / "leak_verdicts.jsonl"


# ---------------------------------------------------------------------------
# A completed phase replayed whole: zero model calls, byte-identical
# ---------------------------------------------------------------------------

def test_a_completed_phase_replays_with_zero_model_calls(monkeypatch,
                                                         tmp_path):
    _install_plugin(monkeypatch, PhasePlugin())
    ctx = _ctx(tmp_path, _handles(allowed=("t1", "t2", "t3"),
                                  bare_correct=("t2",), leaked=("t3",)))
    skills = [_skill_on(ctx, raw) for raw in ("t1", "t2", "t3")]
    _write_bank(ctx, "p02:learn", skills)
    phase = _phase()

    phase_execute.run(ctx, phase)
    stored = _verdicts(ctx, phase)
    assert [row["status"] for row in stored] == [
        verdicts.STATUS_NORMAL,
        verdicts.STATUS_NOT_RUN_CONTROL_PERFECT,
        verdicts.STATUS_NOT_TESTABLE]
    assert stored[1]["cause"] == verdicts.CAUSE_WEAK_ALREADY_KNOWS
    assert stored[2]["cause"] == verdicts.CAUSE_LEAK
    # Every unit spent exactly its own calls: three generated exams, the
    # bare answers (t2's was perfect and t3's leaked, so no with-skill
    # answers for them), the judgements and the two leak audits.
    assert {name: len(handle.client.calls)
            for name, handle in ctx.handles.items()} == {
        "examiner": 3, "answerer": 4, "judge": 4, "auditor": 2}

    before = {
        "exams": _tree(ctx.run_dir / "stage3_generation" / "exams"),
        "attempts": _tree(_attempts_dir(ctx)),
        "leak_ledger": _leak_ledger(ctx).read_bytes(),
        "phase_dir": _tree(phase_bank.phase_bank_dir(ctx.run_dir,
                                                     phase.label)),
    }

    # The replay: every handle explodes on any call (an empty FakeClient
    # script raises). A completed phase's measurements stand as stored.
    replay = _ctx(tmp_path, {role: make_fake_handle(role, []) for role in
                              ("examiner", "answerer", "judge", "auditor")})
    second = phase_execute.run(replay, phase).summary

    assert second["complete"] is True
    assert _tree(replay.run_dir / "stage3_generation" / "exams") == \
        before["exams"]
    assert _tree(_attempts_dir(replay)) == before["attempts"]
    assert _leak_ledger(replay).read_bytes() == before["leak_ledger"]
    assert _tree(phase_bank.phase_bank_dir(replay.run_dir,
                                           phase.label)) == before["phase_dir"]


# ---------------------------------------------------------------------------
# A partial replay: the measured units stand, only the holes spend calls
# ---------------------------------------------------------------------------

def test_a_partial_replay_answers_only_the_unmeasured_units(monkeypatch,
                                                            tmp_path):
    """The interrupted pass: one unit measured, one still owed. The
    interruption is the real one -- the owed unit's model call raises
    InfrastructureError, so the pass writes its summary (infra_failed
    non-empty, hence NOT a completion signal) and raises; the resume
    re-enters the phase, reuses the measured unit's judged attempts and
    spends calls only on the owed one. (exam phase whose pass COMPLETED is
    skipped whole by the completion signal instead -- the test above
    pins that path; and the bank cannot grow between two passes of the
    same phase in a sequence replay, because the previous learn phase's
    artifact is frozen and its own completion signal skips it.)"""
    _install_plugin(monkeypatch, PhasePlugin())

    def _infra_exploding(allowed, owed_raw):
        """Handles that serve ``allowed`` and raise InfrastructureError
        for the owed unit -- the simulated mid-phase crash."""
        handles = _handles(allowed=allowed)
        marker = f"SUBTASK LABEL: fix-{owed_raw}"
        question = _QUESTIONS[owed_raw]

        def wrap(handle):
            inner = handle.client.script

            def script(system, user):
                if marker in user or question in user:
                    raise InfrastructureError("simulated infra crash")
                return inner(system, user)
            handle.client.script = script
            return handle
        for name in ("examiner", "answerer", "judge", "auditor"):
            wrap(handles[name])
        return handles

    ctx = _ctx(tmp_path, _infra_exploding(("t1",), "t2"))
    measured = _skill_on(ctx, "t1")
    owed = _skill_on(ctx, "t2")
    _write_bank(ctx, "p02:learn", [measured, owed])
    phase = _phase()
    with pytest.raises(InfrastructureError):
        phase_execute.run(ctx, phase)
    measured_attempts = _tree(_attempts_dir(ctx))
    measured_exam = _tree(ctx.run_dir / "stage3_generation" / "exams")
    assert len(measured_attempts) == 2  # t1's bare + with-skill, judged
    assert len(measured_exam) == 1      # only t1's exam was generated

    # Any model call for t1 (the replayed, already judged unit) explodes.
    resumed = _ctx(tmp_path, _handles(allowed=("t2",)))
    phase_execute.run(resumed, phase)

    # Both units carry a completed verdict; the measured one was reused
    # (zero calls) and the owed one ran -- the call counts below pin the
    # difference.
    assert [row["status"] for row in _verdicts(resumed, phase)] == [
        verdicts.STATUS_NORMAL, verdicts.STATUS_NORMAL]
    # The measured unit's artifacts are byte-identical, and only the owed
    # unit spent calls: one exam generation, one leak audit, one bare
    # and one with-skill answer with their judgements.
    attempts_after = _tree(_attempts_dir(resumed))
    for name, blob in measured_attempts.items():
        assert attempts_after[name] == blob
    exams_after = _tree(resumed.run_dir / "stage3_generation" / "exams")
    for name, blob in measured_exam.items():
        assert exams_after[name] == blob
    assert {name: len(handle.client.calls)
            for name, handle in resumed.handles.items()} == {
        "examiner": 1, "answerer": 2, "judge": 2, "auditor": 1}
    # The leak ledger kept its append-once shape: one row per (skill,
    # version), the replayed unit's row untouched.
    ledger_rows = [line for line in
                   _leak_ledger(resumed).read_text().splitlines() if line]
    assert len(ledger_rows) == 2


# ---------------------------------------------------------------------------
# The pre-existing in-flight path: a crash between the answer and the
# judge resumes by judging the stored answer, never re-asking it
# ---------------------------------------------------------------------------

def test_a_crash_between_the_answer_and_the_judge_resumes_the_answer(
        monkeypatch, tmp_path):
    _install_plugin(monkeypatch, PhasePlugin())
    # The judge dies inside the FIRST pass with an error that is fatal to
    # the batch (not a repair-budget ModelOutputError), after the bare
    # answer was persisted unjudged -- the answer-first crash point.
    handles = _handles(allowed=("t1",))
    handles["judge"] = make_fake_handle("judge", [AssertionError("judge died")])
    ctx = _ctx(tmp_path, handles)
    skill = _skill_on(ctx, "t1")
    _write_bank(ctx, "p02:learn", [skill])
    phase = _phase()
    with pytest.raises(AssertionError, match="judge died"):
        phase_execute.run(ctx, phase)

    exam_id = make_exam_id(skill.subtask_id)
    bare_id = make_attempt_id(exam_id, 0)
    stored = ctx.store.load("attempts", bare_id, Attempt)
    assert stored.weak_answer == "bare answer"
    assert stored.judge_verdict is None
    # The stored answer is the only thing the crash left behind.
    assert list(_tree(_attempts_dir(ctx))) == [
        urllib.parse.quote(bare_id, safe="") + ".json"]

    # The resume: the exam exists (the examiner explodes on any call),
    # the pending bare attempt is judged on its stored answer, then the
    # with-skill answer runs and is judged.
    resumed_handles = _handles(allowed=())
    resumed_handles["answerer"] = make_fake_handle(
        "answerer", ["skilled answer"])
    resumed_handles["judge"] = make_fake_handle("judge", [
        json_script({"verdict": "incorrect", "error_description": "vague"}),
        json_script({"verdict": "correct", "error_description": ""}),
    ])
    resumed_handles["auditor"] = _auditor()
    resumed = _ctx(tmp_path, resumed_handles)
    phase_execute.run(resumed, phase)

    assert [row["status"] for row in _verdicts(resumed, phase)] == [
        verdicts.STATUS_NORMAL]
    # The weak model was never re-asked the bare question: the resumed
    # answerer's only call is the with-skill answer.
    assert len(resumed_handles["answerer"].client.calls) == 1
    bare = resumed.store.load("attempts", bare_id, Attempt)
    assert bare.weak_answer == "bare answer"
    assert bare.judge_verdict is False
    assert len(_tree(_attempts_dir(resumed))) == 2
    with_skill = resumed.store.load(
        "attempts", make_attempt_id(exam_id, 1), Attempt)
    assert with_skill.judge_verdict is True


# ---------------------------------------------------------------------------
# The guard's scope: a later exam phase is a new measurement round
# ---------------------------------------------------------------------------

def test_a_later_phase_over_a_new_version_remeasures_the_bare_control(
        monkeypatch, tmp_path):
    # A second exam phase after a revise is a FRESH round, not a replay: the
    # bare control is re-answered and the new version gets its own leak
    # audit (the W2-D two-round skeleton's shape -- the replay guard must
    # not swallow it). Only the exam itself is reused (its exists check).
    _install_plugin(monkeypatch, PhasePlugin())
    ctx = _ctx(tmp_path, _handles(allowed=("t1",)))
    skill = _skill_on(ctx, "t1")
    _write_bank(ctx, "p02:learn", [skill])
    phase_execute.run(ctx, _phase(2, "exam"))
    assert [row["status"] for row in _verdicts(ctx, _phase(2, "exam"))] == [
        verdicts.STATUS_NORMAL]
    assert {name: len(handle.client.calls)
            for name, handle in ctx.handles.items()} == {
        "examiner": 1, "answerer": 2, "judge": 2, "auditor": 1}

    # The learn phase between the two exam phases revised the skill: same
    # id, new version, new content (a changed block, so rule 1 does not
    # reuse the previous verdict either).
    revised = Skill(skill_id=skill.skill_id, content=SKILL_TEXT + "\n3. Verify.",
                    subtask_id=skill.subtask_id,
                    source_subtasks=[skill.subtask_id], version=2,
                    status="pending")
    _write_bank(ctx, "p04:learn", [revised])
    second_ctx = _ctx(tmp_path, _handles(allowed=("t1",)))
    phase_execute.run(second_ctx, _phase(4, "exam"))

    assert [row["status"] for row in _verdicts(second_ctx, _phase(4, "exam"))] == [
        verdicts.STATUS_NORMAL]
    # The bare control was re-answered, the with-skill answer ran, the
    # new version was audited; only the stored exam was reused.
    assert {name: len(handle.client.calls)
            for name, handle in second_ctx.handles.items()} == {
        "examiner": 0, "answerer": 2, "judge": 2, "auditor": 1}
    assert len(_tree(_attempts_dir(second_ctx))) == 4  # 2 + the round's 2
    ledger_rows = [line for line in
                   _leak_ledger(second_ctx).read_text().splitlines() if line]
    assert len(ledger_rows) == 2  # one verdict per version
