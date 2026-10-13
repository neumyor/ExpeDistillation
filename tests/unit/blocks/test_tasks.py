"""D1/D2 tests: lazy plugin loading via an in-memory stub plugin (a real
meta-path finder, so the actual import machinery -- and the plugin's
register-at-import side effect -- is exercised without any benchmark
dependency), plus the frozen-list split validation.
"""

import importlib
import importlib.abc
import importlib.machinery
import sys

import pytest

from expedistill.benchmarks.base import BenchmarkPlugin, Task, ViewRules
from expedistill.benchmarks.registry import get_plugin
from expedistill.blocks.tasks import load_tasks, split_tasks
from expedistill.infra.reliability import ConfigError


class _FakePlugin(BenchmarkPlugin):
    """Minimal plugin: enough of the interface for load_tasks to be real."""

    def __init__(self, name, tasks):
        self._name = name
        self._tasks = tasks

    @property
    def name(self):
        return self._name

    def load_tasks(self, tasks_spec):
        if tasks_spec is None:
            return list(self._tasks)
        return [t for t in self._tasks if t.task_id in tasks_spec]

    def run(self, task, handle, skills_block, workdir):
        raise NotImplementedError

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "TEST_TASK"


class _RegisteringLoader(importlib.abc.Loader):
    """Executes a stub plugin package whose import side effect registers it."""

    def __init__(self, plugin):
        self._plugin = plugin

    def create_module(self, spec):
        return None  # default module object

    def exec_module(self, module):
        from expedistill.benchmarks.registry import register_plugin
        register_plugin(self._plugin)


class _SilentLoader(importlib.abc.Loader):
    """Imports cleanly but never registers (broken plugin)."""

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        pass


class _StubFinder(importlib.abc.MetaPathFinder):
    def __init__(self, plugin_name, loader):
        self._fullname = "expedistill.benchmarks." + plugin_name
        self._spec = importlib.machinery.ModuleSpec(self._fullname, loader)

    def find_spec(self, fullname, path=None, target=None):
        return self._spec if fullname == self._fullname else None


@pytest.fixture
def stub_plugin():
    """Install a meta-path finder that serves one stub plugin package.

    Yields an installer; each test picks a unique plugin name so leftover
    registry entries cannot collide across tests.
    """
    installed = []

    def install(plugin_name, tasks, register=True):
        loader = _RegisteringLoader(_FakePlugin(plugin_name, tasks))
        if not register:
            loader = _SilentLoader()
        finder = _StubFinder(plugin_name, loader)
        sys.meta_path.insert(0, finder)
        installed.append(plugin_name)
        return finder

    yield install
    for plugin_name in installed:
        sys.modules.pop("expedistill.benchmarks." + plugin_name, None)


def _tasks(*ids):
    return [Task(task_id=i, instruction=f"do {i}") for i in ids]


# ---------------------------------------------------------------------------
# D1 load_tasks
# ---------------------------------------------------------------------------

def test_load_tasks_lazily_imports_and_registers(stub_plugin):
    stub_plugin("stubload", _tasks("t1", "t2"))
    assert "expedistill.benchmarks.stubload" not in sys.modules
    with pytest.raises(ConfigError):
        get_plugin("stubload")  # not registered until the package is imported
    tasks = load_tasks("stubload", None)
    assert "expedistill.benchmarks.stubload" in sys.modules
    assert [t.task_id for t in tasks] == ["t1", "t2"]


def test_load_tasks_passes_spec_through_to_plugin(stub_plugin):
    stub_plugin("stubspec", _tasks("t1", "t2", "t3"))
    tasks = load_tasks("stubspec", ["t1", "t3"])
    assert [t.task_id for t in tasks] == ["t1", "t3"]


def test_load_tasks_does_not_import_other_plugins(stub_plugin):
    stub_plugin("stublazy", _tasks("t1"))
    # Delta check: other test modules may already have imported real plugin
    # packages into sys.modules; what matters is that load_tasks does not
    # ADD sibling plugin packages (their deps may not even be installable).
    before = set(sys.modules)
    load_tasks("stublazy", None)
    added = set(sys.modules) - before
    assert not [
        name for name in added
        if name.startswith("expedistill.benchmarks.")
        and not name.startswith("expedistill.benchmarks.stublazy")
    ]


def test_load_tasks_unknown_plugin_is_config_error():
    with pytest.raises(ConfigError) as exc:
        load_tasks("no_such_plugin", None)
    message = str(exc.value)
    assert "no_such_plugin" in message
    assert "not installed/importable" in message


def test_load_tasks_imported_but_unregistered_plugin_is_config_error(stub_plugin):
    stub_plugin("stubsilent", _tasks("t1"), register=False)
    with pytest.raises(ConfigError) as exc:
        load_tasks("stubsilent", None)
    assert "stubsilent" in str(exc.value)


def test_load_tasks_rejects_non_string_benchmark():
    with pytest.raises(ConfigError):
        load_tasks("", None)
    with pytest.raises(ConfigError):
        load_tasks(None, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# D2 split_tasks
# ---------------------------------------------------------------------------

def test_split_full_mode_passthrough():
    tasks = _tasks("a", "b")
    train, test, reserved = split_tasks(tasks, "full", None)
    assert train == tasks and test == tasks
    assert train is not tasks  # fresh lists: mutating one side changes neither
    assert reserved == []  # full mode holds nothing back


def test_split_mode_explicit_lists():
    tasks = _tasks("a", "b", "c")
    train, test, reserved = split_tasks(
        tasks, "split", {"train": ["a", "b"], "test": ["c"]})
    assert [t.task_id for t in train] == ["a", "b"]
    assert [t.task_id for t in test] == ["c"]
    assert reserved == []


def test_split_mode_three_keys_returns_reserved():
    tasks = _tasks("a", "b", "c", "d")
    train, test, reserved = split_tasks(
        tasks, "split",
        {"train": ["a"], "test": ["b"], "reserved": ["c", "d"]})
    assert [t.task_id for t in train] == ["a"]
    assert [t.task_id for t in test] == ["b"]
    assert [t.task_id for t in reserved] == ["c", "d"]
    # Reserved tasks are in NEITHER arm, whatever the old shape would say.
    assert "c" not in [t.task_id for t in train + test]


def test_split_output_follows_input_task_order():
    # The frozen lists carry membership, not ordering.
    tasks = _tasks("a", "b", "c", "d")
    train, _, reserved = split_tasks(
        tasks, "split",
        {"train": ["c", "a"], "test": ["b"], "reserved": ["d"]})
    assert [t.task_id for t in train] == ["a", "c"]
    assert [t.task_id for t in reserved] == ["d"]


def test_split_unknown_ids():
    with pytest.raises(ConfigError, match="zz"):
        split_tasks(
            _tasks("a", "b"), "split", {"train": ["a"], "test": ["zz", "b"]})
    with pytest.raises(ConfigError, match="zz"):
        split_tasks(
            _tasks("a", "b"), "split",
            {"train": ["a"], "test": ["b"], "reserved": ["zz"]})


@pytest.mark.parametrize("spec", [
    # Every pair of lists must be disjoint, not just train/test: a task
    # listed twice across arms silently double-counts wherever it lands.
    # (Each spec covers a, b and c exactly once across the three lists
    # except for the one duplicated task.)
    pytest.param({"train": ["a", "b"], "test": ["b", "c"], "reserved": []},
                 id="train-test"),
    pytest.param({"train": ["a", "b"], "test": ["c"], "reserved": ["b"]},
                 id="train-reserved"),
    pytest.param({"train": ["a"], "test": ["b", "c"], "reserved": ["c"]},
                 id="test-reserved"),
])
def test_split_overlap_rejected(spec):
    with pytest.raises(ConfigError, match="overlap"):
        split_tasks(_tasks("a", "b", "c"), "split", spec)


def test_split_must_cover_every_task():
    with pytest.raises(ConfigError, match="missing"):
        split_tasks(
            _tasks("a", "b", "c"), "split", {"train": ["a"], "test": ["c"]})
    # A task in reserved is covered too: it is loaded, just not run -- but
    # a task missing from ALL THREE lists is still an error, reserved or
    # not.
    with pytest.raises(ConfigError, match="missing"):
        split_tasks(
            _tasks("a", "b", "c", "d"), "split",
            {"train": ["a"], "test": ["b"], "reserved": ["c"]})


def test_split_spec_shape_validated():
    tasks = _tasks("a")
    with pytest.raises(ConfigError):
        split_tasks(tasks, "split", None)
    with pytest.raises(ConfigError):
        split_tasks(tasks, "split", {"train": ["a"]})
    with pytest.raises(ConfigError):
        split_tasks(tasks, "split", ["a"])
    with pytest.raises(ConfigError):
        split_tasks(tasks, "split", {"train": "a", "test": ["a"]})
    # Exactly one of the two accepted key sets -- no extras, no missing
    # test, no ratio shorthand leaking in here (stage 0 derives that).
    with pytest.raises(ConfigError):
        split_tasks(tasks, "split", {"train": ["a"], "test": ["a"],
                                    "reserved": [], "extra": []})
    with pytest.raises(ConfigError):
        split_tasks(tasks, "split", {"train": ["a"], "reserved": ["a"]})
    with pytest.raises(ConfigError):
        split_tasks(tasks, "split", {"train": ["a"], "test": ["a"],
                                    "ratio": 0.5})


def test_split_reserved_must_be_id_list():
    with pytest.raises(ConfigError, match="reserved"):
        split_tasks(_tasks("a", "b"), "split",
                    {"train": ["a"], "test": ["b"], "reserved": "a"})
    with pytest.raises(ConfigError, match="reserved"):
        split_tasks(_tasks("a", "b"), "split",
                    {"train": ["a"], "test": ["b"], "reserved": [1]})


def test_split_unknown_mode():
    with pytest.raises(ConfigError, match="half"):
        split_tasks(_tasks("a"), "half", None)
