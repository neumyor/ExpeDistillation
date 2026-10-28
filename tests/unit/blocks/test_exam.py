"""Generalization exam block tests: scene, trigger, plan, judge.

FakeClient only; no network (AGENTS.md section 8). Every normal-path test pins
the FULL rendered prompt against the named template, so a template edit or a
variable wired to the wrong field fails here. The validation tests pin the
closed contracts: non-empty scene fields, catalog-matching skill names, the
max_reads ceiling, and the success/failure verdict vocabulary.
"""

import pytest

from expedistill.blocks.exam import choose_skills, judge, write_plan, write_scene
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm import json_script, make_fake_handle
from expedistill.prompts import PromptNotFoundError, get as get_prompt

SCENE = "The service rejects the login and prints E401. You have shell access."
CATALOG = "- rotate-credential: rotate a rejected credential before restarting"
KNOWN = ("rotate-credential", "check-logs")
REFERENCE = "Rotate the credential, then restart the service."


class RecordingUsage:
    """Test double for the B7 UsageRecorder (same record signature)."""

    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


# ---------------------------------------------------------------------------
# write_scene (EXAM_SCENE)
# ---------------------------------------------------------------------------

def test_write_scene_wiring_and_fields():
    handle = make_fake_handle(script=json_script({
        "scene_text": "  A login is rejected with E401.  ",
        "expected_behavior": "  Rotate the credential, then restart.  ",
        "reference_answer": "  The credential is stale.  ",
    }))

    scene = write_scene("do the task", "step 0", "step 1", "SKILL BODY",
                        "written after a failure", handle)

    assert handle.client.calls[0]["user"] == get_prompt("EXAM_SCENE").format(
        task_instruction="do the task",
        prior_steps="step 0",
        future_steps="step 1",
        skill_content="SKILL BODY",
        change_note="written after a failure",
    )
    assert scene == {
        "scene_text": "A login is rejected with E401.",
        "expected_behavior": "Rotate the credential, then restart.",
        "reference_answer": "The credential is stale.",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"scene_text": "", "expected_behavior": "b", "reference_answer": "r"},
        {"scene_text": "s", "expected_behavior": "   ", "reference_answer": "r"},
        {"scene_text": "s", "expected_behavior": "b", "reference_answer": None},
        {"scene_text": "s", "expected_behavior": "b"},
        {"scene_text": 3, "expected_behavior": "b", "reference_answer": "r"},
        ["not", "an", "object"],
    ],
)
def test_write_scene_broken_contract_raises_model_output_error(payload):
    handle = make_fake_handle(script=json_script(payload))
    with pytest.raises(ModelOutputError):
        write_scene("t", "p", "f", "c", "n", handle)


# ---------------------------------------------------------------------------
# choose_skills (EXAM_TRIGGER)
# ---------------------------------------------------------------------------

def test_choose_skills_wiring_and_result():
    handle = make_fake_handle(script=json_script({
        "skills": ["rotate-credential"],
        "reason": "  the catalog line names a rejected credential  ",
    }))

    selected, reason = choose_skills(
        SCENE, CATALOG, KNOWN, 3, handle)

    assert handle.client.calls[0]["user"] == get_prompt("EXAM_TRIGGER").format(
        scene_text=SCENE, skill_catalog=CATALOG, max_skill_reads="3",
    )
    assert selected == ["rotate-credential"]
    assert reason == "the catalog line names a rejected credential"


def test_choose_skills_allows_an_empty_selection():
    handle = make_fake_handle(script=json_script(
        {"skills": [], "reason": "no catalog line applies"}))
    assert choose_skills(SCENE, CATALOG, KNOWN, 3, handle) == (
        [], "no catalog line applies")


@pytest.mark.parametrize("answer,expected", [
    # The caller may know a skill by id, directory, folder file, or the flat
    # -SKILL.md form; the model may answer in any of those forms too. The
    # matched entry comes back in the CALLER's spelling.
    ("subtask/skill", "subtask/skill"),
    ("rotate-credential/SKILL.md", "rotate-credential"),
    ("./rotate-credential", "rotate-credential"),
    ("rotate-credential-SKILL.md", "rotate-credential"),
    ("check-logs/SKILL.md", "check-logs/SKILL.md"),
    ("check-logs", "check-logs/SKILL.md"),
])
def test_choose_skills_normalizes_id_directory_and_file_forms(answer, expected):
    known = ("subtask/skill", "rotate-credential", "check-logs/SKILL.md")
    handle = make_fake_handle(script=json_script(
        {"skills": [answer], "reason": "applies"}))
    selected, _ = choose_skills(SCENE, CATALOG, known, 3, handle)
    assert selected == [expected]


def test_choose_skills_accepts_one_known_name_as_a_bare_string():
    handle = make_fake_handle(script=json_script(
        {"skills": ["check-logs/SKILL.md"], "reason": "applies"}))
    selected, _ = choose_skills(SCENE, CATALOG, "check-logs", 1, handle)
    assert selected == ["check-logs"]


def test_choose_skills_returns_the_callers_canonical_spelling():
    # known_names lists the folder file, the model answers the directory:
    # the caller's spelling wins so downstream bookkeeping sees one name.
    known = ("check-logs/SKILL.md",)
    handle = make_fake_handle(script=json_script(
        {"skills": ["check-logs"], "reason": "applies"}))
    assert choose_skills(SCENE, CATALOG, known, 1, handle)[0] == [
        "check-logs/SKILL.md"]


def test_choose_skills_deduplicates_in_model_order():
    handle = make_fake_handle(script=json_script(
        {"skills": ["check-logs", "check-logs/SKILL.md"], "reason": "r"}))
    selected, _ = choose_skills(SCENE, CATALOG, KNOWN, 2, handle)
    assert selected == ["check-logs"]


def test_choose_skills_unknown_name_raises():
    handle = make_fake_handle(script=json_script(
        {"skills": ["invented-skill"], "reason": "r"}))
    with pytest.raises(ModelOutputError, match="unknown skill"):
        choose_skills(SCENE, CATALOG, KNOWN, 3, handle)


def test_choose_skills_over_max_reads_raises():
    handle = make_fake_handle(script=json_script(
        {"skills": ["rotate-credential", "check-logs"], "reason": "r"}))
    with pytest.raises(ModelOutputError, match="at most"):
        choose_skills(SCENE, CATALOG, KNOWN, 1, handle)


@pytest.mark.parametrize("payload", [
    {"skills": "rotate-credential", "reason": "r"},
    {"skills": [""], "reason": "r"},
    {"skills": [None], "reason": "r"},
    {"skills": ["rotate-credential"], "reason": 7},
    {"skills": ["rotate-credential"]},
    {"reason": "r"},
    ["rotate-credential"],
])
def test_choose_skills_broken_contract_raises_model_output_error(payload):
    handle = make_fake_handle(script=json_script(payload))
    with pytest.raises(ModelOutputError):
        choose_skills(SCENE, CATALOG, KNOWN, 3, handle)


@pytest.mark.parametrize("max_reads", [-1, 1.5, True, "3"])
def test_choose_skills_bad_max_reads_is_a_caller_bug(max_reads):
    handle = make_fake_handle(script=json_script({"skills": []}))
    with pytest.raises(ValueError):
        choose_skills(SCENE, CATALOG, KNOWN, max_reads, handle)
    assert handle.client.calls == []


def test_choose_skills_bad_known_names_is_a_caller_bug():
    handle = make_fake_handle(script=json_script({"skills": []}))
    with pytest.raises(ValueError):
        choose_skills(SCENE, CATALOG, ("ok", ""), 3, handle)
    assert handle.client.calls == []


# ---------------------------------------------------------------------------
# write_plan (EXAM_PLAN)
# ---------------------------------------------------------------------------

def test_write_plan_wiring_and_result():
    handle = make_fake_handle(script=json_script(
        {"plan": "  Rotate the credential.\\nRestart and verify.  "}))

    plan = write_plan(SCENE, "SKILL BODY", "the catalog line applies", handle)

    assert handle.client.calls[0]["user"] == get_prompt("EXAM_PLAN").format(
        scene_text=SCENE,
        selected_skills="SKILL BODY",
        selection_reason="the catalog line applies",
    )
    assert plan == "Rotate the credential.\\nRestart and verify."


def test_write_plan_renders_a_sequence_of_skills_as_separated_blocks():
    handle = make_fake_handle(script=json_script({"plan": "p"}))
    write_plan(SCENE, ["first skill", "second skill"], "reason", handle)
    assert "first skill\n\nsecond skill" in handle.client.calls[0]["user"]


@pytest.mark.parametrize("payload", [
    {"plan": ""},
    {"plan": "   "},
    {"plan": 7},
    {},
    ["plan"],
])
def test_write_plan_broken_contract_raises_model_output_error(payload):
    handle = make_fake_handle(script=json_script(payload))
    with pytest.raises(ModelOutputError):
        write_plan(SCENE, "body", "reason", handle)


# ---------------------------------------------------------------------------
# judge (EXAM_JUDGE)
# ---------------------------------------------------------------------------

def test_judge_success_maps_true_and_uses_the_judge_prompt():
    handle = make_fake_handle(script=json_script(
        {"verdict": "success", "reason": "  the order is right  "}))

    assert judge(SCENE, "expected", REFERENCE, CATALOG,
                 "rotate-credential", "applies", "the plan", handle) == (
        True, "the order is right")
    assert handle.client.calls[0]["user"] == get_prompt("EXAM_JUDGE").format(
        scene_text=SCENE,
        expected_behavior="expected",
        reference_answer=REFERENCE,
        skill_catalog=CATALOG,
        selected_skills="rotate-credential",
        selection_reason="applies",
        plan="the plan",
    )


def test_judge_failure_maps_false_with_reason():
    handle = make_fake_handle(script=json_script(
        {"verdict": "failure", "reason": "it restarts without rotating"}))
    assert judge(SCENE, "e", REFERENCE, CATALOG, [], "none apply", "p",
                 handle) == (False, "it restarts without rotating")


def test_judge_renders_opened_skill_bodies_as_separated_blocks():
    handle = make_fake_handle(script=json_script(
        {"verdict": "success", "reason": "ok"}))
    judge(SCENE, "e", REFERENCE, CATALOG, ["first", "second"], "r", "p",
          handle)
    assert "first\n\nsecond" in handle.client.calls[0]["user"]


@pytest.mark.parametrize(
    "payload",
    [
        {"verdict": "maybe", "reason": "r"},
        # Case-sensitive on purpose: guessing what "Success" meant would
        # silently flip the exam's verdict.
        {"verdict": "Success", "reason": "r"},
        {"verdict": 1, "reason": "r"},
        {"reason": "verdict key missing"},
        {"verdict": "success", "reason": None},
        ["success", "r"],
    ],
)
def test_judge_broken_contract_raises_model_output_error(payload):
    handle = make_fake_handle(script=json_script(payload))
    with pytest.raises(ModelOutputError):
        judge(SCENE, "e", REFERENCE, CATALOG, [], "r", "p", handle)


# ---------------------------------------------------------------------------
# Accounting and the retired prompt names
# ---------------------------------------------------------------------------

def test_blocks_forward_usage_and_run_dir(tmp_path):
    usage = RecordingUsage()
    handle = make_fake_handle(script=json_script({
        "scene_text": "s",
        "expected_behavior": "b",
        "reference_answer": "r",
    }))
    write_scene("t", "p", "f", "c", "n", handle, usage=usage,
                run_dir=tmp_path)

    assert len(usage.records) == 1
    assert usage.records[0]["prompt"] == "EXAM_SCENE"
    assert usage.records[0]["status"] == "ok"
    assert len(list((tmp_path / "llm_requests").glob("*.json"))) == 1


@pytest.mark.parametrize("retired", ["EXAM_GEN", "ANSWER", "JUDGE"])
def test_retired_exam_prompt_names_are_gone(retired):
    with pytest.raises(PromptNotFoundError):
        get_prompt(retired)
