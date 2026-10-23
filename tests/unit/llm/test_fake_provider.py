"""The registry's ``fake`` provider: the plan-scripted smoke client.

Pins three things: the registry/builder wiring (a plan problem is a
ConfigError before anything runs), the per-prompt script against the
REAL templates (the anchors are matched on the registered prompt text,
so a template edit that breaks an anchor fails here, not in a smoke),
and the weak-executor policy table the live-round split depends on.
"""

import json

import pytest

from expedistill.infra.reliability import ConfigError
from expedistill.llm.call import extract_json
from expedistill.llm.fake_provider import (
    PLAN_ENV,
    REVISION_MARKER,
    FakeProviderClient,
    from_env,
    load_plan,
)
from expedistill.llm.providers import PROVIDERS, default_client
from expedistill.prompts import get as get_prompt

QUESTION = "What is the capital of the test country?"
ANSWER = "Testville"

PLAN = {
    "strong_model": "fake-strong",
    "weak_model": "fake-weak",
    "questions": {
        QUESTION: {"answer": ANSWER, "policy": "revised_skills"},
    },
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(PLAN), encoding="utf-8")
    monkeypatch.setenv(PLAN_ENV, str(path))
    return from_env()


# ---------------------------------------------------------------------------
# the registry / builder wiring
# ---------------------------------------------------------------------------

def test_the_registry_names_the_fake_provider():
    assert set(PROVIDERS) == {"openai_compat", "fake"}


def test_default_client_builds_the_fake_provider(tmp_path, monkeypatch):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(PLAN), encoding="utf-8")
    monkeypatch.setenv(PLAN_ENV, str(path))
    monkeypatch.setenv("EXPEDISTILL_LLM_PROVIDER", "fake")
    built = default_client(timeout_sec=1.0)
    assert isinstance(built, FakeProviderClient)


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


def test_plan_without_questions_is_a_config_error(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"questions": {}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="questions"):
        load_plan(str(path))


def test_plan_policy_vocabulary_is_checked(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(
        {"questions": {QUESTION: {"answer": ANSWER, "policy": "sometimes"}}}),
        encoding="utf-8")
    with pytest.raises(ConfigError, match="policy"):
        load_plan(str(path))


# ---------------------------------------------------------------------------
# the method-prompt script, against the registered templates
# ---------------------------------------------------------------------------

def test_segment_covers_the_whole_view(client):
    view = ("task: searchqa:t1 (final status: success)\n\n"
            "step 0\naction: Finish[x]\nobservation: Answer is CORRECT.\n\n"
            "step 1\naction: Search[q]\nobservation: a passage")
    user = get_prompt("SEGMENT").format(trajectory_view=view, K="5")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert len(parsed) == 1
    assert parsed[0]["start"] == 0 and parsed[0]["end"] == 2
    assert parsed[0]["success"] is True
    assert parsed[0]["label"] and parsed[0]["description"]


def test_segment_reads_a_failure_view(client):
    view = ("task: searchqa:t1 (final status: failure)\n\n"
            "step 0\naction: Finish[x]\nobservation: Answer is INCORRECT.")
    user = get_prompt("SEGMENT").format(trajectory_view=view, K="5")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed[0]["end"] == 1 and parsed[0]["success"] is False


def test_distill_returns_the_protocol_v2_contract(client):
    user = get_prompt("SKILL_DISTILL").format(
        label="l", description="d", steps_view="s", segment_position="p",
        profile_section="w")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert set(parsed) == {"when_to_use", "steps", "verify",
                           "not_applicable_when", "summary"}
    assert isinstance(parsed["steps"], list) and parsed["steps"]


def test_diagnose_returns_the_protocol_v2_contract(client):
    user = get_prompt("SKILL_DIAGNOSE").format(
        label="l", description="d", context_summary="c", steps_view="s",
        segment_position="p", profile_section="w")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert set(parsed) == {"when_to_use", "steps", "verify",
                           "not_applicable_when", "summary"}


def test_attribution_revises_every_listed_skill(client):
    listing = "skill_id: s1\n[When to use] x\n\nskill_id: s2\n[When to use] y"
    user = get_prompt("LIVE_RUN_ATTRIBUTION").format(
        task_instruction="ti", baseline_summary="b", live_views="v",
        skills_listing=listing, profile_section="w")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed["diagnosis"].strip()
    assert parsed["skill_actions"] == [
        {"skill_id": "s1", "action": "revise",
         "problem": parsed["skill_actions"][0]["problem"]},
        {"skill_id": "s2", "action": "revise",
         "problem": parsed["skill_actions"][1]["problem"]}]
    assert all(entry["problem"].strip()
               for entry in parsed["skill_actions"])


def test_revision_appends_the_marker_and_keeps_the_skill(client):
    original = ("[When to use] always\n[Steps] do it\n"
                "[Verify] done\n[Do not use when] never")
    user = get_prompt("LIVE_SKILL_REVISION").format(
        skill_text=original, attribution_report="r", failure_view="f",
        profile_section="w")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed["failure_category"] == "wrong_trigger"
    assert parsed["skill_text"] == (
        original + "\n" + REVISION_MARKER +
        ": name the concrete state before acting.")
    assert parsed["skill_text"] != original  # a new version, not a no-op


def test_rewrite_appends_the_marker(client):
    original = ("[When to use] always\n[Steps] do it\n"
                "[Verify] done\n[Do not use when] never")
    user = get_prompt("REWRITE").format(
        skill_text=original, question_text="q", weak_answer="a",
        error_description="e", profile_section="w")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert REVISION_MARKER in parsed["skill_text"]
    assert parsed["skill_text"].startswith("[When to use]")


def test_similarity_groups_the_first_pair(client):
    listing = "- s1: content one\n- s2: content two\n- s3: content three"
    user = get_prompt("SIMILARITY").format(skills_listing=listing)
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed == {"similar_groups": [["s1", "s2"]], "conflicts": [],
                      "low_value": []}


def test_similarity_with_one_skill_groups_nothing(client):
    user = get_prompt("SIMILARITY").format(skills_listing="- s1: content one")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed == {"similar_groups": [], "conflicts": [], "low_value": []}


def test_merge_keeps_the_first_sources_markers(client):
    block = ("### s1\n[When to use] a\n[Steps] b\n[Verify] c\n"
             "[Do not use when] d\n\n### s2\n[When to use] e\n"
             "[Steps] f\n[Verify] g\n[Do not use when] h")
    user = get_prompt("MERGE").format(mode="dedup", skills_block=block)
    parsed = extract_json(client.complete("fake-strong", "s", user))
    merged = parsed["content"]
    assert parsed["summary"]
    assert merged.startswith("[When to use] a")
    assert merged != ("[When to use] a\n[Steps] b\n[Verify] c\n"
                     "[Do not use when] d")


def test_group_covers_every_listed_skill(client):
    listing = "- s1: content one\n- s2: content two"
    user = get_prompt("GROUP").format(skills_listing=listing)
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert set(parsed["groups"]) == {"s1", "s2"}


def test_an_unexpected_prompt_fails_loudly(client):
    with pytest.raises(AssertionError, match="does not"):
        client.complete("fake-strong", "s", "a prompt nobody scripted")


# ---------------------------------------------------------------------------
# the execution policy table
# ---------------------------------------------------------------------------

def _execution_user(skills_block: str = "") -> str:
    # The SearchQA task prompt's shape: the question verbatim, the
    # skills block and the action grammar. The plan matches by question
    # substring, so the anchor pieces are what the real template emits.
    return ("You are answering a trivia question.\n" + skills_block +
            "## Question\n" + QUESTION + "\n\n## Actions\n"
            "- Finish[<answer>]\n")


def test_strong_model_always_answers(client):
    assert client.complete(
        "fake-strong", "s", _execution_user()) == f"Finish[{ANSWER}]"


def test_weak_policy_revised_skills_needs_the_marker(client):
    bare = client.complete("fake-weak", "s", _execution_user())
    assert bare == "Finish[not the answer the grader expects]"
    skills = _execution_user("## Skill rules\n- original advice\n")
    assert client.complete(
        "fake-weak", "s", skills) == "Finish[not the answer the grader expects]"
    revised = _execution_user(
        "## Skill rules\n- advice REVISED-BY-G11: now concrete\n")
    assert client.complete("fake-weak", "s", revised) == f"Finish[{ANSWER}]"


def test_weak_policy_table(tmp_path, monkeypatch):
    plan = {"strong_model": "s-model", "weak_model": "w-model",
            "questions": {
                QUESTION: {"answer": ANSWER, "policy": "never"}}}
    path = tmp_path / "p.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    never = FakeProviderClient(load_plan(str(path)))
    with_skills = _execution_user("## Skill rules\n- x\n")
    assert never.complete(
        "w-model", "s", with_skills) == (
        "Finish[not the answer the grader expects]")
    assert never.complete("s-model", "s", with_skills) == f"Finish[{ANSWER}]"

    plan["questions"][QUESTION]["policy"] = "skills"
    path.write_text(json.dumps(plan), encoding="utf-8")
    by_skills = FakeProviderClient(load_plan(str(path)))
    assert by_skills.complete(
        "w-model", "s", with_skills) == f"Finish[{ANSWER}]"
    assert by_skills.complete(
        "w-model", "s", _execution_user()) == (
        "Finish[not the answer the grader expects]")

    plan["questions"][QUESTION]["policy"] = "always"
    path.write_text(json.dumps(plan), encoding="utf-8")
    always = FakeProviderClient(load_plan(str(path)))
    assert always.complete(
        "w-model", "s", _execution_user()) == f"Finish[{ANSWER}]"


def test_every_call_is_recorded(client):
    client.complete("fake-strong", "s", _execution_user())
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "fake-strong"
    assert client.calls[0]["reply"] == f"Finish[{ANSWER}]"


def test_a_method_prompt_embedding_the_question_is_not_execution(client):
    # The G10 attribution prompt embeds the rendered task instruction
    # (question included); the anchor must win over the question match.
    listing = "skill_id: s1\n[When to use] x"
    user = get_prompt("LIVE_RUN_ATTRIBUTION").format(
        task_instruction=_execution_user(), baseline_summary="b",
        live_views="v", skills_listing=listing, profile_section="w")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed["skill_actions"][0]["skill_id"] == "s1"
