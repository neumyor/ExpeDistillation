"""W1-B / R2 + R2b tests: the execution modules record facts, not verdicts.

Pins the contract points of R2 and R2b (docs/decisions/
execute_learn_repair_plan_20261016.md, boundaries doc section 3 rule 2):

1. The TB plugin passes the benchmark scorer's verdict through verbatim
   (``correctness``) and reports raw facts beside it (``verifier_ran``,
   the trial's exception); a reward without verifier evidence carries NO
   verdict (``correctness=None``, stored, never mechanically retried) --
   judging it is acceptance's named rule.
2. K1's retry stays mechanical: a run that came back with a scorer
   verdict -- however suspicious its facts look -- is never retried;
   only a run that raised an infrastructure failure is.
3. Exam answering (the exam-phase execution half) persists the answer with
   correctness blank (``judge_verdict=None``) and never grades.

No network, no real model: the TB remote is the stateful fake from
tests/unit/benchmarks/tb_fake_remote.py, the model is llm.fake.
"""

import importlib
import importlib.abc
import importlib.machinery
import sys
import types

from expedistill.benchmarks.base import BenchmarkPlugin, RunOutcome, Task, ViewRules
from expedistill.benchmarks.registry import _reset_registry, register_plugin
from expedistill.benchmarks.tb import TBPlugin
from expedistill.composites.batch_execute import batch_execute
from expedistill.composites.exam_answering import answer_exam
from expedistill.core.models import Attempt, Exam
from expedistill.infra.reliability import InfrastructureError
from expedistill.infra.store import Store
from expedistill.llm.call import ModelHandle
from expedistill.llm.fake import make_fake_handle
from tests.unit.benchmarks.tb_fake_remote import JobsRemoteLink, trial_files

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"


def _install_plugin(plugin):
    """Serve ``plugin`` through the real import machinery (the stub finder
    pattern of tests/composites/test_batch_execute.py)."""
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
    sys.meta_path.append(Finder())
    return plugin


class CountingPlugin(BenchmarkPlugin):
    """Stub plugin that counts run() calls and replays a scripted outcome."""

    def __init__(self, outcomes):
        self.calls = 0
        self._outcomes = list(outcomes)

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        return []

    def run(self, task, handle, skills_block, workdir):
        self.calls += 1
        return self._outcomes.pop(0)

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


def _tb_task():
    return Task(task_id="train-fasttext", instruction="train-fasttext",
                meta={"task_name": "train-fasttext", "attempt_token": "a00"})


def _tb_plugin(link):
    return TBPlugin(link=link, poll_seconds=0.0, deadline_seconds=10.0)


_HANDLE = ModelHandle(name="qwen3.6-flash-distill", role="executor_weak",
                      client=None)


# ---------------------------------------------------------------------------
# 1. The TB plugin reports facts; the scorer's verdict passes through
# ---------------------------------------------------------------------------

def test_scorer_verdict_passes_through_when_the_verifier_ran(tmp_path):
    # A scored trial: correctness is the scorer's reward and NOTHING else.
    link = JobsRemoteLink()
    outcome = _tb_plugin(link).run(_tb_task(), _HANDLE, "", tmp_path / "work")
    assert outcome.meta["reward"] == 1.0
    assert outcome.meta["verifier_ran"] is True
    assert outcome.correctness is True


def test_a_reward_without_verifier_evidence_carries_no_verdict(tmp_path):
    # The pseudo-zero pattern (reward present, pytest never collected) is
    # no verdict in EITHER reward direction (R2b): the run is stored with
    # correctness=None and the raw facts beside it -- never invalidated by
    # the plugin, never mechanically retried, judged by acceptance's
    # named rule.
    link = JobsRemoteLink(on_submit=lambda run_id, task, model: trial_files(
        run_id, task, result="result_failure.json",
        verifier_log="verifier_not_collected.txt"))
    outcome = _tb_plugin(link).run(_tb_task(), _HANDLE, "", tmp_path / "work")
    assert outcome.meta["reward"] == 0.0
    assert outcome.meta["verifier_ran"] is False
    assert "verdict_reason" not in outcome.meta
    assert outcome.correctness is None


def test_a_positive_reward_without_verifier_evidence_carries_no_verdict(
        tmp_path):
    # The historically dangerous direction (2026-10-16 review D2): a
    # POSITIVE reward with no verifier evidence must not score as a
    # success -- the guard is symmetric in the reward value.
    link = JobsRemoteLink(on_submit=lambda run_id, task, model: trial_files(
        run_id, task, result="result_success.json",
        verifier_log="verifier_not_collected.txt"))
    outcome = _tb_plugin(link).run(_tb_task(), _HANDLE, "", tmp_path / "work")
    assert outcome.meta["reward"] == 1.0
    assert outcome.meta["verifier_ran"] is False
    assert outcome.correctness is None


def test_reward_absent_trial_reports_no_verdict_and_raw_exception_facts(
        tmp_path):
    # The campaign's model-exception set (AgentTimeoutError et al.) used
    # to be reclassified as the model's failure by the plugin. R2 removed
    # that; R2b additionally stopped the mechanical retry: with no scorer
    # verdict there is no claim about the model, the run is stored with
    # correctness=None and the exception travels as a fact, and judging
    # the trial is acceptance's.
    link = JobsRemoteLink(on_submit=lambda run_id, task, model: trial_files(
        run_id, task, result="result_model_fail.json"))
    outcome = _tb_plugin(link).run(_tb_task(), _HANDLE, "", tmp_path / "work")
    assert outcome.meta["reward"] is None
    assert outcome.meta["exception_type"] == "AgentTimeoutError"
    assert outcome.meta["exception_message"]
    assert outcome.correctness is None


# ---------------------------------------------------------------------------
# 2. K1's retry is mechanical: no verdict is ever second-guessed
# ---------------------------------------------------------------------------

def test_a_suspicious_looking_result_is_executed_exactly_once(tmp_path):
    # A scorer verdict with alarming facts (a verifier that looks broken,
    # an error text in meta) is a RESULT: K1 stores it verbatim and never
    # retries it -- "after a result comes back, no retry, however
    # suspicious it looks" (plan R2 item 2).
    plugin = _install_plugin(CountingPlugin([
        RunOutcome(steps=[], correctness=False,
                   meta={"error": "verifier log looks broken"}),
    ]))
    try:
        result = batch_execute(
            store=Store(tmp_path / "run"),
            benchmark=_FAKE,
            tasks=[Task(task_id="t1", instruction="do t1")],
            role="weak",
            model_name="weak-7b",
            handle=make_fake_handle("weak"),
            n_attempts=1,
            workdir_root=tmp_path / "run" / "workdirs",
            infra_retries=2,
            infra_backoff=(0.0, 0.0),
        )
        assert plugin.calls == 1
        assert [t.correctness for t in result] == [False]
        assert result[0].meta["error"] == "verifier log looks broken"
    finally:
        sys.meta_path.pop()
        sys.modules.pop(_MODULE, None)
        _reset_registry()


def test_only_an_infrastructure_failure_is_retried(tmp_path):
    # The plugin raised an infrastructure failure: the unit reruns
    # mechanically, blind to the reason, until a result lands. A stored
    # correctness=None run (no scorer verdict) is NOT retried.
    class RaisingPlugin(CountingPlugin):
        def run(self, task, handle, skills_block, workdir):
            self.calls += 1
            if self._outcomes:
                raise self._outcomes.pop(0)
            return RunOutcome(steps=[], correctness=False, meta={})

    plugin = _install_plugin(RaisingPlugin([
        InfrastructureError("sandbox down"),
    ]))
    try:
        result = batch_execute(
            store=Store(tmp_path / "run"),
            benchmark=_FAKE,
            tasks=[Task(task_id="t1", instruction="do t1")],
            role="weak",
            model_name="weak-7b",
            handle=make_fake_handle("weak"),
            n_attempts=1,
            workdir_root=tmp_path / "run" / "workdirs",
            infra_retries=1,
            infra_backoff=(0.0, 0.0),
        )
        assert plugin.calls == 2
        assert [t.correctness for t in result] == [False]
    finally:
        sys.meta_path.pop()
        sys.modules.pop(_MODULE, None)
        _reset_registry()


# ---------------------------------------------------------------------------
# 3. Exam answering leaves correctness blank (boundaries doc section 3
#    rule 5: answering is execution, "right or wrong" stays None)
# ---------------------------------------------------------------------------

def _exam():
    return Exam(
        exam_id="searchqa:q1:exam0000",
        question_text="What is the capital of France?",
        reference_answer="Paris",
        subtask_id="searchqa:q1:weak:0:seg0000",
        skill_id="searchqa:q1:weak:0:seg0000:skill0000",
    )


def test_answer_exam_persists_an_unjudged_attempt_and_never_grades(tmp_path):
    handle = make_fake_handle("weak", script=["Paris, the city of light."])
    store = Store(tmp_path / "run")
    attempt = answer_exam(
        store=store,
        exam=_exam(),
        answerer_handle=handle,
        skills_block="",
        purpose="acceptance",
    )
    # Correctness is blank: answering is execution, the judge is acceptance.
    assert attempt.judge_verdict is None
    assert attempt.weak_answer == "Paris, the city of light."
    # Exactly one model call happened: the answer. No judge call.
    assert len(handle.client.calls) == 1
    stored = store.load("attempts", attempt.attempt_id, Attempt)
    assert stored.judge_verdict is None
    assert stored.weak_answer == "Paris, the city of light."
