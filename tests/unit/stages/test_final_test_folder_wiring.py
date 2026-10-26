"""Skill-folder M3, the stage wiring: materialize once per phase, attach
to the with-bank units' tasks, refuse on an incapable plugin.

The test phase is the only phase a folder unit can reach (the load-time
pairing holds it there), so ``_run_test`` is the one driver that
materializes: the folder delivery's rows share ONE phase-level manifest,
ONE ``materialize_skill_folder`` call under the phase directory serves
every unit (idempotent across resumes), and ``_plan_units`` attaches
(manifest, directory) to exactly those units' Task meta -- the plugin's
own opaque channel. The bare control arm's rows never carry the
manifest, so its units stay bare.

The fake-plugin route of tests/unit/stages/test_phase_execute.py (the real import
machinery, no network); the TB-specific upload behaviour is
tests/unit/benchmarks/test_tb_folder_sandbox.py's business.
"""

import importlib
import importlib.abc
import importlib.machinery
import json
import sys
import types
from pathlib import Path

import pytest

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.registry import _reset_registry
from expedistill.composites import phase_bank, verdicts
from expedistill.core.config import (
    CompareSpec,
    ExecutePhaseSpec,
    ExperimentConfig,
    PhaseInstance,
)
from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill
from expedistill.infra.reliability import ConfigError as InfraConfigError
from expedistill.infra.store import Store
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.fake import make_fake_handle
from expedistill.stages import phase_execute
from expedistill.stages.context import RunSettings, StageContext

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"
TASK_IDS = ("t1", "t2", "t3")

MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "2. Restart the service.",
    "[Do not use when] The status command already reports running.",
])


class FolderPlugin(BenchmarkPlugin):
    """Records every run() call's task and skills block; declares the
    folder capability when asked to (the E1 guard keys on it)."""

    def __init__(self, capable=False):
        self.capable = capable
        self.calls = []

    @property
    def name(self):
        return _FAKE

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
        return "TB_TASK_PROMPT_FOLDER" if self.capable else None


def _install_plugin(monkeypatch, plugin):
    fullname = _MODULE

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            from expedistill.benchmarks.registry import register_plugin
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
def _clean():
    _reset_registry()
    verdicts.BENCHMARK_VERDICT_RULES.setdefault(_FAKE, "scorer")
    yield
    sys.modules.pop(_MODULE, None)
    _reset_registry()
    verdicts.BENCHMARK_VERDICT_RULES.pop(_FAKE, None)


def _config():
    return ExperimentConfig(
        benchmark=_FAKE, mode="split",
        models={"executor_strong": "big-model",
                "executor_weak": "weak-7b"},
        weak_profile="a weak model",
        n_attempts_strong=1, n_attempts_weak=1,
        rules={}, sequence=["baseline"],
        phases={"baseline": {"kind": "execute", "role": "baseline",
                      "tasks": "train", "delivery": {"name": "none"},
                      "execution": {"name": "real",
                                    "arms": ["executor_strong",
                                             "executor_weak"]}}})


def _ctx(tmp_path):
    run_dir = tmp_path / "run"
    return StageContext(
        run_dir=run_dir,
        store=Store(run_dir),
        config=_config(),
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles={role: make_fake_handle(role)
                 for role in ("executor_strong", "executor_weak",
                              "examiner", "answerer", "judge", "auditor")},
        workdir_root=run_dir / "workdirs",
        run_settings=RunSettings(),
    )


def _c_phase(compare="fresh_bare"):
    return PhaseInstance(
        index=2, name="test", label="p03:test",
        spec=ExecutePhaseSpec(
            role="test", tasks="test",
            delivery={"name": "folder"},
            execution={"name": "real_folder"},
            compare=(CompareSpec(against=compare)
                     if compare is not None else None),
            acceptance=None, kind="execute"),
        task_ids=TASK_IDS)


def _group_skill(task_id, status="accepted"):
    subtask_id = make_subtask_id(
        make_trajectory_id(make_task_id(_FAKE, task_id), "strong", 0), 0)
    return Skill(skill_id=make_skill_id(subtask_id), content=MARKED,
                 subtask_id=subtask_id, source_subtasks=[subtask_id],
                 version=1, status=status)


def _write_bank(ctx, skills):
    directory = phase_bank.phase_bank_dir(ctx.run_dir, "p02:learn")
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / phase_bank.BANK_NAME, "w",
              encoding="utf-8") as handle:
        for skill in skills:
            handle.write(json.dumps(skill.to_dict(), ensure_ascii=False)
                         + "\n")


# ---------------------------------------------------------------------------
# The capable-plugin test phase
# ---------------------------------------------------------------------------

def test_c_phase_materializes_once_and_attaches_to_with_bank_units(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, FolderPlugin(capable=True))
    ctx = _ctx(tmp_path)
    skills = [_group_skill("t1"), _group_skill("t2")]
    _write_bank(ctx, skills)
    phase = _c_phase()

    result = phase_execute.run(ctx, phase).summary

    assert result["role"] == "test"
    assert len(plugin.calls) == 2 * len(TASK_IDS)  # both arms, weak x1
    with_bank = [call for call in plugin.calls
                 if call["skills_block"]]
    control = [call for call in plugin.calls
               if not call["skills_block"]]
    assert len(with_bank) == len(TASK_IDS)
    assert len(control) == len(TASK_IDS)
    # Every with-bank unit carries the manifest and the materialized
    # directory; the control units carry neither.
    manifests = {id(call["task"].meta.get("skill_folder"))
                 for call in with_bank}
    assert len(manifests) == 1  # one shared read-only manifest
    manifest = with_bank[0]["task"].meta["skill_folder"]
    folder_dir = Path(with_bank[0]["task"].meta["skill_folder_dir"])
    assert folder_dir == Path(ctx.run_dir) / "phases" / "p03_test" / "skill_folder"
    for call in control:
        assert call["task"].meta.get("skill_folder") is None
        assert call["task"].meta.get("skill_folder_dir") is None
    # The folder on disk: the catalog plus one <skill>/SKILL.md per
    # accepted skill (frontmatter plus the skill body).
    assert sorted(path.relative_to(folder_dir).as_posix()
                  for path in folder_dir.rglob("*") if path.is_file()) == \
        sorted([manifest["catalog_filename"]]
               + [entry["filename"] for entry in manifest["files"]])
    assert (folder_dir / manifest["catalog_filename"]).read_text(
        encoding="utf-8") == manifest["catalog_text"]
    for skill in skills:
        filename = manifest["files"][skills.index(skill)]["filename"]
        text = (folder_dir / filename).read_text(encoding="utf-8")
        assert text.split("\n---\n", 1)[1].lstrip("\n") == skill.content
    # The skills block handed to the plugin is the catalog (the delivery's
    # frozen row shape); the capable plugin is the one that must not
    # inject it -- the prompt test holds that side.
    for call in with_bank:
        assert call["skills_block"] == manifest["catalog_text"]


def test_a_resumed_c_phase_materializes_idempotently(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, FolderPlugin(capable=True))
    ctx = _ctx(tmp_path)
    _write_bank(ctx, [_group_skill("t1")])
    phase = _c_phase()
    phase_execute.run(ctx, phase)
    folder_dir = Path(ctx.run_dir) / "phases" / "p03_test" / "skill_folder"
    before = {path.relative_to(folder_dir).as_posix():
              (path.read_bytes(), path.stat().st_mtime_ns)
              for path in folder_dir.rglob("*") if path.is_file()}

    phase_execute.run(ctx, phase)

    after = {path.relative_to(folder_dir).as_posix():
             (path.read_bytes(), path.stat().st_mtime_ns)
             for path in folder_dir.rglob("*") if path.is_file()}
    # Same bytes AND untouched files: unchanged content is not rewritten
    # (M2's idempotency contract, held through the phase driver).
    assert before == after


def test_an_empty_accepted_bank_runs_the_folder_units_bare(
        monkeypatch, tmp_path):
    # The D9 empty-injection lesson at the folder boundary: the manifest
    # lists no files, so no folder materializes, nothing attaches, and
    # the with-bank units run with an empty skills block -- never a
    # guidance claiming skills that do not exist.
    plugin = _install_plugin(monkeypatch, FolderPlugin(capable=True))
    ctx = _ctx(tmp_path)
    _write_bank(ctx, [_group_skill("t1", status="pending")])
    phase = _c_phase()

    result = phase_execute.run(ctx, phase).summary

    assert result["complete"] is True
    for call in plugin.calls:
        assert call["skills_block"] == ""
        assert call["task"].meta.get("skill_folder") is None
    assert not (Path(ctx.run_dir) / "phases" / "p03_test"
                / "skill_folder").exists()


# ---------------------------------------------------------------------------
# The incapable-plugin refusal, through the phase driver
# ---------------------------------------------------------------------------

def test_a_folder_c_phase_on_an_incapable_plugin_stops_loudly(
        monkeypatch, tmp_path):
    plugin = _install_plugin(monkeypatch, FolderPlugin(capable=False))
    ctx = _ctx(tmp_path)
    _write_bank(ctx, [_group_skill("t1")])
    phase = _c_phase()

    with pytest.raises(InfraConfigError, match="does not support skill"):
        phase_execute.run(ctx, phase)
    assert plugin.calls == []  # refused before any unit ran


def test_an_incapable_plugin_is_refused_even_with_an_empty_accepted_bank(
        monkeypatch, tmp_path):
    # The M6 review's empty-bank gap: the per-unit E1 guard fires only
    # when a unit carries the manifest, and an empty accepted bank
    # attaches none -- without the phase-level check this configuration
    # (searchqa/alfworld + folder + empty bank) would run the whole
    # phase bare under the folder experiment's name. The phase driver
    # now refuses it BEFORE dispatch, bank or no bank.
    plugin = _install_plugin(monkeypatch, FolderPlugin(capable=False))
    ctx = _ctx(tmp_path)
    _write_bank(ctx, [_group_skill("t1", status="pending")])
    phase = _c_phase()

    with pytest.raises(InfraConfigError, match="does not support skill"):
        phase_execute.run(ctx, phase)
    assert plugin.calls == []  # nothing ran: the refusal precedes dispatch
