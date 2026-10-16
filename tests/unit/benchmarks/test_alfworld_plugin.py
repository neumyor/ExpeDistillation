"""Tests for benchmarks/alfworld/plugin.py and the package registration.

No eval venv, no real alfworld: the plugin is exercised against the same
fake adapter protocol stub as the env tests (see test_alfworld_env.py), and
task loading against a synthetic ALFWORLD_DATA tree. One live-marked smoke
documents the real single-task command.
"""

import json
import os
import string
import subprocess
import sys
from pathlib import Path

import pytest

from expedistill.infra.reliability import InfrastructureError

import expedistill.benchmarks.alfworld as alfworld_pkg
from expedistill.benchmarks.alfworld.env import (
    DATA_ENV,
    EVAL_DIR_ENV,
    VENV_ENV,
    ALFWorldEnv,
)
from expedistill.benchmarks.alfworld.plugin import (
    DEFAULT_MAX_STEPS,
    MAX_STEPS_ENV,
    TASK_PROMPT_NAME,
    TASK_PROMPT_TEMPLATE,
    clean_task_id,
    enumerate_gamefiles,
)
import expedistill.benchmarks.alfworld.env as env_module
from expedistill.benchmarks.registry import get_plugin, plugin_names, register_plugin
from expedistill.infra.reliability import ConfigError
from expedistill.prompts import get as get_prompt
from expedistill.llm import make_fake_handle

#: The same fake adapter the env tests use (see test_alfworld_env.py).
FAKE_ADAPTER_SOURCE = '''\
import json
import sys

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
    steps += 1
    if action == "win":
        emit("O", obs("Task is SOLVED.", won=True, done=True, reason="won"))
        break
    if steps >= BUDGET:
        emit("O", obs("you did: " + action, done=True, reason="budget"))
        break
    emit("O", obs("you did: " + action))
'''

GAME_REL = "json_2.1.1/valid_seen/pick_and_place_simple-Mug-None-Desk-1/trial-1/game.tw-pddl"
GAME_REL_2 = "json_2.1.1/valid_unseen/look_at_obj_in_light-Lamp-None-DeskLamp-2/trial-1/game.tw-pddl"


@pytest.fixture
def plugin():
    """The real alfworld plugin, re-registered if a registry reset wiped it."""
    if "alfworld" not in plugin_names():
        register_plugin(alfworld_pkg.PLUGIN)
    return get_plugin("alfworld")


@pytest.fixture
def data_root(tmp_path):
    """A synthetic ALFWORLD_DATA tree with two games and instructions."""
    return _make_data_root(tmp_path / "alfworld-data")


def _make_data_root(root):
    for rel, instruction in (
        (GAME_REL, "put a clean mug on the desk"),
        (GAME_REL_2, "look at the lamp under the desklamp"),
    ):
        game = root / rel
        game.parent.mkdir(parents=True)
        game.write_text("dummy game file", encoding="utf-8")
        (game.parent / "traj_data.json").write_text(
            json.dumps({"turk_annotations": {"anns": [{"task_desc": instruction}]}}),
            encoding="utf-8")
    (root / "logic").mkdir()
    (root / "logic" / "alfred.pddl").write_text("", encoding="utf-8")
    return root


@pytest.fixture
def eval_env(tmp_path, data_root, monkeypatch):
    """Point the plugin's env-var resolution at the synthetic tree."""
    monkeypatch.setenv(DATA_ENV, str(data_root))
    monkeypatch.setenv(VENV_ENV, sys.executable)
    monkeypatch.setenv(EVAL_DIR_ENV, str(tmp_path))
    # The fake adapter ignores it, but the constructor must find a file.
    config = tmp_path / "textworld.yaml"
    config.write_text("dummy: true\n", encoding="utf-8")
    monkeypatch.setenv("ALFWORLD_CONFIG", str(config))
    monkeypatch.setattr(env_module, "ADAPTER_SOURCE", FAKE_ADAPTER_SOURCE)
    return data_root


# ---------------------------------------------------------------------------
# Import safety and registration
# ---------------------------------------------------------------------------

def test_import_succeeds_without_alfworld_environment():
    """Import + registration work with every ALFWORLD_* variable unset."""
    scrubbed = {
        key: value for key, value in os.environ.items()
        if not key.startswith("ALFWORLD")
        and not key.startswith("EXPEDISTILL_ALFWORLD")
    }
    result = subprocess.run(
        [sys.executable, "-c",
         "import expedistill.benchmarks.alfworld as pkg\n"
         "from expedistill.benchmarks.registry import plugin_names\n"
         "assert 'alfworld' in plugin_names()\n"
         "assert pkg.PLUGIN.name == 'alfworld'\n"
         "print('ok')"],
        env=scrubbed, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_plugin_identity(plugin):
    assert plugin.name == "alfworld"
    assert plugin.task_prompt_name == "ALFWORLD_TASK_PROMPT"
    assert get_plugin("alfworld") is plugin


def test_view_rules_are_lenient(plugin):
    rules = plugin.view_rules
    # ALFWorld observations are short: a few hundred characters. The defaults
    # are deliberately lenient so nothing realistic is ever truncated.
    assert rules.max_obs_chars >= 4000
    assert rules.truncate("x" * 500) == "x" * 500


def test_max_steps_env_var(monkeypatch, plugin):
    assert plugin.max_steps() == DEFAULT_MAX_STEPS == 50
    monkeypatch.setenv(MAX_STEPS_ENV, "7")
    assert plugin.max_steps() == 7
    monkeypatch.setenv(MAX_STEPS_ENV, "zero")
    with pytest.raises(ConfigError, match=MAX_STEPS_ENV):
        plugin.max_steps()


# ---------------------------------------------------------------------------
# Task prompt (B2 contract)
# ---------------------------------------------------------------------------

def test_task_prompt_has_exactly_the_three_variables():
    fields = [fname for _, fname, _, _ in string.Formatter().parse(TASK_PROMPT_TEMPLATE)
              if fname]
    assert fields == ["instruction", "skills_block", "history"]


def test_task_prompt_registered_in_b2():
    assert get_prompt(TASK_PROMPT_NAME) == TASK_PROMPT_TEMPLATE


def test_task_prompt_renders_bare_and_with_skills():
    bare = TASK_PROMPT_TEMPLATE.format(
        instruction="put a clean mug on the desk", skills_block="", history="")
    skills = "SKILL: open the fridge before searching it."
    with_skills = TASK_PROMPT_TEMPLATE.format(
        instruction="put a clean mug on the desk",
        skills_block=skills, history="")
    # Bare and skills runs share the template; the only difference is the block.
    assert "SKILL: open the fridge" in with_skills
    assert with_skills.replace(skills, "") == bare
    # The household command format from the old adapter's contract is present.
    assert "go to <receptacle>" in bare
    assert "move <held object> to <receptacle>" in bare


# ---------------------------------------------------------------------------
# Task loading (D1)
# ---------------------------------------------------------------------------

def test_load_tasks_none_without_data_env_is_config_error(plugin, monkeypatch):
    monkeypatch.delenv(DATA_ENV, raising=False)
    with pytest.raises(ConfigError, match=DATA_ENV):
        plugin.load_tasks(None)


def test_load_tasks_none_enumerates_all_games(plugin, data_root, monkeypatch):
    monkeypatch.setenv(DATA_ENV, str(data_root))
    tasks = plugin.load_tasks(None)
    assert [task.task_id for task in tasks] == sorted([GAME_REL, GAME_REL_2])
    first = tasks[0]
    assert first.instruction == "put a clean mug on the desk"
    assert first.meta["gamefile"] == GAME_REL
    assert first.meta["split"] == "valid_seen"


def test_tasks_do_not_depend_on_the_data_root_location(plugin, tmp_path, monkeypatch):
    # Stage 0 freezes a digest over task id, instruction and meta: moving an
    # identical data tree to another path must not change any of them, or a
    # resume on the moved data fails with FrozenProtocolChanged.
    root_a = _make_data_root(tmp_path / "machine-a" / "alfworld")
    root_b = _make_data_root(tmp_path / "elsewhere" / "data")
    monkeypatch.setenv(DATA_ENV, str(root_a))
    tasks_a = plugin.load_tasks(None)
    monkeypatch.setenv(DATA_ENV, str(root_b))
    tasks_b = plugin.load_tasks(None)
    assert tasks_a == tasks_b
    flat = json.dumps([task.meta for task in tasks_a])
    assert str(tmp_path) not in flat


def test_absolute_id_under_the_data_root_becomes_relative(plugin, data_root,
                                                          monkeypatch):
    monkeypatch.setenv(DATA_ENV, str(data_root))
    [task] = plugin.load_tasks([str(data_root / GAME_REL)])
    assert task.task_id == GAME_REL
    assert task.meta["gamefile"] == GAME_REL


def test_tasks_loaded_on_one_root_run_on_a_moved_root(plugin, eval_env, tmp_path,
                                                      monkeypatch):
    [task] = plugin.load_tasks([GAME_REL])
    moved = eval_env.rename(tmp_path / "moved-data")
    monkeypatch.setenv(DATA_ENV, str(moved))
    handle = make_fake_handle(role="executor", script=["look", "win"])
    outcome = plugin.run(task, handle, "", tmp_path / "work")
    assert outcome.correctness is True


def test_load_tasks_list_strips_old_bookkeeping_suffix(plugin, data_root, monkeypatch):
    monkeypatch.setenv(DATA_ENV, str(data_root))
    tasks = plugin.load_tasks([GAME_REL + "___7", GAME_REL_2])
    assert [task.task_id for task in tasks] == [GAME_REL, GAME_REL_2]


def test_load_tasks_unknown_id_is_config_error(plugin, data_root, monkeypatch):
    monkeypatch.setenv(DATA_ENV, str(data_root))
    with pytest.raises(ConfigError, match="does not resolve"):
        plugin.load_tasks(["json_2.1.1/valid_seen/ghost/game.tw-pddl"])


def test_load_tasks_file_spec(plugin, data_root, tmp_path, monkeypatch):
    monkeypatch.setenv(DATA_ENV, str(data_root))
    task_file = tmp_path / "tasks.txt"
    task_file.write_text(
        f"# one comment line\n{GAME_REL}___3\n\n{GAME_REL_2}\n", encoding="utf-8")
    tasks = plugin.load_tasks(str(task_file))
    assert [task.task_id for task in tasks] == [GAME_REL, GAME_REL_2]


def test_load_tasks_missing_file_is_config_error(plugin, monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_ENV, str(tmp_path))
    with pytest.raises(ConfigError, match="does not exist"):
        plugin.load_tasks(str(tmp_path / "no-such-file.txt"))


def test_clean_task_id_only_strips_trailing_suffix():
    assert clean_task_id("task___12") == "task"
    assert clean_task_id("  task___0 ") == "task"
    # SearchQA-style blanks survive; game ids with digits never match.
    assert clean_task_id("____ Jesus rose") == "____ Jesus rose"
    assert clean_task_id("trial-20170907-1") == "trial-20170907-1"


def test_enumerate_gamefiles_requires_data_subdir(tmp_path):
    with pytest.raises(ConfigError, match="json_2.1.1"):
        enumerate_gamefiles(tmp_path)


# ---------------------------------------------------------------------------
# run(): full roundtrip against the fake adapter
# ---------------------------------------------------------------------------

def test_run_success_roundtrip(plugin, eval_env, tmp_path):
    handle = make_fake_handle(role="executor", script=["look", "win"])
    task = plugin.load_tasks([GAME_REL])[0]
    outcome = plugin.run(task, handle, "", tmp_path)

    assert outcome.correctness is True
    assert outcome.meta["termination"] == "terminated"
    assert [step["action"] for step in outcome.steps] == ["look", "win"]
    assert outcome.steps[-1]["observation"] == "Task is SOLVED."
    # The first prompt carries the instruction AND the room description the
    # env reported at reset (ALFWorld's task line alone names no receptacles).
    user = handle.client.calls[0]["user"]
    assert "put a clean mug on the desk" in user
    assert "You are in the middle of a room" in user


def test_run_dumps_requests_under_the_handle_run_dir(plugin, eval_env, tmp_path):
    # README section 2: B1 dumps live in <run>/llm_requests/. K1 attaches the
    # run dir to the working handle; the unit workdir must not override it.
    run_dir = tmp_path / "run"
    workdir = tmp_path / "work" / "unit-0"
    workdir.mkdir(parents=True)
    handle = make_fake_handle(role="executor", script=["look", "win"],
                              run_dir=run_dir)
    task = plugin.load_tasks([GAME_REL])[0]
    outcome = plugin.run(task, handle, "", workdir)

    assert outcome.correctness is True
    assert len(list((run_dir / "llm_requests").glob("*.json"))) == 2
    assert not (workdir / "llm_requests").exists()


def test_run_budget_exhaustion_is_failure(plugin, eval_env, tmp_path):
    handle = make_fake_handle(role="executor", script=["a", "b", "c"])
    task = plugin.load_tasks([GAME_REL])[0]
    outcome = plugin.run(task, handle, "", tmp_path)

    assert outcome.correctness is False
    assert outcome.meta["termination"] == "truncated"
    assert len(outcome.steps) == 3


def test_run_skills_block_reaches_the_prompt(plugin, eval_env, tmp_path):
    skills = "SKILL: check the fridge first, cold items live there."
    handle = make_fake_handle(role="executor", script=["win"])
    task = plugin.load_tasks([GAME_REL])[0]
    outcome = plugin.run(task, handle, skills, tmp_path)
    assert outcome.correctness is True
    assert skills in handle.client.calls[0]["user"]


def test_run_dead_adapter_raises_infra(plugin, eval_env, tmp_path,
                                        monkeypatch):
    monkeypatch.setattr(
        env_module, "ADAPTER_SOURCE",
        "import sys\nsys.stderr.write('dead on arrival\\n')\nsys.exit(3)\n")
    handle = make_fake_handle(role="executor", script=["look"])
    task = plugin.load_tasks([GAME_REL])[0]
    # R2b: a dead environment is an infrastructure failure that propagates
    # (K1 retries the unit); nothing is stored for the failed attempt.
    with pytest.raises(InfrastructureError, match="dead on arrival"):
        plugin.run(task, handle, "", tmp_path)
    # No model call was spent on a dead environment.
    assert handle.client.calls == []


# ---------------------------------------------------------------------------
# Live smoke: the real 1-task command (requires the outer eval environment)
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_live_real_adapter_one_task(plugin, tmp_path):
    """Real one-task smoke against the outer benchmark/alfworld-eval stack.

    This is the command the subprocess bridge runs, spelled out for runbooks
    (all five variables come from the environment; see .env.example)::

        ALFWORLD_DATA=<data> ALFWORLD_CONFIG=<eval>/configs/textworld.yaml \\
        ALFWORLD_BENCH_SRC=<src> \\
        PYTHONPATH=<EXPEDISTILL_ALFWORLD_EVAL_DIR>/src \\
        <EXPEDISTILL_ALFWORLD_VENV> adapter.py \\
            --gamefile <gamefile> --config $ALFWORLD_CONFIG --seed 42

    It loads the first game under $ALFWORLD_DATA, reads the handshake and
    initial observation, and performs one engine step ("look"). No model is
    involved. Skipped when the outer environment is not configured.
    """
    missing = [name for name in (VENV_ENV, EVAL_DIR_ENV, DATA_ENV)
               if not os.environ.get(name)]
    if missing:
        pytest.skip(f"live smoke needs the outer eval environment: {missing} unset")

    ids = enumerate_gamefiles(Path(os.environ[DATA_ENV]))
    assert ids, f"no game files under $ALFWORLD_DATA ({os.environ[DATA_ENV]})"
    task = plugin.load_tasks([sorted(ids)[0]])[0]
    assert task.instruction, "no instruction annotation found for the smoke task"

    env = ALFWorldEnv(task.task_id)
    try:
        observation = env.reset()
        assert observation, "empty initial observation from the real adapter"
        assert env.max_steps and env.max_steps >= 1
        followup = env.step("look")
        assert isinstance(followup, str)
    finally:
        env.close()
