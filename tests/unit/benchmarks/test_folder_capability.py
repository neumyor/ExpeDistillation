"""Skill-folder M3, the plugin capability bit and its unit-level guard.

The capability is ``BenchmarkPlugin.skill_folder_prompt_name`` (None =
the plugin cannot run a folder unit; the name doubles as the folder
prompt's B2 registration, so the capability and its prompt freeze
together). Pinned here:

* the real plugins: tb, searchqa and alfworld declare it (and the name
  each names is really registered);
* E1's guard: a folder unit (the delivery's manifest on the task)
  reaching a plugin without the capability is a loud ConfigError
  naming the benchmark, never a silent plain-injection run;
* a plugin WITH the capability runs the folder unit through unchanged
  (E1 adds no folder behaviour of its own -- the plugin owns that);
* stage 0 freezes the declared folder prompt's digest and refuses an
  unregistered name.
"""

import dataclasses

import pytest

from expedistill.benchmarks.base import Task
from expedistill.blocks.execute import run_one
from expedistill.infra.reliability import ConfigError
from expedistill.llm.call import ModelHandle
from expedistill.prompts.registry import PROMPTS, digest
from expedistill.stages.stage0_freeze import _prompt_digests
from tests.helpers.plugins import ensure_plugin
from tests.helpers.fake_plugin import serve_plugin
from tests.helpers.stub_plugin import StubPlugin


_FOLDER_MANIFEST = {
    "bank_fingerprint": "bank:000000000000",
    "catalog_filename": "catalog.md",
    "catalog_text": "## Skill folder catalog (fallback)\n",
    "description_mode": "summary_then_when_to_use",
    "files": [{"skill_id": "s1", "version": 1,
               "filename": "s1/SKILL.md"}],
}


# ---------------------------------------------------------------------------
# The real plugins' declarations
# ---------------------------------------------------------------------------

_FOLDER_PROMPTS = [
    ("tb", "TB_SKILL_READ"),
    ("searchqa", "SEARCHQA_TASK_PROMPT_FOLDER"),
    ("alfworld", "ALFWORLD_TASK_PROMPT_FOLDER"),
]


@pytest.mark.parametrize("benchmark, prompt_name", _FOLDER_PROMPTS)
def test_the_plugin_declares_it_and_its_prompt_is_registered(
        benchmark, prompt_name):
    plugin = ensure_plugin(benchmark)
    assert plugin.skill_folder_prompt_name == prompt_name
    assert prompt_name in PROMPTS


# ---------------------------------------------------------------------------
# E1's unit-level guard (the fake-plugin route of test_execute.py)
# ---------------------------------------------------------------------------

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
    plugin = serve_plugin(monkeypatch, StubPlugin())
    with pytest.raises(ConfigError, match="does not support skill folders") \
            as caught:
        run_one(phase_label="p03:practice", benchmark="fakebench",
                task=_folder_task(),
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
    plugin = serve_plugin(monkeypatch, StubPlugin())
    run_one(phase_label="p03:practice", benchmark="fakebench",
            task=Task(task_id="t1", instruction="do t1"),
            model_role="weak", model_name="weak-model",
            attempt_index=0, skills_injected=None, skills_block="",
            handle=HANDLE, workdir=tmp_path)
    assert len(plugin.calls) == 1


def test_a_folder_unit_on_a_capable_plugin_reaches_run_unchanged(monkeypatch, tmp_path, _no_fakebench):
    # E1 adds no folder behaviour: a capable plugin receives the unit
    # exactly as a normal one (manifest and path ride the task's meta,
    # which is the plugin's own channel).
    plugin = serve_plugin(monkeypatch, StubPlugin(
        skill_folder_prompt_name="TB_SKILL_READ"))
    task = _folder_task()
    run_one(phase_label="p03:practice", benchmark="fakebench", task=task,
            model_role="weak", model_name="weak-model",
            attempt_index=0, skills_injected="bank:000000000000",
            skills_block="## Skill folder catalog\n",
            handle=HANDLE, workdir=tmp_path)
    assert [(c["task"], c["skills_block"]) for c in plugin.calls] == [
        (task, "## Skill folder catalog\n")]


# ---------------------------------------------------------------------------
# The stage-0 freeze of the declared folder prompt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("benchmark, prompt_name", _FOLDER_PROMPTS)
def test_stage0_freezes_the_folder_prompt_digest(benchmark, prompt_name):
    ensure_plugin(benchmark)
    digests = _prompt_digests(benchmark)
    assert digests[prompt_name] == digest(prompt_name)
    # Only the declaring plugin's folder prompt is frozen with its run.
    assert {name for _, name in _FOLDER_PROMPTS if name in digests} \
        == {prompt_name}


def test_stage0_freezes_the_tb_prompts_beside_the_folder_prompt():
    ensure_plugin("tb")
    digests = _prompt_digests("tb")
    for name in ("TB_TERMINUS_TEMPLATE", "TB_DISCIPLINE",
                 "TB_PLAYBOOK_UPDATE"):
        assert digests[name] == digest(name)


def test_stage0_refuses_an_unregistered_folder_prompt_name(
        monkeypatch):
    plugin = ensure_plugin("tb")
    monkeypatch.setattr(
        type(plugin), "skill_folder_prompt_name",
        "TB_SKILL_READ_MISSING", raising=False)
    with pytest.raises(ConfigError,
                       match="skill_folder_prompt_name"):
        _prompt_digests("tb")


def test_a_plugin_without_the_capability_freezes_no_extra_prompt(
        monkeypatch):
    plugin = ensure_plugin("searchqa")
    monkeypatch.setattr(
        type(plugin), "skill_folder_prompt_name", None, raising=False)
    monkeypatch.setattr(
        type(plugin), "prompt_pack", dataclasses.replace(
            plugin.prompt_pack, executor_prompts=("SEARCHQA_TASK_PROMPT",)))
    digests = _prompt_digests("searchqa")
    assert "SEARCHQA_TASK_PROMPT_FOLDER" not in digests
