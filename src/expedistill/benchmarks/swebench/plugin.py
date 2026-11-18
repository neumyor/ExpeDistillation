"""SWE-bench Verified plugin (layer 3): one bridge call per ``run()``.

Execution belongs to the outer swebench-eval harness (mini-SWE-agent in an
E2B sandbox, the official swebench scoring), which runs on its own Python.
The plugin never imports it: every unit is one call of the harness's bridge
script in the harness's own environment
(docs/decisions/swebench_plugin_20261118.md, the interface section):

    <root>/.venv/bin/python <root>/scripts/run_unit.py run --instance <id>
        --out <dir> --model <model id> --max-input-tokens <N>
        [--skill-block-file <file>] [--skill-dir <dir>]

``<root>`` is ``EXPEDISTILL_SWEBENCH_ROOT``, read when tasks are loaded or a
unit runs, never at import. The bridge reads the harness's own ``.env``, so
no endpoint or key passes through this process.

- Skill block: a bare unit sends none, so its prompt is the harness's bare
  prompt. Any other unit sends ``protocol.render_block``'s text, which the
  bridge places before ``<pr_description>``; a folder unit also sends its
  materialized folder, which the bridge writes to /skills in the agent
  sandbox.
- Attempts: every call gets a fresh directory under ``workdir`` (named by
  K1's ``attempt_token``), and the bridge a fresh ``--out`` inside it,
  because the harness's runner returns an existing final unit instead of
  running it again. The block file and the bridge's stdout and stderr are
  kept beside it.
- Outcome: the bridge prints its JSON as the last stdout line. Exit 0 with
  status ``final`` is the scorer's verdict (``resolved``); exit 0 with
  status ``infra`` raises ``InfrastructureError`` (K1 retries the unit; the
  harness has already retried inside the call); exit 2 is a ConfigError;
  any other exit is a CodeBugError.
- Facts: steps from the unit's ``traj.json`` (``trajectory.py``); the first
  user message as ``initial_prompt``; for an unresolved unit a
  ``grader_report`` (patch status, failing tests, the test log's last
  lines -- never the gold patch or the test code); token totals into the
  handle's usage ledger, one row per unit.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, NoReturn, Optional

from expedistill.benchmarks.base import (
    READ_RULE_KEY,
    READ_RULE_MANDATORY,
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.skill_folder import SkillFolder
from expedistill.benchmarks.swebench.prompt_pack import PROMPT_PACK
from expedistill.benchmarks.swebench.protocol import render_block
from expedistill.benchmarks.swebench.trajectory import (
    initial_prompt,
    steps_from_messages,
)
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
)

__all__ = ["ROOT_ENV", "SWEBenchPlugin", "grader_report"]

#: The swebench-eval checkout on this machine.
ROOT_ENV = "EXPEDISTILL_SWEBENCH_ROOT"
_BRIDGE_PYTHON = (".venv", "bin", "python")
_BRIDGE_SCRIPT = ("scripts", "run_unit.py")
#: The bridge's exit code for a refused argument or configuration.
_CONFIG_EXIT = 2

_INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: Terminal color codes of the test log (pytest colors its output).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_ATTEMPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Files of one attempt directory.
_OUT_DIR = "out"
_BLOCK_FILE = "skill_block.txt"
_STDOUT_FILE = "bridge_stdout.txt"
_STDERR_FILE = "bridge_stderr.txt"
_STDERR_TAIL_CHARS = 2000

#: The grader report's bounds (Terminal Bench's character cap; the test
#: log tail the harness's own learners see).
_REPORT_MAX_CHARS = 3000
_REPORT_LOG_LINES = 40
_REPORT_MAX_TESTS = 20
_REPORT_LINE_CHARS = 300
_PATCH_STATUS = {
    "apply_failed": "the patch did not apply",
    "empty_patch": "empty patch",
    "eval_timeout": "the evaluation timed out",
    "unresolved": "the patch applied and the tests ran",
}
_TEST_GROUPS = ("FAIL_TO_PASS", "PASS_TO_PASS")


def _clipped(line: str) -> str:
    line = line.rstrip()
    return (line if len(line) <= _REPORT_LINE_CHARS
            else line[:_REPORT_LINE_CHARS] + " ...")


def grader_report(result: Mapping[str, Any], test_log: Optional[str]) -> str:
    """What the scoring of one unresolved unit says, bounded in size: the
    patch status, the failing FAIL_TO_PASS and PASS_TO_PASS tests, and the
    test log's last lines (kept whole when the bound cuts). ``result`` is
    the unit's ``score/result.json`` ({} when absent). Pure."""
    status = result.get("status")
    text = _PATCH_STATUS.get(status) or (
        str(status) if status else "no grading result")
    parts = [f"Patch status: {text}."]
    error = str(result.get("error") or "").strip()
    if status == "apply_failed" and error:
        parts[0] += f"\nApply error: {_clipped(error.splitlines()[0])}"
    tests = result.get("tests") or {}
    for group in _TEST_GROUPS:
        failed = [str(name) for name in
                  ((tests.get(group) or {}).get("failure") or [])]
        if not failed:
            continue
        names = [f"- {_clipped(name)}" for name in failed[:_REPORT_MAX_TESTS]]
        if len(failed) > _REPORT_MAX_TESTS:
            names.append(f"- ... and {len(failed) - _REPORT_MAX_TESTS} more")
        parts.append(f"Failing {group} tests:\n" + "\n".join(names))
    report = "\n\n".join(parts)
    if len(report) > _REPORT_MAX_CHARS:
        return report[:_REPORT_MAX_CHARS].rstrip() + "\n..."
    lines = _ANSI_RE.sub("", test_log or "").splitlines()[-_REPORT_LOG_LINES:]
    if any(line.strip() for line in lines):
        title = "Last lines of the test log:\n"
        tail = "\n".join(_clipped(line) for line in lines)
        room = _REPORT_MAX_CHARS - len(report) - len(title) - 2
        if len(tail) > room:
            tail = "...\n" + tail[len(tail) - max(room - 4, 0):]
        report += "\n\n" + title + tail
    return report


def _root() -> Path:
    value = os.environ.get(ROOT_ENV, "").strip()
    if not value:
        raise ConfigError(
            f"{ROOT_ENV} is not set: it names the swebench-eval checkout "
            "whose bridge script runs every SWE-bench unit (see "
            ".env.example)")
    root = Path(value).expanduser()
    if not root.is_absolute():
        raise ConfigError(f"{ROOT_ENV} must be an absolute path, got "
                          f"{value!r}")
    return root


def _bridge(root: Path, args: List[str], stdout: Any,
            stderr: Any) -> "subprocess.CompletedProcess[bytes]":
    """Run the bridge to completion. No timeout: one unit is a whole agent
    run plus its scoring."""
    command = [str(root.joinpath(*_BRIDGE_PYTHON)),
               str(root.joinpath(*_BRIDGE_SCRIPT)), *args]
    # The bridge runs on the harness's own interpreter; this process's
    # module path would leak the wrong packages into it.
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONPATH", "PYTHONHOME")}
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL,
                              stdout=stdout, stderr=stderr, cwd=str(root),
                              env=env, check=False)
    except OSError as exc:
        raise ConfigError(
            f"cannot start the SWE-bench bridge {command[1]} with "
            f"{command[0]} ({ROOT_ENV}): {exc}") from None


def _refused(code: int, what: str, stderr: str) -> NoReturn:
    """Raise for a non-zero bridge exit; ``what`` names the call."""
    tail = stderr.strip()[-_STDERR_TAIL_CHARS:] or "(no stderr)"
    if code == _CONFIG_EXIT:
        raise ConfigError(f"the SWE-bench bridge refused {what}: {tail}")
    raise CodeBugError(
        f"the SWE-bench bridge failed on {what} with exit code {code}: "
        f"{tail}")


def _json_output(stdout: str, what: str) -> Any:
    """The bridge's JSON: the whole stdout, else its last non-empty line."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    for text in (stdout, lines[-1] if lines else ""):
        try:
            return json.loads(text)
        except ValueError:
            continue
    raise CodeBugError(
        f"the SWE-bench bridge printed no JSON for {what}; stdout ends "
        f"with {stdout.strip()[-500:]!r}")


def _instance_id(value: Any) -> str:
    instance_id = str(value)
    if not _INSTANCE_RE.match(instance_id):
        raise ConfigError(
            f"invalid SWE-bench instance id {instance_id!r}: must match "
            f"{_INSTANCE_RE.pattern}")
    return instance_id


def _count(value: Any) -> Optional[int]:
    return value if isinstance(value, int) \
        and not isinstance(value, bool) else None


class SWEBenchPlugin(BenchmarkPlugin):
    """SWE-bench Verified: delegate one unit to the swebench-eval bridge,
    then read its unit directory."""

    name = "swebench"
    #: mini-SWE-agent's instance template (``protocol.py``): what every
    #: unit's first message is built from, bare or not.
    task_prompt_name = "SWEBENCH_INSTANCE_TEMPLATE"
    #: A folder unit's block carries this card-reading rule after its
    #: ``<available_skills>`` list.
    skill_folder_prompt_name = "SWEBENCH_SKILL_READ"
    #: ``read_rule: mandatory`` folder units carry this rule instead: a
    #: card read before the first task command.
    skill_folder_mandatory_prompt_name = "SWEBENCH_SKILL_READ_MANDATORY"
    verdict_rule = "scorer"
    playbook_in_skills_block = True
    prompt_pack = PROMPT_PACK

    def __init__(self) -> None:
        self._view_rules = ViewRules(max_obs_chars=8000, head_keep=1000,
                                     tail_keep=1000)

    @property
    def view_rules(self) -> ViewRules:
        """Command output can be a whole file or test log; a larger middle
        cut than the default keeps prompts useful without hiding the cut."""
        return self._view_rules

    def load_tasks(self, tasks_spec) -> List[Task]:
        """An explicit list of instance ids, described by the bridge: the
        instruction is the issue text, ``meta["repo"]`` the repository.
        ``None`` (every instance) and task files are refused: the train and
        test sets are experiment inputs, frozen in the config."""
        if tasks_spec is None or isinstance(tasks_spec, str):
            raise ConfigError(
                "SWE-bench needs an explicit list of instance ids; neither "
                "the whole dataset (None) nor a task file is supported, got "
                f"{tasks_spec!r}")
        if not isinstance(tasks_spec, (list, tuple)):
            raise CodeBugError(
                f"tasks_spec must be None, str or a list, got "
                f"{type(tasks_spec).__name__}")
        ids = [_instance_id(value) for value in tasks_spec]
        if not ids:
            raise ConfigError("the SWE-bench task list is empty")
        args = ["describe"]
        for instance_id in sorted(set(ids)):
            args += ["--instance", instance_id]
        what = f"describe of {len(set(ids))} instance(s)"
        done = _bridge(_root(), args, subprocess.PIPE, subprocess.PIPE)
        if done.returncode != 0:
            _refused(done.returncode, what,
                     done.stderr.decode("utf-8", errors="replace"))
        rows = _json_output(done.stdout.decode("utf-8", errors="replace"),
                            what)
        described: Dict[str, Mapping[str, Any]] = {}
        for row in rows if isinstance(rows, list) else [rows]:
            if not isinstance(row, Mapping) or not all(
                    isinstance(row.get(key), str) for key in
                    ("instance_id", "repo", "problem_statement")):
                raise CodeBugError(
                    "the SWE-bench bridge's describe must print a list of "
                    "{instance_id, repo, problem_statement} strings, got "
                    f"{str(row)[:300]!r}")
            described[row["instance_id"]] = row
        missing = sorted(set(ids) - set(described))
        if missing:
            raise CodeBugError(
                f"the SWE-bench bridge's describe left out {missing}")
        return [Task(task_id=instance_id,
                     instruction=described[instance_id]["problem_statement"],
                     meta={"repo": described[instance_id]["repo"]})
                for instance_id in ids]

    def run(self, task: Task, handle: Any, skills_block: str,
            workdir: Any) -> RunOutcome:
        """One unit, one bridge call. ``InfrastructureError`` when the
        harness closed the unit as an infrastructure failure (K1 retries);
        ConfigError / CodeBugError stop the phase (B6)."""
        instance_id = _instance_id(task.task_id)
        model = getattr(handle, "name", None)
        if not isinstance(model, str) or not model.strip():
            raise CodeBugError(
                f"the SWE-bench runner needs handle.name (the model id), "
                f"got {handle!r}")
        max_input_tokens = getattr(handle, "max_input_tokens", None)
        if max_input_tokens is None:
            raise ConfigError(
                f"the SWE-bench runner needs the {getattr(handle, 'role', '')}"
                " handle's max_input_tokens (the config's input budget for "
                "the role): the agent's input ceiling is part of the setting")
        if isinstance(max_input_tokens, bool) \
                or not isinstance(max_input_tokens, int) \
                or max_input_tokens <= 0:
            raise CodeBugError(
                "handle.max_input_tokens must be a positive int, got "
                f"{max_input_tokens!r}")
        root = _root()
        folder = SkillFolder.from_task(task)
        mandatory = folder is not None and (
            task.meta["skill_folder"].get(READ_RULE_KEY)
            == READ_RULE_MANDATORY)
        block = render_block(folder=folder, skills_block=skills_block or "",
                             mandatory=mandatory)
        if mandatory:
            prompt_name = self.skill_folder_mandatory_prompt_name
        elif folder is not None:
            prompt_name = self.skill_folder_prompt_name
        else:
            prompt_name = self.task_prompt_name
        attempt = _fresh_attempt_dir(Path(workdir).resolve(), task)
        out = attempt / _OUT_DIR
        args = ["run", "--instance", instance_id, "--out", str(out),
                "--model", model,
                "--max-input-tokens", str(max_input_tokens)]
        if block:
            block_file = attempt / _BLOCK_FILE
            block_file.write_text(block, encoding="utf-8")
            args += ["--skill-block-file", str(block_file)]
        folder_dir = task.meta.get("skill_folder_dir") \
            if folder is not None else None
        if folder_dir:
            args += ["--skill-dir", str(Path(folder_dir).resolve())]
        what = f"unit {instance_id} ({attempt})"
        with open(attempt / _STDOUT_FILE, "wb") as stdout, \
                open(attempt / _STDERR_FILE, "wb") as stderr:
            code = _bridge(root, args, stdout, stderr).returncode
        stdout_text = (attempt / _STDOUT_FILE).read_text(
            encoding="utf-8", errors="replace")
        if code < 0:
            # Killed by a signal (the host's OOM killer, a stop of this
            # process's group): an infrastructure failure, retried by K1.
            raise InfrastructureError(
                f"the SWE-bench bridge for {what} was killed by signal "
                f"{-code}")
        if code != 0:
            _refused(code, what, (attempt / _STDERR_FILE).read_text(
                encoding="utf-8", errors="replace"))
        payload = _json_output(stdout_text, what)
        if not isinstance(payload, Mapping) \
                or payload.get("status") not in ("final", "infra") \
                or not isinstance(payload.get("unit_dir"), str):
            raise CodeBugError(
                f"the SWE-bench bridge's result for {what} is not "
                f"{{status: final|infra, unit_dir, ...}}: "
                f"{str(payload)[:500]!r}")
        tokens = (_count(payload.get("prompt_tokens")),
                  _count(payload.get("completion_tokens")))
        if payload["status"] == "infra":
            self._record_usage(handle, str(out), instance_id, tokens,
                               "infra", prompt_name)
            raise InfrastructureError(
                f"SWE-bench {what} ended in an infrastructure failure "
                f"(score status {payload.get('score_status')!r}): "
                f"{payload.get('error') or 'no error text'}")
        meta = {"model": model, "max_input_tokens": max_input_tokens,
                "prompt_name": prompt_name}
        # A relative unit directory is relative to the bridge's cwd, root.
        return self._outcome(payload, root / payload["unit_dir"], handle,
                             out, instance_id, tokens, meta)

    def _outcome(self, payload: Mapping[str, Any], unit_dir: Path,
                 handle: Any, out: Path, instance_id: str, tokens: Any,
                 meta: Dict[str, Any]) -> RunOutcome:
        """The RunOutcome of a final unit, read from its unit directory."""
        traj_path = unit_dir / "traj.json"
        traj = (json.loads(traj_path.read_text(encoding="utf-8"))
                if traj_path.is_file() else {})
        messages = traj.get("messages") or []
        resolved = payload.get("resolved")
        meta.update({
            "instance_id": instance_id,
            "exit_status": payload.get("exit_status"),
            "score_status": payload.get("score_status"),
            "n_steps": payload.get("n_steps"),
            "prompt_tokens": tokens[0],
            "completion_tokens": tokens[1],
            "agent_wall_sec": payload.get("agent_wall_sec"),
            "bridge_unit_dir": str(unit_dir),
        })
        if payload.get("error"):
            meta["error"] = str(payload["error"])
        prompt = initial_prompt(messages)
        if prompt:
            meta["initial_prompt"] = prompt
        if resolved is not True:
            score = unit_dir / "score"
            result_path = score / "result.json"
            log_path = score / "test_output.txt"
            meta["grader_report"] = grader_report(
                json.loads(result_path.read_text(encoding="utf-8"))
                if result_path.is_file() else {},
                log_path.read_text(encoding="utf-8", errors="replace")
                if log_path.is_file() else None)
        outcome = RunOutcome(steps=steps_from_messages(messages),
                             correctness=resolved, meta=meta)
        self._record_usage(handle, str(out), instance_id, tokens,
                           "ok" if resolved is not None else "unscored",
                           meta["prompt_name"])
        return outcome

    def _record_usage(self, handle: Any, request_id: str, instance_id: str,
                      tokens: Any, status: str, prompt_name: str) -> None:
        """One usage row per unit, from the bridge's agent totals (null
        when it reported none, never 0)."""
        recorder = getattr(handle, "usage", None)
        if recorder is None:
            return
        recorder.record(
            role=str(getattr(handle, "role", "executor")),
            prompt=prompt_name,
            model=str(handle.name),
            in_tokens=tokens[0],
            out_tokens=tokens[1],
            request_id=request_id,
            status=status,
            meta={"swebench_instance": instance_id},
        )


def _fresh_attempt_dir(workdir: Path, task: Task) -> Path:
    """A new directory under ``workdir`` for one call: named by K1's
    ``attempt_token`` (``attempt`` without one), with a counter when that
    name is taken (a resume after a crash reuses the token)."""
    meta = task.meta if isinstance(task.meta, dict) else {}
    base = str(meta.get("attempt_token") or "attempt")
    if not _ATTEMPT_RE.match(base):
        raise ConfigError(
            f"attempt_token {base!r} must match {_ATTEMPT_RE.pattern}")
    workdir.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        path = workdir / (base if index == 0 else f"{base}-{index}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            index += 1
