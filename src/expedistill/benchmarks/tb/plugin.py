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
   **Skill-folder units (the real_folder execution, M3)** ride the same
   mechanism: the SKILL.md carries the folder GUIDANCE
   (``TB_SKILL_FOLDER_GUIDANCE`` -- never the catalog text), and the
   manifest's catalog.md and skill files are uploaded beside it into the
   same per-run directory, which the environment mounts into the sandbox
   read-only at ``/opt/openclaw-skills/<task>/``; the agent cats what it
   needs, every command stays in the recorded terminal session, and the
   plugin neither intercepts nor budgets the reads (M4 parses them).
2. **Launch**: one ssh round trip runs ``nohup setsid env <VARS> bash
   <runner> > <root>/logs/<run_id>.log 2>&1 < /dev/null & echo $!``. The
   runner is detached from the ssh session, so a dropped connection cannot
   kill a live trial (the 2026-09-20 supervisor-kill lesson); the echoed pid
   is tracked directly, never discovered via ``pgrep -f``.
3. **Poll**: one ssh round trip per interval checks the trial's
   ``result.json`` and, while absent, that the pid is still alive and
   still our runner (identity check, see below). A dead
   pid with no result means the runner exited early (config/adapter error)
   -> an InfrastructureError with the runner log tail. The overall deadline
   and the poll interval are configurable (env or constructor).
4. **Finish**: fetch ``result.json``, the verifier's pytest log and
   ``agent/trajectory.json`` into ``workdir``; read the facts; reconstruct
   steps.

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
  raises InfrastructureError and the caller retries under a fresh token,
  with no usage row (no rollout happened in this call). A run id is never
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

Fact reporting (R2/R2b, docs/decisions/
execute_learn_repair_plan_20261016.md: execution records facts,
acceptance judges -- boundaries doc sections 2.2 and 2.3):

- a ``result.json`` whose verifier actually ran (pytest collected
  something) carries the benchmark scorer's verdict and it passes through
  verbatim as ``correctness`` (reward > 0 -> True, reward == 0 -> False),
  whatever the verifier log looks like beyond that;
- a trial with no reward, or a reward claimed without verifier evidence
  (the campaign's "pseudo zero" -- the verifier log missing or pytest
  never collected anything, in EITHER reward direction), carries no
  scorer verdict: the run is stored with ``correctness=None`` and the
  trial's exception_type / exception_message / ``verifier_ran`` as raw
  facts. Execution does not mechanically retry it and does not reclassify
  the model-vs-infra exceptions (AgentTimeoutError et al.); judging
  those runs is acceptance's named-rule decision;
- infrastructure failures (transport, deadline, dead runner, an
  untrusted pre-existing run id) raise ``InfrastructureError`` -- K1's
  mechanical retry, never a stored record.

``classify_result`` below keeps the pre-R2 classification ONLY as the
archive validator's historical lens (scripts/analysis/
tb_archive_validate.py re-derives the status archived trials were
produced under); ``run`` never calls it.

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
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Tuple

from expedistill.benchmarks.base import BenchmarkPlugin, RunOutcome, Task, ViewRules
from expedistill.benchmarks.tb.remote import ENV_ROOT, RemoteLink
from expedistill.benchmarks.tb.trajectory import (
    reconstruct_skip_counts,
    reconstruct_steps,
    total_usage,
)
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
)
from expedistill.infra.store import write_json
from expedistill.prompts.registry import PromptSpec, register

__all__ = [
    "TBPlugin",
    "TB_SKILL_FOLDER_GUIDANCE",
    "TB_TASK_PROMPT",
    "TB_TASK_PROMPT_FOLDER",
    "TB_TASK_PROMPT_FOLDER_INLINE",
    "TB_TASK_PROMPT_FOLDER_INLINE_NAME",
    "classify_result",
    "result_facts",
    "runner_env",
]

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

# --------------------------------------------------------------------------
# B2: the skill-folder guidance and the folder unit's task prompt
# (skill-folder M3).
#
# A folder unit's remote trial rides the SAME skills-mode mechanism a
# skills-block trial does: the plugin uploads a per-run directory under
# ``<TBENCH_SKILL_ROOT>/<run_id>/<task_name>/`` whose SKILL.md the remote
# skill agent injects into the agent's task prompt, and the
# TencentTaskSkillE2BEnvironment (outer scripts/tb21, vendored
# tencent_tb2_skill_env.py) uploads that WHOLE directory -- SKILL.md and
# every other file in it -- into the sandbox read-only at
# ``/opt/openclaw-skills/<task_name>/``. The folder play uses exactly
# that: SKILL.md carries the GUIDANCE below (the prompt-side injection --
# the catalog text is never injected), and catalog.md plus the skill
# files ride the same directory into the sandbox, where the agent cats
# them. So the guidance's path wording is a remote contract: it must
# name /opt/openclaw-skills, the mount the vendored environment hardcodes.
# --------------------------------------------------------------------------
TB_SKILL_FOLDER_GUIDANCE = """Your task's skill folder is mounted read-only inside this sandbox.
Run `ls /opt/openclaw-skills/` to find this task's directory name, then read
the catalog: `cat /opt/openclaw-skills/<directory>/catalog.md`. Every catalog
line lists one skill as "<skill id> | <file to cat> | <one-line description>".
cat only the skill file the current step needs, one at a time; do not open
many skill files at once; come back to the catalog when a later step needs
another one. These skills are lessons distilled from earlier runs: use
them as references when they apply to the current task, and ignore them
when they do not."""

TB_TASK_PROMPT_FOLDER = register(
    PromptSpec(
        name="TB_TASK_PROMPT_FOLDER",
        template=(
            "You are an AI assistant solving a command-line task inside a "
            "Linux sandbox.\n"
            "You act through a terminal: inspect the environment, run "
            "shell commands, read\n"
            "their output, and iterate until the task is done.\n"
            "\n"
            "SKILL FOLDER (lessons distilled from earlier runs live as "
            "files in this\n"
            "sandbox; use them as references when they apply to the "
            "current task, and\n"
            "ignore them when they do not):\n"
            + TB_SKILL_FOLDER_GUIDANCE + "\n"
            "\n"
            "TASK:\n"
            "{instruction}\n"
            "\n"
            "TERMINAL HISTORY (most recent state last):\n"
            "{history}\n"
            "Decide your next action from the terminal state above. Work "
            "in small,\n"
            "verifiable steps, check the effect of each command before "
            "moving on, and do\n"
            "not declare the task complete until you have verified the "
            "goal against\n"
            "the task description.\n"
        ),
        description=(
            "E1 TB runner, skill-folder mode (the real_folder execution, "
            "inline_catalog=False): the folder unit's task prompt -- the "
            "SKILLS slot is replaced by the folder guidance section "
            "(TB_SKILL_FOLDER_GUIDANCE verbatim, which is also what the "
            "remote skill agent injects as the trial's SKILL.md); the "
            "catalog text is never part of the prompt. No "
            "{skills_block} slot: nothing variable is injected. See "
            "TB_TASK_PROMPT_FOLDER_INLINE for the inline-catalog variant."
        ),
    )
)

#: The registered name of the inline-catalog folder prompt, used to switch
#: on the FolderParams.inline_catalog flag without a magic string in run().
TB_TASK_PROMPT_FOLDER_INLINE_NAME = "TB_TASK_PROMPT_FOLDER_INLINE"

TB_TASK_PROMPT_FOLDER_INLINE = register(
    PromptSpec(
        name=TB_TASK_PROMPT_FOLDER_INLINE_NAME,
        template=(
            "You are an AI assistant solving a command-line task inside a "
            "Linux sandbox.\n"
            "You act through a terminal: inspect the environment, run "
            "shell commands, read\n"
            "their output, and iterate until the task is done.\n"
            "\n"
            "SKILL FOLDER (lessons distilled from earlier runs; use them "
            "as references\n"
            "when they apply to the current task, and ignore them when "
            "they do not):\n"
            + TB_SKILL_FOLDER_GUIDANCE + "\n"
            "\n"
            "SKILL CATALOG (every skill available in the folder is listed "
            "below; cat only\n"
            "the skill file the current step needs, one at a time):\n"
            "{skills_block}\n"
            "\n"
            "TASK:\n"
            "{instruction}\n"
            "\n"
            "TERMINAL HISTORY (most recent state last):\n"
            "{history}\n"
            "Decide your next action from the terminal state above. Work "
            "in small,\n"
            "verifiable steps, check the effect of each command before "
            "moving on, and do\n"
            "not declare the task complete until you have verified the "
            "goal against\n"
            "the task description.\n"
        ),
        description=(
            "E1 TB runner, skill-folder mode (the real_folder execution, "
            "inline_catalog=True): the folder unit's task prompt with the "
            "catalog text injected verbatim into {skills_block}, so the "
            "agent sees the full catalog in the prompt without needing to "
            "cat catalog.md first. The skill bodies still live in the "
            "mounted folder for on-demand cat. The guidance section "
            "(TB_SKILL_FOLDER_GUIDANCE) also appears so the agent knows "
            "the folder path. See TB_TASK_PROMPT_FOLDER for the "
            "file-only variant where the agent must cat the catalog."
        ),
    )
)

#: Poll/deadline knobs (ssh round trips are expensive: poll modestly).
POLL_ENV = "EXPEDISTILL_TB_POLL_SECONDS"
DEADLINE_ENV = "EXPEDISTILL_TB_DEADLINE_SECONDS"
DEFAULT_POLL_SECONDS = 60.0
#: A trial's own worst case with the campaign knobs: agent cap 7200s +
#: verifier override 7200s (TBENCH_VERIFIER_OVERRIDE wins, the x2 multiplier
#: does not stack) + sandbox setup 360s x3 = 15480s; the default deadline
#: adds a transport margin on top -> 4.5 hours. (.env sets the same value.)
DEFAULT_DEADLINE_SECONDS = 16200.0

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

#: Verdict vocabulary fixed from the tb21 campaign audit (audit_trials.py),
#: used ONLY by :func:`classify_result` (the archive validator's historical
#: lens): exceptions that were counted as the model's own doing vs.
#: infrastructure. An unknown exception with no reward was infrastructure,
#: matching the campaign's "unrecognized -> rerun as infra" rule.
_MODEL_EXCEPTIONS = frozenset({
    "AgentTimeoutError",
    "ContextLengthExceededError",
    "OutputLengthExceededError",
})
#: The campaign's tmux rule (audit_trials.py classify_exception), also
#: classify_result-only: the exception must BE a RuntimeError whose message
#: shows a broken tmux/send-keys -- an infra exception that merely mentions
#: tmux stayed infra.
_TMUX_ERROR_RE = re.compile(r"tmux|send.{0,20}keys", re.IGNORECASE)
_TMUX_ERROR_TYPE = "RuntimeError"

#: The pseudo-zero EVIDENCE, still a live fact on the execution path: every
#: TB2.1 verifier is pytest, so whether the verifier log shows pytest
#: collecting anything says whether a verification actually ran. R2 records
#: that as the raw fact ``verifier_ran``; invalidating the run on it is
#: acceptance's named-rule decision, not the plugin's.
_PYTEST_COLLECTED_RE = re.compile(r"collected [0-9]+ item")

_REMOTE_VERIFIER_LOG = "verifier/test-stdout.txt"
_REMOTE_TRAJECTORY = "agent/trajectory.json"


def result_facts(result: Dict[str, Any],
                 verifier_log: Optional[str]) -> Dict[str, Any]:
    """Read one Harbor trial's raw facts (R2: the plugin reports, it does
    not judge). Pure.

    Returns the fact dict for ``RunOutcome.meta``: the verifier reward as
    parsed (``None`` when absent or not a number), ``verifier_ran`` (True /
    False when the verifier log shows pytest did / did not collect,
    ``None`` when no log was handed in -- the execution path never does
    that: a fetch failure raises InfrastructureError and K1 retries), and
    the trial's exception type and shortened message. No status, no
    verdict: what a fact pattern
    means for a run's validity is acceptance's named-rule decision.
    """
    reward: Optional[float] = None
    verifier_result = result.get("verifier_result") or {}
    raw_reward = (verifier_result.get("rewards") or {}).get("reward")
    try:
        reward = float(raw_reward)
    except (TypeError, ValueError):
        reward = None
    exception = result.get("exception_info") or {}
    verifier_ran: Optional[bool]
    if verifier_log is None:
        verifier_ran = None
    else:
        verifier_ran = bool(_PYTEST_COLLECTED_RE.search(verifier_log))
    return {
        "reward": reward,
        "verifier_ran": verifier_ran,
        "exception_type": exception.get("exception_type"),
        "exception_message": _short(exception.get("exception_message")),
    }


def classify_result(result: Dict[str, Any],
                    verifier_log: Optional[str]) -> Tuple[str, Optional[float], Dict[str, Any]]:
    """The pre-R2 verdict classification, kept ONLY as the archive
    validator's historical lens: scripts/analysis/tb_archive_validate.py
    re-derives the status an archived trial was produced under and checks
    it against the campaign audit. ``run`` no longer calls this -- the
    execution path passes the scorer's verdict through and reports raw
    facts (:func:`result_facts`); validity judgments (pseudo zero,
    model-vs-infra exceptions) live with acceptance's named rules now.
    Returns (status, reward, info). Pure.
    """
    # The reward/exception parsing below is a FROZEN copy of
    # result_facts' parsing, on purpose: this lens must reproduce the
    # parsing as of the archived campaign, not follow the live function.
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
    if exception_type in _MODEL_EXCEPTIONS:
        return "failure", None, info
    if (exception_type == _TMUX_ERROR_TYPE
            and _TMUX_ERROR_RE.search(message)):
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


def _env_int(name: str, default: int) -> int:
    """The environment's value, or the default; a positive int."""
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an int, got {raw!r}") from None
    if value < 1:
        raise ConfigError(f"{name} must be >= 1, got {value!r}")
    return value


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


#: One folder ships once per process: the cache-ensure critical section
#: is process-wide (K1's parallelism is a thread pool; the run directory
#: lock already serialises whole drivers).
_FOLDER_CACHE_LOCK = threading.Lock()

#: Rolling upload admission (user ruling 2026-10-18): at most this many
#: units may be between "started building connections" and "the submit
#: returned" at once, and the next unit is admitted only when an earlier
#: one's upload COMPLETES -- never by a timer alone. A transport-shape
#: guard (the 2026-10-18 storm was 84 units x 141 per-file scp
#: connections and node29's sshd MaxStartups dropped them): it changes
#: when units start, never what they measure. Resumed units skip it
#: (they only poll); polls run outside it (one ssh per poll period).
_UPLOAD_GATE_WIDTH = _env_int("EXPEDISTILL_TB_UPLOAD_GATE", 8)
_UPLOAD_GATE = threading.BoundedSemaphore(_UPLOAD_GATE_WIDTH)


def _folder_dir_digest(folder_dir: Path) -> str:
    """The CONTENT digest of a materialized skill folder: sha256 over the
    sorted (name, bytes) of every file it holds. The cache key -- the
    same folder re-uses the remote cache, a changed folder (a re-curated
    bank) gets a new one, and two folders with equal digests are
    byte-identical by construction."""
    digest = hashlib.sha256()
    for path in sorted(entry for entry in folder_dir.iterdir()
                       if entry.is_file()):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _q(value: str) -> str:
    return shlex.quote(str(value))


#: The task-meta keys the M3 stage wiring attaches to a folder unit (the
#: delivery manifest and the materialized folder's local path). A task
#: carrying the manifest is a folder unit; the pair travels the plugin's
#: own opaque meta channel, so the plugins that never read it never see
#: a folder unit.
_FOLDER_MANIFEST_KEY = "skill_folder"
_FOLDER_DIR_KEY = "skill_folder_dir"


def _folder_of(task: Task) -> Optional[Tuple[Dict[str, Any], Path]]:
    """A folder unit's (manifest, materialized local directory), or None.

    The stage wiring (phase_execute) attaches both keys to every folder
    unit's task before K1 dispatch; the manifest is the folder delivery's
    ``aux["skill_folder"]`` (a NAME mapping -- no skill bodies -- plus
    the catalog text and the bank fingerprint) and the directory is what
    ``materialize_skill_folder`` wrote from it. A manifest without the
    directory (or the other way round) is a wiring bug, not a
    configuration problem."""
    meta = task.meta if isinstance(task.meta, dict) else {}
    manifest = meta.get(_FOLDER_MANIFEST_KEY)
    if manifest is None:
        return None
    if not isinstance(manifest, dict):
        raise CodeBugError(
            f"task {task.task_id!r} carries {_FOLDER_MANIFEST_KEY!r} but "
            f"it is not the folder manifest mapping, got {manifest!r}")
    folder_dir = meta.get(_FOLDER_DIR_KEY)
    if not isinstance(folder_dir, str) or not folder_dir:
        raise CodeBugError(
            f"the folder unit {task.task_id!r} carries a folder manifest "
            f"but no {_FOLDER_DIR_KEY!r}: the phase driver must "
            "materialize the folder before dispatch")
    return manifest, Path(folder_dir)


def runner_env(run_id: str, jobs_dir: str, retry_archive_root: str,
               task_name: str, model_name: str, eval_mode: str,
               skill_root: Optional[str]) -> Dict[str, str]:
    """The environment the tb21 runner is launched with, verbatim.

    ``_submit`` puts exactly these variables in front of ``bash <runner>``
    (plus the forwarded campaign knobs below); the DRY_RUN smoke reuses this
    function so the pre-flight validation exercises the real submission
    contract, not a copy of it. One Harbor trial per invocation:
    n_attempts=1, concurrency=1, max_trial_retries=0 -- K1 owns sampling and
    infra retries (B6), so a RunOutcome maps 1:1 onto one remote trial
    directory and an internal retry could never hide inside a unit.
    """
    env: Dict[str, str] = {
        "RUN_ID": run_id,
        "JOBS_DIR": jobs_dir,
        # Keep every remote artifact inside our root, including the
        # runner's retry archive (its skills-mode default lives under
        # another user's directory).
        "RETRY_ARCHIVE_ROOT": retry_archive_root,
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
    return env


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
    # The skill-folder capability (M3): a folder unit runs under the
    # folder prompt registered above, and ``run`` brings the
    # materialized folder into the sandbox through the skills-mode
    # directory (the module docstring's submission design).
    skill_folder_prompt_name = "TB_TASK_PROMPT_FOLDER"

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
        """One task, one remote Harbor trial. Infrastructure failures
        (transport, deadline, dead runner, an untrusted pre-existing run
        id) raise ``InfrastructureError`` -- K1 retries the unit;
        ConfigError/CodeBugError propagate (they stop the phase, B6).
        """
        task_name = self._task_name(task)
        model_name = self._model_name(handle)
        skills_block = skills_block or ""
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        link = self._link if self._link is not None else RemoteLink()
        run_id = self._make_run_id(task_name, task)
        # A folder unit rides the skills-mode mechanism with the GUIDANCE
        # as its SKILL.md (the catalog text is never part of SKILL.md;
        # the catalog lives in catalog.md inside the same directory).
        # inline_catalog=True: the catalog text was placed in skills_block
        # by the delivery driver; the INLINE prompt template renders it
        # via {skills_block} so the agent sees it without catting a file.
        # inline_catalog=False (default): the SKILL.md carries guidance
        # only; the agent must cat catalog.md from the mounted folder.
        folder = _folder_of(task)
        inline_catalog = (
            bool(task.meta.get("inline_catalog", False))
            if isinstance(task.meta, dict) else False
        ) if folder is not None else False
        if folder is not None and inline_catalog:
            skill_md = TB_SKILL_FOLDER_GUIDANCE
            folder_prompt_name = TB_TASK_PROMPT_FOLDER_INLINE_NAME
        elif folder is not None:
            skill_md = TB_SKILL_FOLDER_GUIDANCE
            folder_prompt_name = self.skill_folder_prompt_name
        else:
            skill_md = skills_block
            folder_prompt_name = self.task_prompt_name
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
            "prompt_name": folder_prompt_name,
        }
        try:
            # Root resolution and everything after it goes over the wire, so
            # a transport failure anywhere below is re-raised as an
            # InfrastructureError below (B6 retries infra, it never stops
            # the phase).
            root = self._remote_root(link)
            meta["remote_root"] = root
            meta["jobs_dir"] = f"{root}/jobs"
            eval_mode, skill_meta = self._skill_plan(root, run_id, task_name,
                                                     skill_md, folder)
            meta.update(skill_meta)
            record_file = workdir / _RECORD_DIR / f"{run_id}.json"
            conditions = self._conditions(run_id, eval_mode, skill_md, folder)
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
                # Rolling admission: the whole connection-building burst of
                # a fresh launch (upload + submit) happens under the gate,
                # so the next unit starts only after an earlier one's
                # upload completed -- the poll phase (the long wait) runs
                # outside it and never holds a slot.
                with _UPLOAD_GATE:
                    self._upload_launch_files(link, root, run_id, skill_meta,
                                              task_name, skill_md, workdir,
                                              conditions, folder)
                    pid = self._submit(link, root, run_id, task_name,
                                       model_name, eval_mode,
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
        except InfrastructureError:
            # Transport failure (ssh timeout, scp error) anywhere above --
            # including the poll guard and the result fetch: nothing was
            # fetched, so no partial steps; a usage row only if a rollout
            # was actually launched or rejoined. The token is spent either
            # way (the pre-R2b path returned the same failure as an
            # outcome), so the retry asks for a fresh one.
            if launched:
                # Usage-ledger label (pre-R2b rows say "infra_error";
                # both simply count as a non-ok row).
                self._record_usage(handle, run_id, task_name, (None, None),
                                   "infra",
                                   prompt_name=meta.get("prompt_name"))
            raise
        # deadline / dead runner (outside the transport handler above: the
        # failure raised here must not be re-caught and usage-recorded
        # twice). An infrastructure failure: the partial trajectory and the
        # runner log tail are fetched into the workdir for debugging before
        # the raise; the failure itself travels as the exception (K1
        # retries the unit).
        meta["polls"] = polls
        if outcome[0] == "deadline":
            meta["deadline_kill"] = self._kill_runner(link, pid, run_id)
        self._augment_partial(link, meta, workdir)
        # Usage-ledger label (pre-R2b rows say "infra_error"; both
        # simply count as a non-ok row).
        self._record_usage(handle, run_id, task_name, (None, None),
                           "infra", prompt_name=meta.get("prompt_name"))
        if outcome[0] == "deadline":
            raise InfrastructureError(
                f"TB rollout {run_id} exceeded the "
                f"{deadline_seconds:g}s deadline without result.json "
                f"(deadline_kill: "
                f"{json.dumps(meta['deadline_kill'], sort_keys=True)}; "
                f"runner log tail: {meta.get('runner_log_tail', '')})")
        raise InfrastructureError(
            f"remote runner for {run_id} (pid {pid}) exited without "
            "producing result.json "
            f"(runner log tail: {meta.get('runner_log_tail', '')})")

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
                    skill_md: str,
                    folder: Optional[Tuple[Dict[str, Any], Path]]
                    ) -> Tuple[str, Dict[str, Any]]:
        """The eval mode and where the run's SKILL.md lives remotely. Pure:
        a reused or rejoined run reports the same provenance without
        uploading anything.

        A folder unit (``folder`` is its (manifest, local directory)) rides
        the skills mode: its SKILL.md is the GUIDANCE (the caller passes
        it as ``skill_md``), and the manifest's catalog and skill files
        reach the sandbox beside it through the same per-run directory.
        The trajectory keeps every terminal command, so what the agent
        actually cat'd stays in the record for M4's parsing -- execution
        here neither intercepts nor budgets the reads."""
        if not skill_md.strip():
            return "bare", {"eval_mode": "bare", "skills_injected": False}
        remote_dir = f"{root}/skills/{run_id}"
        # The E2B skill env looks the skill directory up by the environment
        # name, whose authoring export normalizes dots to dashes (the tb21
        # bundle's _TASK_SKILL_DIRECTORY_OVERRIDES: "install-windows-3.11" ->
        # "install-windows-3-11"). Uploading only the dotted spelling made
        # every skills-mode trial of install-windows-3.11 die in env setup
        # ("task skill directory is unavailable", TB v3 run 2026-10-13).
        # Both spellings are therefore uploaded; the meta records the dashed
        # alias when one exists.
        dash_alias = task_name.replace(".", "-")
        meta: Dict[str, Any] = {
            "eval_mode": "skills",
            "skills_injected": True,
            "remote_skill_root": remote_dir,
            "remote_skill_file": f"{remote_dir}/{task_name}/SKILL.md",
            "remote_skill_alias_file": (
                f"{remote_dir}/{dash_alias}/SKILL.md"
                if dash_alias != task_name else None),
        }
        if folder is not None:
            manifest, folder_dir = folder
            meta["skill_folder"] = {
                "local_dir": str(folder_dir),
                "bank_fingerprint": manifest.get("bank_fingerprint"),
                "catalog_filename": manifest.get("catalog_filename"),
                "files": [entry.get("filename")
                          for entry in manifest.get("files", [])],
            }
        return "skills", meta

    def _upload_launch_files(self, link: RemoteLink, root: str, run_id: str,
                             skill_meta: Dict[str, Any], task_name: str,
                             skill_md: str, workdir: Path,
                             conditions: Dict[str, Any],
                             folder: Optional[Tuple[Dict[str, Any], Path]]
                             ) -> None:
        """Fresh launches only: upload the run's SKILL.md (the D9 block
        verbatim, or the folder GUIDANCE for a folder unit; the remote
        skill agent wraps it into the agent's task prompt), a folder
        unit's catalog and skill files beside it (the whole per-run
        directory reaches the sandbox -- module docstring), then the
        remote submit meta -- last, so a meta on the remote side means
        everything else is already there."""
        dirs = [f"{root}/jobs"]
        if skill_meta["eval_mode"] == "skills":
            dirs.append(f"{skill_meta['remote_skill_root']}/{task_name}")
            alias_file = skill_meta.get("remote_skill_alias_file")
            if alias_file:
                dirs.append(str(Path(alias_file).parent))
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
            local.write_text(skill_md, encoding="utf-8")
            link.put(str(local), skill_meta["remote_skill_file"],
                     timeout=SUBMIT_TIMEOUT_SECONDS)
            alias_file = skill_meta.get("remote_skill_alias_file")
            if alias_file:
                link.put(str(local), alias_file,
                         timeout=SUBMIT_TIMEOUT_SECONDS)
            if folder is not None:
                self._upload_folder_files(link, root, run_id, skill_meta,
                                          task_name, folder)
        local_meta = workdir / _RECORD_DIR / f"{run_id}{_REMOTE_META_SUFFIX}"
        write_json(local_meta, conditions)
        link.put(str(local_meta),
                 f"{root}/jobs/{run_id}{_REMOTE_META_SUFFIX}",
                 timeout=SUBMIT_TIMEOUT_SECONDS)

    def _upload_folder_files(self, link: RemoteLink, root: str, run_id: str,
                             skill_meta: Dict[str, Any], task_name: str,
                             folder: Tuple[Dict[str, Any], Path]) -> None:
        """A folder unit's catalog and skill files reach the run's remote
        skill directory (and its dashed alias directory, the same spelling
        rule SKILL.md follows) through a PHASE-ONCE REMOTE CACHE: the
        whole materialized folder ships as one tar over one scp into
        ``<root>/skills_cache/<content digest>/`` (idempotent -- the
        atomic ``mv`` of the extraction IS the existence marker), and
        every unit stages its copy with ONE ssh (``mkdir -p`` + ``cp
        -r`` from the cache). ``cp`` copies bytes, so the sandbox's
        ``/opt/openclaw-skills/<task>/`` contents are byte-identical to
        the per-file scp path this replaces -- the 2026-10-18 storm
        (84 units x 141 files, one ssh connection each, node29's sshd
        MaxStartups dropped them by the dozen) is a transport-shape
        problem, not a measurement change.

        The manifest is a NAME mapping (no bodies), so the bytes come
        from the directory the stage materialized; a file the manifest
        names but the directory lacks is a code bug (the folder on the
        remote side would be incomplete, and the sandbox env rejects a
        skill directory without SKILL.md -- a half folder would die in
        env setup, the 2026-10-13 lesson)."""
        manifest, folder_dir = folder
        if not folder_dir.is_dir():
            raise CodeBugError(
                f"TB run {run_id}: the folder unit's materialized "
                f"directory {str(folder_dir)!r} does not exist; the phase "
                "driver must materialize the folder before dispatch")
        names = [entry["filename"] for entry in manifest.get("files", [])]
        catalog_name = manifest.get("catalog_filename")
        if manifest.get("catalog_text"):
            names.append(catalog_name)
        missing = [name for name in names
                   if not (folder_dir / name).is_file()]
        if missing:
            raise CodeBugError(
                f"TB run {run_id}: the materialized folder "
                f"{str(folder_dir)!r} is missing {missing}, which the "
                "manifest names -- the folder cannot ship incomplete")
        # The cache key is the CONTENT digest (the sorted file names and
        # bytes of the materialized directory, which the bank fingerprint
        # pins through the manifest): same folder -> same cache, a
        # re-curated bank -> a new cache, never a mixed one.
        digest = _folder_dir_digest(folder_dir)
        cache = f"{root}/skills_cache/{digest}"
        with _FOLDER_CACHE_LOCK:
            _, _, rc = link.run(f"test -d {_q(cache)}",
                                timeout=SUBMIT_TIMEOUT_SECONDS)
            if rc != 0:
                self._ship_folder_cache(link, root, cache, folder_dir,
                                        digest)
        targets = [f"{skill_meta['remote_skill_root']}/{task_name}"]
        alias_file = skill_meta.get("remote_skill_alias_file")
        if alias_file:
            targets.append(str(Path(alias_file).parent))
        parts = ["mkdir -p " + " ".join(_q(target) for target in targets)]
        parts += [f"cp -r {_q(cache)}/. {_q(target)}/"
                  for target in targets]
        _, err, rc = link.run(" && ".join(parts),
                              timeout=SUBMIT_TIMEOUT_SECONDS)
        if rc != 0:
            raise InfrastructureError(
                f"remote folder copy for TB run {run_id} failed "
                f"(rc={rc}): {_short(err)}")

    def _ship_folder_cache(self, link: RemoteLink, root: str, cache: str,
                           folder_dir: Path, digest: str) -> None:
        """The once-per-folder shipping: one tar, one scp, one atomic
        extract. Called under the cache lock; a crash mid-ship leaves at
        most a staging directory and a temporary tar, both of which the
        next attempt removes before retrying -- the cache directory
        itself only ever appears whole (the mv)."""
        files = sorted(path for path in folder_dir.iterdir()
                       if path.is_file())
        handle, tar_path = tempfile.mkstemp(suffix=".tar",
                                            prefix="skill_folder_")
        os.close(handle)
        try:
            with tarfile.open(tar_path, "w") as tar:
                for path in files:
                    tar.add(path, arcname=path.name)
            cache_root = f"{root}/skills_cache"
            _, err, rc = link.run(f"mkdir -p {_q(cache_root)}",
                                  timeout=SUBMIT_TIMEOUT_SECONDS)
            if rc != 0:
                raise InfrastructureError(
                    f"remote skills_cache mkdir failed (rc={rc}): "
                    f"{_short(err)}")
            remote_tar = f"{cache_root}/{digest}.tmp{os.getpid()}.tar"
            link.put(tar_path, remote_tar,
                     timeout=SUBMIT_TIMEOUT_SECONDS)
            staging = f"{cache_root}/{digest}.staging{os.getpid()}"
            _, err, rc = link.run(
                f"rm -rf {_q(staging)} && mkdir -p {_q(staging)} "
                f"&& tar -xf {_q(remote_tar)} -C {_q(staging)} "
                f"&& mv {_q(staging)} {_q(cache)} "
                f"&& rm -f {_q(remote_tar)}",
                timeout=SUBMIT_TIMEOUT_SECONDS)
            if rc != 0:
                raise InfrastructureError(
                    f"remote skill folder cache extract failed "
                    f"(rc={rc}): {_short(err)}")
        finally:
            os.unlink(tar_path)

    # ------------------------------------------------------------------
    # Remote resume
    # ------------------------------------------------------------------

    def _conditions(self, run_id: str, eval_mode: str, skill_md: str,
                    folder: Optional[Tuple[Dict[str, Any], Path]]
                    ) -> Dict[str, Any]:
        """What the launch of ``run_id`` depends on beyond the run id:
        the forwarded runner knobs and the uploaded content's bytes.

        Skills mode digests the SKILL.md; a folder unit additionally
        folds in the manifest's bank fingerprint and catalog text -- the
        guidance alone is a constant, and a run id whose folder changed
        (a re-curated bank) must not be reused as if it were the same
        trial."""
        knobs = {knob: os.environ.get(knob) or "" for knob in _FORWARDED_KNOBS}
        env_digest = hashlib.sha256(json.dumps(
            knobs, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        skill_digest = None
        if eval_mode == "skills":
            digest_payload = skill_md
            if folder is not None:
                manifest = folder[0]
                digest_payload = "\n".join([
                    skill_md,
                    str(manifest.get("bank_fingerprint")),
                    str(manifest.get("catalog_text")),
                ])
            skill_digest = hashlib.sha256(
                digest_payload.encode("utf-8")).hexdigest()
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
               problem: str) -> NoReturn:
        """A run id that exists remotely but may not be reused or rejoined.
        Resubmitting would start a second trial into a directory holding a
        stale one (duplicate trials, mixed logs: the 2026-09-20
        double-driver incident), so it raises InfrastructureError and the
        caller retries under a fresh attempt token. No usage row: no
        rollout happened in this call."""
        meta["resumed"] = "stale"
        meta["remote_pid"] = recorded_pid
        self._augment_partial(link, meta, workdir)
        raise InfrastructureError(
            f"TB run {run_id} already exists remotely but {problem}; "
            "refusing to reuse or resubmit into it")

    def _kill_runner(self, link: RemoteLink, pid: int,
                     run_id: str) -> Dict[str, Any]:
        """Deadline: stop the runner this unit launched (or rejoined), by
        its recorded pid only, and only after verifying -- in the same
        shell command, to narrow the race -- that the pid still IS that
        runner (``_ours``). The runner was started under ``setsid``, so it
        leads its own process group: killing the group reaches the Harbor
        children holding the sandbox; the bare pid is the fallback. An
        unverifiable pid is never signalled. Best effort -- the failure
        being raised either way."""
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

        The launch environment comes from :func:`runner_env` (one Harbor
        trial per run(): the knobs and their rationale live there).
        """
        log_path = f"{root}/logs/{run_id}.log"
        env = runner_env(run_id, f"{root}/jobs", f"{root}/retry_archives",
                         task_name, model_name, eval_mode, skill_root)
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
        # The vendored Harbor writes result.json with a plain write_text, so a
        # fetch racing the write (or a killed runner) can land a truncated or
        # non-object file. That is transport-grade, not a code bug: every
        # other parse site here treats it as infra, and without this guard
        # the ValueError would stop the whole K1 pass (2026-10-13 review).
        try:
            result_data = json.loads(result_local.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InfrastructureError(
                f"TB trial {run_id}: unreadable result.json "
                f"(refetches on retry): {exc}") from None
        if not isinstance(result_data, dict):
            raise InfrastructureError(
                f"TB trial {run_id}: result.json is not a JSON object "
                f"(got {type(result_data).__name__})")

        # The verifier log is the scorer's evidence. A fetch failure only
        # threatens a verdict when the trial claims one (a reward came
        # back): that is an infrastructure failure (K1 retries the unit
        # -- a scored rollout must not be voided by a transport hiccup),
        # never a silent "no verdict". A trial that died before its
        # verifier ran has no reward and no log to fetch -- best effort
        # there, the facts ride meta and correctness is None either way.
        # Only a log that arrives and shows pytest never collected
        # leaves a rewarded trial unscored (the pseudo zero).
        verifier_log: Optional[str] = None
        try:
            verifier_local = workdir / "verifier_test_stdout.txt"
            link.get(meta["remote"]["verifier_log"], str(verifier_local),
                     timeout=FETCH_TIMEOUT_SECONDS)
            verifier_log = verifier_local.read_text(encoding="utf-8",
                                                    errors="replace")
        except (InfrastructureError, OSError) as exc:
            verifier_fetch_error = exc
        else:
            verifier_fetch_error = None

        facts = result_facts(result_data, verifier_log)
        if verifier_fetch_error is not None and facts["reward"] is not None:
            raise InfrastructureError(
                f"TB trial {run_id}: verifier log fetch failed "
                f"(refetches on retry): {verifier_fetch_error}") from None
        meta.update(facts)
        reward = facts["reward"]

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

        # The scorer verdict, or its absence, as a fact (R2b, module
        # docstring): a reward whose verifier actually ran passes through
        # verbatim; anything else (no reward, or a reward claimed without
        # verifier evidence -- the pseudo zero, in either reward direction)
        # carries NO verdict, so correctness stays None with the facts
        # riding in meta. No mechanical retry either way: the run is
        # stored and acceptance's named rule decides what it counts as.
        if reward is not None and facts["verifier_ran"] is True:
            correctness: Optional[bool] = reward > 0.0
            usage_status = "ok"
        else:
            correctness = None
            usage_status = "unscored"
        outcome = RunOutcome(steps=steps, correctness=correctness)
        self._record_usage(handle, run_id, task_name, usage_pair,
                           usage_status, prompt_name=meta.get("prompt_name"))
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
        steps = reconstruct_steps(traj)
        meta.update(reconstruct_skip_counts(traj))
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
                partial = json.loads(local.read_text(encoding="utf-8"))
                meta["partial_steps"] = reconstruct_steps(partial)
                meta.update(reconstruct_skip_counts(partial))
                meta["remote"]["partial_trajectory"] = path
            log_path = f"{meta['remote_root']}/logs/{run_id}.log"
            out, _, _ = link.run(f"tail -c 2000 {_q(log_path)} 2>/dev/null",
                                 timeout=POLL_TIMEOUT_SECONDS)
            if out.strip():
                meta["runner_log_tail"] = _short(out, limit=2000)
        except (InfrastructureError, OSError, ValueError):
            # Best effort by definition; the failure being raised stands
            # with or without the partial artifacts.
            pass

    def _record_usage(self, handle: Any, run_id: str, task_name: str,
                      usage_pair: Tuple[Optional[int], Optional[int]],
                      status: str,
                      prompt_name: Optional[str] = None) -> None:
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
            prompt=prompt_name or self.task_prompt_name,
            model=str(handle.name),
            in_tokens=in_tokens,
            out_tokens=out_tokens,
            request_id=run_id,
            status=status,
            meta={"tb_task": task_name, "run_id": run_id},
        )
