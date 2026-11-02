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
    clean_task_id,
    enumerate_gamefiles,
)
from expedistill.benchmarks.alfworld.protocol import (
    CORRECTION_PROMPT_NAME,
    FOLDER_PROMPT_NAME,
    FOLDER_PROMPT_NO_HISTORY_NAME,
    MAX_OUTPUT_TOKENS,
    PROMPT_NAME,
    PROMPT_NO_HISTORY_NAME,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_NAME,
    TEMPERATURE,
    extract_task_description,
    format_admissible_actions,
    project_action,
)
import expedistill.benchmarks.alfworld.env as env_module
from expedistill.benchmarks.registry import get_plugin, plugin_names, register_plugin
from expedistill.infra.reliability import ConfigError
from expedistill.prompts import get as get_prompt
from expedistill.llm import make_fake_handle
from expedistill.prompts import PROMPTS
from tests.helpers.skill_folder import (
    CARD_BODY,
    folder_task_meta,
    write_skill_folder,
)

#: The same fake adapter the env tests use (see test_alfworld_env.py).
FAKE_ADAPTER_SOURCE = '''\
import json
import sys

def emit(tag, payload):
    sys.stdout.write(tag + " " + json.dumps(payload) + "\\n")
    sys.stdout.flush()

ADMISSIBLE = ["go to fridge 1", "look", "win"]

def obs(text, won=False, done=False, reason=None):
    return {"text": text, "admissible": ADMISSIBLE,
            "won": won, "done": done, "reason": reason}

BUDGET = 3
emit("H", {"budget": BUDGET})
emit("O", obs("You are in the middle of a room. You see a fridge 1.\\n\\n"
              "Your task is to: put a clean mug on the desk"))
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
    # The unified protocol refuses a loop budget that disagrees with the
    # engine's; the fake adapter's engine budget is 3.
    monkeypatch.setenv(MAX_STEPS_ENV, "3")
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
    assert plugin.task_prompt_name == PROMPT_NAME
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

def test_task_prompt_is_the_unified_protocol():
    # Exactly the variables the unified runner's templates use: the
    # no-history step and the later steps.
    no_history = [name for _, name, _, _ in string.Formatter().parse(
        get_prompt(PROMPT_NO_HISTORY_NAME)) if name]
    assert no_history == ["current_observation", "skill_block",
                          "admissible_actions"]
    history = [name for _, name, _, _ in string.Formatter().parse(
        get_prompt(PROMPT_NAME)) if name]
    assert history == ["task_description", "skill_block", "step_count",
                       "history_length", "action_history", "current_step",
                       "current_observation", "admissible_actions"]
    # The action protocol and the system message are the unified ones.
    template = get_prompt(PROMPT_NAME)
    assert "<think>brief reason</think><action>one admissible action</action>" \
        in template
    assert "Do not output anything after </action>." in template
    assert get_prompt(SYSTEM_PROMPT_NAME) == \
        "You are an expert agent operating in the ALFRED Embodied Environment."
    assert SYSTEM_PROMPT == get_prompt(SYSTEM_PROMPT_NAME)
    assert TEMPERATURE == 0.0 and MAX_OUTPUT_TOKENS == 4096


def test_task_prompt_renders_bare_and_with_skills():
    template = get_prompt(PROMPT_NAME)
    variables = dict(
        task_description="put a clean mug on the desk", step_count="1",
        history_length="1", action_history="[Observation 1: 'o', Action 1: 'a']",
        current_step="2", current_observation="you see a fridge 1",
        admissible_actions=format_admissible_actions(
            ["go to fridge 1", "look"]))
    bare = template.format(skill_block="", **variables)
    skills = "SKILL: open the fridge before searching it."
    with_skills = template.format(skill_block=f"\n{skills}\n\n", **variables)
    assert skills in with_skills
    assert with_skills.replace(f"\n{skills}\n\n", "") == bare


def test_project_action_matches_the_unified_parser():
    assert project_action("<think>go</think><action>look</action>") == \
        ("look", True)
    assert project_action("<think>go</think><action>Look</action>") == \
        ("look", True)
    assert project_action("<action>look</action>") == ("look", False)
    assert project_action("<think>想</think><action>look</action>")[1] is False
    assert project_action("") == ("look", False)


def test_extract_task_description_splits_the_room_description():
    initial = "You are in a room.\n\nYour task is to: put a mug on a desk"
    assert extract_task_description(initial) == "put a mug on a desk"
    assert extract_task_description("no task line") == "no task line"


def test_format_admissible_actions_drops_help():
    assert format_admissible_actions(["go to fridge 1", "help", "look"]) == \
        "'go to fridge 1'\n 'look'"


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
    handle = make_fake_handle(
        role="executor", script=[reply("look"), reply("win")])
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

def reply(action):
    """One valid unified-protocol response for ``action``."""
    return f"<think>next step</think><action>{action}</action>"


def test_run_success_roundtrip(plugin, eval_env, tmp_path):
    handle = make_fake_handle(role="executor", script=[
        reply("look"), reply("go to fridge 1"), reply("win")])
    task = plugin.load_tasks([GAME_REL])[0]
    outcome = plugin.run(task, handle, "", tmp_path)

    assert outcome.correctness is True
    assert outcome.meta["termination"] == "terminated"
    assert [step["action"] for step in outcome.steps] == [
        "look", "go to fridge 1", "win"]
    assert outcome.steps[-1]["observation"] == "Task is SOLVED."
    # The first prompt is the unified no-history template: the initial
    # observation carries the task line and the room description, and the
    # engine's admissible commands are listed.
    first = handle.client.calls[0]
    assert first["system"] == SYSTEM_PROMPT
    user = first["user"]
    assert "put a clean mug on the desk" in user
    assert "You are in the middle of a room" in user
    assert "Your admissible actions of the current situation are: " \
           "['go to fridge 1'\n 'look'\n 'win']." in user
    # Step 2 is the history template: the task line is repeated and the last
    # interaction is shown.
    second = handle.client.calls[1]["user"]
    assert "Your task is to: put a clean mug on the desk" in second
    assert "[Observation 1: '" in second
    assert "Action 1: 'look'" in second
    assert "You are now at step 2" in second
    # By step 3 the last TWO interactions are shown; the room description
    # appears once, inside the history bracket, and is not repeated in the
    # current-observation slot (the old local prompt repeated it every step).
    third = handle.client.calls[2]["user"]
    assert "You are now at step 3" in third
    assert third.count("You are in the middle of a room") == 1
    current = third.split("your current observation is: ", 1)[1]
    assert "You are in the middle of a room" not in current
    # The room description travels with the execution record too, so the
    # skill writer, exam scene and judge rebuild the scene the run saw.
    assert "You are in the middle of a room" in \
        outcome.meta["initial_observation"]


def test_run_uses_the_unified_generation_settings(plugin, eval_env, tmp_path):
    handle = make_fake_handle(role="executor", script=[reply("win")])
    task = plugin.load_tasks([GAME_REL])[0]
    plugin.run(task, handle, "", tmp_path)
    call = handle.client.calls[0]
    assert call["temperature"] == TEMPERATURE == 0.0
    assert call["max_tokens"] == MAX_OUTPUT_TOKENS == 4096


def test_an_invalid_reply_gets_one_correction(plugin, eval_env, tmp_path):
    # Free-form reply (the old prompt's shape) is invalid under the unified
    # protocol; the loop asks once with the unified diagnostics and uses the
    # corrected action.
    handle = make_fake_handle(
        role="executor", script=["look", reply("win")])
    task = plugin.load_tasks([GAME_REL])[0]
    outcome = plugin.run(task, handle, "", tmp_path)
    assert outcome.correctness is True
    assert outcome.meta["invalid_actions"] == 1
    assert outcome.meta["corrections"] == 1
    correction = handle.client.calls[1]["user"]
    assert "VALIDATION FAILED" in correction
    assert "missing opening <think> tag" in correction
    assert "Do not output anything before <think>" in correction


def test_a_still_invalid_correction_executes_look(plugin, eval_env, tmp_path):
    handle = make_fake_handle(role="executor", script=[
        "free form", "still free form", reply("win")])
    task = plugin.load_tasks([GAME_REL])[0]
    outcome = plugin.run(task, handle, "", tmp_path)
    assert outcome.meta["invalid_actions"] == 1
    assert outcome.steps[0]["action"] == "look"
    assert outcome.correctness is True


def test_an_action_outside_the_admissible_list_is_invalid(plugin, eval_env,
                                                          tmp_path):
    handle = make_fake_handle(role="executor", script=[
        reply("go to fridge 2"), reply("win")])
    task = plugin.load_tasks([GAME_REL])[0]
    outcome = plugin.run(task, handle, "", tmp_path)
    assert outcome.meta["invalid_actions"] == 1
    assert "is not in the admissible action list" in \
        handle.client.calls[1]["user"]
    assert outcome.steps[0]["action"] == "win"


def test_run_dumps_requests_under_the_handle_run_dir(plugin, eval_env, tmp_path):
    # README section 2: B1 dumps live in <run>/llm_requests/. K1 attaches the
    # run dir to the working handle; the unit workdir must not override it.
    run_dir = tmp_path / "run"
    workdir = tmp_path / "work" / "unit-0"
    workdir.mkdir(parents=True)
    handle = make_fake_handle(
        role="executor", script=[reply("look"), reply("win")],
        run_dir=run_dir)
    task = plugin.load_tasks([GAME_REL])[0]
    outcome = plugin.run(task, handle, "", workdir)

    assert outcome.correctness is True
    assert len(list((run_dir / "llm_requests").glob("*.json"))) == 2
    assert not (workdir / "llm_requests").exists()


def test_run_budget_exhaustion_is_failure(plugin, eval_env, tmp_path):
    handle = make_fake_handle(role="executor", script=[
        reply("look"), reply("go to fridge 1"), reply("look")])
    task = plugin.load_tasks([GAME_REL])[0]
    outcome = plugin.run(task, handle, "", tmp_path)

    assert outcome.correctness is False
    assert outcome.meta["termination"] == "truncated"
    assert len(outcome.steps) == 3


def test_run_skills_block_reaches_the_prompt(plugin, eval_env, tmp_path):
    skills = "SKILL: check the fridge first, cold items live there."
    handle = make_fake_handle(role="executor", script=[reply("win")])
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


# ---------------------------------------------------------------------------
# skill-folder execution (progressive disclosure)
# ---------------------------------------------------------------------------

def _folder_task(plugin, tmp_path, names=("alpha", "beta")):
    manifest, _, locations = write_skill_folder(tmp_path / "folder", names)
    task = plugin.load_tasks([GAME_REL])[0]
    task.meta.update(folder_task_meta(manifest, tmp_path / "folder"))
    return task, locations


def test_the_plugin_declares_the_folder_capability(plugin):
    assert plugin.skill_folder_prompt_name == FOLDER_PROMPT_NAME
    assert FOLDER_PROMPT_NAME in PROMPTS
    assert FOLDER_PROMPT_NO_HISTORY_NAME in PROMPTS
    # The protocol's other prompts freeze with the task prompt.
    assert set(plugin.protocol_prompt_names) == {
        SYSTEM_PROMPT_NAME, PROMPT_NO_HISTORY_NAME,
        FOLDER_PROMPT_NO_HISTORY_NAME, CORRECTION_PROMPT_NAME}


def test_the_folder_prompts_are_the_plain_prompts_plus_the_read_rule():
    for plain_name, folder_name in (
            (PROMPT_NO_HISTORY_NAME, FOLDER_PROMPT_NO_HISTORY_NAME),
            (PROMPT_NAME, FOLDER_PROMPT_NAME)):
        plain = PROMPTS[plain_name].template
        folder = PROMPTS[folder_name].template
        assert "ReadSkill" not in plain
        assert "ReadSkill[<location>]" in folder
        # Deleting the inserted paragraph gives the plain prompt back, so the
        # two arms differ only by the skill-reading text.
        anchor = "Do not output anything after </action>.\n"
        head, tail = plain.split(anchor, 1)
        rest = folder[len(head + anchor):]
        if tail:
            assert rest.endswith(tail)
            insertion = rest[:-len(tail)]
        else:
            insertion = rest
        assert "Skill cards are optional." in insertion
        assert folder.replace(insertion, "", 1) == plain
        assert {name for _, name, _, _ in string.Formatter().parse(folder)
                if name} == {name for _, name, _, _ in string.Formatter().parse(
                    plain) if name}


def test_a_folder_unit_shows_the_card_list_and_hides_the_card_text(
        plugin, eval_env, tmp_path):
    task, locations = _folder_task(plugin, tmp_path)
    handle = make_fake_handle(role="executor", script=[reply("win")])
    plugin.run(task, handle, "CATALOG FALLBACK", tmp_path)
    first_user = handle.client.calls[0]["user"]
    assert "<available_skills>" in first_user
    for location in locations:
        assert f"<location>{location}</location>" in first_user
    assert "ReadSkill[<location>]" in first_user
    assert CARD_BODY not in first_user
    assert "CATALOG FALLBACK" not in first_user


def test_opening_a_card_costs_no_game_step(plugin, eval_env, tmp_path):
    # The fake adapter allows 3 game steps; two reads plus two commands
    # fit only because a read never reaches the game. The unified parser
    # lowercases the action, so the read arrives as readskill[...]; the
    # folder path keeps its case.
    task, locations = _folder_task(plugin, tmp_path)
    handle = make_fake_handle(role="executor", script=[
        reply(f"ReadSkill[{locations[0]}]"),
        reply(f"ReadSkill[{locations[1]}]"),
        reply("look"), reply("win")])
    outcome = plugin.run(task, handle, "", tmp_path)
    assert outcome.correctness is True
    assert [step["action"] for step in outcome.steps] == [
        f"ReadSkill[{locations[0]}]", f"ReadSkill[{locations[1]}]",
        "look", "win"]
    assert outcome.meta["skill_reads"] == 2
    assert outcome.meta["skill_list_shown"] is True
    assert outcome.meta["steps_used"] == 2
    # After two reads the last two interactions are (initial, read 1) and
    # (card alpha, read 2), and the current observation is card beta.
    third_user = handle.client.calls[2]["user"]
    assert CARD_BODY in third_user
    assert "(card alpha)" in third_user and "(card beta)" in third_user
    assert "ReadSkill[<location>]" in third_user


def test_a_read_past_the_cap_costs_a_game_step(plugin, eval_env, tmp_path):
    task, locations = _folder_task(
        plugin, tmp_path, names=("a", "b", "c", "d"))
    handle = make_fake_handle(role="executor", script=[
        reply(f"ReadSkill[{location}]") for location in locations[:4]
    ] + [reply("win")])
    outcome = plugin.run(task, handle, "", tmp_path)
    assert outcome.meta["skill_reads"] == 3
    refusal = outcome.steps[3]
    assert "Skill read limit reached" in refusal["observation"]
    # Three reads cost no game step; the refused fourth does, and so does the
    # winning command.
    assert outcome.meta["steps_used"] == 2


def test_a_unit_without_a_folder_keeps_the_plain_prompt(
        plugin, eval_env, tmp_path):
    task = plugin.load_tasks([GAME_REL])[0]
    handle = make_fake_handle(role="executor", script=[reply("win")])
    outcome = plugin.run(task, handle, "", tmp_path)
    assert "ReadSkill" not in handle.client.calls[0]["user"]
    assert "skill_reads" not in outcome.meta
