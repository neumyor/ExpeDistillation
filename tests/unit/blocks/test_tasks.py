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
    assert "is not available: importing" in message


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

def _ids(tasks):
    return [task.task_id for task in tasks]


def test_split_explicit_lists():
    tasks = _tasks("a", "b", "c")
    train, val, test, reserved = split_tasks(
        tasks, {"train": ["a", "b"], "test": ["c"]})
    assert _ids(train) == ["a", "b"]
    assert _ids(test) == ["c"]
    assert val == [] and reserved == []


def test_split_four_keys_returns_every_list():
    tasks = _tasks("a", "b", "c", "d", "e")
    train, val, test, reserved = split_tasks(
        tasks,
        {"train": ["a"], "val": ["e"], "test": ["b"],
         "reserved": ["c", "d"]})
    assert _ids(train) == ["a"]
    assert _ids(val) == ["e"]
    assert _ids(test) == ["b"]
    assert _ids(reserved) == ["c", "d"]


@pytest.mark.parametrize("key", ["val", "reserved"])
def test_split_null_optional_list_is_empty(key):
    divided = split_tasks(_tasks("a", "b"),
                          {"train": ["a"], "test": ["b"], key: None})
    assert [_ids(part) for part in divided] == [["a"], [], ["b"], []]


def test_split_output_follows_input_task_order():
    # The frozen lists carry membership, not ordering.
    tasks = _tasks("a", "b", "c", "d", "e")
    train, val, _, reserved = split_tasks(
        tasks,
        {"train": ["c", "a"], "val": ["e"], "test": ["b"],
         "reserved": ["d"]})
    assert _ids(train) == ["a", "c"]
    assert _ids(val) == ["e"]
    assert _ids(reserved) == ["d"]


@pytest.mark.parametrize("key", ["test", "val", "reserved"])
def test_split_unknown_ids(key):
    spec = {"train": ["a"], "test": ["b"]}
    spec[key] = spec.get(key, []) + ["zz"]
    with pytest.raises(ConfigError, match="zz"):
        split_tasks(_tasks("a", "b"), spec)


@pytest.mark.parametrize("first,second", [
    ("train", "val"), ("train", "test"), ("train", "reserved"),
    ("val", "test"), ("val", "reserved"), ("test", "reserved")])
def test_split_overlap_rejected(first, second):
    # Every pair of lists must be disjoint: a task listed twice silently
    # double-counts wherever it lands.
    spec = {"train": ["a"], "val": ["b"], "test": ["c"], "reserved": ["d"]}
    spec[second] = spec[second] + spec[first]
    with pytest.raises(ConfigError, match=f"'{first}' and '{second}'"):
        split_tasks(_tasks("a", "b", "c", "d"), spec)


def test_split_must_cover_every_task():
    with pytest.raises(ConfigError, match="missing"):
        split_tasks(
            _tasks("a", "b", "c"), {"train": ["a"], "test": ["c"]})
    # A task in val or reserved is covered too -- but a task missing from
    # all four lists is still an error.
    with pytest.raises(ConfigError, match="missing"):
        split_tasks(
            _tasks("a", "b", "c", "d", "e"),
            {"train": ["a"], "val": ["e"], "test": ["b"],
             "reserved": ["c"]})


def test_split_spec_shape_validated():
    tasks = _tasks("a")
    with pytest.raises(ConfigError):
        split_tasks(tasks, None)
    with pytest.raises(ConfigError):
        split_tasks(tasks, {"train": ["a"]})
    with pytest.raises(ConfigError):
        split_tasks(tasks, ["a"])
    with pytest.raises(ConfigError):
        split_tasks(tasks, {"train": "a", "test": ["a"]})
    # train and test required, val and reserved optional -- no extras, no
    # ratio shorthand leaking in here (stage 0 derives that).
    with pytest.raises(ConfigError):
        split_tasks(tasks, {"train": ["a"], "test": ["a"],
                           "reserved": [], "extra": []})
    with pytest.raises(ConfigError):
        split_tasks(tasks, {"train": ["a"], "reserved": ["a"]})
    with pytest.raises(ConfigError):
        split_tasks(tasks, {"train": ["a"], "test": ["a"],
                           "ratio": 0.5})


@pytest.mark.parametrize("key", ["val", "reserved"])
def test_split_optional_lists_must_be_id_lists(key):
    with pytest.raises(ConfigError, match=key):
        split_tasks(_tasks("a", "b"),
                    {"train": ["a"], "test": ["b"], key: "a"})
    with pytest.raises(ConfigError, match=key):
        split_tasks(_tasks("a", "b"),
                    {"train": ["a"], "test": ["b"], key: [1]})
