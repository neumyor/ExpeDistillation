"""K1: run E1 over every (task, attempt) unit, with resume and retries.

Layer 5 composite (README 1.4 K1 row): E1 executes one task once, K1 owns
everything around that -- unit enumeration, disk persistence, resume by
deterministic ID, and the B6 retry policy for infrastructure failures. Every
finished execution is stored the moment it comes back (one unit = one file,
B3's atomic write), so a crash can only lose the unit it was working on and a
resumed run redoes exactly that unit (AGENTS.md section 6 rule 3).

Pinned design decisions (see the repo docs this was specified against):

- Accounting reaches the local plugin runners through the HANDLE: K1 builds
  its working handle with ``dataclasses.replace(handle, usage=usage,
  run_dir=run_dir)`` so ``call_model``'s fallback records everything the
  runner calls through it. E1's own ``usage`` / ``run_dir`` parameters stay
  unused.
- Remote run identity (docs/decisions/tb_run_namespace_20261011.md): each
  unit's task carries two keys before ``run_one`` is called.
  ``meta["run_namespace"]`` is 10 hex of sha256 over the resolved
  ``store.run_dir``, ``role``, ``model_name``, the canonical JSON of
  ``skills_injected`` and the sha256 of the rendered ``skills_block`` (a
  regrouped bank keeps its reference but changes what the remote agent
  reads) -- identical across resumes of the same call, and
  different for every arm (stage 1 strong vs weak, the stage-6 arm stores)
  and every experiment, because trajectory ids alone do not carry the arm.
  ``meta["attempt_token"]`` is ``a{attempt:02d}``, plus ``r{retry}`` on
  infra retries (e.g. ``a03r1``). The TB plugin builds its remote RUN_ID
  from both, so arms sharing a remote root never read each other's trials.
  The caller's Task objects are never mutated -- K1 works on ``replace``
  copies.
- Resume of remote trials is the plugin's half of the contract: given the
  same (namespace, token) a resumed unit asks for the same run id, and the
  TB plugin reuses its ``result.json`` or rejoins its live runner instead of
  resubmitting; a run id that exists remotely with neither comes back as
  ``infra_error`` and is retried under the next token.
- Infra retries: an ``infra_error`` outcome is retried up to ``infra_retries``
  times per call (default 2 = 3 attempts; next attempt_token suffix per
  retry, same workdir), sleeping ``random.uniform(*infra_backoff)`` seconds
  between attempts -- the fix for the 2026-10-13 stage-6 driver meltdown,
  where 32 workers' ssh submissions tripped sshd rate limiting at dispatch
  and every worker's single zero-backoff retry burned out inside the same
  5-second storm window: a 15-45s random wait carries a retry past the
  storm, and the random draw keeps simultaneous retries from re-colliding
  in one burst. Still ``infra_error`` after that is recorded in the
  unit-failure ledger (unit id = trajectory_id) and skipped -- never a
  valid Trajectory for metrics, and never stored under its trajectory_id
  (a stored file means "done", so storing it would make resume skip a
  unit that still needs a retry).
  The retry index continues across calls: ``<workdir>/k1_retry_cursor.json``
  holds the next unused index, advanced after every infra_error, so a
  resume starts from the token that was in flight when the process died
  (rejoinable remotely) and never re-asks for a token already spent (which
  the TB plugin would refuse forever). A fresh run's tokens are still
  ``a00``, ``a00r1``, ...
- Resume trusts a stored unit only if it was produced under the same
  inputs: a stored trajectory whose ``model_role`` / ``model_name`` /
  ``skills_injected`` differ from the request is a ConfigError naming the
  unit and both values (a re-curated bank or a swapped model must go to a
  new run directory); stored files are never overwritten.
- Stopping: once any unit raises (a non-infrastructure error), no further
  unit is started; units already running finish and are stored, and each
  gated-off unit is appended to ``<run_dir>/skip_audit.jsonl`` (gate
  "error") for the resume to re-offer. A
  ConfigError / ProviderRefusalError among the failures is re-raised
  unwrapped (the first by unit order, with a note of how many units
  failed) so the CLI reports it as a configuration problem; code bugs and
  anything else stay a ParallelError with the full traceback.
- Dispatch order is attempt-major (every task's attempt 0, then every
  task's attempt 1, ...), so one task's attempts are never adjacent; the
  return value stays sorted by trajectory_id.
- Dispatch stagger: every unit sleeps ``random.uniform(0,
  dispatch_stagger)`` seconds after its worker picks it up and before it
  starts executing, spreading every wave of connections, not just the
  batch opening -- TB units run for similar durations, so completions
  re-clump and the next wave would collide all at once without it. The
  serial path (workers=1) staggers the same way. Default 0 keeps the old
  behaviour.
- Heartbeat: while units are pending a daemon thread prints one stderr
  line every ``_HEARTBEAT_SECONDS`` seconds (default 300) from a disk
  snapshot -- done / failed / skipped / in_flight / elapsed.
  ``in_flight`` is a difference (pending minus the three finished
  dispositions), so units a raise left unfinalized still count as in
  flight; next to the per-unit exception print that makes a stalled pass
  locatable, which the 2026-10-13 stage-6 stalls were not (a normally
  slow pass and a dead one looked the same from the logs).
- ``skip_unit_ids``: trajectory ids the caller has declared terminal (an
  accepted infra shortfall). They are excluded from pending -- never
  executed, never recorded as failed-to-retry -- and, being unstored, are
  absent from the return value; nothing else changes.
- Exceptions raised by ``plugin.run`` are code bugs and propagate (see
  Stopping).

K1 does not compute metrics or select F; the stages do (README 1.5). The
return value is the whole unit set as it stands in the store after this call,
sorted by trajectory_id, including trajectories a previous call stored.
"""

import dataclasses
import hashlib
import json
import math
import os
import random
import sys
import threading
import time
import traceback
import urllib.parse
from pathlib import Path
from typing import (
    Any, Collection, Dict, List, Optional, Sequence, Tuple, Union,
)

from expedistill.benchmarks.base import Task
from expedistill.blocks.execute import make_task_id, run_one
from expedistill.core.ids import make_trajectory_id
from expedistill.core.models import Trajectory
from expedistill.infra.parallel import ParallelError, run_parallel
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
    ProviderRefusalError,
    UnitFailureRecorder,
    with_retries,
)
from expedistill.infra.store import Store, append_jsonl, read_json, write_json

__all__ = ["batch_execute"]

#: Stage label written into the unit-failure ledger. K1 itself does not know
#: which pipeline stage is running (it is stage 1's executor and stage 6's
#: tester alike), so the label names the composite, not the stage.
_FAILURE_STAGE = "k1"

#: Hex digits of the run namespace: 40 bits keeps run ids short while making
#: an accidental collision between the few dozen arms sharing a remote root
#: negligible.
_NAMESPACE_HEX = 10

#: Per-unit record of the next unused infra-retry index (module docstring).
_RETRY_CURSOR = "k1_retry_cursor.json"

#: Seconds between heartbeat log lines while units are pending. A module
#: constant the loop re-reads every round, so tests can monkeypatch it.
_HEARTBEAT_SECONDS = 300.0


@dataclasses.dataclass(frozen=True)
class _Unit:
    """One pending (task, attempt) execution, resolved to its durable ID."""

    task: Task
    attempt_index: int
    trajectory_id: str


def _attempt_token(attempt_index: int, retry: int) -> str:
    """Deterministic per-execution token (see module docstring, TB rule)."""
    token = f"a{attempt_index:02d}"
    if retry:
        token += f"r{retry}"
    return token


def _canonical(value: Any) -> str:
    """Canonical JSON of a provenance value (``skills_injected``)."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise CodeBugError(
            f"skills_injected must be JSON-serialisable, got "
            f"{type(value).__name__}: {exc}") from None


def _run_namespace(store_dir: Union[str, Path], role: str, model_name: str,
                   skills_injected: Any, skills_block: str = "") -> str:
    """Deterministic digest naming one arm of one experiment remotely.

    The store dir is resolved (``realpath``) so a symlinked or relative
    path to the same run dir is the same experiment. ``skills_injected``
    must be JSON-serialisable -- it is stored in every Trajectory anyway.
    """
    payload = json.dumps(
        {
            "store_dir": os.path.realpath(str(store_dir)),
            "role": role,
            "model_name": model_name,
            "skills_injected": _canonical(skills_injected),
            "skills_block_sha256": hashlib.sha256(
                (skills_block or "").encode("utf-8")).hexdigest(),
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_NAMESPACE_HEX]


def _read_retry_cursor(path: Path) -> int:
    """Next unused retry index for the unit (0 when never retried)."""
    if not path.exists():
        return 0
    try:
        cursor = read_json(path)["next_retry"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CodeBugError(f"unreadable K1 retry cursor {path}: {exc}") from None
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise CodeBugError(f"invalid K1 retry cursor {path}: {cursor!r}")
    return cursor


def _check_stored(traj: Trajectory, *, role: str, model_name: str,
                  skills_injected: Any) -> None:
    """A stored unit is "done" only for the inputs it was produced under."""
    diffs = []
    if traj.model_role != role:
        diffs.append(f"model_role: stored {traj.model_role!r}, "
                     f"requested {role!r}")
    if traj.model_name != model_name:
        diffs.append(f"model_name: stored {traj.model_name!r}, "
                     f"requested {model_name!r}")
    if _canonical(traj.skills_injected) != _canonical(skills_injected):
        diffs.append(f"skills_injected: stored {traj.skills_injected!r}, "
                     f"requested {skills_injected!r}")
    if diffs:
        raise ConfigError(
            f"{traj.trajectory_id}: the stored unit was produced under "
            f"different inputs ({'; '.join(diffs)}); resuming would reuse "
            "it as if it were this request's result. Use a new run "
            "directory.")


def _line_count(path: Path) -> int:
    """Lines of an append-only ledger, 0 when it does not exist yet.

    Read in binary mode (never a decode error on a torn multibyte tail) and
    a torn final line counts as one line, mirroring read_jsonl's tolerance
    for an interrupted tail; the heartbeat only ever subtracts baselines
    taken the same way, so the ambiguity cancels out.
    """
    try:
        with path.open("rb") as stream:
            return sum(1 for _ in stream)
    except FileNotFoundError:
        return 0


def _reraise(error: ParallelError, pending: Sequence["_Unit"]) -> None:
    """Surface a config/provider problem unwrapped; keep bugs wrapped."""
    failures: Dict[int, BaseException] = error.failures
    config_like = (ConfigError, ProviderRefusalError)
    if not failures or not all(isinstance(exc, config_like)
                               for exc in failures.values()):
        raise error
    index = min(failures)
    first = failures[index]
    note = (f"{first} [K1: {len(failures)} unit(s) failed, first "
            f"{pending[index].trajectory_id}; no further units were started]")
    try:
        surfaced = type(first)(note)
    except Exception:  # noqa: BLE001 - an exotic constructor: keep original
        surfaced = first
    raise surfaced from error


def _execute_unit(
    unit: _Unit,
    *,
    store: Store,
    benchmark: str,
    role: str,
    model_name: str,
    run_namespace: str,
    skills_injected: Any,
    skills_block: str,
    workdir_root: Path,
    handle: Any,
    recorder: UnitFailureRecorder,
    infra_retries: int,
    infra_backoff: Tuple[float, float],
) -> Optional[Trajectory]:
    """Run one unit with B6 infra retries; save it or record its failure.

    Retries sleep ``random.uniform(*infra_backoff)`` seconds between
    attempts. Returns the stored Trajectory, or None when the unit
    exhausted its infra retries (recorded in the ledger; a later resume
    retries it because nothing was stored under its trajectory_id).
    """
    workdir = workdir_root / urllib.parse.quote(unit.trajectory_id, safe="")
    workdir.mkdir(parents=True, exist_ok=True)
    cursor_path = workdir / _RETRY_CURSOR
    first_retry = _read_retry_cursor(cursor_path)
    calls = 0

    def once() -> Trajectory:
        nonlocal calls
        retry = first_retry + calls
        calls += 1
        # A fresh copy per call: the attempt_token differs per retry and the
        # caller's Task object must stay untouched.
        task = dataclasses.replace(
            unit.task,
            meta={**unit.task.meta,
                  "attempt_token": _attempt_token(unit.attempt_index, retry),
                  "run_namespace": run_namespace},
        )
        traj = run_one(
            benchmark=benchmark,
            task=task,
            model_role=role,
            model_name=model_name,
            attempt_index=unit.attempt_index,
            skills_injected=skills_injected,
            skills_block=skills_block,
            handle=handle,
            workdir=workdir,
        )
        if traj.status == "infra_error":
            # This token is spent: the next call (in this process or a
            # resumed one) must ask for a fresh one.
            write_json(cursor_path, {"next_retry": retry + 1})
            # Same disposition as a raised InfrastructureError: retry the
            # whole unit; the work is valid, the machine or service was not.
            raise InfrastructureError(
                f"{unit.trajectory_id}: infrastructure failure: "
                f"{traj.meta.get('error', 'no error detail')}")
        return traj

    try:
        traj = with_retries(
            once,
            attempts=infra_retries + 1,
            retry_on=InfrastructureError,
            # A random draw per retry: two workers retrying at once must
            # not land on the same second (module docstring, infra
            # retries). (0.0, 0.0) keeps the old zero-backoff behaviour.
            backoff=lambda failures: random.uniform(*infra_backoff),
            on_give_up=lambda exc: recorder.record(
                unit.trajectory_id, _FAILURE_STAGE, exc, str(exc)),
        )
    except InfrastructureError:
        return None
    store.save(traj, "trajectories")
    return traj


def batch_execute(
    *,
    store: Store,
    benchmark: str,
    tasks: Sequence[Task],
    role: str,
    model_name: str,
    handle: Any,
    n_attempts: int,
    skills_injected: Any = None,
    skills_block: str = "",
    workdir_root: Union[str, Path],
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
    workers: int = 1,
    infra_retries: int = 2,
    infra_backoff: Tuple[float, float] = (15.0, 45.0),
    dispatch_stagger: float = 0.0,
    skip_unit_ids: Collection[str] = (),
) -> List[Trajectory]:
    """Execute every (task, attempt) unit, storing each Trajectory as it lands.

    Units are (task, attempt_index) for ``attempt_index in range(n_attempts)``,
    dispatched attempt-major (attempt, then raw task_id), so the same inputs
    always run the same units in the same order across runs. Units whose
    trajectory_id is already in the store are skipped (resume), as are
    ``skip_unit_ids`` (declared terminal by the caller); each pending
    unit runs through its benchmark plugin in its own workdir and is saved
    immediately on completion.

    ``handle`` is the model handle the plugin's runner calls through; the
    working handle handed to the runners is a copy carrying ``usage`` and
    ``run_dir`` so per-attempt accounting works without E1 knowing about it.
    ``workers`` threads the units through B5 (``thread=True``); once a
    unit raises, no new unit starts, and the error propagates after the
    running ones finish (ConfigError / ProviderRefusalError unwrapped, code
    bugs as ParallelError), leaving the units that finished safely stored.
    Stored units are verified against the request (module docstring).
    Infra retries sleep ``random.uniform(*infra_backoff)`` seconds between
    attempts, and each worker sleeps ``random.uniform(0,
    dispatch_stagger)`` seconds before starting a unit, so a burst of
    units -- and their retries -- cannot collide on the same seconds;
    while units are pending a heartbeat thread logs a progress line to
    stderr every ``_HEARTBEAT_SECONDS`` (pinned decisions in the module
    docstring).

    Returns every Trajectory of the unit set present in the store after this
    call (pre-existing ones included), sorted by trajectory_id. Units whose
    infra retries ran out are absent: they are recorded in
    ``<store.run_dir>/failed_units.jsonl`` and retried by the next call.
    """
    if infra_retries < 0:
        raise ValueError(f"infra_retries must be >= 0, got {infra_retries}")
    backoff_lo, backoff_hi = infra_backoff
    if not (math.isfinite(backoff_lo) and math.isfinite(backoff_hi)
            and 0 <= backoff_lo <= backoff_hi):
        raise ValueError(f"infra_backoff must be a (min, max) seconds pair "
                         f"with 0 <= min <= max and finite bounds, got "
                         f"{infra_backoff!r}")
    if not (math.isfinite(dispatch_stagger) and dispatch_stagger >= 0):
        raise ValueError(f"dispatch_stagger must be >= 0 (seconds) and "
                         f"finite, got {dispatch_stagger!r}")
    if isinstance(skip_unit_ids, (str, bytes)):
        raise CodeBugError(
            "skip_unit_ids must be a collection of trajectory ids, got a "
            f"bare string {skip_unit_ids!r}")
    skipped = frozenset(skip_unit_ids)
    units: List[_Unit] = []
    for task in sorted(tasks, key=lambda t: t.task_id):
        task_id = make_task_id(benchmark, task.task_id)
        for attempt in range(n_attempts):
            units.append(_Unit(
                task=task,
                attempt_index=attempt,
                trajectory_id=make_trajectory_id(task_id, role, attempt),
            ))
    pending: List[_Unit] = []
    for unit in units:
        if store.exists("trajectories", unit.trajectory_id):
            _check_stored(
                store.load("trajectories", unit.trajectory_id, Trajectory),
                role=role, model_name=model_name,
                skills_injected=skills_injected)
        elif unit.trajectory_id not in skipped:
            pending.append(unit)
    # Attempt-major dispatch (stable sort keeps task order within an
    # attempt): one broken task's attempts are spread across the batch.
    pending.sort(key=lambda u: u.attempt_index)
    # Dispatch observability (from the 2026-10-13 stall diagnosis): the
    # pool drains silently when the queue is gated shut, and without these
    # numbers a pass looks identical whether 32 or 200 units were
    # dispatched. The skipped count is the intersection with this call's
    # units -- a declared id matching no unit here is not a skip.
    skipped_actual = sum(1 for u in units if u.trajectory_id in skipped)
    print(f"K1 dispatch[{role}]: {len(pending)} pending units "
          f"({skipped_actual} skipped, {len(units)} total), "
          f"workers={workers}, stagger={dispatch_stagger}s, "
          f"backoff={backoff_lo}-{backoff_hi}s",
          file=sys.stderr, flush=True)
    if pending:
        working_handle = dataclasses.replace(handle, usage=usage, run_dir=run_dir)
        recorder = UnitFailureRecorder(store.run_dir)
        skip_audit_path = Path(store.run_dir) / "skip_audit.jsonl"
        namespace = _run_namespace(store.run_dir, role, model_name,
                                   skills_injected, skills_block)
        # Error stop (module docstring, Stopping): a plain Event, set the
        # moment a unit raises; after that the gate below audits every
        # queued unit instead of starting it.
        stop = threading.Event()

        def run(unit: _Unit) -> Optional[Trajectory]:
            if stop.is_set():
                append_jsonl(skip_audit_path, {
                    "unit": unit.trajectory_id, "gate": "error",
                    "time": time.time()})
                return None
            if dispatch_stagger:
                # Stagger only past the gate: a gated-off unit pays no sleep.
                time.sleep(random.uniform(0, dispatch_stagger))
            try:
                traj = _execute_unit(
                    unit,
                    store=store,
                    benchmark=benchmark,
                    role=role,
                    model_name=model_name,
                    run_namespace=namespace,
                    skills_injected=skills_injected,
                    skills_block=skills_block,
                    workdir_root=Path(workdir_root),
                    handle=working_handle,
                    recorder=recorder,
                    infra_retries=infra_retries,
                    infra_backoff=infra_backoff,
                )
            except Exception:
                stop.set()
                # The pool holds the exception until every future drains, which
                # can be hours (remote pollers with 4.5h deadlines) -- during
                # the 2026-10-13 stage-6 stalls each pass silently skipped the
                # whole queue and the real error only surfaced (if ever) much
                # later. Print it the moment it happens so the pass is
                # diagnosable while it is still running.
                print(
                    "K1 unit raised (dispatch stops, pool draining): "
                    f"{unit.trajectory_id}\n{traceback.format_exc()}",
                    file=sys.stderr, flush=True)
                raise
            return traj

        # Heartbeat (module docstring): a disk snapshot every
        # _HEARTBEAT_SECONDS while the pass runs, with zero shared mutable
        # state -- every number is recomputed from the files. The failed
        # and skipped baselines make the deltas mean "this call"; done
        # needs no baseline because pending already excluded every stored
        # unit at dispatch time.
        t0 = time.monotonic()
        baseline_failed = _line_count(recorder.path)
        baseline_skipped = _line_count(skip_audit_path)
        heartbeat_stop = threading.Event()

        def _heartbeat() -> None:
            while not heartbeat_stop.wait(_HEARTBEAT_SECONDS):
                try:
                    done = sum(1 for u in pending
                               if store.exists("trajectories",
                                               u.trajectory_id))
                    failed = _line_count(recorder.path) - baseline_failed
                    skipped_now = (_line_count(skip_audit_path)
                                  - baseline_skipped)
                    in_flight = len(pending) - done - failed - skipped_now
                    print(f"K1 heartbeat[{role}]: done={done} "
                          f"failed={failed} skipped={skipped_now} "
                          f"in_flight={in_flight} "
                          f"of {len(pending)} pending, "
                          f"elapsed={time.monotonic() - t0:.0f}s",
                          file=sys.stderr, flush=True)
                except Exception:  # noqa: BLE001 - observer never breaks the pass
                    print("K1 heartbeat: degraded (snapshot failed)",
                          file=sys.stderr, flush=True)

        threading.Thread(target=_heartbeat, daemon=True,
                         name="k1-heartbeat").start()
        try:
            run_parallel(run, pending, workers, thread=True)
        except ParallelError as error:
            _reraise(error, pending)
        finally:
            heartbeat_stop.set()
    return [store.load("trajectories", u.trajectory_id, Trajectory)
            for u in units
            if store.exists("trajectories", u.trajectory_id)]
