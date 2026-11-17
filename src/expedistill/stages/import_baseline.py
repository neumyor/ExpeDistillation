"""Reuse an earlier run's baseline trajectories in a fresh run (layer 7).

A baseline is the expensive half of the method's input: both executors on
every train task, several samples each. When an earlier run already ran
the same baseline -- same benchmark, same two executor models, same
sampling depths, a superset of the new train list -- its trajectories can
stand in for the new run's, and the new run's baseline phase finds every
unit stored and makes no model call.

``import_baseline`` prepares the new run directory before its first
``expedistill run`` (like ``import-bank``): it reads the source run's
``manifest.json`` and its baseline trajectories
(``stage1_baseline/trajectories/``, Trajectory schema 2, ids without a
phase), rewrites each ``trajectory_id`` under the new config's first phase
label (``ids.make_trajectory_id``), and saves the records as Trajectory
schema 3 in the new run's trajectory store (``Store`` kind
``trajectories``: ``<run>/stage1_baseline/trajectories/``, where the
baseline phase looks for stored units). Every other field is copied
unchanged, with one addition: given the benchmark's plugin, the execution
facts its runner records today and the source record lacks (the plugin's
``backfill_meta``: TB's grader report, read from the verifier log the
record points to) are added to ``meta``. Only the new config's train tasks
are imported.

The identity checks run before anything is written; each failure is a
ConfigError:

* the same benchmark;
* the same two executor model names;
* the same sampling depth for both arms;
* the new train list is a subset of the source's train list (the tasks
  its baseline ran);
* every source trajectory is a schema-2 record whose fields agree with
  its id, and every (train task, role, sample) of the new config has
  exactly one.

``BASELINE_ORIGIN.md`` at the new run's root records the source, the
check results, the per-role counts and successes, the facts added to
``meta`` and how many records got each, and one sha256 over the sorted
per-file sha256 of the imported source files (the source files as read,
before any addition). After the import a
normal ``run`` treats the baseline units as done and still writes the
answer sheets, verdicts and summary; a trajectory judged invalid is
re-dispatched by the phase's ordinary ``invalid_redispatch_limit`` path.

This is a narrow tool for one product, the baseline trajectories; of the
source run it reads nothing else, and outside it only what the plugin's
``backfill_meta`` reads.
"""

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from expedistill.core import ids
from expedistill.core.config import (
    PHASE_ROLE_BASELINE,
    ExecutePhaseSpec,
    ExperimentConfig,
    load_config,
    phase_label,
)
from expedistill.core.models import (
    MODEL_ROLES,
    TRAJECTORY_SCHEMA_VERSION,
    Trajectory,
)
from expedistill.infra.manifest import MANIFEST_NAME
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import RunLock
from expedistill.infra.store import KIND_DIRS, Store, read_json

__all__ = ["BASELINE_ORIGIN_NAME", "SOURCE_TRAJECTORY_SCHEMA",
           "import_baseline"]

#: The provenance record written at the new run's root.
BASELINE_ORIGIN_NAME = "BASELINE_ORIGIN.md"

#: The trajectory schema of a source baseline: ids without a phase.
SOURCE_TRAJECTORY_SCHEMA = 2

_TRAJ_PREFIX = "traj/"

#: Executor role table key per trajectory role.
_EXECUTOR = {"strong": "executor_strong", "weak": "executor_weak"}

Key = Tuple[str, str, int]


def import_baseline(*, source_run: Any, config_path: Any,
                    run_dir: Any, plugin: Any = None) -> Dict[str, Any]:
    """Write ``source_run``'s baseline trajectories for the new config's
    train tasks into the fresh ``run_dir``; return a JSON-safe summary
    (``run_dir``, ``source_run``, ``phase_label``, ``trajectories``,
    ``by_role``, ``backfilled``, ``source_files_sha256``). ``plugin`` is
    the benchmark's plugin, whose ``backfill_meta`` adds what the source
    records lack; without it nothing is added."""
    source_run = Path(source_run).expanduser().resolve()
    run_path = Path(run_dir)
    try:
        config = load_config(str(config_path))
    except (ValueError, OSError) as exc:
        raise ConfigError(f"invalid config {config_path}: {exc}") from exc
    label = _baseline_label(config)
    source = _source_identity(source_run)
    checks = _check_identity(source, config)
    train = [ids.make_task_id(config.benchmark, task)
             for task in config.split["train"]]
    selected = _select(source_run, config, train)
    backfill = plugin.backfill_meta if plugin is not None else None
    backfilled: Dict[str, int] = {}
    trajectories = []
    for key, (_, raw) in sorted(selected.items()):
        added = dict(backfill(raw.get("meta") or {})) if backfill else {}
        for name in added:
            backfilled[name] = backfilled.get(name, 0) + 1
        trajectories.append(_rewritten(raw, label, key, added))
    digest = _files_digest([path for path, _ in selected.values()])

    with RunLock(run_path):
        _require_fresh_run_dir(run_path)
        store = Store(run_path)
        for trajectory in trajectories:
            store.save(trajectory, "trajectories")
        by_role = _by_role(trajectories)
        (run_path / BASELINE_ORIGIN_NAME).write_text(
            _render_origin(source_run=source_run, config_path=config_path,
                           label=label, checks=checks, train=train,
                           by_role=by_role, backfilled=backfilled,
                           total=len(trajectories), digest=digest),
            encoding="utf-8")
    return {
        "run_dir": str(run_path),
        "source_run": str(source_run),
        "phase_label": label,
        "trajectories": len(trajectories),
        "by_role": by_role,
        "backfilled": dict(sorted(backfilled.items())),
        "source_files_sha256": digest,
    }


# ---------------------------------------------------------------------------
# The two identities
# ---------------------------------------------------------------------------

def _baseline_label(config: ExperimentConfig) -> str:
    """The label of the new config's first phase, which must be the
    baseline the imported trajectories stand in for."""
    first = config.sequence[0]
    spec = config.phases[first]
    if not (isinstance(spec, ExecutePhaseSpec)
            and spec.role == PHASE_ROLE_BASELINE):
        raise ConfigError(
            f"the new config's first phase {first!r} is not a "
            f"{PHASE_ROLE_BASELINE!r} phase; there is no baseline to import")
    if not isinstance(config.split, dict) or "train" not in config.split:
        raise ConfigError(
            "the new config needs an explicit train list: the imported "
            "tasks are its train tasks")
    return phase_label(0, first)


def _source_identity(source_run: Path) -> Dict[str, Any]:
    """What the source manifest froze: the benchmark, the executor model
    names, the two depths and the train list."""
    path = source_run / MANIFEST_NAME
    if not path.is_file():
        raise ConfigError(
            f"the source run {source_run} has no {MANIFEST_NAME}; only a "
            "frozen run's baseline can be imported")
    manifest = read_json(path)
    config = manifest.get("config")
    split = manifest.get("split")
    if not isinstance(config, dict) or not isinstance(split, dict) \
            or not isinstance(config.get("models"), dict) \
            or not isinstance(split.get("train"), list):
        raise ConfigError(
            f"{path} carries no frozen config with a role table, or no "
            "frozen train list")
    return {
        "benchmark": manifest.get("benchmark", config.get("benchmark")),
        "models": {role: config["models"].get(_EXECUTOR[role])
                   for role in MODEL_ROLES},
        "depths": {"strong": config.get("n_attempts_strong"),
                   "weak": config.get("n_attempts_weak")},
        "train": list(split["train"]),
    }


def _depths(config: ExperimentConfig) -> Dict[str, int]:
    return {"strong": config.n_attempts_strong,
            "weak": config.n_attempts_weak}


def _check_identity(source: Mapping[str, Any],
                    config: ExperimentConfig) -> List[str]:
    """The manifest-level checks; returns one line per passed check."""
    if source["benchmark"] != config.benchmark:
        raise ConfigError(
            f"the source run is a {source['benchmark']!r} run and the new "
            f"config a {config.benchmark!r} one")
    models = {role: config.models[_EXECUTOR[role]] for role in MODEL_ROLES}
    if source["models"] != models:
        raise ConfigError(
            f"the executor models differ: the source ran {source['models']}"
            f", the new config names {models}")
    if source["depths"] != _depths(config):
        raise ConfigError(
            f"the sampling depths differ: the source ran "
            f"{source['depths']}, the new config asks for "
            f"{_depths(config)}")
    outside = sorted(set(config.split["train"]) - set(source["train"]))
    if outside:
        raise ConfigError(
            f"{len(outside)} train task(s) of the new config are not in "
            f"the source run's train list: {outside[:5]}")
    return [
        f"benchmark 相同：{config.benchmark}",
        f"执行模型相同：强 {models['strong']}，弱 {models['weak']}",
        f"采样次数相同：强 {config.n_attempts_strong}，"
        f"弱 {config.n_attempts_weak}",
        f"新配置的 train 列表（{len(config.split['train'])} 题）包含于源 run "
        f"的 train 列表（{len(source['train'])} 题）",
    ]


# ---------------------------------------------------------------------------
# The source trajectories
# ---------------------------------------------------------------------------

def _source_key(trajectory_id: str) -> Key:
    """``(task_id, role, attempt)`` of a schema-2 id
    ``traj/<task_id>/<role>/<attempt:02d>``."""
    if not trajectory_id.startswith(_TRAJ_PREFIX):
        raise ValueError(trajectory_id)
    task_id, role, attempt = \
        trajectory_id[len(_TRAJ_PREFIX):].rsplit("/", 2)
    if not task_id or role not in MODEL_ROLES \
            or not (attempt.isascii() and attempt.isdigit()) \
            or f"{int(attempt):02d}" != attempt:
        raise ValueError(trajectory_id)
    return task_id, role, int(attempt)


def _read_source(path: Path) -> Tuple[Key, Dict[str, Any]]:
    """One source record and its key; ConfigError when it is not a
    schema-2 baseline record whose fields agree with its id."""
    raw = read_json(path)
    if not isinstance(raw, dict) \
            or raw.get("schema_version") != SOURCE_TRAJECTORY_SCHEMA:
        version = raw.get("schema_version") if isinstance(raw, dict) \
            else None
        raise ConfigError(
            f"{path.name} is a schema-{version} trajectory; "
            f"import-baseline reads schema-{SOURCE_TRAJECTORY_SCHEMA} "
            "baseline records (ids without a phase)")
    try:
        key = _source_key(str(raw.get("trajectory_id")))
    except ValueError:
        raise ConfigError(
            f"{path.name} has the trajectory id "
            f"{raw.get('trajectory_id')!r}, not a schema-"
            f"{SOURCE_TRAJECTORY_SCHEMA} id") from None
    if (raw.get("task_id"), raw.get("model_role"),
            raw.get("attempt_index")) != key:
        raise ConfigError(
            f"{path.name}: the record's task, role and sample "
            f"({raw.get('task_id')!r}, {raw.get('model_role')!r}, "
            f"{raw.get('attempt_index')!r}) disagree with its id")
    return key, raw


def _select(source_run: Path, config: ExperimentConfig,
            train: Sequence[str]
            ) -> Dict[Key, Tuple[Path, Dict[str, Any]]]:
    """The source records of the new train tasks, exactly one per
    expected (task, role, sample)."""
    directory = source_run / KIND_DIRS["trajectories"]
    paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    wanted = set(train)
    depths = _depths(config)
    found: Dict[Key, List[Tuple[Path, Dict[str, Any]]]] = {}
    for path in paths:
        key, raw = _read_source(path)
        if key[0] in wanted:
            found.setdefault(key, []).append((path, raw))
    expected = {(task, role, attempt) for task in wanted
                for role in MODEL_ROLES for attempt in range(depths[role])}
    missing = sorted(expected - set(found))
    extra = sorted(set(found) - expected)
    repeated = sorted(key for key, items in found.items() if len(items) > 1)
    if missing or extra or repeated:
        raise ConfigError(
            "the source baseline does not hold exactly one trajectory per "
            "(train task, role, sample) of the new config: "
            f"{len(missing)} missing {missing[:3]}, {len(extra)} beyond the "
            f"sampling depth {extra[:3]}, {len(repeated)} repeated "
            f"{repeated[:3]}")
    for key, ((path, raw),) in found.items():
        name = config.models[_EXECUTOR[key[1]]]
        if raw.get("benchmark") != config.benchmark \
                or raw.get("model_name") != name:
            raise ConfigError(
                f"{path.name}: the record says benchmark "
                f"{raw.get('benchmark')!r} and model "
                f"{raw.get('model_name')!r}, the new config "
                f"{config.benchmark!r} and {name!r}")
    return {key: items[0] for key, items in found.items()}


def _rewritten(raw: Mapping[str, Any], label: str, key: Key,
               added: Mapping[str, Any]) -> Trajectory:
    """The record under its phase-tagged id, every other field unchanged
    but for the ``added`` meta facts, which are new keys only."""
    data = dict(raw)
    data["trajectory_id"] = ids.make_trajectory_id(label, *key)
    data["schema_version"] = TRAJECTORY_SCHEMA_VERSION
    if added:
        meta = dict(raw.get("meta") or {})
        taken = sorted(set(added) & set(meta))
        if taken:
            raise ConfigError(
                f"source trajectory {raw.get('trajectory_id')!r} already "
                f"records {taken}; a backfill adds missing facts only")
        data["meta"] = {**meta, **added}
    trajectory = Trajectory.from_dict(data)
    written = trajectory.to_dict()
    if written != data:
        changed = sorted(name for name in set(data) | set(written)
                         if written.get(name) != data.get(name))
        raise ConfigError(
            f"source trajectory {raw.get('trajectory_id')!r} does not "
            f"carry over unchanged (fields {changed}); import-baseline "
            "does not convert records")
    return trajectory


def _files_digest(paths: Sequence[Path]) -> str:
    """sha256 over the sorted per-file sha256 hex digests, one per line."""
    digests = sorted(hashlib.sha256(Path(path).read_bytes()).hexdigest()
                     for path in paths)
    return hashlib.sha256("\n".join(digests).encode("ascii")).hexdigest()


def _by_role(trajectories: Sequence[Trajectory]) -> Dict[str, Dict[str, int]]:
    return {role: {"count": sum(1 for item in trajectories
                                if item.model_role == role),
                   "successes": sum(1 for item in trajectories
                                    if item.model_role == role
                                    and item.correctness is True)}
            for role in MODEL_ROLES}


# ---------------------------------------------------------------------------
# The new run directory
# ---------------------------------------------------------------------------

def _require_fresh_run_dir(run_dir: Path) -> None:
    """Refuse a directory with a manifest, a stored trajectory or an
    earlier import record: the import happens once, before the first run
    freezes anything."""
    taken = [name for name in (MANIFEST_NAME, BASELINE_ORIGIN_NAME)
             if (run_dir / name).exists()]
    trajectories = run_dir / KIND_DIRS["trajectories"]
    if trajectories.is_dir() and any(trajectories.glob("*.json")):
        taken.append(KIND_DIRS["trajectories"])
    if taken:
        raise ConfigError(
            f"{run_dir} already holds {', '.join(taken)}; import the "
            "baseline into a new run directory")


def _render_origin(*, source_run: Path, config_path: Any, label: str,
                   checks: Sequence[str], train: Sequence[str],
                   by_role: Mapping[str, Mapping[str, int]],
                   backfilled: Mapping[str, int], total: int,
                   digest: str) -> str:
    lines = [
        "# 复用的摸底轨迹（BASELINE_ORIGIN）",
        "",
        f"- 源 run：`{source_run}`",
        f"- 新配置：`{config_path}`",
        f"- 摸底相位：`{label}`（轨迹编号按这个标签改写，其余字段原样，"
        "只在 meta 里补充下面列出的执行事实）",
        f"- 导入的训练题：{len(train)} 道",
        "",
        "## 核对结果（全部通过）",
        "",
    ]
    lines += [f"- {check}" for check in checks]
    lines += [
        f"- 源轨迹都是 schema {SOURCE_TRAJECTORY_SCHEMA}，字段与编号一致；"
        "每个（题、角色、序号）恰好一条",
        "",
        "## 逐角色条数",
        "",
        "| 角色 | 条数 | 成功数 |",
        "|---|---|---|",
    ]
    lines += [f"| {role} | {counts['count']} | {counts['successes']} |"
              for role, counts in by_role.items()]
    lines += ["", "## 补充的执行事实", ""]
    if backfilled:
        lines += ["| meta 键 | 补上的条数 | 未能补上的条数 |", "|---|---|---|"]
        lines += [f"| `{name}` | {count} | {total - count} |"
                  for name, count in sorted(backfilled.items())]
    else:
        lines.append("无（插件没有可补充的执行事实，或源轨迹都已记录）")
    lines += [
        "",
        "## 源文件摘要",
        "",
        "全部导入的源轨迹文件各算一个 sha256，排序后按行拼接再算一次 sha256：",
        "",
        f"`{digest}`",
    ]
    return "\n".join(lines) + "\n"
