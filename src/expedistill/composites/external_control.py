"""The external-campaign control: frozen per-task scores read from a
pinned score file, zero execution (the v4 image of the old stage-6
``control_source: external_campaign`` ruling).

The old system's rule (TB-dev, user ruling 2026-10-16): the bare
control arm of the final test is NEVER re-run; the comparison reads the
existing AgentBrew campaign's numbers -- the byte-identical harness and
task set -- and the run's own summary says so (``control_source``) rather
than inventing zeros. This module is the v4 counterpart's loading half:
the C-phase driver resolves ``compare.against: external_campaign`` to
the metrics built HERE instead of reading any trajectory store.

The score file's format is the AgentBrew final archive's summary shape:

``{"tasks": {"<model>:<task>": {"n": 5, "rewards": [0.0, ...],
"success_rate": float}, ...}, "<model>_avg@5": ..., ...}``

-- one per-attempt reward (0.0 / 1.0) per trial, ``n`` trials per task.
The aligned discipline (pre-registered, matching the old external
comparison report): the control's per-task rate is the mean over the
FIRST ``n_attempts`` rewards -- the campaign archived five attempts per
task while the experiment's C phase samples the frozen weak depth, and
the aligned column of the old report was exactly "the first 3 attempts".
The full reward vectors stay in the file copied into the phase
directory, so a post-hoc read of the unaligned depth needs no rerun.

Identity checks, all BEFORE any unit dispatches (the C driver loads
the control first, exactly where the reused-phase control's
completeness is checked -- a bad file must stop the phase before the
with-bank arm spends anything):

* the file exists and its bytes hash to the frozen ``sha256`` (a
  re-exported or edited campaign file is a ConfigError naming both
  digests -- the control's identity is pinned in the frozen config);
* the campaign covers ``<model_key>:<task>`` for EVERY test task (a
  missing task is listed, never silently widened or dropped);
* every covered entry carries at least ``n_attempts`` rewards, each
  reward exactly 0 or 1 (anything else is not a success-rate archive).

The metrics use the SAME shape as
:func:`expedistill.blocks.metrics.compute_task_metrics` (attempts /
valid / success / infra per task, keyed by the STORED prefixed task
id), so the C summary's delta goes through the same
``summarize``/``final_test_delta`` pair every other control uses. An
archived trial is always a scored trial (the campaign's own verdict),
so ``valid == attempts`` and ``infra == 0`` -- the honest shape of a
control with no missing units, never a fabricated zero.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from expedistill.core.ids import make_task_id
from expedistill.infra.reliability import ConfigError

__all__ = [
    "load_external_control",
    "verify_external_control_file",
]

#: The name the C driver copies the score file under inside its phase
#: directory: the run keeps the exact bytes its control numbers came
#: from, next to the summary that consumed them.
EXTERNAL_CONTROL_NAME = "external_control.json"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_external_control_file(path: str, sha256: str) -> Path:
    """The score file exists and hashes to the pinned ``sha256``.

    Returns the resolved path; raises ConfigError naming the offending
    side (missing file / digest mismatch). Pure bytes -- no parsing,
    so the freeze step can pin the file before the C phase exists.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise ConfigError(
            f"the external campaign score file {file_path} does not "
            "exist; the external control is part of the frozen method, "
            "so the file must be in place before the run starts")
    actual = _file_sha256(file_path)
    if actual != sha256:
        raise ConfigError(
            f"the external campaign score file {file_path} hashes to "
            f"sha256:{actual} but the frozen config pins "
            f"sha256:{sha256}; the control's numbers may not drift -- "
            "re-export the original campaign file or pin the new digest "
            "in a NEW experiment config")
    return file_path


def load_external_control(
    *,
    path: str,
    sha256: str,
    model_key: str,
    benchmark: str,
    tasks: Sequence[Any],
    n_attempts: int,
) -> Tuple[Dict[str, Dict[str, int]], Dict[str, List[float]]]:
    """The external control's per-task metrics and aligned rewards.

    ``tasks`` are the test phase's Task objects (any object with a
    ``task_id``); the returned metrics are keyed by the STORED prefixed
    id (``make_task_id(benchmark, task_id)``), the same key
    ``compute_task_metrics`` uses, so the arms' task sets compare
    directly. The second return value is ``{prefixed id: [rewards the
    control consumed]}`` (the first ``n_attempts`` per task), for the
    summary's audit record. Raises ConfigError on any of the identity
    failures in the module docstring; touches no store and executes
    nothing.
    """
    if n_attempts < 1:
        raise ConfigError(
            f"the external control needs a positive aligned depth, got "
            f"{n_attempts!r}")
    file_path = verify_external_control_file(path, sha256)
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ConfigError(
            f"the external campaign score file {file_path} is not "
            f"readable JSON: {exc}; the control's identity is the "
            "frozen sha256, so the file must be the archived campaign "
            "summary, not a hand edit") from exc
    rows = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(rows, Mapping):
        raise ConfigError(
            f"the external campaign score file {file_path} has no "
            "'tasks' mapping; the control reads the AgentBrew campaign "
            "summary shape (tasks -> '<model>:<task>' -> {n, rewards})")
    metrics: Dict[str, Dict[str, int]] = {}
    rewards_used: Dict[str, List[float]] = {}
    missing: List[str] = []
    for task in tasks:
        raw_id = task.task_id
        entry = rows.get(f"{model_key}:{raw_id}")
        if not isinstance(entry, Mapping):
            missing.append(raw_id)
            continue
        rewards = entry.get("rewards")
        if not isinstance(rewards, list) or len(rewards) < n_attempts:
            raise ConfigError(
                f"the external campaign score file {file_path} archives "
                f"{len(rewards) if isinstance(rewards, list) else 'no'} "
                f"attempt(s) for {model_key}:{raw_id}, fewer than the "
                f"aligned depth {n_attempts}; the control cannot be "
                "aligned to this run's sampling depth")
        aligned = rewards[:n_attempts]
        for reward in aligned:
            if reward not in (0, 1, 0.0, 1.0) or isinstance(reward, bool):
                raise ConfigError(
                    f"the external campaign score file {file_path} has a "
                    f"non-binary reward {reward!r} for {model_key}:"
                    f"{raw_id}; the control reads success-rate archives "
                    "(0/1 per attempt), not partial scores")
        prefixed = make_task_id(benchmark, raw_id)
        success = sum(1 for reward in aligned if reward in (1, 1.0))
        metrics[prefixed] = {
            "attempts": n_attempts,
            "valid": n_attempts,
            "success": success,
            "infra": 0,
        }
        rewards_used[prefixed] = [float(reward) for reward in aligned]
    if missing:
        raise ConfigError(
            f"the external campaign score file {file_path} covers none "
            f"of {len(missing)} test task(s) under the model key "
            f"{model_key!r} ({sorted(missing)}); the control and the "
            "with-bank arm must cover the SAME task set -- align the "
            "score file or the model key with the frozen test split")
    return metrics, rewards_used


def external_control_record(
    *, spec: Mapping[str, Any], n_attempts: int,
    rewards: Mapping[str, List[float]],
) -> Dict[str, Any]:
    """The C summary's ``external_control`` audit record: the pinned
    identity (path / sha256 / model key) plus the aligned depth and the
    per-task reward vectors actually consumed, so the summary alone
    reproduces the control's numbers without the file."""
    return {
        "path": spec["path"],
        "sha256": spec["sha256"],
        "model_key": spec["model_key"],
        "n_attempts": n_attempts,
        "rewards": {task_id: list(values)
                    for task_id, values in sorted(rewards.items())},
    }
