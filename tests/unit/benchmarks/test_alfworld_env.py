"""Tests for benchmarks/alfworld/env.py.

No eval venv, no real alfworld: the protocol layer is tested against recorded
transcripts (tests/fixtures/alfworld/), and the process lifecycle against a
FAKE adapter -- a tiny stub script spawned with sys.executable that speaks the
same line protocol. That proves the real thing's contract (spawn / exchange /
deadline / death / reaping) without importing textworld.
"""

import os
import sys
from pathlib import Path

import pytest

from expedistill.benchmarks.alfworld.env import (
    BENCH_SRC_ENV,
    CONFIG_ENV,
    DATA_ENV,
    EVAL_DIR_ENV,
    VENV_ENV,
    ALFWorldEnv,
    extract_action,
    parse_adapter_line,
    resolve_gamefile,
)
from expedistill.infra.reliability import CodeBugError, ConfigError, InfrastructureError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "alfworld"

#: A fake adapter speaking the documented line protocol. Budget 3. Actions:
#: "die" exits without replying, "hang" stalls past any read deadline,
#: "garbage" breaks the protocol, "win" solves the task; anything else is
#: echoed.
FAKE_ADAPTER_SOURCE = '''\
import json
import sys
import time

def emit(tag, payload):
    sys.stdout.write(tag + " " + json.dumps(payload) + "\\n")
    sys.stdout.flush()

def obs(text, won=False, done=False, reason=None):
    return {"text": text, "won": won, "done": done, "reason": reason}

BUDGET = 3
emit("H", {"budget": BUDGET})
emit("O", obs("You are in the middle of a room. You see a fridge 1."))
steps = 0
while True:
    line = sys.stdin.readline()
    if not line:
        break
    action = line.strip()
    if not action:
        continue
    if action == "die":
        sys.stderr.write("fake adapter dying on request\\n")
        sys.exit(3)
    if action == "hang":
        time.sleep(30)
    if action == "garbage":
        sys.stdout.write("this line breaks the protocol\\n")
        sys.stdout.flush()
        continue
    steps += 1
    if action == "win":
        emit("O", obs("Task is SOLVED.", won=True, done=True, reason="won"))
        break
    if steps >= BUDGET:
        emit("O", obs("you did: " + action, done=True, reason="budget"))
        break
    emit("O", obs("you did: " + action))
'''

GAME_REL = "json_2.1.1/valid_seen/task-1/trial-1/game.tw-pddl"


def make_env(tmp_path, **overrides):
    """An ALFWorldEnv wired to the fake adapter, with all env vars bypassed."""
    data_root = tmp_path / "alfworld-data"
    game = data_root / GAME_REL
    game.parent.mkdir(parents=True)
    game.write_text("dummy game file", encoding="utf-8")
    config = tmp_path / "textworld.yaml"
    config.write_text("dummy: true\n", encoding="utf-8")
    params = dict(
        venv_python=sys.executable,
        eval_dir=str(tmp_path),
        data_root=str(data_root),
        config_path=str(config),
        adapter_source=FAKE_ADAPTER_SOURCE,
        read_timeout=10.0,
    )
    params.update(overrides)
    return ALFWorldEnv(GAME_REL, **params)


# ---------------------------------------------------------------------------
# Protocol parsing, against recorded fixtures
# ---------------------------------------------------------------------------

def read_fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def test_success_transcript_parses():
    messages = [parse_adapter_line(line) for line in read_fixture("episode_success.txt")]
    assert [tag for tag, _ in messages] == ["H", "O", "O", "O"]
    assert messages[0][1] == {"budget": 50}
    assert messages[1][1]["text"].startswith("You are in the middle of a room.")
    assert messages[2][1]["text"] == "You pick up the apple 1 from the countertop 1."
    assert messages[3][1] == {"text": "Task is SOLVED.", "won": True,
                              "done": True, "reason": "won"}


def test_budget_failure_transcript_parses():
    messages = [parse_adapter_line(line) for line in
                read_fixture("episode_failure_budget.txt")]
    assert [tag for tag, _ in messages] == ["H", "O", "O", "O", "O", "O"]
    texts = [payload["text"] for tag, payload in messages if tag == "O"]
    assert "Nothing happens." in texts
    # Only the child strips "You arrive at loc ...": the recorded transcript
    # shows the stripped form, and the parent accepts it back as data.
    assert texts[3] == "On the countertop 1, you see a bread 1, and a knife 1."
    assert messages[-1][1] == {"text": "Nothing happens.", "won": False,
                               "done": True, "reason": "budget"}


def test_adapter_error_transcript_parses():
    tag, payload = parse_adapter_line(read_fixture("adapter_error.txt")[0])
    assert tag == "X"
    assert "ALFWORLD_DATA is not set" in payload["error"]


def test_parse_adapter_line_rejects_garbage():
    for bad in ["", "   ", "not a protocol line",
                "Q {\"budget\": 3}", "O {\"not\": \"a string\"}",
                "O \"a plain string\"", "E {\"won\": true}",
                "H [1, 2]", "O \"unterminated", "H budget"]:
        with pytest.raises(ValueError):
            parse_adapter_line(bad)


# ---------------------------------------------------------------------------
# Action extraction (per-plugin knowledge that moved out of the old L1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("go to fridge 1", "go to fridge 1"),
    ("> go to fridge 1", "go to fridge 1"),
    ("> go to fridge 1\nbecause I need the apple", "go to fridge 1"),
    ("- open cabinet 2", "open cabinet 2"),
    ("put apple 1 in fridge 1", "put apple 1 in/on fridge 1"),
    ("> put apple 1 on fridge 1", "put apple 1 in/on fridge 1"),
    ("put apple 1 in/on fridge 1", "put apple 1 in/on fridge 1"),
    ("put apple 1 near fridge 1", "put apple 1 near fridge 1"),  # no rewrite
    ("  GO TO fridge 1  ", "GO TO fridge 1"),
])
def test_extract_action_commands(raw, expected):
    assert extract_action(raw) == (expected, False)


@pytest.mark.parametrize("raw", [
    "think: I need to find the apple",
    "> think: check the fridge first",
    "think",
])
def test_extract_action_thoughts_never_reach_the_engine(raw):
    assert extract_action(raw) == ("", True)


def test_extract_action_empty_reply():
    assert extract_action("") == ("", False)
    assert extract_action("   \n  ") == ("", False)


# ---------------------------------------------------------------------------
# Gamefile resolution and configuration
# ---------------------------------------------------------------------------

def test_resolve_gamefile_accepts_relative_and_absolute(tmp_path):
    game = tmp_path / "data" / GAME_REL
    game.parent.mkdir(parents=True)
    game.write_text("x", encoding="utf-8")
    assert resolve_gamefile(GAME_REL, str(tmp_path / "data")) == str(game.resolve())
    assert resolve_gamefile(str(game), str(tmp_path)) == str(game.resolve())


def test_resolve_gamefile_missing_id_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="does not resolve"):
        resolve_gamefile("json_2.1.1/nope/game.tw-pddl", str(tmp_path))
    with pytest.raises(ConfigError, match="does not exist"):
        resolve_gamefile(str(tmp_path / "missing.tw-pddl"), str(tmp_path))


def test_missing_env_vars_are_config_errors(monkeypatch, tmp_path):
    for name in (VENV_ENV, EVAL_DIR_ENV, DATA_ENV, CONFIG_ENV, BENCH_SRC_ENV,
                 "EXPEDISTILL_ALFWORLD_READ_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError, match=VENV_ENV):
        ALFWorldEnv(GAME_REL)
    monkeypatch.setenv(VENV_ENV, str(sys.executable))
    with pytest.raises(ConfigError, match=EVAL_DIR_ENV):
        ALFWorldEnv(GAME_REL)
    monkeypatch.setenv(EVAL_DIR_ENV, str(tmp_path))
    with pytest.raises(ConfigError, match=DATA_ENV):
        ALFWorldEnv(GAME_REL)


def test_unknown_task_id_fails_fast_without_spawning(tmp_path):
    with pytest.raises(ConfigError, match="does not resolve"):
        make_env(tmp_path, data_root=str(tmp_path))  # data root without games


# ---------------------------------------------------------------------------
# Process lifecycle against the fake adapter
# ---------------------------------------------------------------------------

def test_reset_returns_initial_observation_and_budget(tmp_path):
    env = make_env(tmp_path)
    try:
        observation = env.reset()
        assert observation == "You are in the middle of a room. You see a fridge 1."
        assert env.max_steps == 3
        assert not env.is_terminated()
        assert env._proc is not None
    finally:
        env.close()


def test_second_reset_on_fresh_episode_does_not_respawn(tmp_path):
    env = make_env(tmp_path)
    try:
        first = env.reset()
        pid = env._proc.pid
        assert env.reset() == first
        assert env._proc.pid == pid
    finally:
        env.close()


def test_step_roundtrip_and_success(tmp_path):
    env = make_env(tmp_path)
    try:
        env.reset()
        assert env.step("open fridge 1") == "you did: open fridge 1"
        assert not env.is_terminated()
        assert not env.success_fn()
        assert env.step("win") == "Task is SOLVED."
        assert env.is_terminated()
        assert env.success_fn()
        assert env.curr_step == 2
    finally:
        env.close()


def test_repeated_action_terminates_the_episode(tmp_path):
    env = make_env(tmp_path)
    try:
        env.reset()
        assert env.step("go to fridge 1") == "you did: go to fridge 1"
        observation = env.step("go to fridge 1")
        assert env.is_terminated()
        assert not env.success_fn()
        assert env.termination_reason == "repeated_action"
        assert "episode is terminated" in observation
    finally:
        env.close()


def test_thought_and_empty_replies_do_not_touch_the_engine(tmp_path):
    env = make_env(tmp_path)
    try:
        env.reset()
        assert env.step("> think: where is the apple?") == "OK."
        assert env.step("") == "Nothing happens."
        # Neither consumed an engine step: a plain action still works.
        assert env.step("look") == "you did: look"
        assert env.curr_step == 3
    finally:
        env.close()


def test_engine_budget_ends_the_episode_as_truncation(tmp_path):
    env = make_env(tmp_path)
    try:
        env.reset()
        assert env.step("look") == "you did: look"
        assert env.step("examine fridge 1") == "you did: examine fridge 1"
        assert env.step("inventory") == "you did: inventory"
        assert env.truncated
        assert not env.is_terminated()
        assert not env.success_fn()
        assert env.termination_reason == "budget"
    finally:
        env.close()


def test_child_death_is_infrastructure_error_and_is_reaped(tmp_path):
    env = make_env(tmp_path)
    try:
        env.reset()
        pid = env._proc.pid
        with pytest.raises(InfrastructureError, match="exited before replying"):
            env.step("die")
        # The failure reaped the child: no orphan left behind.
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert env._proc is None
    finally:
        env.close()


def test_protocol_violation_is_infrastructure_error(tmp_path):
    env = make_env(tmp_path)
    try:
        env.reset()
        with pytest.raises(InfrastructureError, match="protocol violation"):
            env.step("garbage")
    finally:
        env.close()


def test_read_deadline_is_infrastructure_error_and_kills_child(tmp_path):
    env = make_env(tmp_path, read_timeout=0.5)
    try:
        env.reset()
        pid = env._proc.pid
        with pytest.raises(InfrastructureError, match="deadline"):
            env.step("hang")
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        env.close()


def test_close_terminates_a_live_child(tmp_path):
    env = make_env(tmp_path)
    env.reset()
    pid = env._proc.pid
    assert env._proc.poll() is None
    env.close()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    env.close()  # idempotent


def test_context_manager_closes_on_exception(tmp_path):
    with make_env(tmp_path) as env:
        env.reset()
        pid = env._proc.pid
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_step_before_reset_is_code_bug(tmp_path):
    env = make_env(tmp_path)
    try:
        with pytest.raises(CodeBugError, match="reset"):
            env.step("look")
    finally:
        env.close()


def test_missing_venv_python_fails_fast_as_config_error(tmp_path):
    # A nonexistent interpreter is a configuration problem, not something to
    # retry: it is caught at construction, before any subprocess spawn.
    with pytest.raises(ConfigError, match=VENV_ENV):
        make_env(tmp_path, venv_python=str(tmp_path / "not-a-python"))


def test_spawn_failure_is_infrastructure_error(tmp_path):
    broken = tmp_path / "not-executable"
    broken.write_text("#!nope\n", encoding="utf-8")
    broken.chmod(0o644)
    env = make_env(tmp_path, venv_python=str(broken))
    try:
        with pytest.raises(InfrastructureError, match="could not start"):
            env.reset()
    finally:
        env.close()


def test_child_pythonpath_is_only_the_eval_src(tmp_path, monkeypatch):
    """The child must see the eval src as its ONLY PYTHONPATH.

    Mixing our own venv's paths into the foreign interpreter is the incident
    class the subprocess split exists to avoid, so a leaky parent PYTHONPATH
    must not survive the spawn.
    """
    monkeypatch.setenv("PYTHONPATH", "/definitely/leaky/parent/path")
    probe = (
        "import json, os, sys\n"
        "def emit(tag, payload):\n"
        "    sys.stdout.write(tag + ' ' + json.dumps(payload) + '\\n')\n"
        "    sys.stdout.flush()\n"
        "emit('H', {'budget': 5})\n"
        "emit('O', {'text': os.environ.get('PYTHONPATH', '<unset>') + '|' "
        "+ os.environ.get('ALFWORLD_DATA', '<unset>'), 'won': False, "
        "'done': False, 'reason': None})\n"
    )
    data_root = tmp_path / "alfworld-data"
    game = data_root / GAME_REL
    game.parent.mkdir(parents=True)
    game.write_text("dummy", encoding="utf-8")
    config = tmp_path / "textworld.yaml"
    config.write_text("dummy\n", encoding="utf-8")
    env = ALFWorldEnv(
        GAME_REL,
        venv_python=sys.executable,
        eval_dir=str(tmp_path),
        data_root=str(data_root),
        config_path=str(config),
        adapter_source=probe,
        read_timeout=10.0,
    )
    try:
        observation = env.reset()
        pythonpath, data = observation.split("|", 1)
        assert pythonpath == str(tmp_path / "src")
        assert data == str(data_root)
    finally:
        env.close()


# ---------------------------------------------------------------------------
# The adapter script itself is stdlib-clean Python (it runs in a foreign venv)
# ---------------------------------------------------------------------------

def test_adapter_source_compiles_without_the_eval_repo():
    compile(FAKE_ADAPTER_SOURCE, "fake_adapter.py", "exec")
    import expedistill.benchmarks.alfworld.env as env_module
    compile(env_module.ADAPTER_SOURCE, "adapter.py", "exec")


def test_adapter_source_documents_the_protocol():
    from expedistill.benchmarks.alfworld.env import ADAPTER_SOURCE
    for tag_line in ('H <json>', 'O <json>', 'X <json>'):
        assert tag_line in ADAPTER_SOURCE
    assert "gamefile" in ADAPTER_SOURCE
    # Exactly one observation answers every action: no separate end marker.
    assert "E <json>" not in ADAPTER_SOURCE
