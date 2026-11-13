"""D1 task loading and D2 splitting (layer 4).

D1 loads a benchmark's tasks by lazily importing ONLY the named plugin
package. Plugin packages register themselves at import (see
``benchmarks/registry.py``), so importing one pulls in its dependencies --
and the ALFWorld plugin needs a different venv -- which is why nothing here
may import ``expedistill.benchmarks.<name>`` eagerly or touch the other
plugins.

D2 adapts the old ``evaluation/splits.py`` discipline (ExpeToSkill-merged):
a split is frozen data, not a procedure. The config carries explicit train /
val / test / reserved task-id lists (no ratio sampling here -- stage 0
derives the frozen lists before the run starts, and what D2 sees is already
data), and D2 only verifies that the lists are consistent with the loaded
task set: unknown ids, overlap, and uncovered tasks are ConfigErrors, so a
stale split fails loudly instead of silently evaluating half a task set.

``val`` is the list a practice phase may run instead of train; ``reserved``
is held back from every phase (the TB reuse experiment keeps AgentBrew's
validation slice there for dev smokes). Both are optional -- the same key
contract core.config validates at load time (different layer, defense in
depth) -- and an absent one is empty, never absent from the return.
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
from expedistill.core.config import (
    SPLIT_LIST_KEYS,
    SPLIT_REQUIRED_KEYS,
    split_lists,
)
from expedistill.infra.reliability import ConfigError

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
    split_spec: Optional[dict],
) -> Tuple[List[Task], List[Task], List[Task], List[Task]]:
    """Divide the loaded tasks into (train, val, test, reserved) (D2).

    ``split_spec`` must be explicit frozen lists: ``train`` and ``test``
    required, ``val`` and ``reserved`` optional (D2 re-checks the key set
    because what reaches it is data by then, not necessarily a freshly
    loaded config). Every list is a list of task-id strings. Unknown ids,
    an overlap between ANY two lists, and tasks missing from all four are
    ConfigErrors (with the offending ids listed). An absent optional list
    yields ``[]`` -- callers may rely on all four elements (stage 0
    freezes all four keys).

    All four returned lists keep the input ``tasks`` order; the split lists
    carry membership, not ordering.
    """
    key_set = frozenset(split_spec) if isinstance(split_spec, dict) else None
    if key_set is None or not SPLIT_REQUIRED_KEYS <= key_set \
            or key_set - set(SPLIT_LIST_KEYS):
        raise ConfigError(
            "split_spec must give the lists 'train' and 'test' (optionally "
            "'val' and 'reserved'), got " + repr(split_spec))
    id_lists = split_lists(split_spec)
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
    for index, first in enumerate(SPLIT_LIST_KEYS):
        for second in SPLIT_LIST_KEYS[index + 1:]:
            overlap = sorted(set(id_lists[first]) & set(id_lists[second]))
            if overlap:
                raise ConfigError(
                    f"split lists {first!r} and {second!r} overlap on: "
                    f"{overlap}")
    missing = sorted(known - listed)
    if missing:
        raise ConfigError(
            "train, val, test and reserved must cover every loaded task; "
            f"missing: {missing}")

    memberships = {name: set(ids) for name, ids in id_lists.items()}
    train, val, test, reserved = (
        [task for task in tasks if task.task_id in memberships[name]]
        for name in SPLIT_LIST_KEYS)
    return train, val, test, reserved


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
