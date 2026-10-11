"""TerminalBench plugin (layer 3): one remote Harbor rollout per ``run()``.

TB is the remote benchmark: execution happens on node29 via Harbor + the
Tencent sandbox (README 1.3, TB runner row). This plugin never runs Harbor
itself and never steps interactively; it submits one trial, waits for the
verifier, and rebuilds the stepwise trajectory from the trial directory.

Submission design (mirrors the outer ``scripts/tb21`` reference link; the
runner script there is the real remote entry point):

1. **Skills delivery**: a non-empty skills block is uploaded verbatim as
   ``<root>/skills/<run_id>/<task>/SKILL.md`` (scp) and the runner is put in
   ``TBENCH_EVAL_MODE=skills`` with ``TBENCH_SKILL_ROOT`` pointing at the
   per-run directory. The Tencent skill agent/environment reads
   ``<TBENCH_SKILL_ROOT>/<task>/SKILL.md`` and injects it into the agent's
   task prompt, so the remote agent sees the skills block as part of its
   task prompt. Bare runs (empty block) use ``TBENCH_EVAL_MODE=bare``.
2. **Launch**: one ssh round trip runs ``nohup setsid env <VARS> bash
   <runner> > <root>/logs/<run_id>.log 2>&1 < /dev/null & echo $!``. The
   runner is detached from the ssh session, so a dropped connection cannot
   kill a live trial (the 2026-09-20 supervisor-kill lesson); the echoed pid
   is tracked directly, never discovered via ``pgrep -f``.
3. **Poll**: one ssh round trip per interval checks the trial's
   ``result.json`` and, while absent, that the pid is still alive and
   still our runner (identity check, see below). A dead
   pid with no result means the runner exited early (config/adapter error)
   -> infra_error with the runner log tail. The overall deadline and the
   poll interval are configurable (env or constructor).
4. **Finish**: fetch ``result.json``, the verifier's pytest log and
   ``agent/trajectory.json`` into ``workdir``; classify; reconstruct steps.

Run ids and remote resume (docs/decisions/tb_run_namespace_20261011.md):

- The remote RUN_ID is ``ed_<task>_<run_namespace>_<attempt_token>``.
  ``task.meta["run_namespace"]`` is K1's digest of (store dir, role, model,
  injected-skills reference, sha256 of the rendered skills block) and
  ``task.meta["attempt_token"]`` its per-execution
  token, so two arms (stage 1 strong vs weak, stage 6 vs stage 1) or two
  experiments sharing one remote root never share a run id -- the jobs dir
  is shared per root and the poll globs by run id, so a shared id would
  hand one arm the other arm's ``result.json``. Without a namespace (direct
  callers) the id is ``ed_<task>_<token>``.
- At submit time the plugin writes two records of the launch conditions
  (run id, sha256 of the forwarded ``_FORWARDED_KNOBS`` values, sha256 of
  the uploaded SKILL.md): ``<root>/jobs/<run_id>.ed_meta.json`` remotely
  (uploaded before the runner starts) and
  ``<workdir>/tb_remote/<run_id>.json`` locally (plus the runner pid).
- Before submitting, one probe round trip inspects the run id remotely.
  Anything found there is trusted only if BOTH records exist and match
  this request; then a ``result.json`` is reused as is (no resubmit, no
  upload) and a result-less run is rejoined if the recorded pid is still
  alive and verifiably our runner (``/proc/<pid>/environ`` holds
  ``RUN_ID=<run_id>``; ``kill -0`` alone would accept a process that
  reused the pid). Every other case where the run id already exists
  (jobs dir, runner log -- created by the launch redirection -- or meta)
  is an ``infra_error`` and the caller retries under a fresh token, with no
  usage row (no rollout happened in this call). A run id is never
  submitted twice.
- On the deadline the plugin terminates the runner it recorded, after
  the same identity check in the same shell command -- the
  process group led by the recorded pid (``setsid`` made the runner its
  leader, so the group is exactly the runner and its Harbor children),
  falling back to the pid alone; never ``pkill``/``pgrep -f`` -- and
  records the attempt in ``meta["deadline_kill"]``.
- Timing: a reused or rejoined trial reports its real duration from
  ``result.json`` (``started_at`` / ``finished_at``), or ``None`` with
  ``source="unknown"`` when those fields are missing -- never the fetch
  time.

Verdict rules (pre-registered, from the tb21 campaign's audit_trials.py --
fixed before looking at data, outer AGENTS.md section 2):

- reward present AND the verifier log shows pytest ran (``collected N
  items``) -> success (reward > 0) / failure (reward == 0);
- reward present but the verifier log is missing or pytest never collected
  -> ``infra_error`` (the campaign's "pseudo zero": a broken verifier
  download misread as a model failure);
- reward absent + exception of the model's own making (AgentTimeoutError,
  context/output length, tmux keystroke errors) -> ``failure``;
- reward absent + anything else (sandbox/setup/verifier infra, transport
  errors, no exception at all) -> ``infra_error``.

Token accounting: Harbor's ``final_metrics`` totals are recorded through the
B7 UsageRecorder (one line per rollout, run_id as request_id); missing
totals are recorded as null, never 0 (AGENTS.md section 6 rule 5).

Remote work happens only under ``EXPEDISTILL_TB_ROOT`` (validated to sit
under ``~/niuyiming/`` or ``/data2/niuyiming/``); local scratch only under
the caller's ``workdir``.
"""

import datetime
import hashlib
import json
import os
import posixpath
import re
import shlex
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from expedistill.benchmarks.base import BenchmarkPlugin, RunOutcome, Task, ViewRules
from expedistill.benchmarks.tb.remote import ENV_ROOT, RemoteLink
from expedistill.benchmarks.tb.trajectory import reconstruct, total_usage
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
)
from expedistill.infra.store import write_json
from expedistill.prompts.registry import PromptSpec, register

__all__ = ["TBPlugin", "TB_TASK_PROMPT", "classify_result"]

# --------------------------------------------------------------------------
# B2: the TB task execution prompt.
#
# Exactly the three interface variables {instruction}, {skills_block},
# {history}; the same template serves bare and skills runs ({skills_block}
# renders empty when bare). Note the remote caveat: Harbor's Terminus agent
# composes its own prompt, so this template is the frozen protocol of what
# the remote agent effectively receives -- the skills block reaches it via
# the SKILL.md mechanism above, and the {history} slot is empty because the
# remote agent manages its own terminal history. The digest is what the
# manifest freezes; the text change discipline is the same as every prompt.
# --------------------------------------------------------------------------
TB_TASK_PROMPT = register(
    PromptSpec(
        name="TB_TASK_PROMPT",
        template="""You are an AI assistant solving a command-line task inside a Linux sandbox.
You act through a terminal: inspect the environment, run shell commands, read
their output, and iterate until the task is done.

SKILLS (lessons distilled from earlier runs; use them as references when they
apply to the current task, and ignore them when they do not):
{skills_block}
TASK:
{instruction}

TERMINAL HISTORY (most recent state last):
{history}
Decide your next action from the terminal state above. Work in small,
verifiable steps, check the effect of each command before moving on, and do
not declare the task complete until you have verified the goal against the
task description.
""",
        description=(
            "E1 TB runner: terminal-agent execution prompt; the skills slot "
            "renders empty for bare runs."
        ),
    )
)

#: The tb21 runner script on the remote host (outer scripts/tb21, mirrored at
#: /data2/niuyiming/scripts/). Override with EXPEDISTILL_TB_RUNNER.
DEFAULT_RUNNER = "/data2/niuyiming/scripts/run_tencent_tb21_smoke_niuyiming.sh"
RUNNER_ENV = "EXPEDISTILL_TB_RUNNER"

#: Poll/deadline knobs (ssh round trips are expensive: poll modestly).
POLL_ENV = "EXPEDISTILL_TB_POLL_SECONDS"
DEADLINE_ENV = "EXPEDISTILL_TB_DEADLINE_SECONDS"
DEFAULT_POLL_SECONDS = 60.0
#: Agent cap is 7200s; the default deadline adds verifier (x2) and sandbox
#: setup (360s x3) margins -> 3 hours.
DEFAULT_DEADLINE_SECONDS = 10800.0

SUBMIT_TIMEOUT_SECONDS = 120.0
POLL_TIMEOUT_SECONDS = 60.0
FETCH_TIMEOUT_SECONDS = 180.0

#: Consecutive transport failures tolerated while polling before the unit is
#: declared an infra error (one blip must not forfeit a 2-hour rollout).
MAX_POLL_FAILURES = 3

#: Runner knobs read from the LOCAL environment and forwarded verbatim to the
#: remote run (same names as the tb21 campaign.env, so the campaign file stays
#: the source of truth; nothing here is a method parameter).
_FORWARDED_KNOBS = (
    "MODEL_API_BASE",
    "TBENCH_REASONING_EFFORT",
    "TBENCH_PYPI_MIRROR",
    "TBENCH_VPC_DNS",
    "TBENCH_APT_SNAPSHOT_BULLSEYE",
    "TBENCH_APT_INTRANET_MIRROR",
    "TBENCH_VERIFIER_MULTIPLIER",
    "TBENCH_SETUP_MULTIPLIER",
    "TBENCH_VERIFIER_OVERRIDE",
)

#: TB task ids are directory names in the remote dataset; anything outside
#: this charset would corrupt the remote glob, so it is rejected up front.
_TASK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ATTEMPT_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
#: Run ids name remote files (``logs/<run_id>.log``, the runner's
#: ``configs/<run_id>.yaml``); keep them well inside a 255-byte file name.
_MAX_RUN_ID_LEN = 200

#: Local submit records (one ``<run_id>.json`` per launched run id, under
#: the unit's workdir), read back by a resumed process.
_RECORD_DIR = "tb_remote"
#: Remote twin of the submit record, next to the run's jobs dir.
_REMOTE_META_SUFFIX = ".ed_meta.json"

#: Verdict vocabulary fixed from the tb21 campaign audit (audit_trials.py):
#: exceptions that are the model's own doing vs. infrastructure. An unknown
#: exception with no reward is infrastructure, matching the campaign's
#: "unrecognized -> rerun as infra" rule.
_MODEL_EXCEPTIONS = frozenset({
    "AgentTimeoutError",
    "ContextLengthExceededError",
    "OutputLengthExceededError",
})
_TMUX_ERROR_RE = re.compile(r"tmux|send.{0,20}keys", re.IGNORECASE)

#: The campaign's pseudo-zero guard: every TB2.1 verifier is pytest, so a
#: reward claimed without pytest having collected anything is a broken
#: verifier run, not a model failure.
_PYTEST_COLLECTED_RE = re.compile(r"collected [0-9]+ item")

_REMOTE_VERIFIER_LOG = "verifier/test-stdout.txt"
_REMOTE_TRAJECTORY = "agent/trajectory.json"


def classify_result(result: Dict[str, Any],
                    verifier_log: Optional[str]) -> Tuple[str, Optional[float], Dict[str, Any]]:
    """Map a Harbor trial ``result.json`` (+ verifier log) to a RunOutcome
    status. Returns (status, reward, info) where ``info`` carries the
    exception fields for meta. Pure.
    """
    reward: Optional[float] = None
    verifier_result = result.get("verifier_result") or {}
    raw_reward = (verifier_result.get("rewards") or {}).get("reward")
    try:
        reward = float(raw_reward)
    except (TypeError, ValueError):
        reward = None
    exception = result.get("exception_info") or {}
    info: Dict[str, Any] = {
        "exception_type": exception.get("exception_type"),
        "exception_message": _short(exception.get("exception_message")),
    }
    if reward is not None:
        if verifier_log is None or not _PYTEST_COLLECTED_RE.search(verifier_log):
            # Pseudo zero: verifier artifacts missing or pytest never ran.
            return "infra_error", reward, dict(info, verdict_reason="pseudo_zero")
        return ("success" if reward > 0.0 else "failure"), reward, info
    exception_type = str(exception.get("exception_type") or "")
    if not exception_type:
        return "infra_error", None, dict(info, verdict_reason="no_reward_no_exception")
    message = str(exception.get("exception_message") or "")
    if exception_type in _MODEL_EXCEPTIONS or _TMUX_ERROR_RE.search(message):
        return "failure", None, info
    return "infra_error", None, info


def _trial_timing(result: Dict[str, Any]) -> Dict[str, Any]:
    """The trial's own start/finish from Harbor's ``result.json`` (ISO
    timestamps); duration None and source "unknown" when absent."""
    def parse(value: Any) -> Optional[float]:
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            stamp = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=datetime.timezone.utc)
        return stamp.timestamp()

    begin = parse(result.get("started_at"))
    end = parse(result.get("finished_at"))
    if begin is None or end is None:
        return {"started_at": begin, "finished_at": end, "duration_s": None,
                "source": "unknown"}
    return {"started_at": begin, "finished_at": end,
            "duration_s": end - begin, "source": "result.json"}


def _short(value: Any, limit: int = 400) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if len(text) > limit:
        text = text[:limit - 3] + "..."
    return text


def _env_float(name: str, default: float, override: Optional[float]) -> float:
    """Constructor override wins, then the environment, then the default."""
    if override is not None:
        return float(override)
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value!r}")
    return value


def _q(value: str) -> str:
    return shlex.quote(str(value))


def _ours(pid: int, run_id: str) -> str:
    """Shell condition: ``pid`` is alive AND is our runner for ``run_id``.

    A bare ``kill -0`` cannot tell our runner from an unrelated process that
    reused its pid. The runner's initial environment (``env RUN_ID=...``
    before ``exec bash``) is in ``/proc/<pid>/environ``; anything that
    cannot be verified -- no /proc, no permission, a different RUN_ID --
    counts as NOT ours (dead for polling, stale for resume, never killed).
    """
    return (f"tr '\\0' '\\n' < /proc/{int(pid)}/environ 2>/dev/null "
            f"| grep -qxF {_q('RUN_ID=' + run_id)}")


class TBPlugin(BenchmarkPlugin):
    """TerminalBench: delegate one rollout to remote Harbor, then rebuild
    the trajectory from the trial directory."""

    name = "tb"
    task_prompt_name = "TB_TASK_PROMPT"

    def __init__(self,
                 *,
                 link: Optional[RemoteLink] = None,
                 usage_recorder: Any = None,
                 poll_seconds: Optional[float] = None,
                 deadline_seconds: Optional[float] = None,
                 runner_script: Optional[str] = None) -> None:
        # Everything deferred: importing the plugin package instantiates
        # this, and import must be side-effect-safe (no env reads, which
        # could raise on a misconfigured operator shell).
        self._link = link
        self.usage_recorder = usage_recorder
        self._poll_seconds = poll_seconds
        self._deadline_seconds = deadline_seconds
        self._runner_script = runner_script
        self._root: Optional[str] = None
        self._view_rules = ViewRules(max_obs_chars=8000, head_keep=1000,
                                     tail_keep=1000)

    # ------------------------------------------------------------------
    # BenchmarkPlugin interface
    # ------------------------------------------------------------------

    @property
    def view_rules(self) -> ViewRules:
        """Terminal output can be a full command dump; a larger middle cut
        than the default keeps prompts useful without hiding truncation."""
        return self._view_rules

    def load_tasks(self, tasks_spec) -> List[Task]:
        """TB instructions live in the remote task directory, so a Task here
        carries only the task NAME; the remote run reads the instruction.

        ``None`` is rejected: the TB task set is an experiment input, never
        "all 89 tasks by accident". A ``list[str]`` is task names; a ``str``
        is a path to a text file, one task name per line (``#`` comments and
        blank lines allowed).
        """
        names: List[str]
        if tasks_spec is None:
            raise ConfigError(
                "TB needs an explicit task list (a list of task names or a "
                "path to a task file); passing None would silently mean the "
                "whole 89-task set. See .env.example / configs/.")
        if isinstance(tasks_spec, str):
            path = Path(tasks_spec).expanduser()
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ConfigError(f"cannot read TB task file {tasks_spec}: {exc}") from None
            names = [line.split("#", 1)[0].strip()
                     for line in text.splitlines()]
            names = [name for name in names if name]
        elif isinstance(tasks_spec, (list, tuple)):
            names = [str(name) for name in tasks_spec]
        else:
            raise CodeBugError(
                f"tasks_spec must be None, str or a list, got "
                f"{type(tasks_spec).__name__}")
        if not names:
            raise ConfigError("TB task list is empty")
        for name in names:
            if not _TASK_NAME_RE.match(name):
                raise ConfigError(
                    f"invalid TB task name {name!r}: must match "
                    f"{_TASK_NAME_RE.pattern}")
        return [Task(task_id=name, instruction=name, meta={"task_name": name})
                for name in names]

    def run(self, task: Task, handle: Any, skills_block: str,
            workdir: Any) -> RunOutcome:
        """One task, one remote Harbor trial. Infra failures come back as
        ``RunOutcome(status="infra_error")``; ConfigError/CodeBugError
        propagate (they stop the phase, B6).
        """
        task_name = self._task_name(task)
        model_name = self._model_name(handle)
        skills_block = skills_block or ""
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        link = self._link if self._link is not None else RemoteLink()
        run_id = self._make_run_id(task_name, task)
        poll_seconds = _env_float(POLL_ENV, DEFAULT_POLL_SECONDS,
                                  self._poll_seconds)
        deadline_seconds = _env_float(DEADLINE_ENV, DEFAULT_DEADLINE_SECONDS,
                                     self._deadline_seconds)
        started = time.time()
        launched = False
        meta: Dict[str, Any] = {
            "benchmark": "tb",
            "task_name": task_name,
            "model": model_name,
            "run_id": run_id,
        }
        try:
            # Root resolution and everything after it goes over the wire, so
            # a transport failure anywhere below is an infra_error outcome,
            # never a raise (B6 retries infra, it never stops the phase).
            root = self._remote_root(link)
            meta["remote_root"] = root
            meta["jobs_dir"] = f"{root}/jobs"
            eval_mode, skill_meta = self._skill_plan(root, run_id, task_name,
                                                     skills_block)
            meta.update(skill_meta)
            record_file = workdir / _RECORD_DIR / f"{run_id}.json"
            conditions = self._conditions(run_id, eval_mode, skills_block)
            record = self._read_record(record_file, run_id)
            recorded_pid = record.get("pid") if record else None
            state, found, remote_meta = self._probe(
                link, root, run_id, task_name, recorded_pid)
            if state != "absent":
                problem = self._untrusted(state, record, remote_meta,
                                          conditions, recorded_pid)
                if problem is not None:
                    return self._stale(link, meta, workdir, run_id,
                                       recorded_pid, problem)
            if state == "result":
                # Finished earlier (by this unit before a crash): reuse it.
                meta["resumed"] = "result"
                meta["remote_pid"] = recorded_pid
                return self._finish(link, workdir, meta, found, started, 0,
                                    handle, run_id, task_name)
            if state == "alive":
                meta["resumed"] = "alive"
                pid = int(recorded_pid)
            else:
                meta["resumed"] = None
                self._upload_launch_files(link, root, run_id, skill_meta,
                                          task_name, skills_block, workdir,
                                          conditions)
                pid = self._submit(link, root, run_id, task_name, model_name,
                                   eval_mode,
                                   skill_meta.get("remote_skill_root"))
                write_json(record_file, dict(conditions, pid=pid))
            launched = True
            meta["remote_pid"] = pid
            outcome, polls = self._poll(link, meta["jobs_dir"], run_id,
                                        task_name, pid,
                                        poll_seconds, deadline_seconds)
            if outcome[0] == "result":
                return self._finish(link, workdir, meta, outcome[1],
                                    started, polls, handle, run_id, task_name)
            # deadline / dead runner: infra_error with whatever partial
            # trajectory the trial directory already holds.
            meta["polls"] = polls
            if outcome[0] == "deadline":
                meta["deadline_kill"] = self._kill_runner(link, pid, run_id)
            self._augment_partial(link, meta, workdir)
            if outcome[0] == "deadline":
                exc = InfrastructureError(
                    f"TB rollout {run_id} exceeded the {deadline_seconds:g}s "
                    "deadline without result.json")
            else:
                exc = InfrastructureError(
                    f"remote runner for {run_id} (pid {pid}) exited without "
                    "producing result.json")
            result = RunOutcome.infra_error(meta.pop("partial_steps", []), exc)
            result.meta.update(meta)
            self._record_usage(handle, run_id, task_name, (None, None),
                               "infra_error")
            return result
        except InfrastructureError as exc:
            # Transport failure (ssh timeout, scp error). Nothing was fetched,
            # so there are no partial steps to keep; a usage row only if a
            # rollout was actually launched or rejoined.
            result = RunOutcome.infra_error([], exc)
            result.meta.update(meta)
            if launched:
                self._record_usage(handle, run_id, task_name, (None, None),
                                   "infra_error")
            return result

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    def _task_name(self, task: Task) -> str:
        name = task.meta.get("task_name") if isinstance(task.meta, dict) else None
        name = str(name or task.task_id)
        if not _TASK_NAME_RE.match(name):
            raise ConfigError(
                f"invalid TB task name {name!r}: must match "
                f"{_TASK_NAME_RE.pattern}")
        return name

    def _model_name(self, handle: Any) -> str:
        name = getattr(handle, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise CodeBugError(
                f"TB runner needs handle.name (the model id), got {handle!r}")
        return name

    def _make_run_id(self, task_name: str, task: Task) -> str:
        """``ed_<task>_<namespace>_<attempt token>``, or ``ed_<task>_<token>``
        when the task carries no ``run_namespace``. The token defaults to
        timestamp + random suffix; K1 passes an explicit one via
        ``task.meta["attempt_token"]`` and the arm/experiment digest via
        ``task.meta["run_namespace"]`` so run ids stay deterministic,
        resumable, and distinct across arms (module docstring)."""
        meta = task.meta if isinstance(task.meta, dict) else {}
        token = meta.get("attempt_token")
        if token is None:
            token = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        token = str(token)
        if not _ATTEMPT_TOKEN_RE.match(token):
            raise ConfigError(
                f"attempt_token {token!r} must match {_ATTEMPT_TOKEN_RE.pattern}")
        namespace = meta.get("run_namespace")
        if namespace is None:
            run_id = f"ed_{task_name}_{token}"
        else:
            namespace = str(namespace)
            if not _ATTEMPT_TOKEN_RE.match(namespace):
                raise ConfigError(
                    f"run_namespace {namespace!r} must match "
                    f"{_ATTEMPT_TOKEN_RE.pattern}")
            run_id = f"ed_{task_name}_{namespace}_{token}"
        if len(run_id) > _MAX_RUN_ID_LEN:
            raise ConfigError(
                f"TB run id {run_id!r} is longer than {_MAX_RUN_ID_LEN} "
                "characters; shorten the task name or attempt token")
        return run_id

    def _remote_root(self, link: RemoteLink) -> str:
        """Resolve ``~`` in the configured root and enforce the allowed
        prefixes (outer AGENTS.md section 9). One extra ssh round trip per
        plugin instance: the validation needs $HOME either way."""
        if self._root is not None:
            return self._root
        out, err, rc = link.run('printf %s "$HOME"', timeout=POLL_TIMEOUT_SECONDS)
        if rc != 0 or not out.strip():
            raise InfrastructureError(
                f"cannot resolve $HOME on {link.host} (rc={rc})")
        home = out.strip()
        root = link.root
        if root.startswith("~"):
            root = home + root[1:]
        root = root.rstrip("/") or "/"
        home_root = home.rstrip("/") + "/niuyiming"
        if not (root == home_root or root.startswith(home_root + "/")
                or root == "/data2/niuyiming"
                or root.startswith("/data2/niuyiming/")):
            raise ConfigError(
                f"{ENV_ROOT} ({root}) must live under ~/niuyiming/ or "
                "/data2/niuyiming/ (outer AGENTS.md section 9)")
        self._root = root
        return root

    def _skill_plan(self, root: str, run_id: str, task_name: str,
                    skills_block: str) -> Tuple[str, Dict[str, Any]]:
        """The eval mode and where the run's SKILL.md lives remotely. Pure:
        a reused or rejoined run reports the same provenance without
        uploading anything."""
        if not skills_block.strip():
            return "bare", {"eval_mode": "bare", "skills_injected": False}
        remote_dir = f"{root}/skills/{run_id}"
        return "skills", {
            "eval_mode": "skills",
            "skills_injected": True,
            "remote_skill_root": remote_dir,
            "remote_skill_file": f"{remote_dir}/{task_name}/SKILL.md",
        }

    def _upload_launch_files(self, link: RemoteLink, root: str, run_id: str,
                             skill_meta: Dict[str, Any], task_name: str,
                             skills_block: str, workdir: Path,
                             conditions: Dict[str, Any]) -> None:
        """Fresh launches only: upload the skills block as the run's
        SKILL.md (the D9 block verbatim; the remote skill agent wraps it into
        the agent's task prompt), then the remote submit meta -- last, so a
        meta on the remote side means everything else is already there."""
        dirs = [f"{root}/jobs"]
        if skill_meta["eval_mode"] == "skills":
            dirs.append(f"{skill_meta['remote_skill_root']}/{task_name}")
        out, err, rc = link.run(
            "mkdir -p " + " ".join(_q(d) for d in dirs),
            timeout=SUBMIT_TIMEOUT_SECONDS)
        if rc != 0:
            raise InfrastructureError(
                f"remote mkdir for TB run {run_id} failed (rc={rc}): "
                f"{_short(err)}")
        if skill_meta["eval_mode"] == "skills":
            local = workdir / "skills" / task_name / "SKILL.md"
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(skills_block, encoding="utf-8")
            link.put(str(local), skill_meta["remote_skill_file"],
                     timeout=SUBMIT_TIMEOUT_SECONDS)
        local_meta = workdir / _RECORD_DIR / f"{run_id}{_REMOTE_META_SUFFIX}"
        write_json(local_meta, conditions)
        link.put(str(local_meta),
                 f"{root}/jobs/{run_id}{_REMOTE_META_SUFFIX}",
                 timeout=SUBMIT_TIMEOUT_SECONDS)

    # ------------------------------------------------------------------
    # Remote resume
    # ------------------------------------------------------------------

    def _conditions(self, run_id: str, eval_mode: str,
                    skills_block: str) -> Dict[str, Any]:
        """What the launch of ``run_id`` depends on beyond the run id:
        the forwarded runner knobs and the SKILL.md content."""
        knobs = {knob: os.environ.get(knob) or "" for knob in _FORWARDED_KNOBS}
        env_digest = hashlib.sha256(json.dumps(
            knobs, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        skill_digest = (hashlib.sha256(skills_block.encode("utf-8")).hexdigest()
                        if eval_mode == "skills" else None)
        return {
            "run_id": run_id,
            "env_sha256": env_digest.hexdigest(),
            "skill_sha256": skill_digest,
        }

    def _read_record(self, record_file: Path,
                     run_id: str) -> Optional[Dict[str, Any]]:
        """This unit's submit record for ``run_id``, if it has a valid one.
        An unreadable record counts as none: whatever is remote is then
        treated as stale, never reused and never resubmitted into."""
        try:
            record = json.loads(record_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or record.get("run_id") != run_id:
            return None
        pid = record.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return None
        return record

    def _probe(self, link: RemoteLink, root: str, run_id: str,
               task_name: str, recorded_pid: Optional[int],
               ) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
        """One round trip: what does the remote side hold for ``run_id``?

        Returns ``(state, result_path, remote_meta)``. ``state`` is
        ``"result"`` for a finished trial, ``"alive"`` when the run exists
        and the recorded pid still runs, ``"stale"`` when it exists
        otherwise, and ``"absent"`` when nothing was ever launched or
        uploaded under this id. "Exists" is the jobs dir, the runner log
        (the launch's own redirection creates it before the pid is echoed,
        so a launch whose pid never made it into the local record still
        counts) or the remote meta. ``remote_meta`` is the parsed
        ``<run_id>.ed_meta.json`` (None when missing or unparsable).
        """
        jobs_run = f"{root}/jobs/{run_id}"
        glob = f"{_q(jobs_run)}/{_q(task_name)}__*/result.json"
        log_path = f"{root}/logs/{run_id}.log"
        meta_path = f"{root}/jobs/{run_id}{_REMOTE_META_SUFFIX}"
        if recorded_pid is not None:
            existing = (f"if {_ours(recorded_pid, run_id)}; then "
                        "printf 'ALIVE\\n'; else printf 'STALE\\n'; fi")
        else:
            existing = "printf 'STALE\\n'"
        check = (
            f"res=$(ls -d {glob} 2>/dev/null | head -n 1); "
            "if [ -n \"$res\" ]; then printf 'RESULT %s\\n' \"$res\"; "
            f"elif [ -e {_q(jobs_run)} ] || [ -e {_q(log_path)} ] "
            f"|| [ -e {_q(meta_path)} ]; then {existing}; "
            "else printf 'ABSENT\\n'; fi; "
            f"if [ -f {_q(meta_path)} ]; then printf 'META '; "
            f"tr -d '\\n' < {_q(meta_path)}; printf '\\n'; fi"
        )
        out, err, rc = link.run(check, timeout=POLL_TIMEOUT_SECONDS)
        if rc != 0:
            raise InfrastructureError(
                f"resume probe for TB run {run_id} failed (rc={rc}): "
                f"{_short(err)}")
        lines = out.strip().splitlines()
        line = lines[0].strip() if lines else ""
        remote_meta: Optional[Dict[str, Any]] = None
        for extra in lines[1:]:
            if extra.startswith("META "):
                try:
                    parsed = json.loads(extra[len("META "):])
                except ValueError:
                    parsed = None
                remote_meta = parsed if isinstance(parsed, dict) else None
        if line.startswith("RESULT "):
            return "result", line.split(" ", 1)[1].strip(), remote_meta
        if line in ("ALIVE", "STALE", "ABSENT"):
            return line.lower(), None, remote_meta
        raise InfrastructureError(
            f"resume probe for TB run {run_id} returned {_short(out)!r}")

    def _untrusted(self, state: str, record: Optional[Dict[str, Any]],
                   remote_meta: Optional[Dict[str, Any]],
                   conditions: Dict[str, Any],
                   recorded_pid: Optional[int]) -> Optional[str]:
        """Why what the probe found may not be reused or rejoined (None
        when it may). Reuse needs local evidence that THIS unit launched the
        run under THESE conditions, and the remote meta must agree."""
        keys = ("run_id", "env_sha256", "skill_sha256")
        if record is None:
            return "this unit has no local submit record for it"
        if any(record.get(key) != conditions[key] for key in keys):
            return ("its local submit record was made under different "
                    "conditions (forwarded env knobs or SKILL.md)")
        if remote_meta is None:
            return "its remote submit meta is missing or unreadable"
        if any(remote_meta.get(key) != conditions[key] for key in keys):
            return ("its remote submit meta does not match this request "
                    "(forwarded env knobs or SKILL.md)")
        if state == "stale":
            return f"recorded pid {recorded_pid} is not alive"
        return None

    def _stale(self, link: RemoteLink, meta: Dict[str, Any], workdir: Path,
               run_id: str, recorded_pid: Optional[int],
               problem: str) -> RunOutcome:
        """A run id that exists remotely but may not be reused or rejoined.
        Resubmitting would start a second trial into a directory holding a
        stale one (duplicate trials, mixed logs: the 2026-09-20
        double-driver incident), so it is an infra_error and the caller
        retries under a fresh attempt token. No usage row: no rollout
        happened in this call."""
        meta["resumed"] = "stale"
        meta["remote_pid"] = recorded_pid
        self._augment_partial(link, meta, workdir)
        exc = InfrastructureError(
            f"TB run {run_id} already exists remotely but {problem}; "
            "refusing to reuse or resubmit into it")
        result = RunOutcome.infra_error(meta.pop("partial_steps", []), exc)
        result.meta.update(meta)
        return result

    def _kill_runner(self, link: RemoteLink, pid: int,
                     run_id: str) -> Dict[str, Any]:
        """Deadline: stop the runner this unit launched (or rejoined), by
        its recorded pid only, and only after verifying -- in the same
        shell command, to narrow the race -- that the pid still IS that
        runner (``_ours``). The runner was started under ``setsid``, so it
        leads its own process group: killing the group reaches the Harbor
        children holding the sandbox; the bare pid is the fallback. An
        unverifiable pid is never signalled. Best effort -- the outcome is
        infra_error either way."""
        cmd = (f"if {_ours(pid, run_id)}; then "
               f"kill -TERM -- -{int(pid)} 2>/dev/null || kill -TERM {int(pid)}; "
               "printf 'KILLED %s\\n' \"$?\"; "
               "else printf 'NOT_OURS\\n'; fi")
        info: Dict[str, Any] = {"pid": int(pid)}
        try:
            out, err, rc = link.run(cmd, timeout=POLL_TIMEOUT_SECONDS)
        except InfrastructureError as exc:
            info.update(verified=None, signalled=None, error=_short(exc))
            return info
        line = out.strip().splitlines()[0] if out.strip() else ""
        if line == "NOT_OURS":
            info.update(verified=False, signalled=False)
        elif line.startswith("KILLED "):
            kill_rc = line.split(" ", 1)[1].strip()
            info.update(verified=True, signalled=True,
                        rc=int(kill_rc) if kill_rc.isdigit() else kill_rc)
        else:
            info.update(verified=None, signalled=None, rc=rc,
                        error=_short(err) or f"unexpected output {_short(out)!r}")
        return info

    def _submit(self, link: RemoteLink, root: str, run_id: str, task_name: str,
                model_name: str, eval_mode: str,
                skill_root: Optional[str]) -> int:
        """Launch the runner detached from the ssh session; return its pid.

        One Harbor trial per run(): n_attempts=1, concurrency=1,
        max_trial_retries=0. K1 owns sampling and infra retries (B6), so a
        RunOutcome maps 1:1 onto one remote trial directory and an internal
        retry could never hide inside a unit.
        """
        log_path = f"{root}/logs/{run_id}.log"
        env: Dict[str, str] = {
            "RUN_ID": run_id,
            "JOBS_DIR": f"{root}/jobs",
            # Keep every remote artifact inside our root, including the
            # runner's retry archive (its skills-mode default lives under
            # another user's directory).
            "RETRY_ARCHIVE_ROOT": f"{root}/retry_archives",
            "TBENCH_RUN_MODE": "selected",
            "TBENCH_TASK_NAMES": task_name,
            "TBENCH_EVAL_MODE": eval_mode,
            "TBENCH_N_ATTEMPTS": "1",
            "TBENCH_N_CONCURRENT": "1",
            "TBENCH_MAX_TRIAL_RETRIES": "0",
            # The runner adds the openai/ provider prefix itself.
            "MODEL_NAME": model_name,
        }
        if eval_mode == "skills":
            env["TBENCH_SKILL_ROOT"] = str(skill_root)
        for knob in _FORWARDED_KNOBS:
            value = os.environ.get(knob)
            if value:
                env[knob] = value
        exports = " ".join(f"{key}={_q(value)}" for key, value in env.items())
        runner_script = (self._runner_script
                         or os.environ.get(RUNNER_ENV, DEFAULT_RUNNER))
        script = (
            f"cd {_q(root)} && mkdir -p {_q(root + '/logs')} {_q(env['JOBS_DIR'])}; "
            f"if [ -f {_q(root + '/env.sh')} ]; then . {_q(root + '/env.sh')}; fi; "
            f"nohup setsid env {exports} bash {_q(runner_script)} "
            f"> {_q(log_path)} 2>&1 < /dev/null & echo $!"
        )
        out, err, rc = link.run(script, timeout=SUBMIT_TIMEOUT_SECONDS)
        if rc != 0:
            raise InfrastructureError(
                f"submitting TB run {run_id} failed (rc={rc}): {_short(err)}")
        try:
            pid = int(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise InfrastructureError(
                f"TB submit for {run_id} returned no pid (stdout: {_short(out)})") from None
        return pid

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll(self, link: RemoteLink, jobs_dir: str, run_id: str,
              task_name: str, pid: int, poll_seconds: float,
              deadline_seconds: float) -> Tuple[Tuple[str, Optional[str]], int]:
        """Wait for the trial's result.json.

        Returns ``(("result", remote_path), polls)`` on completion and
        ``(("deadline", None) | ("dead", None), polls)`` otherwise. A few
        consecutive ssh failures are tolerated (one blip must not forfeit a
        long rollout); past that, InfrastructureError propagates.
        """
        glob = (f"{_q(jobs_dir)}/{_q(run_id)}/{_q(task_name)}__*/result.json")
        check = (
            f"res=$(ls -d {glob} 2>/dev/null | head -n 1); "
            "if [ -n \"$res\" ]; then printf 'RESULT %s\\n' \"$res\"; "
            f"elif ! {_ours(pid, run_id)}; then printf 'DEAD\\n'; "
            "else printf 'PENDING\\n'; fi"
        )
        end = time.monotonic() + deadline_seconds
        polls = 0
        failures = 0
        while True:
            polls += 1
            try:
                out, err, rc = link.run(check, timeout=POLL_TIMEOUT_SECONDS)
            except InfrastructureError:
                rc = -1
            if rc != 0:
                failures += 1
                if failures >= MAX_POLL_FAILURES:
                    raise InfrastructureError(
                        f"{failures} consecutive poll failures for TB run "
                        f"{run_id}; remote link unusable")
            else:
                failures = 0
                line = out.strip().splitlines()[0] if out.strip() else ""
                if line.startswith("RESULT "):
                    return ("result", line.split(" ", 1)[1].strip()), polls
                if line == "DEAD":
                    return ("dead", None), polls
            if time.monotonic() >= end:
                return ("deadline", None), polls
            time.sleep(poll_seconds)

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    def _finish(self, link: RemoteLink, workdir: Path, meta: Dict[str, Any],
                result_remote: str, started: float, polls: int, handle: Any,
                run_id: str, task_name: str) -> RunOutcome:
        """Fetch + parse the finished trial and build the RunOutcome."""
        trial_dir = posixpath.dirname(result_remote.rstrip("/"))
        meta["polls"] = polls
        meta["remote"] = {
            "result": result_remote,
            "trajectory": f"{trial_dir}/{_REMOTE_TRAJECTORY}",
            "verifier_log": f"{trial_dir}/{_REMOTE_VERIFIER_LOG}",
        }
        result_local = workdir / "result.json"
        link.get(result_remote, str(result_local), timeout=FETCH_TIMEOUT_SECONDS)
        result_data = json.loads(result_local.read_text(encoding="utf-8"))

        # Best effort: a reward without verifier evidence is a pseudo zero
        # either way, and a fetch failure here is an infra failure below.
        verifier_log: Optional[str] = None
        try:
            verifier_local = workdir / "verifier_test_stdout.txt"
            link.get(meta["remote"]["verifier_log"], str(verifier_local),
                     timeout=FETCH_TIMEOUT_SECONDS)
            verifier_log = verifier_local.read_text(encoding="utf-8",
                                                    errors="replace")
        except (InfrastructureError, OSError):
            verifier_log = None

        status, reward, info = classify_result(result_data, verifier_log)
        meta.update(info)
        meta["reward"] = reward

        steps, usage_pair = self._fetch_steps(link, workdir, meta)

        if meta.get("resumed") in ("result", "alive"):
            # This process did not watch the whole trial: its own clock
            # would report the fetch (or partial wait), not the rollout.
            meta["timing"] = _trial_timing(result_data)
        else:
            finished = time.time()
            meta["timing"] = {
                "started_at": started,
                "finished_at": finished,
                "duration_s": finished - started,
                "source": "local",
            }
        meta["local"] = {
            "result": str(result_local),
            "trajectory": str(workdir / "trajectory.json"),
            "workdir": str(workdir),
        }
        self._record_usage(handle, run_id, task_name, usage_pair,
                           "ok" if status != "infra_error" else "infra_error")

        if status == "infra_error":
            reason = info.get("verdict_reason") or info.get("exception_type")
            outcome = RunOutcome.infra_error(
                steps, InfrastructureError(f"TB trial {run_id}: {reason}"))
        else:
            outcome = RunOutcome(steps=steps, status=status)
        outcome.meta.update(meta)
        return outcome

    def _fetch_steps(self, link: RemoteLink, workdir: Path,
                     meta: Dict[str, Any]) -> Tuple[List[Dict[str, str]],
                                                    Tuple[Optional[int], Optional[int]]]:
        """Fetch the agent trajectory, reconstruct steps and Harbor usage."""
        try:
            traj_local = workdir / "trajectory.json"
            link.get(meta["remote"]["trajectory"], str(traj_local),
                     timeout=FETCH_TIMEOUT_SECONDS)
            traj = json.loads(traj_local.read_text(encoding="utf-8"))
        except (InfrastructureError, OSError, ValueError) as exc:
            # The verdict stands on result.json; a missing trajectory only
            # costs the stepwise view. Recorded, not raised.
            meta["trajectory_error"] = _short(exc)
            return [], (None, None)
        steps, skipped = reconstruct(traj)
        meta["skipped_steps"] = skipped
        return steps, total_usage(traj)

    def _augment_partial(self, link: RemoteLink, meta: Dict[str, Any],
                         workdir: Path) -> None:
        """Deadline/dead-runner path: keep the partial trajectory (if the
        trial dir already holds one) and the runner log tail for debugging."""
        jobs_dir, run_id, task_name = (meta["jobs_dir"], meta["run_id"],
                                        meta["task_name"])
        meta["partial_steps"] = []
        meta["remote"] = {}
        try:
            glob = (f"{_q(jobs_dir)}/{_q(run_id)}/{_q(task_name)}"
                    f"__*/{_REMOTE_TRAJECTORY}")
            out, _, _ = link.run(f"ls -d {glob} 2>/dev/null | head -n 1",
                                 timeout=POLL_TIMEOUT_SECONDS)
            path = out.strip().splitlines()[0] if out.strip() else ""
            if path:
                local = workdir / "trajectory_partial.json"
                link.get(path, str(local), timeout=FETCH_TIMEOUT_SECONDS)
                steps, skipped = reconstruct(
                    json.loads(local.read_text(encoding="utf-8")))
                meta["partial_steps"] = steps
                meta["skipped_steps"] = skipped
                meta["remote"]["partial_trajectory"] = path
            log_path = f"{meta['remote_root']}/logs/{run_id}.log"
            out, _, _ = link.run(f"tail -c 2000 {_q(log_path)} 2>/dev/null",
                                 timeout=POLL_TIMEOUT_SECONDS)
            if out.strip():
                meta["runner_log_tail"] = _short(out, limit=2000)
        except (InfrastructureError, OSError, ValueError):
            # Best effort by definition; the outcome is already infra_error.
            pass

    def _record_usage(self, handle: Any, run_id: str, task_name: str,
                      usage_pair: Tuple[Optional[int], Optional[int]],
                      status: str) -> None:
        """One B7 ledger line per remote rollout; missing Harbor totals are
        recorded as null, never 0.

        The recorder is the constructor-injected one if present, else the
        one attached to the handle (the same E1-threaded fallback call_model
        uses, so K1 wiring works uniformly for local and remote plugins).
        """
        recorder = self.usage_recorder
        if recorder is None:
            recorder = getattr(handle, "usage", None)
        if recorder is None:
            return
        in_tokens, out_tokens = usage_pair
        recorder.record(
            role=str(getattr(handle, "role", "executor")),
            prompt=self.task_prompt_name,
            model=str(handle.name),
            in_tokens=in_tokens,
            out_tokens=out_tokens,
            request_id=run_id,
            status=status,
            meta={"tb_task": task_name, "run_id": run_id},
        )
