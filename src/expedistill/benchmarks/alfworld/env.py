"""ALFWorld environment (layer 3): the eval repo's TextWorld env behind a subprocess.

Design decision (see the plugin spec): ``alfworld`` and ``textworld`` are only
importable inside the outer ``benchmark/alfworld-eval`` venv, so this process
must never import them -- mixing site-packages across venvs is exactly the
incident class to avoid. ``ALFWorldEnv`` therefore spawns the eval venv's
python (``EXPEDISTILL_ALFWORLD_VENV``) with ``PYTHONPATH`` pointing only at
the eval repo (``EXPEDISTILL_ALFWORLD_EVAL_DIR/src``) and the three
``ALFWORLD_*`` variables, running ``ADAPTER_SOURCE``: a stdlib-only script
that loads one game file through the eval repo's ``AlfworldTextEnv`` and
speaks the line protocol below. Importing this module (and the whole plugin
package) needs no environment variables at all; every variable is resolved at
spawn time, and a missing one is a ``ConfigError``, not an ``ImportError``.

Line protocol (parent -> child on stdin, child -> parent on stdout, one JSON
message per line; payloads are JSON, so observations never break framing):

  H <json>   handshake once the game loaded: ``{"budget": <int step budget>}``
  O <json>   one observation, self-describing (exactly one per action, plus
             one for the initial state): ``{"text": <str>, "won": <bool>,
             "done": <bool>, "reason": "won"|"budget"|"done"|null}``
  X <json>   fatal child error: ``{"error": <str>}``; the child exits non-zero

  parent -> child: one action per line (already extracted and normalized by
  :func:`extract_action`); EOF on stdin ends the episode cleanly. Exactly one
  O answers every action, so a mid-episode step can never block waiting for a
  separate end marker; ``won``/``done`` in that reply flag termination. A
  child that exits without replying is an infrastructure failure, never the
  agent's fault.

Timeouts: every read is bounded by a deadline (default 120 s, override with
``EXPEDISTILL_ALFWORLD_READ_TIMEOUT_SECONDS``) enforced through a reader
thread and a queue, not SIGALRM -- so the env works from any thread (B5) and
a deadline can never orphan the child: on deadline, death or protocol error
the child is terminated and reaped by its own PID (never pgrep). ``close()``
and the context manager guarantee the same.

Adapted from the old SkillExpand adapter (ExpeToSkill-merged
src/skillexpand/benchmarks/alfworld.py): the ``put ... in/on`` action
normalization, the repeated-action episode termination, the "Task is SOLVED."
success observation, the "You arrive at loc ..." observation stripping and
the think-only replies consuming a step with an "OK." observation all come
from there; the old in-process engine access is replaced by the subprocess
protocol above.
"""

import json
import os
import re
import subprocess
import tempfile
import threading
from collections import deque
from pathlib import Path
from queue import Empty, Queue
from typing import Optional, Tuple

from expedistill.benchmarks.base import BaseEnv
from expedistill.infra.reliability import CodeBugError, ConfigError, InfrastructureError

__all__ = [
    "ADAPTER_SOURCE",
    "BENCH_SRC_ENV",
    "CONFIG_ENV",
    "DATA_ENV",
    "DEFAULT_READ_TIMEOUT_SECONDS",
    "DEFAULT_SEED",
    "EVAL_DIR_ENV",
    "READ_TIMEOUT_ENV",
    "VENV_ENV",
    "ALFWorldEnv",
    "extract_action",
    "parse_adapter_line",
    "resolve_gamefile",
]

#: Environment variables (names are shared with .env.example).
VENV_ENV = "EXPEDISTILL_ALFWORLD_VENV"
EVAL_DIR_ENV = "EXPEDISTILL_ALFWORLD_EVAL_DIR"
DATA_ENV = "ALFWORLD_DATA"
CONFIG_ENV = "ALFWORLD_CONFIG"
BENCH_SRC_ENV = "ALFWORLD_BENCH_SRC"
READ_TIMEOUT_ENV = "EXPEDISTILL_ALFWORLD_READ_TIMEOUT_SECONDS"

#: Game loading in ALFWorld takes seconds, not milliseconds; the deadline only
#: guards against a hung adapter, so it stays generous.
DEFAULT_READ_TIMEOUT_SECONDS = 120.0
DEFAULT_SEED = 42

#: The eval repo's data layout: <ALFWORLD_DATA>/json_2.1.1/<split>/<task>/.../game.tw-pddl
GAME_SUFFIX = "game.tw-pddl"
DATA_ROOT_SUBDIR = "json_2.1.1"
DEFAULT_CONFIG_RELPATH = ("configs", "textworld.yaml")

#: Child -> parent message tags (see the module docstring for the protocol).
_TAGS = ("H", "O", "X")

# Old-adapter action knowledge ------------------------------------------------
#: ``put apple 1 in fridge 1`` and ``... on fridge 1`` both mean the engine's
#: ``put apple 1 in/on fridge 1``; normalize instead of failing the action.
_PUT_RE = re.compile(r"put (\w+\s*\d+) (?:in|on) (\w+\s*\d+)")
#: A ReAct-style thought (``> think: ...``): never sent to the engine.
_THINK_RE = re.compile(r"think(?::|\s|$)", re.I)
#: The ``>`` command prefix and markdown-style bullets.
_LEAD_RE = re.compile(r"^(?:>|[-*]\s*)+\s*")


ADAPTER_SOURCE = '''\
"""ALFWorld episode adapter: runs INSIDE the eval repo's venv, never ours.

Executed as ``<venv python> adapter.py --gamefile G --config C --seed S`` with
``PYTHONPATH`` pointing at the eval repo's src; imports the eval repo's
``AlfworldTextEnv`` and speaks the line protocol documented in
``expedistill.benchmarks.alfworld.env``:

  parent -> child: one action per line on stdin; EOF ends the episode
  child -> parent, one message per stdout line:
    H <json> handshake once the game loaded: {"budget": <int>}
    O <json> one observation per action (and one for the initial state):
            {"text": <str>, "won": <bool>, "done": <bool>, "reason": ...}
    X <json> {"error": <str>} fatal; exit(1) afterwards

The protocol stream is the ORIGINAL stdout, saved before the swap below: the
alfworld/textworld stack prints to stdout on some code paths, and a stray
line would corrupt the protocol, so library stdout is redirected to stderr
first. Only the standard library is imported here besides the eval repo and
the alfworld stack its venv provides.
"""
import argparse
import json
import os
import sys

TASK_SOLVED = "Task is SOLVED."

_PROTOCOL = sys.stdout


def _setup_streams():
    """Reserve the real stdout for the protocol; send library output to stderr."""
    global _PROTOCOL
    saved = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    _PROTOCOL = os.fdopen(saved, "w", encoding="utf-8")


def emit(tag, payload):
    _PROTOCOL.write(tag + " " + json.dumps(payload) + "\\n")
    _PROTOCOL.flush()


def observation_message(text, won=False, done=False, reason=None):
    return {"text": text, "won": bool(won), "done": bool(done), "reason": reason}


def process_observation(text):
    # ExpeL/ReAct convention kept from the old adapter: the engine's
    # "You arrive at loc <pddl id>. " location echo is noise for the agent.
    if text.startswith("You arrive at loc "):
        text = text[text.find(". ") + 2:]
    return text


def main():
    _setup_streams()
    parser = argparse.ArgumentParser()
    parser.add_argument("--gamefile", required=True)
    parser.add_argument("--config", default="")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    from alfworld_eval.env import AlfworldTextEnv, split_for_gamefile

    config = args.config or os.environ.get("ALFWORLD_CONFIG") or "configs/textworld.yaml"
    env = AlfworldTextEnv(
        config_path=config,
        split=split_for_gamefile(args.gamefile),
        seed=args.seed,
        gamefiles=[args.gamefile],
    )
    try:
        observation = env.reset()
        emit("H", {"budget": int(env.step_budget)})
        emit("O", observation_message(process_observation(observation.text)))
        steps = 0
        while True:
            line = sys.stdin.readline()
            if not line:
                break  # EOF: the parent closed the episode
            action = line.strip()
            if not action:
                continue
            steps += 1
            result = env.step(action)
            if result.won:
                emit("O", observation_message(TASK_SOLVED, won=True, done=True,
                                              reason="won"))
                break
            if steps >= env.step_budget:
                emit("O", observation_message(process_observation(
                    result.observation.text), done=True, reason="budget"))
                break
            if result.done:
                emit("O", observation_message(process_observation(
                    result.observation.text), done=True, reason="done"))
                break
            emit("O", observation_message(process_observation(
                result.observation.text)))
    finally:
        env.close()


try:
    main()
except SystemExit:
    raise
except BaseException as exc:  # reported through the protocol, never a traceback on stdout
    try:
        emit("X", {"error": type(exc).__name__ + ": " + str(exc)})
    finally:
        sys.exit(1)
'''


def parse_adapter_line(line: str) -> Tuple[str, object]:
    """Parse one child stdout line into ``(tag, payload)``.

    Raises ``ValueError`` on anything that is not a protocol message; the env
    turns that into an ``InfrastructureError`` (a protocol violation means
    the adapter is broken or mismatched, not that the agent failed).
    """
    text = str(line).strip()
    if not text:
        raise ValueError("blank line on the protocol stream")
    tag, _, rest = text.partition(" ")
    if tag not in _TAGS:
        raise ValueError(f"unknown protocol tag in {text[:80]!r}")
    try:
        payload = json.loads(rest)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON payload after {tag!r}: {exc}") from None
    if not isinstance(payload, dict):
        raise ValueError(f"{tag} payload must be a JSON object")
    if tag == "H" and "budget" not in payload:
        raise ValueError("H payload must carry a 'budget' key")
    if tag == "O":
        if not isinstance(payload.get("text"), str):
            raise ValueError("O payload must carry a string 'text'")
        for flag in ("won", "done"):
            if flag in payload and not isinstance(payload[flag], bool):
                raise ValueError(f"O payload flag {flag!r} must be a boolean")
    if tag == "X" and not isinstance(payload.get("error"), str):
        raise ValueError("X payload must carry a string 'error'")
    return tag, payload


def extract_action(raw: object) -> Tuple[str, bool]:
    """Per-plugin action parsing: a model reply -> one engine command.

    Returns ``(command, is_think)``. The shared react loop hands the whole
    stripped model reply to the env, so ALFWorld knowledge lives here:

    - only the first non-empty line is a command (the line protocol is
      line-based; a multi-line reply would otherwise be two actions);
    - a leading ``>`` or list bullet is the ReAct command prefix, dropped;
    - a reply that is only a thought (``think: ...``) never reaches the
      engine: it consumes a step and answers ``OK.``, exactly like the old
      adapter's ``_step_without_action``;
    - ``put X in/on Y`` is normalized to the engine's ``in/on`` form.

    An empty result means the reply contained no executable command; the env
    answers with an invalid-action observation instead of raising.
    """
    text = str(raw).strip()
    if not text:
        return "", False
    command = _LEAD_RE.sub("", text.splitlines()[0].strip()).strip()
    if _THINK_RE.match(command):
        return "", True
    if command.lower().startswith("put"):
        match = _PUT_RE.match(command)
        if match is not None:
            command = f"put {match.group(1)} in/on {match.group(2)}"
    return command, False


def resolve_gamefile(task_id: str, data_root: str) -> str:
    """Resolve a task id to an existing absolute game-file path.

    Ids are paths relative to ``$ALFWORLD_DATA`` (the convention of the eval
    repo's items.json manifests); absolute paths pass through. A missing game
    is a ``ConfigError``: the task list is frozen in the manifest, so an id
    that does not resolve is a configuration problem, not a runtime one.
    """
    raw = str(task_id).strip()
    path = Path(raw).expanduser()
    if path.is_absolute():
        if path.is_file():
            return str(path)
        raise ConfigError(f"ALFWorld gamefile {raw!r} does not exist")
    candidate = Path(data_root).expanduser() / raw
    if candidate.is_file():
        return str(candidate.resolve())
    raise ConfigError(
        f"ALFWorld task id {task_id!r} does not resolve to a game file under "
        f"{str(data_root)!r}")


class ALFWorldEnv(BaseEnv):
    """One ALFWorld episode driven through the subprocess line protocol.

    Contract mapping (``BaseEnv``): ``reset`` spawns the adapter and returns
    the initial observation (the room description; the handshake before it
    fixes ``max_steps`` to the engine's own step budget); ``step`` sends one
    action and reads exactly one self-describing observation reply, flagging
    ``terminated``/``truncated`` from its ``won``/``done`` fields;
    ``success_fn`` reads the reported ``won``. Invalid actions are answered
    by the engine with "Nothing happens." -- they never raise. A dead or
    hung adapter raises ``InfrastructureError`` (run_react converts it to an
    infra-error outcome); ``close`` terminates and reaps the child so
    orphans are impossible.
    """

    def __init__(
        self,
        task_id: str,
        *,
        venv_python: Optional[str] = None,
        eval_dir: Optional[str] = None,
        data_root: Optional[str] = None,
        config_path: Optional[str] = None,
        bench_src: Optional[str] = None,
        read_timeout: Optional[float] = None,
        seed: Optional[int] = None,
        adapter_source: Optional[str] = None,
    ):
        self.task_id = str(task_id)
        self.venv_python = self._resolve_path(venv_python, VENV_ENV, kind="file")
        self.eval_dir = self._resolve_path(eval_dir, EVAL_DIR_ENV, kind="dir")
        self.data_root = self._resolve_path(data_root, DATA_ENV, kind="dir")
        self.config_path = self._resolve_config(config_path)
        self.bench_src = self._resolve_path(bench_src, BENCH_SRC_ENV, kind="dir",
                                            required=False)
        self.read_timeout = self._resolve_read_timeout(read_timeout)
        self.seed = DEFAULT_SEED if seed is None else int(seed)
        self.adapter_source = ADAPTER_SOURCE if adapter_source is None else adapter_source
        # Validated here so a bad id fails fast, before any subprocess spawn.
        self.gamefile = resolve_gamefile(self.task_id, self.data_root)

        self.terminated = False
        self.truncated = False
        self.termination_reason: Optional[str] = None
        self._won = False
        self.curr_step = 0
        self.max_steps: Optional[int] = None
        self._last_action: Optional[str] = None
        self._initial_observation: Optional[str] = None

        self._proc: Optional[subprocess.Popen] = None
        self._messages: Optional[Queue] = None
        self._threads = []
        self._stderr_tail = deque(maxlen=30)
        self._script_dir = None

    # -- configuration -------------------------------------------------------

    @staticmethod
    def _resolve_path(value, env_name, kind, required=True):
        raw = value if value is not None else os.environ.get(env_name, "")
        if not str(raw).strip():
            if required:
                raise ConfigError(
                    f"{env_name} is not set and no explicit value was given; "
                    "the ALFWorld plugin needs the outer benchmark/alfworld-eval "
                    "environment (see .env.example)")
            return None
        path = Path(str(raw)).expanduser()
        if not path.exists():
            raise ConfigError(f"{env_name} points to a missing path: {raw!r}")
        if kind == "file" and not path.is_file():
            raise ConfigError(f"{env_name} must be a file, got {raw!r}")
        if kind == "dir" and not path.is_dir():
            raise ConfigError(f"{env_name} must be a directory, got {raw!r}")
        return str(path)

    def _resolve_config(self, config_path):
        if config_path is not None:
            path = Path(config_path).expanduser()
            if not path.is_file():
                raise ConfigError(f"ALFWorld config {config_path!r} does not exist")
            return str(path)
        raw = os.environ.get(CONFIG_ENV, "")
        if str(raw).strip():
            return self._resolve_path(raw, CONFIG_ENV, kind="file")
        # Fall back to the eval repo's own default config so the three
        # ALFWORLD_* variables stay the only required ones for standard use.
        candidate = Path(self.eval_dir).joinpath(*DEFAULT_CONFIG_RELPATH)
        if not candidate.is_file():
            raise ConfigError(
                f"{CONFIG_ENV} is not set and the default config "
                f"{candidate!s} does not exist")
        return str(candidate)

    @staticmethod
    def _resolve_read_timeout(read_timeout):
        raw = read_timeout if read_timeout is not None \
            else os.environ.get(READ_TIMEOUT_ENV, "")
        if str(raw).strip() == "":
            return DEFAULT_READ_TIMEOUT_SECONDS
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            raise ConfigError(
                f"{READ_TIMEOUT_ENV} must be a number, got {raw!r}") from None
        if seconds <= 0 or seconds != seconds or seconds == float("inf"):
            raise ConfigError(
                f"{READ_TIMEOUT_ENV} must be finite and positive, got {raw!r}")
        return seconds

    # -- BaseEnv interface ---------------------------------------------------

    def reset(self):
        """Spawn the adapter and return the initial observation.

        Idempotent while the episode is untouched: ``run()`` pre-resets the
        env to fold the room description into the instruction, and run_react
        then calls ``reset`` again -- that second call must not respawn (one
        subprocess per episode, not two).
        """
        if self._proc is not None and self.curr_step == 0:
            return self._initial_observation
        self._shutdown_child()
        self._stderr_tail.clear()
        self._spawn_child()
        handshake = self._expect("H", "handshake")
        budget = handshake.get("budget")
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
            self._shutdown_child()
            raise InfrastructureError(
                f"adapter handshake has no usable step budget: {handshake!r}")
        self.max_steps = budget
        initial = self._expect("O", "initial observation")
        self._initial_observation = initial["text"]
        self.terminated = False
        self.truncated = False
        self.termination_reason = None
        self._won = False
        self.curr_step = 0
        self._last_action = None
        return self._initial_observation

    def step(self, action: str) -> str:
        if self._proc is None:
            raise CodeBugError(
                "ALFWorldEnv.step called with no live adapter: call reset() "
                "first (reset also restarts the episode after a failure)")
        self.curr_step += 1
        command, is_think = extract_action(action)
        if is_think:
            # Thought-only reply: consume a step, never touch the engine (old
            # adapter's _step_without_action semantics).
            return "OK."
        if not command:
            return "Nothing happens."
        repeated = command == self._last_action
        observation = self._exchange(command)
        self._last_action = command
        if repeated:
            # Old wrapper semantics: a repeated identical action ends the
            # episode. Unlike the old adapter we mark it in the observation
            # too, so a later segmentation/skill pass can see WHY the episode
            # stopped instead of a bare "Nothing happens."
            self.terminated = True
            self.termination_reason = "repeated_action"
            observation = (
                f"{observation}\n(The action {command!r} was repeated; "
                "the episode is terminated.)")
        return observation

    def success_fn(self) -> bool:
        return self._won

    def is_truncated(self) -> bool:
        if self.truncated:
            return True
        return self.max_steps is not None and self.curr_step > self.max_steps

    # -- subprocess driving --------------------------------------------------

    def _exchange(self, command: str) -> str:
        assert self._proc is not None
        try:
            self._proc.stdin.write(command + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._shutdown_child()
            raise InfrastructureError(
                f"ALFWorld adapter stdin broke (task {self.task_id!r}): "
                f"{type(exc).__name__}: {exc}") from exc
        # Exactly one self-describing O answers every action: a mid-episode
        # step must never block waiting for a separate end marker.
        tag, payload = self._read_message()
        if tag == "X":
            self._shutdown_child()
            raise InfrastructureError(
                f"ALFWorld adapter failed (task {self.task_id!r}): "
                f"{payload.get('error')}")
        if tag != "O":
            self._shutdown_child()
            raise InfrastructureError(
                f"adapter protocol violation: expected an observation for "
                f"task {self.task_id!r}, got {tag!r}")
        self._apply_observation(payload)
        return payload["text"]

    def _apply_observation(self, payload: dict) -> None:
        self._won = self._won or bool(payload.get("won"))
        if not payload.get("done") and not payload.get("won"):
            return
        reason = str(payload.get("reason") or "done")
        self.termination_reason = reason
        if payload.get("won"):
            self.terminated = True
        elif reason == "budget":
            # Budget exhaustion is truncation, not termination: run_react
            # then reports "truncated" and scores the episode as failure.
            self.truncated = True
        else:
            self.terminated = True

    def _expect(self, wanted: str, what: str):
        tag, payload = self._read_message()
        if tag == "X":
            self._shutdown_child()
            raise InfrastructureError(
                f"ALFWorld adapter failed while awaiting the {what} "
                f"(task {self.task_id!r}): {payload.get('error')}")
        if tag != wanted:
            self._shutdown_child()
            raise InfrastructureError(
                f"adapter protocol violation: expected {wanted} ({what}) for "
                f"task {self.task_id!r}, got {tag!r}")
        return payload

    def _read_message(self):
        if self._proc is None or self._messages is None:
            raise InfrastructureError(
                f"the ALFWorld adapter is not running (task {self.task_id!r})")
        try:
            kind, data = self._messages.get(timeout=self.read_timeout)
        except Empty:
            self._shutdown_child()
            raise InfrastructureError(
                f"ALFWorld adapter read deadline of {self.read_timeout:g}s "
                f"exceeded (task {self.task_id!r})") from None
        if kind == "eof":
            self._shutdown_child()
            tail = "".join(self._stderr_tail).strip()
            raise InfrastructureError(
                f"ALFWorld adapter exited before replying (task "
                f"{self.task_id!r}); stderr tail: {tail!r}")
        try:
            return parse_adapter_line(data)
        except ValueError as exc:
            self._shutdown_child()
            raise InfrastructureError(
                f"ALFWorld adapter protocol violation (task {self.task_id!r}): "
                f"{exc}") from exc

    def _spawn_child(self) -> None:
        self._script_dir = tempfile.TemporaryDirectory(
            prefix="expedistill-alfworld-adapter-")
        script = Path(self._script_dir.name) / "adapter.py"
        script.write_text(self.adapter_source, encoding="utf-8")

        child_env = dict(os.environ)
        # ONLY the eval repo goes on the child's PYTHONPATH: inheriting our
        # own venv's paths would let the foreign interpreter import our
        # packages -- the cross-venv contamination this split exists to avoid.
        child_env["PYTHONPATH"] = str(Path(self.eval_dir) / "src")
        child_env["ALFWORLD_DATA"] = self.data_root
        if self.config_path is not None:
            child_env["ALFWORLD_CONFIG"] = self.config_path
        if self.bench_src is not None:
            child_env["ALFWORLD_BENCH_SRC"] = self.bench_src

        command = [
            self.venv_python, str(script),
            "--gamefile", self.gamefile,
            "--config", self.config_path or "",
            "--seed", str(self.seed),
        ]
        try:
            self._proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self._shutdown_child()
            raise InfrastructureError(
                f"could not start the ALFWorld adapter {self.venv_python!r}: "
                f"{exc}") from exc
        self._messages = Queue()
        self._threads = [
            threading.Thread(target=self._pump_stdout, daemon=True),
            threading.Thread(target=self._pump_stderr, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _pump_stdout(self) -> None:
        try:
            while True:
                # readline, not iteration: iterating a pipe reads ahead and can
                # hold a finished line back behind the buffering deadline.
                line = self._proc.stdout.readline()
                if not line:
                    break
                self._messages.put(("line", line))
        except Exception:  # the parent may close the pipe under us
            pass
        finally:
            self._messages.put(("eof", None))

    def _pump_stderr(self) -> None:
        try:
            for line in self._proc.stderr:
                self._stderr_tail.append(line)
        except Exception:
            pass

    def _shutdown_child(self) -> None:
        """Terminate and reap the child by its own PID; idempotent."""
        proc, self._proc = self._proc, None
        if proc is not None:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass
            if proc.poll() is None:
                try:  # closing stdin lets a well-behaved child exit on EOF
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                if proc.poll() is None:
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            pass
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads = []
        if self._script_dir is not None:
            try:
                self._script_dir.cleanup()
            except Exception:
                pass
            self._script_dir = None

    def close(self) -> None:
        """Terminate the adapter; safe to call always, never leaves orphans."""
        self._shutdown_child()

    def __enter__(self) -> "ALFWorldEnv":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.close()
        return False
