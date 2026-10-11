"""G4 answering / G5 judging unit tests: prompt wiring and verdict mapping.

FakeClient only; no network (AGENTS.md section 8). The wiring assertions
compare the full user text against the ANSWER/JUDGE templates so a template
edit that breaks the bare/with-skill contract fails here."""

import pytest

from expedistill.blocks.exam import answer, judge
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm import json_script, make_fake_handle
from expedistill.prompts import get as get_prompt


class RecordingUsage:
    """Test double for the B7 UsageRecorder (same record signature)."""

    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


def test_answer_bare_hits_answer_prompt_with_empty_block():
    handle = make_fake_handle(script="  Run the tests first.  ")
    out = answer("The build fails; what next?", "", handle)
    assert out == "Run the tests first."
    assert len(handle.client.calls) == 1
    call = handle.client.calls[0]
    assert call["user"] == get_prompt("ANSWER").format(
        question_text="The build fails; what next?", skills_block=""
    )


def test_answer_with_skills_uses_same_template():
    handle = make_fake_handle(script="ok")
    block = "1. Check the lock file first.\n2. Then rerun."
    out = answer("The build fails; what next?", block, handle)
    assert out == "ok"
    call = handle.client.calls[0]
    assert call["user"] == get_prompt("ANSWER").format(
        question_text="The build fails; what next?", skills_block=block
    )
    assert block in call["user"]


def test_judge_correct_maps_true_and_uses_judge_prompt():
    handle = make_fake_handle(
        script=json_script({"verdict": "correct", "error_description": ""})
    )
    assert judge("q", "the reference", "w", handle) == (True, "")
    assert handle.client.calls[0]["user"] == get_prompt("JUDGE").format(
        question_text="q", reference_answer="the reference", weak_answer="w"
    )


def test_judge_incorrect_maps_false_with_description():
    handle = make_fake_handle(
        script=json_script(
            {"verdict": "incorrect", "error_description": "It skips the lock file."}
        )
    )
    assert judge("q", "r", "w", handle) == (False, "It skips the lock file.")


def test_judge_missing_error_description_defaults_to_empty():
    handle = make_fake_handle(script=json_script({"verdict": "correct"}))
    assert judge("q", "r", "w", handle) == (True, "")


@pytest.mark.parametrize(
    "payload",
    [
        {"verdict": "maybe", "error_description": ""},
        # The verdict contract is case-sensitive on purpose: guessing what
        # "Correct" meant would silently flip acceptance decisions.
        {"verdict": "Correct", "error_description": ""},
        {"verdict": 1, "error_description": ""},
        {"error_description": "verdict key missing"},
        {"verdict": "correct", "error_description": None},
        ["correct", ""],
    ],
)
def test_judge_broken_contract_raises_model_output_error(payload):
    handle = make_fake_handle(script=json_script(payload))
    with pytest.raises(ModelOutputError):
        judge("q", "r", "w", handle)


def test_answer_forwards_usage_recorder():
    usage = RecordingUsage()
    handle = make_fake_handle(script="plain answer")
    answer("q", "", handle, usage=usage)
    assert len(usage.records) == 1
    assert usage.records[0]["prompt"] == "ANSWER"
    assert usage.records[0]["status"] == "ok"


def test_answer_forwards_run_dir_to_request_dump(tmp_path):
    handle = make_fake_handle(script="x")
    answer("q", "", handle, run_dir=tmp_path)
    dumps = list((tmp_path / "llm_requests").glob("*.json"))
    assert len(dumps) == 1
