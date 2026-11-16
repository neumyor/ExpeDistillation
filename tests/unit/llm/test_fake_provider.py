"""The registry's ``fake`` provider: the plan-scripted smoke client.

Pins the registry/builder wiring (a plan problem is a ConfigError before
anything runs), the weak-executor policy table the live-round split depends
on, and the answers the learn phase reads: the segmenter, the text evolver
and the skill-use judges are checked by their REAL consumers, so a reply its
strict validator would refuse fails here and not in a smoke. The golden
matrix drives the diagnosis, typing, card-writer and rewrite replies through
their real consumers. The weak executor is driven through the real SearchQA
plugin, reading cards from a really materialized skill folder.
"""

import dataclasses
import json

import pytest

from expedistill.benchmarks.alfworld.prompt_pack import PROMPT_PACK as ALFWORLD_PACK
from expedistill.benchmarks.searchqa.prompt_pack import PROMPT_PACK as SEARCHQA_PACK
from expedistill.benchmarks.searchqa.plugin import SearchQAPlugin
from expedistill.blocks import adherence
from expedistill.blocks.segment import segment, validate_segments
from expedistill.core.ids import make_type_id
from expedistill.infra.reliability import ConfigError
from expedistill.llm.call import ModelHandle
from expedistill.llm.fake_provider import (
    PLAN_ENV,
    REVISION_MARKER,
    FakeProviderClient,
    from_env,
    load_plan,
)
from expedistill.llm.providers import default_client
from expedistill.methods.evolve_text import evolve_text
from tests.helpers.plugins import ensure_plugin
from tests.helpers.searchqa_stub import fixture_items, install_eval_repo_stub
from tests.helpers.skill_folder import folder_task_meta, write_skill_folder
from tests.helpers.skills import make_skill

ALFWORLD_CONTEXT = ALFWORLD_PACK.vocabulary
SEARCHQA_CONTEXT = SEARCHQA_PACK.vocabulary

ITEM = fixture_items()[0]
QUESTION = ITEM["question"]
ANSWER = ITEM["answers"][0]
WRONG = "not the answer the grader expects"

PLAN = {
    "strong_model": "fake-strong",
    "weak_model": "fake-weak",
    "segment_steps": 1,
    "questions": {QUESTION: {"answer": ANSWER, "policy": "skills",
                             "read": "always"}},
}


@pytest.fixture(autouse=True)
def _searchqa_plugin():
    # Other suites reset the global registry; the plugin (and its prompts)
    # must be registered again for the executor tests.
    ensure_plugin("searchqa")


def _client(plan=None):
    return FakeProviderClient(plan or PLAN)


def _handle(client=None, *, role="author", model="fake-strong",
            context=SEARCHQA_CONTEXT):
    return ModelHandle(name=model, role=role, client=client or _client(),
                       prompt_defaults=context.variables())


# ---------------------------------------------------------------------------
# the registry / builder wiring and the plan
# ---------------------------------------------------------------------------

def _write_plan(tmp_path, plan):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_default_client_builds_the_fake_provider(tmp_path, monkeypatch):
    monkeypatch.setenv(PLAN_ENV, str(_write_plan(tmp_path, PLAN)))
    monkeypatch.setenv("EXPEDISTILL_LLM_PROVIDER", "fake")
    assert isinstance(default_client(timeout_sec=1.0), FakeProviderClient)


def test_missing_plan_is_a_config_error(monkeypatch):
    monkeypatch.delenv(PLAN_ENV, raising=False)
    monkeypatch.setenv("EXPEDISTILL_LLM_PROVIDER", "fake")
    with pytest.raises(ConfigError, match=PLAN_ENV):
        default_client()


def test_broken_plan_is_a_config_error(tmp_path, monkeypatch):
    path = tmp_path / "plan.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv(PLAN_ENV, str(path))
    with pytest.raises(ConfigError, match="not valid JSON"):
        from_env()


@pytest.mark.parametrize("plan, match", [
    ({**PLAN, "segment_steps": 0}, "segment_steps"),
    ({**PLAN, "segment_steps": "2"}, "segment_steps"),
    ({"questions": {}}, "questions"),
    ({"questions": {QUESTION: {"answer": ANSWER, "policy": "sometimes"}}},
     "policy"),
    ({"questions": {QUESTION: {"answer": ANSWER, "read": "maybe"}}},
     "read mode"),
], ids=["segment-steps-zero", "segment-steps-string", "no-questions",
        "policy-vocabulary", "read-mode-vocabulary"])
def test_plan_is_validated_before_anything_runs(tmp_path, plan, match):
    with pytest.raises(ConfigError, match=match):
        load_plan(str(_write_plan(tmp_path, plan)))


# ---------------------------------------------------------------------------
# G1: SEGMENT honours K and the plan's segment size
# ---------------------------------------------------------------------------

def _view(steps, status="success"):
    blocks = [f"step {index}\naction: act {index}\nobservation: ok"
              for index in range(steps)]
    return f"task: searchqa:t1 (final status: {status})\n\n" + "\n\n".join(
        blocks)


@pytest.mark.parametrize("steps, K, expected", [
    (2, 5, 2), (2, 1, 1), (5, 2, 2), (5, 5, 5), (7, 3, 3), (1, 5, 1)])
def test_segments_follow_the_budget_and_cover_the_view(steps, K, expected):
    segments = segment(_view(steps), K, _handle())
    assert len(segments) == expected
    assert validate_segments(steps, segments, K) == []


def test_without_a_segment_size_the_view_is_one_segment():
    handle = _handle(_client({**PLAN, "segment_steps": None}))
    assert len(segment(_view(4), 5, handle)) == 1


def test_only_the_last_segment_carries_the_final_status():
    segments = segment(_view(3, status="failure"), 5, _handle())
    assert [item["success"] for item in segments] == [True, True, False]


# ---------------------------------------------------------------------------
# the weak executor through the real SearchQA plugin
# ---------------------------------------------------------------------------

@pytest.fixture
def eval_repo(monkeypatch):
    return install_eval_repo_stub(monkeypatch)


def _run(tmp_path, *, plan=None, model="fake-weak", folder=True,
         marker=False):
    """One SearchQA episode through the real plugin; ``marker`` puts the
    revision marker into the card text the model can open."""
    task = SearchQAPlugin._to_task(ITEM)
    manifest, bank, locations = write_skill_folder(tmp_path / "f")
    if marker:
        bank = [dataclasses.replace(
            skill, not_applicable_when=skill.not_applicable_when
            + f"\n{REVISION_MARKER}: concrete") for skill in bank]
        manifest, bank, locations = _rewrite_folder(tmp_path / "g", bank)
    if folder:
        task.meta.update(folder_task_meta(
            manifest, tmp_path / ("g" if marker else "f")))
    client = _client(plan)
    handle = _handle(client, role="executor_weak", model=model)
    outcome = SearchQAPlugin().run(task, handle, skills_block="",
                                   workdir=tmp_path)
    return outcome, client, locations


def _rewrite_folder(target, bank):
    from expedistill.composites.skill_materialize import (
        materialize_skill_folder)
    from expedistill.methods.delivery.folder_delivery import (
        skill_folder_manifest)
    manifest = skill_folder_manifest(bank, "summary_then_when_to_use")
    materialize_skill_folder(manifest, bank, target)
    return manifest, bank, [entry["filename"]
                            for entry in manifest["files"]]


def _actions(outcome):
    return [step["action"] for step in outcome.steps]


def test_a_folder_run_reads_the_first_card_searches_then_finishes(
        eval_repo, tmp_path):
    outcome, _, locations = _run(tmp_path)
    assert _actions(outcome) == [
        f"ReadSkill[{locations[0]}]", f"Search[{QUESTION}]",
        f"Finish[{ANSWER}]"]
    assert outcome.correctness is True
    assert outcome.meta["skill_reads"] == 1
    assert outcome.meta["steps_used"] == 2


def test_read_never_leaves_the_card_unopened_and_the_skills_policy_misses(
        eval_repo, tmp_path):
    plan = {**PLAN, "questions": {QUESTION: {
        "answer": ANSWER, "policy": "skills", "read": "never"}}}
    outcome, _, _ = _run(tmp_path, plan=plan)
    assert _actions(outcome) == [f"Search[{QUESTION}]", f"Finish[{WRONG}]"]
    assert outcome.correctness is False
    assert outcome.meta["skill_list_shown"] is True
    assert outcome.meta["skill_reads"] == 0


def test_the_strong_model_answers_correctly_without_opening_a_card(
        eval_repo, tmp_path):
    plan = {**PLAN, "questions": {QUESTION: {
        "answer": ANSWER, "policy": "never", "read": "never"}}}
    outcome, _, _ = _run(tmp_path, plan=plan, model="fake-strong")
    assert _actions(outcome) == [f"Search[{QUESTION}]", f"Finish[{ANSWER}]"]
    assert outcome.correctness is True


def test_a_bare_run_searches_then_finishes_and_misses_the_skills_policy(
        eval_repo, tmp_path):
    outcome, _, _ = _run(tmp_path, folder=False)
    assert _actions(outcome) == [f"Search[{QUESTION}]", f"Finish[{WRONG}]"]
    assert "skill_reads" not in outcome.meta


def test_the_revised_skills_policy_needs_the_marker_in_the_opened_card(
        eval_repo, tmp_path):
    plan = {**PLAN, "questions": {QUESTION: {
        "answer": ANSWER, "policy": "revised_skills", "read": "always"}}}
    plain, _, _ = _run(tmp_path / "a", plan=plan)
    assert plain.correctness is False
    marked, _, _ = _run(tmp_path / "b", plan=plan, marker=True)
    assert marked.correctness is True


def test_auto_reading_is_stable_and_mixed_across_questions():
    client = _client()
    spec = {"answer": ANSWER}
    choices = {client._wants_read(spec, f"question number {index}")
               for index in range(20)}
    assert choices == {True, False}
    assert all(client._wants_read(spec, "same question")
               == client._wants_read(spec, "same question")
               for _ in range(3))


def test_without_search_in_the_action_list_the_original_single_finish_stays():
    client = _client()
    user = f"## Question\n{QUESTION}\n\n## Actions\n- Finish[<answer>]\n"
    assert client.complete("fake-strong", "s", user) == f"Finish[{ANSWER}]"


# ---------------------------------------------------------------------------
# the evolving texts
# ---------------------------------------------------------------------------


def test_the_playbook_carries_every_label_the_override_demands():
    from expedistill.benchmarks.alfworld.playbook import playbook_requirements
    from expedistill.methods.evolve_text import missing_requirements
    handle = dataclasses.replace(
        _handle(context=ALFWORLD_CONTEXT),
        prompt_aliases=dict(ALFWORLD_PACK.overrides))
    text = evolve_text(
        "playbook", "", ["Open the matching card before acting."],
        "BASELINE FACTS", handle, checks=playbook_requirements())
    assert missing_requirements(text, playbook_requirements()) == []


# ---------------------------------------------------------------------------
# the skill-use judges
# ---------------------------------------------------------------------------

def test_the_applicability_judge_lists_every_card_in_order():
    cards = [{"skill_id": f"card/{index}", "description": f"desc {index}"}
             for index in range(4)]
    result = adherence.judge_applicability(
        "task", cards, _view(3), 3, _handle())
    assert list(result) == [card["skill_id"] for card in cards]
    assert {item["applicable"] for item in result.values()} == {True, False}
    for item in result.values():
        assert (item["first_step"] == 0) == item["applicable"]


def test_the_applicability_judge_marks_nothing_applicable_without_steps():
    cards = [{"skill_id": "card/0", "description": "desc"}]
    result = adherence.judge_applicability(
        "task", cards, "(no steps)", 1, _handle())
    assert result["card/0"]["applicable"] is False
    assert result["card/0"]["first_step"] is None


def test_the_adherence_judge_scores_every_numbered_step():
    card = make_skill(make_type_id(1), "at start",
                      steps=["one", "two", "three", "four", "five"],
                      not_applicable_when="never")
    result = adherence.judge_adherence(card, _view(2), _handle())
    assert [item["number"] for item in result] == [1, 2, 3, 4, 5]
    assert {item["status"] for item in result} <= set(
        adherence.ADHERENCE_STATUSES)
    assert all(item["evidence"] == ""
               for item in result if item["status"] == "not_reached")


# ---------------------------------------------------------------------------
# anything else is still a loud refusal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("user", [
    "Summarize the following text for me.",
    "You analyze ONE thing nobody scripted.",
    "A prompt that quotes " + QUESTION[:10] + " without the plan question",
])
def test_unknown_prompts_are_still_refused(user):
    with pytest.raises(AssertionError, match="does not"):
        _client().complete("fake-strong", "s", user)


def test_a_template_drift_in_an_anchor_section_is_loud():
    user = "You sort segments of text.\nno sections at all"
    with pytest.raises(AssertionError, match="anchor"):
        _client().complete("fake-strong", "s", user)


# ---------------------------------------------------------------------------
# the execution policy table
# ---------------------------------------------------------------------------

def _policy_client(policy):
    return _client({**PLAN, "questions": {
        QUESTION: {"answer": ANSWER, "policy": policy}}})


def _execution_user(skills_block: str = "") -> str:
    # The SearchQA task prompt's shape: the question verbatim, the skills
    # block and the action grammar. The plan matches by question substring,
    # so the anchor pieces are what the real template emits.
    return ("You are answering a trivia question.\n" + skills_block +
            "## Question\n" + QUESTION + "\n\n## Actions\n"
            "- Finish[<answer>]\n")


def test_the_strong_model_always_answers_and_every_call_is_recorded():
    client = _policy_client("revised_skills")
    assert client.complete(
        "fake-strong", "s", _execution_user()) == f"Finish[{ANSWER}]"
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "fake-strong"
    assert client.calls[0]["reply"] == f"Finish[{ANSWER}]"


def test_weak_policy_revised_skills_needs_the_marker():
    client = _policy_client("revised_skills")
    assert client.complete(
        "fake-weak", "s", _execution_user()) == f"Finish[{WRONG}]"
    original = _execution_user("## Skill rules\n- original advice\n")
    assert client.complete("fake-weak", "s", original) == f"Finish[{WRONG}]"
    revised = _execution_user(
        "## Skill rules\n- advice REVISED: now concrete\n")
    assert client.complete("fake-weak", "s", revised) == f"Finish[{ANSWER}]"


def test_weak_policy_table():
    with_skills = _execution_user("## Skill rules\n- x\n")
    never = _policy_client("never")
    assert never.complete("fake-weak", "s", with_skills) == f"Finish[{WRONG}]"
    assert never.complete(
        "fake-strong", "s", with_skills) == f"Finish[{ANSWER}]"

    by_skills = _policy_client("skills")
    assert by_skills.complete(
        "fake-weak", "s", with_skills) == f"Finish[{ANSWER}]"
    assert by_skills.complete(
        "fake-weak", "s", _execution_user()) == f"Finish[{WRONG}]"

    always = _policy_client("always")
    assert always.complete(
        "fake-weak", "s", _execution_user()) == f"Finish[{ANSWER}]"
