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

import dataclasses
import enum
import hashlib
import json
import re
from pathlib import PurePath
from typing import Any, List, Optional, Sequence, Set, Tuple, Union

from expedistill.benchmarks.base import Task
from expedistill.benchmarks.registry import resolve_plugin
from expedistill.infra.reliability import ConfigError

#: Key sets accepted in an explicit-list ``split_spec`` (contract 3): the
#: two-key form and the three-key form with the held-back ``reserved``
#: partition. Kept in sync with core.config's ``_SPLIT_LIST_KEY_SETS`` (the
#: load-time shape check); D2 re-checks here because what reaches it is
#: data by then, not necessarily a freshly loaded config.
_TWO_KEY_SET = frozenset({"train", "test"})
_THREE_KEY_SET = frozenset({"train", "test", "reserved"})
_LIST_KEY_ORDER = ("train", "test", "reserved")

__all__ = ["load_tasks", "split_tasks", "tasks_digest"]


def load_tasks(
    plugin_name: str,
    tasks_spec: Union[None, str, List[str]],
) -> List[Task]:
    """Load a benchmark's tasks via its plugin (D1).

    Resolves the plugin through ``resolve_plugin`` (the one import-then-get
    step) and delegates to the plugin's ``load_tasks`` for interpreting
    ``tasks_spec`` (None = all tasks, list = task ids, str = path). An
    unimportable or unregistered plugin is a ConfigError naming the plugin.
    """
    if not isinstance(plugin_name, str) or not plugin_name.strip():
        raise ConfigError(
            f"benchmark name must be a non-empty str, got {plugin_name!r}")
    return resolve_plugin(plugin_name).load_tasks(tasks_spec)


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


# ---------------------------------------------------------------------------
# The frozen manifest's task fingerprint (moved from stage 0; the reuse
# assembler carried a third copy)
# ---------------------------------------------------------------------------

#: Memory addresses inside repr fallbacks are stripped -- a digest must
#: never depend on where an object sits.
_ADDRESS_RE = re.compile(r" at 0x[0-9a-fA-F]+")


def _canonical(value: Any) -> Any:
    """``value`` as plain JSON data, deterministically.

    Dict keys become strings, tuples lists, sets sorted lists; paths, bytes,
    enums and dataclasses map to their natural JSON form. Anything else
    falls back to its type name plus ``repr`` with memory addresses
    stripped -- deterministic, though an opaque object's own state is then
    only as visible as its repr makes it.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(
            item, sort_keys=True, ensure_ascii=False))
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": bytes(value).hex()}
    if isinstance(value, enum.Enum):
        return _canonical(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _canonical(dataclasses.asdict(value))
    kind = f"{type(value).__module__}.{type(value).__qualname__}"
    return {"__repr__": kind + ":" + _ADDRESS_RE.sub("", repr(value))}


def tasks_digest(tasks: Sequence[Task]) -> str:
    """sha256 over the tasks' id, instruction and meta, sorted by id.

    Load order does not matter; any change to an instruction or a meta value
    does. Serialised with sorted keys and no incidental whitespace (the
    old repo's ``schema._canonical_json`` discipline), after ``_canonical``
    has turned non-JSON values into deterministic JSON data.
    """
    payload = [
        {"task_id": task.task_id, "instruction": task.instruction,
         "meta": _canonical(task.meta)}
        for task in sorted(tasks, key=lambda task: task.task_id)
    ]
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
