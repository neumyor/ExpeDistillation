"""Tests for benchmarks/searchqa/env.py: the search action, Finish semantics,
invalid actions, termination flags, reset, and the lazy eval-repo seam.

No network and no eval repo: the seam is monkeypatched with a stub evaluator
that mirrors the real one's contract shape (dict with em/f1/sub_em), and env
interactions replay the recorded fixture transcript."""

import json
import types
from pathlib import Path

import pytest

from expedistill.benchmarks.searchqa import env as searchqa_env
from expedistill.infra.reliability import ConfigError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "searchqa"


@pytest.fixture
def stub_eval_repo(monkeypatch):
    """Install a stub eval repo through the seam tests are meant to patch.

    The stub evaluator implements case-insensitive exact match; the recorded
    transcript fixture was produced under the same semantics.
    """
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
    package = types.ModuleType("searchqa_stub")
    package.evaluator = evaluator
    monkeypatch.setattr(searchqa_env, "import_eval_repo", lambda: package)
    return package


def _fixture_items():
    return json.loads((FIXTURES / "items.json").read_text(encoding="utf-8"))


def _env_for(item, **kw):
    return searchqa_env.SearchQAEnv(
        question=item["question"],
        answers=item["answers"],
        context=item["context"],
        **kw,
    )


# ----------------------------------------------------------------------
# recorded transcript replay
# ----------------------------------------------------------------------

def test_recorded_transcript_replays_bit_for_bit(stub_eval_repo):
    """SearchQAEnv must reproduce the recorded observations exactly; any
    change here is a protocol change and needs a re-recorded fixture."""
    record = json.loads(
        (FIXTURES / "recorded_transcript.json").read_text(encoding="utf-8"))
    item = next(it for it in _fixture_items() if it["id"] == record["task_id"])
    episode = _env_for(item)

    observations = [episode.step(step["action"]) for step in record["steps"]]
    assert observations == [step["observation"] for step in record["steps"]]
    assert episode.terminated
    assert episode.success_fn()
    assert episode.eval_result["em"] == 1.0


# ----------------------------------------------------------------------
# search action
# ----------------------------------------------------------------------

def test_search_returns_best_matching_passage(stub_eval_repo):
    item = _fixture_items()[2]  # the "fum" carol item
    episode = _env_for(item)
    observation = episode.step("Search[traditional Catalan Christmas carol]")
    assert "Fum, fum, fum" in observation
    assert "pa rum pum pum pum" not in observation  # not the runner-up passage


def test_search_truncates_long_passage_with_marker(stub_eval_repo):
    filler = "data " * (searchqa_env.SEARCH_RESULT_CHAR_BUDGET // 4 + 2000)
    episode = searchqa_env.SearchQAEnv(
        question="q", answers=["a"],
        context=f"[DOC] short doc [DOC] {filler}")
    observation = episode.step("Search[data]")
    budget = searchqa_env.SEARCH_RESULT_CHAR_BUDGET
    assert len(observation) <= budget + 100  # budget + marker line
    assert observation.startswith("data")
    assert "characters truncated" in observation


def test_search_without_context_answers_helpfully(stub_eval_repo):
    episode = searchqa_env.SearchQAEnv(question="q", answers=["a"], context="")
    observation = episode.step("Search[anything]")
    assert "No passages" in observation
    assert not episode.terminated


def test_search_does_not_terminate(stub_eval_repo):
    episode = _env_for(_fixture_items()[0])
    for _ in range(3):
        episode.step("Search[penny]")
    assert not episode.terminated
    assert not episode.is_truncated()
    assert episode.success_fn() is False


# ----------------------------------------------------------------------
# finish action
# ----------------------------------------------------------------------

def test_finish_correct_flags_termination_and_success(stub_eval_repo):
    item = _fixture_items()[0]
    episode = _env_for(item)
    observation = episode.step("Finish[Red Cent]")  # stub normalizes case
    assert observation == "Answer is CORRECT."
    assert episode.terminated
    assert episode.is_terminated()
    assert episode.success_fn()
    assert episode.eval_result["gold_answers"] == item["answers"]


def test_finish_incorrect_flags_termination_without_success(stub_eval_repo):
    episode = _env_for(_fixture_items()[0])
    observation = episode.step("Finish[a nickel]")
    assert observation == "Answer is INCORRECT."
    assert episode.terminated
    assert not episode.success_fn()
    assert episode.eval_result["em"] == 0.0


def test_success_fn_false_before_any_finish(stub_eval_repo):
    episode = _env_for(_fixture_items()[0])
    assert episode.success_fn() is False


def test_reset_clears_episode_state(stub_eval_repo):
    episode = _env_for(_fixture_items()[0])
    episode.step("Finish[red cent]")
    assert episode.terminated
    episode.reset()
    assert not episode.terminated
    assert episode.curr_step == 0
    assert episode.answer is None
    assert episode.eval_result is None
    assert episode.success_fn() is False


# ----------------------------------------------------------------------
# invalid actions answer, never raise
# ----------------------------------------------------------------------

@pytest.mark.parametrize("action", [
    "hello there",            # no grammar at all
    "Lookup[red cent]",       # migrated-out action from the old adapter
    "Finish[]",               # empty argument must not parse
    "Search[]",
    "Finish[two\nlines]",     # multi-line argument must not parse
    "",                       # empty action
])
def test_invalid_actions_answer_with_format_never_raise(stub_eval_repo, action):
    episode = _env_for(_fixture_items()[0])
    before = episode.curr_step
    observation = episode.step(action)
    assert observation == searchqa_env.INVALID_ACTION_MESSAGE
    assert "Search[<query>]" in observation
    assert "Finish[<answer>]" in observation
    assert not episode.terminated
    assert episode.curr_step == before + 1  # still burns a step


def test_step_counting_against_budget(stub_eval_repo):
    episode = _env_for(_fixture_items()[0], max_steps=3)
    for _ in range(3):
        episode.step("Search[penny]")
    assert episode.curr_step == 3
    assert not episode.is_truncated()  # budget reached, not exceeded
    episode.step("Search[penny]")
    assert episode.is_truncated()


# ----------------------------------------------------------------------
# the lazy eval-repo seam
# ----------------------------------------------------------------------

def test_seam_requires_env_var(monkeypatch):
    monkeypatch.setattr(searchqa_env, "_EVAL_REPO_CACHE", None)
    monkeypatch.delenv(searchqa_env.EVAL_DIR_ENV, raising=False)
    with pytest.raises(ConfigError, match=searchqa_env.EVAL_DIR_ENV) as excinfo:
        searchqa_env.import_eval_repo()
    assert ".env.example" in str(excinfo.value)


def test_seam_rejects_directory_without_src(monkeypatch, tmp_path):
    monkeypatch.setattr(searchqa_env, "_EVAL_REPO_CACHE", None)
    monkeypatch.setenv(searchqa_env.EVAL_DIR_ENV, str(tmp_path))
    with pytest.raises(ConfigError, match="does not point"):
        searchqa_env.import_eval_repo()


def test_eval_data_dir_requires_env_var(monkeypatch):
    monkeypatch.delenv(searchqa_env.EVAL_DIR_ENV, raising=False)
    with pytest.raises(ConfigError, match=searchqa_env.EVAL_DIR_ENV):
        searchqa_env.eval_data_dir()


def test_parse_action_grammar():
    assert searchqa_env.parse_action("Search[red cent]") == ("Search", "red cent")
    assert searchqa_env.parse_action("Finish[x y]") == ("Finish", "x y")
    assert searchqa_env.parse_action("nope") == (None, None)
    assert searchqa_env.parse_action("Search[]") == (None, None)
