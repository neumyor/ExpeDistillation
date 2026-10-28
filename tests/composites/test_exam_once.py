"""K3 exam_once tests for the generalization exam (FakeClient only).

The exam has two answering turns: skill selection (when a catalog is
present) and the plan.  A bare control has no catalog and only writes the
plan.  These tests pin the call order, the stored attempt shape, the
skill-read step that the usage scanner reads, and the judge fields.
"""


import pytest

from expedistill.composites import exam_once
from expedistill.core import ids
from expedistill.core.models import Attempt, Exam, Skill
from expedistill.infra.reliability import ModelOutputError
from expedistill.infra.store import Store
from expedistill.llm.call import ModelHandle
from expedistill.llm.fake import FakeClient, json_script

SKILL_ID = "traj/tb:demo/weak/00/seg00/skill"
EXAM_ID = ids.make_exam_id(SKILL_ID, 1)
DIRNAME = "traj-tb-demo-weak-00-seg00-skill"
FILENAME = f"{DIRNAME}/SKILL.md"


def make_exam() -> Exam:
    return Exam(
        exam_id=EXAM_ID, skill_id=SKILL_ID, skill_version=1,
        scene_text="The service rejects connections. What next?",
        expected_behavior="Restart the service and verify it.",
        reference_answer="Restart the service.",
        source_ref={"phase_label": "p01:baseline", "scene_step": 0})


def make_folder() -> dict:
    return {
        "catalog_text": f"{SKILL_ID} | {FILENAME} | restart the service\n",
        "files": [{"skill_id": SKILL_ID, "version": 1,
                   "filename": FILENAME}],
        "description_mode": "summary_then_when_to_use",
    }


def make_handles(script):
    client = FakeClient(script)
    answerer = ModelHandle(name="weak-model", role="weak", client=client)
    judge = ModelHandle(name="strong-model", role="judge", client=client)
    return client, answerer, judge


def _store_with_skill(tmp_path):
    store = Store(tmp_path)
    store.save(Skill(
        skill_id=SKILL_ID, content="restart then verify",
        subtask_id="traj/tb:demo/weak/00/seg00",
        source_subtasks=["traj/tb:demo/weak/00/seg00"]), "skills")
    return store


def test_bare_writes_one_plan_then_judges(tmp_path):
    store = _store_with_skill(tmp_path)
    client, answerer, judge = make_handles([
        json_script({"plan": "restart the service"}),
        json_script({"verdict": "success", "reason": ""}),
    ])
    attempt = exam_once(
        store=store, exam=make_exam(), answerer_handle=answerer,
        judge_handle=judge, skill_folder=None, skills_block=None,
        purpose="bare")
    assert len(client.calls) == 2
    assert attempt.attempt_id == f"{EXAM_ID}/att00"
    assert attempt.purpose == "bare"
    assert attempt.weak_answer == "restart the service"
    assert attempt.steps[0].action == "# no skill catalog"
    assert attempt.steps[1].action == "restart the service"
    assert attempt.judge_verdict is True
    assert store.load("attempts", attempt.attempt_id, Attempt) == attempt


def test_catalog_selection_then_plan_then_judge(tmp_path):
    store = _store_with_skill(tmp_path)
    client, answerer, judge = make_handles([
        json_script({"skills": [DIRNAME], "reason": "restart applies"}),
        json_script({"plan": "restart then verify"}),
        json_script({"verdict": "success", "reason": ""}),
    ])
    attempt = exam_once(
        store=store, exam=make_exam(), answerer_handle=answerer,
        judge_handle=judge, skill_folder=make_folder(),
        purpose="acceptance", skill_id=SKILL_ID, skill_version=1)
    assert len(client.calls) == 3
    assert attempt.skill_id == SKILL_ID
    assert attempt.skill_version == 1
    assert attempt.weak_answer == "restart then verify"
    assert attempt.steps[0].action == f"cat {FILENAME}"
    assert attempt.steps[0].observation == "restart then verify"
    assert attempt.steps[1].action == "restart then verify"
    assert attempt.judge_verdict is True


def test_unknown_selected_skill_is_a_model_output_error(tmp_path):
    store = _store_with_skill(tmp_path)
    _client, answerer, judge = make_handles([
        json_script({"skills": ["not-in-the-catalog"], "reason": ""}),
    ])
    with pytest.raises(ModelOutputError, match="unknown skill"):
        exam_once(
            store=store, exam=make_exam(), answerer_handle=answerer,
            judge_handle=judge, skill_folder=make_folder(),
            purpose="acceptance", skill_id=SKILL_ID, skill_version=1)


def test_judge_failure_is_stored_on_the_attempt(tmp_path):
    store = _store_with_skill(tmp_path)
    _client, answerer, judge = make_handles([
        json_script({"plan": "wrong plan"}),
        json_script({"verdict": "failure", "reason": "misses verification"}),
    ])
    attempt = exam_once(
        store=store, exam=make_exam(), answerer_handle=answerer,
        judge_handle=judge, skill_folder=None, skills_block=None,
        purpose="bare")
    assert attempt.judge_verdict is False
    assert attempt.judge_reason == "misses verification"


def test_serial_counts_only_this_exam(tmp_path):
    store = _store_with_skill(tmp_path)
    store.save(Attempt(
        attempt_id=f"{ids.make_exam_id(SKILL_ID, 2)}/att07",
        exam_id=ids.make_exam_id(SKILL_ID, 2), purpose="regression"),
        "attempts")
    _client, answerer, judge = make_handles([
        json_script({"plan": "plan"}),
        json_script({"verdict": "success", "reason": ""}),
    ])
    attempt = exam_once(
        store=store, exam=make_exam(), answerer_handle=answerer,
        judge_handle=judge, skill_folder=None, purpose="bare")
    assert attempt.attempt_id == f"{EXAM_ID}/att00"
