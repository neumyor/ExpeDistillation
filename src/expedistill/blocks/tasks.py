"""D1 task loading and D2 splitting (layer 4).

D1 loads a benchmark's tasks by lazily importing ONLY the named plugin
package. Plugin packages register themselves at import (see
``benchmarks/registry.py``), so importing one pulls in its dependencies --
and the ALFWorld plugin needs a different venv -- which is why nothing here
may import ``expedistill.benchmarks.<name>`` eagerly or touch the other
plugins.

D2 adapts the old ``evaluation/splits.py`` discipline (ExpeToSkill-merged):
a split is frozen data, not a procedure. The config carries explicit train /
test / reserved task-id lists (no ratio sampling here -- stage 0 derives the
frozen lists before the run starts, and what D2 sees is already data), and
D2 only verifies that the lists are consistent with the loaded task set:
unknown ids, overlap, and uncovered tasks are ConfigErrors, so a stale split
fails loudly instead of silently evaluating half a task set.

Protocol v2 (2026-10-13) adds ``reserved``: the frozen partition that
belongs to NEITHER arm (held back from both distillation and the test; the
TB reuse experiment keeps AgentBrew's validation slice there for dev
smokes). The key set is exactly ``{"train", "test"}`` or
``{"train", "test", "reserved"}`` -- the same contract core.config
validates at load time (different layer, defense in depth) -- and a
two-key spec means ``reserved`` is empty, never absent from the return.
"""

from __future__ import annotations

import importlib
from typing import List, Optional, Set, Tuple, Union

from expedistill.benchmarks.base import Task
from expedistill.benchmarks.registry import get_plugin
from expedistill.infra.reliability import ConfigError

#: Key sets accepted in an explicit-list ``split_spec`` (contract 3): the
#: two-key form and the three-key form with the held-back ``reserved``
#: partition. Kept in sync with core.config's ``_SPLIT_LIST_KEY_SETS`` (the
#: load-time shape check); D2 re-checks here because what reaches it is
#: data by then, not necessarily a freshly loaded config.
_TWO_KEY_SET = frozenset({"train", "test"})
_THREE_KEY_SET = frozenset({"train", "test", "reserved"})
_LIST_KEY_ORDER = ("train", "test", "reserved")

__all__ = ["load_tasks", "split_tasks"]


def load_tasks(
    plugin_name: str,
    tasks_spec: Union[None, str, List[str]],
) -> List[Task]:
    """Load a benchmark's tasks via its plugin (D1).

    Imports ``expedistill.benchmarks.<plugin_name>`` (which registers the
    plugin), then delegates to the plugin's ``load_tasks`` for interpreting
    ``tasks_spec`` (None = all tasks, list = task ids, str = path). An
    unimportable plugin is a ConfigError naming the plugin -- the config asked
    for a benchmark whose package is not installed or whose dependencies are
    missing.
    """
    if not isinstance(plugin_name, str) or not plugin_name.strip():
        raise ConfigError(
            f"benchmark name must be a non-empty str, got {plugin_name!r}")
    try:
        importlib.import_module(
            "expedistill.benchmarks." + plugin_name)
    except ImportError as exc:
        raise ConfigError(
            f"benchmark plugin {plugin_name!r} is not installed/importable "
            f"({exc})") from exc
    # A package that imported cleanly but never registered under this name
    # is the same class of problem; get_plugin's ConfigError says so.
    plugin = get_plugin(plugin_name)
    return plugin.load_tasks(tasks_spec)


def split_tasks(
    tasks: List[Task],
    mode: str,
    split_spec: Optional[dict],
) -> Tuple[List[Task], List[Task], List[Task]]:
    """Divide the loaded tasks into (train, test, reserved) (D2, contract 3).

    * ``full``: train and test are both the whole task list (the experiment
      distills on everything and re-runs with the bank) and reserved is
      empty; ``split_spec`` is the config's concern in this mode and is not
      interpreted here.
    * ``split``: ``split_spec`` must be explicit frozen lists with keys
      exactly ``{"train", "test"}`` or ``{"train", "test", "reserved"}``.
      Every list is a list of task-id strings. Unknown ids, an overlap
      between ANY two lists, and tasks missing from all three are
      ConfigErrors (with the offending ids listed). The two-key form yields
      ``reserved == []`` -- callers may rely on the third element always
      being present (stage 0 freezes all three keys even then).

    All three returned lists keep the input ``tasks`` order; the split lists
    carry membership, not ordering.
    """
    if mode == "full":
        return list(tasks), list(tasks), []
    if mode != "split":
        raise ConfigError(
            f"mode must be one of ('split', 'full'), got {mode!r}")
    key_set = frozenset(split_spec) if isinstance(split_spec, dict) else None
    if key_set not in (_TWO_KEY_SET, _THREE_KEY_SET):
        raise ConfigError(
            "split mode requires split_spec = {'train': [...], "
            "'test': [...]} or {'train': [...], 'test': [...], "
            "'reserved': [...]}, got " + repr(split_spec))
    id_lists = {
        "train": split_spec["train"],
        "test": split_spec["test"],
        # Absent in the two-key form: held back means empty, never absent.
        "reserved": split_spec.get("reserved") or [],
    }
    for name, ids in id_lists.items():
        if (not isinstance(ids, list)
                or not all(isinstance(task_id, str) for task_id in ids)):
            raise ConfigError(
                f"split[{name!r}] must be a list of task-id strings, "
                f"got {ids!r}")

    known = {task.task_id for task in tasks}
    listed: Set[str] = set()
    for ids in id_lists.values():
        listed.update(ids)
    unknown = sorted(listed - known)
    if unknown:
        raise ConfigError(
            f"split lists name task ids that were not loaded: {unknown}")
    for first, second in (("train", "test"), ("train", "reserved"),
                          ("test", "reserved")):
        overlap = sorted(set(id_lists[first]) & set(id_lists[second]))
        if overlap:
            raise ConfigError(
                f"split lists {first!r} and {second!r} overlap on: {overlap}")
    missing = sorted(known - listed)
    if missing:
        raise ConfigError(
            "train, test and reserved must cover every loaded task; "
            f"missing: {missing}")

    memberships = {
        name: set(ids) for name, ids in id_lists.items()}
    split = tuple(
        [task for task in tasks if task.task_id in memberships[name]]
        for name in _LIST_KEY_ORDER)
    return split[0], split[1], split[2]
