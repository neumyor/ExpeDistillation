"""Tests for benchmarks/registry.py: register/get/list, duplicate and
unknown names, wrong types. Uses toy plugins only; the real tb/searchqa/
alfworld plugins arrive in a later wave."""

import pytest

from expedistill.benchmarks.base import BenchmarkPlugin, RunOutcome, ViewRules
from expedistill.benchmarks.registry import (
    _reset_registry,
    get_plugin,
    plugin_names,
    register_plugin,
)
from expedistill.infra.reliability import CodeBugError, ConfigError


class _ToyPlugin(BenchmarkPlugin):

    def __init__(self, name="toy"):
        self._name = name

    @property
    def name(self):
        return self._name

    def load_tasks(self, tasks_spec):
        return []

    def run(self, task, handle, skills_block, workdir):
        return RunOutcome()

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "TOY_TASK"


@pytest.fixture(autouse=True)
def clean_registry():
    _reset_registry()
    yield
    _reset_registry()


def test_register_and_get_roundtrip():
    plugin = _ToyPlugin("searchqa")
    assert register_plugin(plugin) is plugin
    assert get_plugin("searchqa") is plugin
    assert plugin_names() == ["searchqa"]


def test_duplicate_name_is_code_bug():
    register_plugin(_ToyPlugin("toy"))
    with pytest.raises(CodeBugError, match="already registered"):
        register_plugin(_ToyPlugin("toy"))
    # The first registration survived.
    assert plugin_names() == ["toy"]


def test_unknown_name_is_config_error_listing_known():
    register_plugin(_ToyPlugin("tb"))
    with pytest.raises(ConfigError, match="unknown benchmark plugin 'nope'") as exc:
        get_plugin("nope")
    assert "tb" in str(exc.value)


def test_unknown_name_with_empty_registry_lists_nothing():
    with pytest.raises(ConfigError, match="nothing registered"):
        get_plugin("anything")


def test_non_plugin_object_rejected():
    with pytest.raises(CodeBugError, match="BenchmarkPlugin"):
        register_plugin("not a plugin")
    with pytest.raises(CodeBugError, match="BenchmarkPlugin"):
        register_plugin(_ToyPlugin)


def test_blank_plugin_name_rejected():
    with pytest.raises(CodeBugError, match="invalid"):
        register_plugin(_ToyPlugin("  "))
    assert plugin_names() == []


def test_non_string_lookup_is_code_bug():
    with pytest.raises(CodeBugError, match="must be a str"):
        get_plugin(123)


def test_plugin_names_sorted():
    for name in ("searchqa", "tb", "alfworld"):
        register_plugin(_ToyPlugin(name))
    assert plugin_names() == ["alfworld", "searchqa", "tb"]
    assert all(isinstance(get_plugin(name).load_tasks(None), list)
               for name in plugin_names())
