"""Tests for benchmarks/searchqa/plugin.py: import-time registration side
effects, the task-prompt contract, task loading (full set / id list / id
file / unknown ids), and full run_react roundtrips through the plugin with
a scripted fake handle. No network, no eval repo: the eval seam is stubbed."""

import importlib
import json
import os
import string
import types
from pathlib import Path

import pytest

import expedistill.benchmarks.searchqa as searchqa_pkg
from expedistill.benchmarks.registry import (
    _reset_registry,
    get_plugin,
    plugin_names,
)
from expedistill.benchmarks.searchqa import env as searchqa_env
from expedistill.benchmarks.searchqa.plugin import (
    MAX_STEPS_ENV,
    SEARCHQA_MAX_STEPS,
    SearchQAPlugin,
)
from expedistill.infra.reliability import ConfigError
from expedistill.llm import make_fake_handle
from expedistill.prompts import PROMPTS

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "searchqa"


# ----------------------------------------------------------------------
# shared helpers
# ----------------------------------------------------------------------

@pytest.fixture
def stub_eval_repo(monkeypatch):
    """Stub the seam with the same evaluator contract the env tests use."""
    evaluator = types.ModuleType("searchqa_stub.evaluator")

    def evaluate(prediction, gold_answers):
        correct = prediction.strip().lower() in {
            str(gold).strip().lower() for gold in gold_answers}
        score = 1.0 if correct else 0.0
        return {
            "em": score,
            "f1": score,
            "sub_em": score,
            "predicted_answer": prediction,
            "gold_answers": list(gold_answers),
        }

    evaluator.evaluate = evaluate
    data = types.ModuleType("searchqa_stub.data")
    data.load_items = lambda path: json.loads(
        Path(path).read_text(encoding="utf-8"))
    package = types.ModuleType("searchqa_stub")
    package.evaluator = evaluator
    package.data = data
    monkeypatch.setattr(searchqa_env, "import_eval_repo", lambda: package)
    return package


def _fixture_items():
    return json.loads((FIXTURES / "items.json").read_text(encoding="utf-8"))


def _make_eval_dir(tmp_path, split_items):
    """Lay out a fake materialized eval repo: data/searchqa_split/<split>/."""
    for split, items in split_items.items():
        split_dir = tmp_path / "data" / "searchqa_split" / split
        split_dir.mkdir(parents=True)
        (split_dir / "items.json").write_text(
            json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _task_for(item):
    return SearchQAPlugin._to_task(item)


# ----------------------------------------------------------------------
# import-time registration side effects
# ----------------------------------------------------------------------

def test_import_registers_plugin_and_prompt():
    # Re-execute the package __init__ on a clean registry: the contract is
    # that importing expedistill.benchmarks.searchqa registers the plugin.
    _reset_registry()
    importlib.reload(searchqa_pkg)
    plugin = get_plugin("searchqa")
    assert isinstance(plugin, SearchQAPlugin)
    assert plugin.name == "searchqa"
    assert plugin.task_prompt_name == "SEARCHQA_TASK_PROMPT"
    assert plugin_names() == ["searchqa"]
    assert "SEARCHQA_TASK_PROMPT" in PROMPTS


def test_import_survives_missing_eval_repo(monkeypatch):
    """The import must not touch the eval repo: point the env var at a
    nonexistent directory and import anyway."""
    monkeypatch.setattr(searchqa_env, "_EVAL_REPO_CACHE", None)
    monkeypatch.setenv(searchqa_env.EVAL_DIR_ENV, "/nonexistent/searchqa-eval")
    _reset_registry()
    importlib.reload(searchqa_pkg)  # must not raise
    assert "searchqa" in plugin_names()
    with pytest.raises(ConfigError, match=searchqa_env.EVAL_DIR_ENV):
        get_plugin("searchqa").load_tasks(None)


# ----------------------------------------------------------------------
# the task prompt contract
# ----------------------------------------------------------------------

def test_task_prompt_has_exactly_the_three_contract_variables():
    template = PROMPTS["SEARCHQA_TASK_PROMPT"].template
    fields = {
        field
        for _, field, _, _ in string.Formatter().parse(template)
        if field is not None
    }
    assert fields == {"instruction", "skills_block", "history"}


def test_task_prompt_renders_bare_and_with_skills():
    template = PROMPTS["SEARCHQA_TASK_PROMPT"].template
    bare = template.format(instruction="Q?", skills_block="", history="")
    assert "Q?" in bare
    with_skills = template.format(
        instruction="Q?", skills_block="## Skill\nbe terse", history="")
    assert "be terse" in with_skills
    # The bare render must not claim skills exist.
    assert "be terse" not in bare
    assert "## Skill" not in bare


def test_task_prompt_documents_the_action_formats():
    template = PROMPTS["SEARCHQA_TASK_PROMPT"].template
    assert "Search[<query>]" in template
    assert "Finish[<answer>]" in template


# ----------------------------------------------------------------------
# task loading
# ----------------------------------------------------------------------

def test_load_tasks_requires_env_var(monkeypatch, stub_eval_repo):
    monkeypatch.delenv(searchqa_env.EVAL_DIR_ENV, raising=False)
    with pytest.raises(ConfigError, match=searchqa_env.EVAL_DIR_ENV):
        SearchQAPlugin().load_tasks(None)


def test_load_tasks_full_set(monkeypatch, stub_eval_repo, tmp_path):
    items = _fixture_items()
    eval_dir = _make_eval_dir(tmp_path, {
        "train": items[:1],
        "val": items[1:2],
        "test": items[2:],
    })
    monkeypatch.setenv(searchqa_env.EVAL_DIR_ENV, str(eval_dir))
    tasks = SearchQAPlugin().load_tasks(None)
    assert [t.task_id for t in tasks] == [it["id"] for it in items]
    first = tasks[0]
    assert first.instruction == items[0]["question"]
    assert first.meta["answers"] == items[0]["answers"]
    assert first.meta["context"] == items[0]["context"]


def test_load_tasks_by_id_list(monkeypatch, stub_eval_repo, tmp_path):
    items = _fixture_items()
    eval_dir = _make_eval_dir(tmp_path, {"test": items})
    monkeypatch.setenv(searchqa_env.EVAL_DIR_ENV, str(eval_dir))
    wanted = [items[2]["id"], items[0]["id"]]
    tasks = SearchQAPlugin().load_tasks(wanted)
    assert [t.task_id for t in tasks] == wanted  # order preserved


def test_load_tasks_unknown_ids_are_config_error(
        monkeypatch, stub_eval_repo, tmp_path):
    eval_dir = _make_eval_dir(tmp_path, {"test": _fixture_items()})
    monkeypatch.setenv(searchqa_env.EVAL_DIR_ENV, str(eval_dir))
    with pytest.raises(ConfigError, match="unknown SearchQA task id"):
        SearchQAPlugin().load_tasks(["no-such-id", "also-missing"])


def test_load_tasks_from_id_file(monkeypatch, stub_eval_repo, tmp_path):
    items = _fixture_items()
    eval_dir = _make_eval_dir(tmp_path, {"test": items})
    monkeypatch.setenv(searchqa_env.EVAL_DIR_ENV, str(eval_dir))
    id_file = tmp_path / "ids.txt"
    id_file.write_text(
        f"\n{items[0]['id']}\n\n  {items[1]['id']}  \n",
        encoding="utf-8")
    tasks = SearchQAPlugin().load_tasks(str(id_file))
    assert [t.task_id for t in tasks] == [items[0]["id"], items[1]["id"]]


def test_load_tasks_missing_id_file_is_config_error(
        monkeypatch, stub_eval_repo, tmp_path):
    monkeypatch.setenv(searchqa_env.EVAL_DIR_ENV, str(tmp_path))
    with pytest.raises(ConfigError, match="cannot read"):
        SearchQAPlugin().load_tasks(str(tmp_path / "nope.txt"))


def test_load_tasks_rejects_bad_spec_type(stub_eval_repo, tmp_path, monkeypatch):
    monkeypatch.setenv(searchqa_env.EVAL_DIR_ENV, str(tmp_path))
    with pytest.raises(ConfigError, match="tasks_spec"):
        SearchQAPlugin().load_tasks(42)


def test_load_tasks_missing_split_files_are_config_error(
        monkeypatch, stub_eval_repo, tmp_path):
    # Only the test split is materialized: loading the "full set" must refuse
    # rather than silently return a subset.
    eval_dir = _make_eval_dir(tmp_path, {"test": _fixture_items()})
    monkeypatch.setenv(searchqa_env.EVAL_DIR_ENV, str(eval_dir))
    with pytest.raises(ConfigError, match="not materialized"):
        SearchQAPlugin().load_tasks(None)
    # ...but explicitly asking for the ids that exist still works.
    tasks = SearchQAPlugin().load_tasks([_fixture_items()[0]["id"]])
    assert len(tasks) == 1


# ----------------------------------------------------------------------
# full roundtrips through run_react
# ----------------------------------------------------------------------

def test_max_steps_env_var(monkeypatch):
    """Mirror of the ALFWorld plugin's max_steps() contract."""
    plugin = SearchQAPlugin()
    monkeypatch.delenv(MAX_STEPS_ENV, raising=False)
    assert plugin.max_steps() == SEARCHQA_MAX_STEPS == 6
    monkeypatch.setenv(MAX_STEPS_ENV, "7")
    assert plugin.max_steps() == 7
    monkeypatch.setenv(MAX_STEPS_ENV, "zero")
    with pytest.raises(ConfigError, match=MAX_STEPS_ENV):
        plugin.max_steps()
    monkeypatch.setenv(MAX_STEPS_ENV, "0")
    with pytest.raises(ConfigError, match=MAX_STEPS_ENV):
        plugin.max_steps()


def test_run_respects_max_steps_env_var(stub_eval_repo, tmp_path, monkeypatch):
    # Read at call time: the override reaches both the env and run_react.
    monkeypatch.setenv(MAX_STEPS_ENV, "2")
    plugin = SearchQAPlugin()
    task = _task_for(_fixture_items()[0])
    handle = make_fake_handle(role="test", script=["Search[penny]"] * 10)
    outcome = plugin.run(task, handle, skills_block="", workdir=tmp_path)
    assert outcome.meta["termination"] == "step_budget"
    assert outcome.meta["steps_used"] == 2


def test_roundtrip_search_then_finish_is_success(
        stub_eval_repo, tmp_path):
    plugin = SearchQAPlugin()
    task = _task_for(_fixture_items()[0])
    handle = make_fake_handle(role="test", script=[
        "Search[red cent]",
        "Finish[red cent]",
    ])
    outcome = plugin.run(task, handle, skills_block="", workdir=tmp_path)
    assert outcome.status == "success"
    assert outcome.meta["termination"] == "terminated"
    assert outcome.meta["steps_used"] == 2
    assert len(outcome.steps) == 2
    assert outcome.steps[0]["action"] == "Search[red cent]"
    assert outcome.meta["searchqa"]["em"] == 1.0
    assert outcome.meta["searchqa"]["gold_answers"] == ["red cent"]


def test_roundtrip_wrong_answer_is_failure(stub_eval_repo, tmp_path):
    plugin = SearchQAPlugin()
    task = _task_for(_fixture_items()[0])
    handle = make_fake_handle(role="test", script=["Finish[a nickel]"])
    outcome = plugin.run(task, handle, skills_block="", workdir=tmp_path)
    assert outcome.status == "failure"
    assert outcome.meta["termination"] == "terminated"
    assert outcome.meta["searchqa"]["em"] == 0.0


def test_roundtrip_step_budget_exhaustion_is_failure(
        stub_eval_repo, tmp_path):
    plugin = SearchQAPlugin()
    task = _task_for(_fixture_items()[0])
    handle = make_fake_handle(role="test", script=["Search[penny]"] * 10)
    outcome = plugin.run(task, handle, skills_block="", workdir=tmp_path)
    assert outcome.status == "failure"
    assert outcome.meta["termination"] == "step_budget"
    assert outcome.meta["steps_used"] == 6  # SEARCHQA_MAX_STEPS


def test_roundtrip_prompt_renders_skills_and_history(
        stub_eval_repo, tmp_path):
    plugin = SearchQAPlugin()
    task = _task_for(_fixture_items()[0])
    skills_block = "## Skill\nSearch the question's key noun before finishing."
    handle = make_fake_handle(role="test", script=[
        "Search[red cent]",
        "Finish[red cent]",
    ])
    plugin.run(task, handle, skills_block=skills_block, workdir=tmp_path)
    first_user = handle.client.calls[0]["user"]
    assert task.instruction in first_user
    assert skills_block in first_user
    second_user = handle.client.calls[1]["user"]
    assert "action: Search[red cent]" in second_user  # {history} rendered
    assert "Answer is CORRECT" not in second_user  # episode not over yet


def test_roundtrip_invalid_actions_stay_in_episode(stub_eval_repo, tmp_path):
    plugin = SearchQAPlugin()
    task = _task_for(_fixture_items()[0])
    handle = make_fake_handle(role="test", script=[
        "what do I do",
        "Search[red cent]",
        "Finish[red cent]",
    ])
    outcome = plugin.run(task, handle, skills_block="", workdir=tmp_path)
    assert outcome.status == "success"
    assert outcome.steps[0]["observation"] == searchqa_env.INVALID_ACTION_MESSAGE


def test_requests_are_dumped_under_the_handle_run_dir(stub_eval_repo, tmp_path):
    """README section 2: every B1 request lands in <run>/llm_requests/. K1
    attaches the run dir to the working handle; the unit workdir must not
    override it and scatter dumps across units."""
    run_dir = tmp_path / "run"
    workdir = tmp_path / "work" / "unit-0"
    workdir.mkdir(parents=True)
    plugin = SearchQAPlugin()
    task = _task_for(_fixture_items()[0])
    handle = make_fake_handle(role="test", script=[
        "Search[red cent]",
        "Finish[red cent]",
    ], run_dir=run_dir)
    outcome = plugin.run(task, handle, skills_block="", workdir=workdir)
    assert outcome.status == "success"
    assert len(list((run_dir / "llm_requests").glob("*.json"))) == 2
    assert not (workdir / "llm_requests").exists()


# ----------------------------------------------------------------------
# live smoke (never run by default; see the marker in pyproject.toml)
# ----------------------------------------------------------------------

@pytest.mark.live
def test_live_one_task_against_eval_repo(tmp_path):
    """Smoke: run 1 real task end to end against the outer eval repo.

    Requires a materialized benchmark/searchqa-eval checkout and exercises
    the real loader, the real evaluator and the env wiring. The handle is
    deliberately scripted (search, then answer with the item's own gold
    answer): the point is the plumbing, not the model. Run with:

        .venv/bin/python -m pytest \\
            tests/unit/benchmarks/test_searchqa_plugin.py \\
            -m live -k eval_repo -q
    """
    if not os.environ.get(searchqa_env.EVAL_DIR_ENV):
        pytest.skip(f"{searchqa_env.EVAL_DIR_ENV} is not set")
    plugin = SearchQAPlugin()
    tasks = plugin.load_tasks(None)
    assert tasks, "the eval repo has no materialized tasks"
    task = tasks[0]
    query = " ".join(task.instruction.split()[:3])
    handle = make_fake_handle(role="live-smoke", script=[
        f"Search[{query}]",
        f"Finish[{task.meta['answers'][0]}]",
    ])
    outcome = plugin.run(task, handle, skills_block="", workdir=tmp_path)
    assert outcome.status == "success", outcome.meta
    assert outcome.meta["termination"] == "terminated"
    assert outcome.meta["searchqa"]["em"] == 1.0
