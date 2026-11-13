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
from expedistill.prompts.learning_context import GENERIC

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
    user = get_prompt("SEGMENT").format(
        **GENERIC.variables(), trajectory_view=view, K="5")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert len(parsed) == 1
    assert parsed[0]["start"] == 0 and parsed[0]["end"] == 2
    assert parsed[0]["success"] is True
    assert parsed[0]["label"] and parsed[0]["description"]


def test_segment_reads_a_failure_view(client):
    view = ("task: searchqa:t1 (final status: failure)\n\n"
            "step 0\naction: Finish[x]\nobservation: Answer is INCORRECT.")
    user = get_prompt("SEGMENT").format(
        **GENERIC.variables(), trajectory_view=view, K="5")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed[0]["end"] == 1 and parsed[0]["success"] is False


_CARD = "[When to use] always\n[Steps]\n1. do it\n[Do not use when] never"


def _render(name, **variables):
    return get_prompt(name).format(
        **{**GENERIC.variables(), "protocol": "", "profile_section": "w",
           "correction_note": "", **variables})


def test_the_content_rewrite_appends_the_marker(client):
    user = _render("REVISE_CONTENT", skill_card=_CARD, playbook="pb",
                   evidence_steps="steps", diagnosis="d")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed["steps"] == ["do it"]
    assert parsed["not_applicable_when"].startswith("never\n")
    assert REVISION_MARKER in parsed["not_applicable_when"]
    assert set(parsed) == {"steps", "not_applicable_when", "revision_reason"}


def test_the_trigger_rewrite_appends_the_marker(client):
    user = _render("REVISE_TRIGGER", current_summary="Do it.",
                   current_when_to_use="always", skill_catalog="c",
                   playbook="pb", evidence_steps="steps", diagnosis="d")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed["summary"] == "Do it."
    assert parsed["when_to_use"].startswith("always ")
    assert REVISION_MARKER in parsed["when_to_use"]


def test_the_trial_analysis_blames_the_last_step(client):
    user = _render("TRIAL_FAILURE_ANALYSIS", task_instruction="do it",
                   playbook="(none)", skill_catalog="(none)",
                   opened_skills="(none)", trial_steps="step 0\nstep 1",
                   last_step="1")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed["failing_step"] == 1
    assert parsed["scope"] in ("subtask", "general")
    assert parsed["reason"]


def test_the_typing_shares_a_type_per_label(client):
    cards = "\n\n".join(
        f"SUBTASK_ID: {subtask_id}\nTASK: t\nSOURCE: strong\n"
        f"SUBTASK_SUCCESS: True\nLABEL: {label}\nDESCRIPTION: d\nSTEPS:\n"
        for subtask_id, label in (("s1", "find it"), ("s2", "heat it"),
                                  ("s3", "find it")))
    user = _render("SUBTASK_TYPING", type_table="type/001 | heat it | x",
                   cards=cards)
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed["new_types"] == [{"key": "new1", "name": "find it",
                                    "entry_state":
                                        "The agent is about to find it."}]
    assert [item["type"] for item in parsed["assignments"]] == [
        "new1", "type/001", "new1"]


def test_the_card_writer_answers_with_the_four_fields(client):
    user = _render("CARD_WRITE", type_name="heat it",
                   entry_state="The object is held.", strong_segments="s",
                   weak_segments="(none)", playbook="(none)")
    parsed = extract_json(client.complete("fake-strong", "s", user))
    assert parsed["when_to_use"] == "The object is held."
    assert set(parsed) == {"summary", "when_to_use", "steps",
                           "not_applicable_when"}


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
        "## Skill rules\n- advice REVISED: now concrete\n")
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
