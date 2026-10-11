"""D1 task loading and D2 splitting (layer 4).

D1 loads a benchmark's tasks by lazily importing ONLY the named plugin
package. Plugin packages register themselves at import (see
``benchmarks/registry.py``), so importing one pulls in its dependencies --
and the ALFWorld plugin needs a different venv -- which is why nothing here
may import ``expedistill.benchmarks.<name>`` eagerly or touch the other
plugins.

D2 adapts the old ``evaluation/splits.py`` discipline (ExpeToSkill-merged):
a split is frozen data, not a procedure. The config carries explicit train /
test task-id lists (no ratio sampling here -- stage 0 derives the frozen
lists before the run starts, and what D2 sees is already data), and D2 only
verifies that the lists are consistent with the loaded task set: unknown ids,
overlap, and uncovered tasks are ConfigErrors, so a stale split fails loudly
instead of silently evaluating half a task set.
"""

from __future__ import annotations

import importlib
from typing import List, Optional, Tuple, Union

from expedistill.benchmarks.base import Task
from expedistill.benchmarks.registry import get_plugin
from expedistill.infra.reliability import ConfigError

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
) -> Tuple[List[Task], List[Task]]:
    """Divide the loaded tasks into (train, test) according to the mode (D2).

    * ``full``: train and test are both the whole task list (the experiment
      distills on everything and re-runs with the bank); ``split_spec`` is
      the config's concern in this mode and is not interpreted here.
    * ``split``: ``split_spec`` must be explicit frozen lists
      ``{"train": [task_id, ...], "test": [task_id, ...]}``. Unknown ids,
      a train/test overlap, and tasks missing from both lists are
      ConfigErrors (with the offending ids listed).

    Both returned lists keep the input ``tasks`` order; the split lists carry
    membership, not ordering.
    """
    if mode == "full":
        return list(tasks), list(tasks)
    if mode != "split":
        raise ConfigError(
            f"mode must be one of ('split', 'full'), got {mode!r}")
    if not isinstance(split_spec, dict) or set(split_spec) != {"train", "test"}:
        raise ConfigError(
            "split mode requires split_spec = {'train': [task_id, ...], "
            "'test': [task_id, ...]}, got " + repr(split_spec))
    train_ids = split_spec["train"]
    test_ids = split_spec["test"]
    for name, ids in (("train", train_ids), ("test", test_ids)):
        if (not isinstance(ids, list)
                or not all(isinstance(task_id, str) for task_id in ids)):
            raise ConfigError(
                f"split[{name!r}] must be a list of task-id strings, "
                f"got {ids!r}")

    known = {task.task_id for task in tasks}
    listed = set(train_ids) | set(test_ids)
    unknown = sorted(listed - known)
    if unknown:
        raise ConfigError(
            f"split lists name task ids that were not loaded: {unknown}")
    overlap = sorted(set(train_ids) & set(test_ids))
    if overlap:
        raise ConfigError(
            f"train and test lists overlap on: {overlap}")
    missing = sorted(known - listed)
    if missing:
        raise ConfigError(
            "train and test must cover every loaded task; missing: "
            f"{missing}")

    train_set = set(train_ids)
    test_set = set(test_ids)
    train = [task for task in tasks if task.task_id in train_set]
    test = [task for task in tasks if task.task_id in test_set]
    return train, test
