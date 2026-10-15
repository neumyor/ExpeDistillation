"""Unit tests for benchmarks.aux.aux_wiring (plugin contract only, no disk).

The wiring must resolve the plugin itself (import IS registration), hand
back the declared (name, scorer) pair, and answer (None, None) for a
plugin without an auxiliary metric. The strict-scorer contract violation
(declared name, default None answer) is exercised end-to-end through
stage 1 in tests/unit/stages/test_stage1.py.
"""

import importlib
import importlib.abc
import importlib.machinery
import sys
import types

import pytest

from expedistill.benchmarks.aux import aux_wiring
from expedistill.benchmarks.base import BenchmarkPlugin, RunOutcome, Task, ViewRules
from expedistill.benchmarks.registry import _reset_registry, register_plugin

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"


class _Plugin(BenchmarkPlugin):
    """The stub: declares ``aux_metric_name`` only when given one."""

    def __init__(self, aux_metric_name=None):
        self._aux_metric_name = aux_metric_name

    @property
    def name(self):
        return _FAKE

    @property
    def aux_metric_name(self):
        return self._aux_metric_name

    def load_tasks(self, tasks_spec):
        return [Task(task_id="t1", instruction="do t1")]

    def run(self, task, handle, skills_block, workdir):
        return RunOutcome(status="success")

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is module-global; a leftover registration from a
    sibling test would make the next install a duplicate-name error."""
    _reset_registry()
    yield
    _reset_registry()


def _install_plugin(monkeypatch, plugin):
    """Serve ``plugin``'s package through the real import machinery
    (same contract as tests/unit/blocks/test_execute.py)."""
    fullname = _MODULE

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


def test_aux_wiring_imports_the_plugin_package(monkeypatch):
    # The wiring must not depend on an execution having imported the plugin
    # (a pure-resume re-aggregation calls no model, but still summarizes).
    _install_plugin(monkeypatch, _Plugin(aux_metric_name="fake_aux"))
    name, scorer = aux_wiring(_FAKE)
    assert name == "fake_aux"
    assert callable(scorer)


def test_aux_wiring_without_a_declaration_is_none_none(monkeypatch):
    _install_plugin(monkeypatch, _Plugin())
    assert aux_wiring(_FAKE) == (None, None)
