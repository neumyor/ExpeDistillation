"""The folder test phase's fixtures, shared by the final-test stage tests:
a fake plugin (optionally folder-capable, optionally one that cats skill
files), the baseline-only config and stage context, the folder test phase
instance, and a hand-written phase bank."""

import json
from pathlib import Path

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.composites import phase_bank
from expedistill.core import ids
from expedistill.core.config import (
    CompareSpec,
    ExecutePhaseSpec,
    ExperimentConfig,
    PhaseInstance,
)
from expedistill.core.ids import make_type_id
from expedistill.infra.store import Store
from expedistill.infra.usage import UsageRecorder
from expedistill.stages.context import RunSettings, StageContext
from tests.helpers.fake_llm import make_fake_handle
from tests.helpers.skills import MARKED, make_skill

FAKE = "fakebench"
TASK_IDS = ("t1", "t2", "t3")

class FolderPlugin(BenchmarkPlugin):
    """Records every run() call's task and skills block; declares the
    folder capability when asked to (the E1 guard keys on it)."""

    def __init__(self, capable=False):
        self.capable = capable
        self.calls = []

    @property
    def name(self):
        return FAKE

    def load_tasks(self, tasks_spec):
        ids = list(TASK_IDS) if tasks_spec is None else list(tasks_spec)
        return [Task(task_id=i, instruction=f"do {i}") for i in ids]

    def run(self, task, handle, skills_block, workdir):
        self.calls.append({"task": task, "skills_block": skills_block})
        handle.client.complete(handle.name, "system", task.instruction)
        return RunOutcome(
            steps=[{"action": f"exec {task.task_id}",
                    "observation": "ok"}],
            correctness=True)

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"

    @property
    def skill_folder_prompt_name(self):
        return "TB_SKILL_READ" if self.capable else None


class CattingPlugin(FolderPlugin):
    """A capable folder plugin whose with-bank units cat skill files
    inside the sandbox mount -- t1 the first manifest file, t2 both,
    t3 nothing (the bare-run shape)."""

    def run(self, task, handle, skills_block, workdir):
        self.calls.append({"task": task, "skills_block": skills_block})
        handle.client.complete(handle.name, "system", task.instruction)
        manifest = task.meta.get("skill_folder") or {}
        names = [entry["filename"] for entry in manifest.get("files", [])]
        which = {"t1": names[:1], "t2": names, "t3": []}[task.task_id]
        action = "\n".join(
            'bash_command({"duration": 5, "keystrokes": '
            f'"cat /opt/openclaw-skills/{task.task_id}/{name}\\n"}})'
            for name in which) or "bash_command({\"keystrokes\": \"ls\\n\"})"
        return RunOutcome(
            steps=[{"action": action, "observation": "ok"}],
            correctness=True)


def folder_config():
    return ExperimentConfig(
        benchmark=FAKE,
        models={"executor_strong": "big-model",
                "executor_weak": "weak-7b"},
        n_attempts_strong=1, n_attempts_weak=1,
        rules={}, sequence=["baseline"],
        phases={"baseline": {"kind": "execute", "role": "baseline",
                      "tasks": "train", "delivery": {"name": "none"},
                      "execution": {"name": "real",
                                    "arms": ["executor_strong",
                                             "executor_weak"]}}})


def folder_ctx_at(run_dir):
    """A stage context on ``run_dir`` with fake handles for every role."""
    run_dir = Path(run_dir)
    return StageContext(
        run_dir=run_dir,
        store=Store(run_dir),
        config=folder_config(),
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles={role: make_fake_handle(role)
                 for role in ("executor_strong", "executor_weak",
                              "judge")},
        workdir_root=run_dir / "workdirs",
        run_settings=RunSettings(),
    )


def folder_ctx(tmp_path):
    return folder_ctx_at(tmp_path / "run")


def folder_c_phase(compare="fresh_bare", **delivery_params):
    return PhaseInstance(
        index=2, name="test", label="p03:test",
        spec=ExecutePhaseSpec(
            role="test", tasks="test",
            delivery={"name": "folder", **delivery_params},
            execution={"name": "real_folder"},
            compare=(CompareSpec(against=compare)
                     if compare is not None else None),
            acceptance=None, kind="execute"),
        task_ids=TASK_IDS)


def skill_card(serial, status="active"):
    return make_skill(make_type_id(serial), status=status, **MARKED)


def write_bank(ctx, skills):
    directory = phase_bank.phase_bank_dir(ctx.run_dir, "p02:learn")
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / phase_bank.BANK_NAME, "w",
              encoding="utf-8") as handle:
        for skill in skills:
            handle.write(json.dumps(skill.to_dict(), ensure_ascii=False)
                         + "\n")


def trajectory_id(task_id):
    return ids.make_trajectory_id(
        "p03:test", ids.make_task_id(FAKE, task_id), "weak", 0)
