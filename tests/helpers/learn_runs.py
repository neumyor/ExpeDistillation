"""A run directory built by hand for learn-phase tests.

The learn phase reads only the execute phases' trial-level artifacts (the
answer sheets, their coverage and unit specs) and the run-level stores, so
:class:`LearnRun` writes exactly those for the baseline and practice
positions of the reference sequence and drives ``stages.phase_learn`` on
the learn positions, with the golden matrix's stub benchmark and the fake
provider. A practice trial's sheet carries the skill folder manifest of
the bank the previous learn phase published, and opening a card is a
``ReadSkill[<location>]`` step, as the real practice phase records them.

The provider's diagnosis is pinned to scope ``subtask`` (the stock fake
makes one trial in four ``general``), so every scenario routes its
failures by the step facts alone.
"""

import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from expedistill.benchmarks.registry import resolve_plugin
from expedistill.composites.phase_bank import load_phase_bank, phase_bank_dir
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
    UnitSpec,
)
from expedistill.core.config import (
    ROLE_DEFAULTS,
    ExperimentConfig,
    PhaseInstance,
    phase_label,
)
from expedistill.core.ids import make_task_id, make_trajectory_id
from expedistill.core.models import Skill, Step, Trajectory
from expedistill.infra.store import (
    Store, object_filename, read_json, write_json, write_jsonl)
from expedistill.inject.skill_text import description_text
from expedistill.llm.call import make_handle
from expedistill.llm.fake_provider import FakeProviderClient
from expedistill.prompts.learning_context import default_slots
from expedistill.stages import phase_learn
from expedistill.stages.context import RunSettings, StageContext
from tests.golden.harness import (
    FAKE_BENCH,
    STRONG_MODEL,
    WEAK_MODEL,
    GoldenPlugin,
    installed_plugin,
)
from tests.helpers.configs import reference_method

__all__ = ["WORK", "CrashBefore", "GoldenPlugin", "LearnRun",
           "PinnedClient", "baseline_trials", "installed_plugin",
           "read_skill", "run_through", "snapshot", "trial"]

PLAN: Dict[str, Any] = {"strong_model": STRONG_MODEL,
                        "weak_model": WEAK_MODEL, "segment_steps": 1,
                        "questions": {}}


class PinnedClient(FakeProviderClient):
    """The fake provider with every diagnosis in scope ``subtask``; a trial
    whose task text contains one of ``early`` fails at step 0 instead of
    its last step (the stub's tasks read "... for task one" / "... for
    task two")."""

    def __init__(self, plan: Optional[Dict[str, Any]] = None,
                 early: Sequence[str] = ()):
        super().__init__(dict(plan or PLAN))
        self.early = tuple(early)

    def _trial_analysis(self, user: str) -> str:
        reply = FakeProviderClient._trial_analysis(user)
        reply = reply.replace('"scope": "general"', '"scope": "subtask"')
        task = user.split("TASK:\n", 1)[1].split("\n\n", 1)[0]
        if any(word in task for word in self.early):
            reply = re.sub(r'"failing_step": \d+', '"failing_step": 0',
                           reply)
        return reply


class CrashBefore:
    """Wraps a client; raises before the ``occurrence``-th call whose
    prompt starts with ``anchor`` (a plain RuntimeError: no stage retries
    it, so it escapes like a process death)."""

    def __init__(self, inner: Any, anchor: str, occurrence: int = 1):
        self.inner = inner
        self.anchor = anchor
        self.left = occurrence
        self.fired = False

    @property
    def calls(self) -> List[Dict[str, Any]]:
        return self.inner.calls

    def complete(self, model: str, system: str, user: str, **kwargs: Any):
        if user.startswith(self.anchor) and not self.fired:
            self.left -= 1
            if self.left == 0:
                self.fired = True
                raise RuntimeError(f"simulated crash before {self.anchor!r}")
        return self.inner.complete(model, system, user, **kwargs)


def trial(task: str, role: str, actions: Sequence[str], *,
          correct: bool, attempt: int = 0) -> Dict[str, Any]:
    """One trial of a hand-built execute phase."""
    return {"task": task, "role": role, "actions": list(actions),
            "correct": correct, "attempt": attempt}


def read_skill(skill_id: str) -> str:
    """The read action that opens ``skill_id``'s card in a practice
    trial."""
    return f"ReadSkill[{_slug(skill_id)}]"


def _slug(skill_id: str) -> str:
    return skill_id.replace("/", "-")


def _config() -> ExperimentConfig:
    return ExperimentConfig.from_dict({
        "benchmark": FAKE_BENCH,
        "models": {"executor_strong": STRONG_MODEL,
                   "executor_weak": WEAK_MODEL},
        "n_attempts_strong": 1,
        "n_attempts_weak": 1,
        "split": {"train": ["t1", "t2"], "test": ["t3"]},
        "rules": {"segment_select": "strong_success_weak_failed",
                  "trajectory_select": "all"},
        "token_budgets": {"default_max_output_tokens": 65536},
        **reference_method(),
        "schema_version": 9,
    })


class LearnRun:
    """The reference sequence's run directory, phase by phase."""

    def __init__(self, run_dir: Path, client: Any = None,
                 workers: int = 1) -> None:
        self.run_dir = Path(run_dir)
        self.config = _config()
        self.store = Store(self.run_dir)
        self.client = client if client is not None else PinnedClient()
        self.workers = workers

    def context(self, client: Any = None, workers: Optional[int] = None
                ) -> StageContext:
        client = client if client is not None else self.client
        resolve_plugin(FAKE_BENCH)
        defaults = dict(default_slots())
        handles = {role: make_handle(role, self.config.models[role], client,
                                     max_tokens=65536,
                                     prompt_defaults=dict(defaults))
                   for role in ROLE_DEFAULTS}
        return StageContext(
            run_dir=self.run_dir, store=self.store, config=self.config,
            usage=None, handles=handles, workdir_root=self.run_dir / "work",
            run_settings=RunSettings(
                workers=self.workers if workers is None else workers))

    def label(self, index: int) -> str:
        return phase_label(index, self.config.sequence[index])

    def phase(self, index: int) -> PhaseInstance:
        name = self.config.sequence[index]
        return PhaseInstance(index=index, name=name, label=self.label(index),
                             spec=self.config.phases[name], task_ids=())

    def learn(self, index: int, client: Any = None,
              workers: Optional[int] = None) -> Dict[str, Any]:
        return phase_learn.run(self.context(client, workers),
                               self.phase(index)).summary

    def phase_dir(self, index: int) -> Path:
        return phase_bank_dir(self.run_dir, self.label(index))

    # -- the execute phases ----------------------------------------------------

    def manifest(self, learn_index: int) -> Dict[str, Any]:
        """The skill folder the practice after ``learn_index`` delivers:
        the published bank's active cards and the evolved playbook."""
        label = self.label(learn_index)
        bank = [Skill.from_dict(row)
                for row in load_phase_bank(self.run_dir, label)]
        active = [skill for skill in bank if skill.status == "active"]
        playbook = read_json(self.phase_dir(learn_index) / "playbook.json")
        return {
            "bank_fingerprint": "bank:test",
            "catalog_text": "\n".join(
                f"{_slug(skill.skill_id)}/SKILL.md | "
                f"{description_text(skill, 'summary_then_when_to_use')}"
                for skill in active),
            "playbook_text": playbook["text"],
            "files": [{"skill_id": skill.skill_id, "version": skill.version,
                       "filename": f"{_slug(skill.skill_id)}/SKILL.md"}
                      for skill in active],
        }

    def execute(self, index: int, trials: Sequence[Dict[str, Any]],
                manifest: Optional[Dict[str, Any]] = None) -> List[str]:
        """Store the trials' trajectories and write the phase's answer
        sheets, coverage and unit specs; returns the sheet ids."""
        label = self.label(index)
        directory = self.phase_dir(index)
        sheets_dir = directory / "answer_sheets"
        sheets_dir.mkdir(parents=True, exist_ok=True)
        coverage, specs, sheet_ids = [], {}, []
        for item in trials:
            task_id = make_task_id(FAKE_BENCH, item["task"])
            role = item["role"]
            trajectory_id = make_trajectory_id(label, task_id, role,
                                               item["attempt"])
            steps = [Step(action=action, observation=f"after {action}")
                     for action in item["actions"]]
            model = STRONG_MODEL if role == "strong" else WEAK_MODEL
            self.store.save(Trajectory(
                trajectory_id=trajectory_id, task_id=task_id,
                benchmark=FAKE_BENCH, model_role=role, model_name=model,
                attempt_index=item["attempt"], steps=steps,
                correctness=item["correct"]), "trajectories")
            unit_id = make_unit_id(phase_label=label, benchmark=FAKE_BENCH,
                                   task_ref=task_id, role=role,
                                   skills_injected=None)
            sheet_id = f"{unit_id}::{trajectory_id}"
            artifacts: Dict[str, Any] = {"trajectory_id": trajectory_id}
            if manifest is not None:
                artifacts["skill_folder"] = manifest
            record = CoverageRecord(unit_id=unit_id,
                                    attempt_index=item["attempt"],
                                    present=True, status="present")
            sheet = AnswerSheet(
                sheet_id=sheet_id, phase_label=label, coverage=record,
                execution=ExecutionRecord(
                    sheet_id=sheet_id, unit_id=unit_id, phase_label=label,
                    task_ref=task_id, role=role, arm=role, model_role=role,
                    model_name=model, sample_index=item["attempt"],
                    steps=steps, raw_correctness=item["correct"],
                    artifacts=artifacts),
                grading=GradingRecord(
                    verdict="success" if item["correct"] else "failure",
                    basis="scorer", rule_name="scorer"))
            write_json(sheets_dir / object_filename(sheet_id),
                       sheet.to_dict(), indent=2)
            coverage.append(record.to_dict())
            specs.setdefault(unit_id, UnitSpec(
                unit_id=unit_id, phase_label=label, task_ref=task_id,
                role=role, arm=role, expected_attempts=1).to_dict())
            sheet_ids.append(sheet_id)
        write_jsonl(directory / "coverage.jsonl", coverage)
        write_jsonl(directory / "unit_specs.jsonl", list(specs.values()))
        return sheet_ids


#: The three steps every hand-built trial takes after any card read.
WORK = ("inspect", "act", "finish")


def baseline_trials(strong_correct: bool = True) -> List[Dict[str, Any]]:
    """Both tasks: the strong model succeeds (``strong_correct``), the
    weak one fails."""
    return [trial(task, role, WORK,
                  correct=strong_correct and role == "strong")
            for task in ("t1", "t2") for role in ("strong", "weak")]


def run_through(run: LearnRun, last_index: int,
                practice: Optional[Callable[[int], Sequence[Dict[str, Any]]]]
                = None, *, first: int = 0, **learn: Any) -> None:
    """Drive the reference sequence from ``first`` to ``last_index``
    (inclusive): the baseline, each learn phase (``learn`` passes on to
    :meth:`LearnRun.learn`), each practice's trials from
    ``practice(index)``."""
    for index in range(first, last_index + 1):
        role = getattr(run.config.phases[run.config.sequence[index]],
                       "role", None)
        if role == "baseline":
            run.execute(index, baseline_trials())
        elif role == "practice":
            run.execute(index, practice(index),
                        manifest=run.manifest(index - 1))
        elif role == "learn":
            run.learn(index, **learn)


def snapshot(run_dir: Path) -> Dict[str, bytes]:
    """Every result file of the run, byte for byte: the request log, the
    failure ledger (it carries times) and the lock files are left out."""
    run_dir = Path(run_dir)
    out = {}
    for path in sorted(run_dir.rglob("*")):
        relative = path.relative_to(run_dir).as_posix()
        if not path.is_file() or relative.startswith("llm_requests/") \
                or relative == "failed_units.jsonl" \
                or path.name.endswith(".lock"):
            continue
        out[relative] = path.read_bytes()
    return out
