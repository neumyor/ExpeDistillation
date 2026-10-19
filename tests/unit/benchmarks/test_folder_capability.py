"""Skill-folder M3, the plugin capability bit and its unit-level guard.

The capability is ``BenchmarkPlugin.skill_folder_prompt_name`` (None =
the plugin cannot run a folder unit; the name doubles as the folder
prompt's B2 registration, so the capability and its prompt freeze
together). Pinned here:

* the real plugins: tb declares it (and the name it names is really
  registered), searchqa and alfworld inherit the None default;
* E1's guard: a folder unit (the delivery's manifest on the task)
  reaching a plugin without the capability is a loud ConfigError
  naming the benchmark, never a silent plain-injection run;
* a plugin WITH the capability runs the folder unit through unchanged
  (E1 adds no folder behaviour of its own -- the plugin owns that);
* stage 0 freezes the declared folder prompt's digest and refuses an
  unregistered name.
"""

import importlib
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
from expedistill.blocks.execute import run_one
from expedistill.infra.reliability import ConfigError
from expedistill.llm.call import ModelHandle
from expedistill.prompts.registry import PROMPTS, digest
from expedistill.stages.stage0_freeze import _prompt_digests
from tests.helpers.plugins import ensure_plugin


_FOLDER_MANIFEST = {
    "bank_fingerprint": "bank:000000000000",
    "catalog_filename": "catalog.md",
    "catalog_text": "## Skill folder catalog\n",
    "files": [{"skill_id": "s1", "version": 1,
               "filename": "s1.md"}],
}


# ---------------------------------------------------------------------------
# The real plugins' declarations
# ---------------------------------------------------------------------------

def test_tb_declares_the_capability_and_its_prompt_is_registered():
    plugin = ensure_plugin("tb")
    assert plugin.skill_folder_prompt_name == "TB_TASK_PROMPT_FOLDER"
    assert plugin.skill_folder_prompt_name in PROMPTS


def test_searchqa_and_alfworld_do_not_declare_it():
    # One line each of their execution logic is untouched: they inherit
    # the None default and never see a folder unit through the config
    # path (the pairing checks keep folder on the test phase; E1's guard
    # holds the boundary for direct callers).
    assert ensure_plugin("searchqa").skill_folder_prompt_name is None
    assert ensure_plugin("alfworld").skill_folder_prompt_name is None


# ---------------------------------------------------------------------------
# E1's unit-level guard (the fake-plugin route of test_execute.py)
# ---------------------------------------------------------------------------

class _Plugin(BenchmarkPlugin):

    def __init__(self, name):
        self._name = name
        self.calls = []

    @property
    def name(self):
        return self._name

    def load_tasks(self, tasks_spec):
        return []

    def run(self, task, handle, skills_block, workdir):
        self.calls.append((task, skills_block))
        return RunOutcome()

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


def _install(monkeypatch, plugin):
    fullname = f"expedistill.benchmarks.{plugin.name}"

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


@pytest.fixture()
def _no_fakebench():
    # Only the fake-plugin E1 tests below need this: "fakebench" must
    # not linger from a previous install (its registration would raise
    # on the duplicate). The real plugins stay registered -- a
    # _reset_registry here would strand their cached modules (an
    # import_module of a cached package never re-runs its registration)
    # and re-importing them would raise on their duplicate prompts.
    import expedistill.benchmarks.registry as plugin_registry
    saved = dict(plugin_registry._REGISTRY._entries)
    plugin_registry._REGISTRY._entries.pop("fakebench", None)
    yield
    plugin_registry._REGISTRY._entries.clear()
    plugin_registry._REGISTRY._entries.update(saved)


HANDLE = ModelHandle(name="weak-model", role="executor_weak", client=None)


def _folder_task():
    return Task(task_id="t1", instruction="do t1",
                meta={"skill_folder": _FOLDER_MANIFEST,
                      "skill_folder_dir": "/tmp/folder"})


def test_a_folder_unit_on_a_plugin_without_the_capability_is_config_error(monkeypatch, tmp_path, _no_fakebench):
    plugin = _install(monkeypatch, _Plugin("fakebench"))
    with pytest.raises(ConfigError, match="does not support skill folders") \
            as caught:
        run_one(benchmark="fakebench", task=_folder_task(),
                model_role="weak", model_name="weak-model",
                attempt_index=0, skills_injected=None,
                skills_block="## Skill folder catalog\n",
                handle=HANDLE, workdir=tmp_path)
    # The message names the benchmark and the reason.
    assert "fakebench" in str(caught.value)
    assert "skill_folder_prompt_name" in str(caught.value)
    assert plugin.calls == []  # refused before the plugin ran anything


def test_a_bare_unit_passes_the_guard_unchanged(
        monkeypatch, tmp_path, _no_fakebench):
    plugin = _install(monkeypatch, _Plugin("fakebench"))
    run_one(benchmark="fakebench",
            task=Task(task_id="t1", instruction="do t1"),
            model_role="weak", model_name="weak-model",
            attempt_index=0, skills_injected=None, skills_block="",
            handle=HANDLE, workdir=tmp_path)
    assert len(plugin.calls) == 1


def test_a_folder_unit_on_a_capable_plugin_reaches_run_unchanged(monkeypatch, tmp_path, _no_fakebench):
    # E1 adds no folder behaviour: a capable plugin receives the unit
    # exactly as a normal one (manifest and path ride the task's meta,
    # which is the plugin's own channel).
    class CapablePlugin(_Plugin):
        skill_folder_prompt_name = "TB_TASK_PROMPT_FOLDER"

    plugin = _install(monkeypatch, CapablePlugin("fakebench"))
    task = _folder_task()
    run_one(benchmark="fakebench", task=task,
            model_role="weak", model_name="weak-model",
            attempt_index=0, skills_injected="bank:000000000000",
            skills_block="## Skill folder catalog\n",
            handle=HANDLE, workdir=tmp_path)
    assert plugin.calls == [(task, "## Skill folder catalog\n")]


# ---------------------------------------------------------------------------
# The stage-0 freeze of the declared folder prompt
# ---------------------------------------------------------------------------

def test_stage0_freezes_the_folder_prompt_digest():
    ensure_plugin("tb")
    digests = _prompt_digests("tb")
    assert digests["TB_TASK_PROMPT_FOLDER"] == digest("TB_TASK_PROMPT_FOLDER")
    # And the plain task prompt still freezes beside it.
    assert digests["TB_TASK_PROMPT"] == digest("TB_TASK_PROMPT")


def test_stage0_refuses_an_unregistered_folder_prompt_name(
        monkeypatch):
    plugin = ensure_plugin("tb")
    monkeypatch.setattr(
        type(plugin), "skill_folder_prompt_name",
        "TB_TASK_PROMPT_MISSING", raising=False)
    with pytest.raises(ConfigError,
                       match="skill_folder_prompt_name"):
        _prompt_digests("tb")


def test_a_plugin_without_the_capability_freezes_no_extra_prompt():
    # searchqa's frozen surface is exactly the method prompts plus its
    # own task prompt -- the capability adds nothing when absent.
    ensure_plugin("searchqa")
    digests = _prompt_digests("searchqa")
    assert "TB_TASK_PROMPT_FOLDER" not in digests
