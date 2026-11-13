"""The fake provider's answers to the paper method's prompts.

Every reply is checked by the REAL consumer of that prompt (the trial
diagnosis, the subtask typing block, the card writer, the two text
evolvers, the two rewrite methods, the two skill-use judges), so a reply
that its strict validator would refuse fails here and not in a smoke. The
weak executor is driven through the real SearchQA plugin, reading cards
from a really materialized skill folder.
"""

import dataclasses
import json

import pytest

from expedistill.benchmarks.alfworld.prompt_pack import PROMPT_PACK as ALFWORLD_PACK
from expedistill.benchmarks.searchqa.prompt_pack import PROMPT_PACK as SEARCHQA_PACK
from expedistill.benchmarks.searchqa.plugin import SearchQAPlugin
from expedistill.blocks import adherence
from expedistill.blocks.segment import segment, validate_segments
from expedistill.blocks.subtask_types import segment_card, type_segments
from expedistill.composites.sheet_facts import sheet_facts
from expedistill.core.ids import (
    make_subtask_id, make_task_id, make_trajectory_id, make_type_id)
from expedistill.core.models import Subtask, SubtaskType
from expedistill.infra.reliability import ConfigError
from expedistill.llm.call import ModelHandle
from expedistill.llm.fake_provider import (
    REVISION_MARKER,
    FakeProviderClient,
    load_plan,
)
from expedistill.methods.cards import CardAuthoringParams
from expedistill.methods.cards.material import Material
from expedistill.methods.cards.writer import write_card
from expedistill.methods.diagnose import diagnose_trial
from expedistill.methods.evolve_text import evolve_text
from expedistill.methods.revise import revise_content, revise_trigger
from tests.helpers.plugins import ensure_plugin
from tests.helpers.searchqa_stub import fixture_items, install_eval_repo_stub
from tests.helpers.skill_folder import (
    CARD_FIELDS, folder_task_meta, write_skill_folder)
from tests.helpers.skills import make_skill
from tests.helpers.traces import execution_sheet

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
# the plan
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("patch, match", [
    ({"segment_steps": 0}, "segment_steps"),
    ({"segment_steps": "2"}, "segment_steps"),
])
def test_plan_segment_steps_is_checked(tmp_path, patch, match):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({**PLAN, **patch}), encoding="utf-8")
    with pytest.raises(ConfigError, match=match):
        load_plan(str(path))


def test_plan_read_mode_vocabulary_is_checked(tmp_path):
    plan = {"questions": {QUESTION: {"answer": ANSWER, "read": "maybe"}}}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ConfigError, match="read mode"):
        load_plan(str(path))


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
# the learn phase: diagnosis, typing, card writing
# ---------------------------------------------------------------------------

def _sheet(task, n_steps=4):
    sheet = execution_sheet(make_task_id("searchqa", task), "weak", 0,
                            benchmark="searchqa", n_steps=n_steps)
    return sheet, sheet_facts(sheet, lambda skill_id: None)


def test_a_diagnosis_blames_the_last_step_and_varies_its_scope():
    findings = [diagnose_trial(*_sheet(f"t{index}"), handle=_handle())
                for index in range(12)]
    assert {finding.failing_step for finding in findings} == {3}
    assert {finding.scope for finding in findings} == {"subtask", "general"}
    assert {bool(finding.model_note) for finding in findings} == {True,
                                                                  False}
    again = diagnose_trial(*_sheet("t0"), handle=_handle())
    assert again == findings[0]


def _subtask(task, index, label):
    trajectory_id = make_trajectory_id(
        "p01:baseline", make_task_id("searchqa", task), "strong", 0)
    return Subtask(
        subtask_id=make_subtask_id(trajectory_id, index),
        phase_label="p02:learn", task_id=make_task_id("searchqa", task),
        trajectory_id=trajectory_id, source="strong",
        trajectory_success=True, segment_index=index,
        step_range=(index, index + 1), label=label,
        description=f"{label}, step {index}", success=True)


def test_the_typing_puts_a_label_into_one_type():
    subtasks = [_subtask("a", 0, "search for the evidence"),
                _subtask("a", 1, "answer the question"),
                _subtask("b", 0, "search for the evidence")]
    cards = {sub.subtask_id: segment_card(sub, "step 0\naction: x", 400)
             for sub in subtasks}
    first = type_segments(cards, [], phase_label="p02:learn",
                          handle=_handle())
    assert [(row.type_id, row.name) for row in first.new_types] == [
        ("type/001", "search for the evidence"),
        ("type/002", "answer the question")]
    assert first.assignments[subtasks[0].subtask_id] == \
        first.assignments[subtasks[2].subtask_id] == "type/001"

    later = [_subtask("c", 0, "answer the question"),
             _subtask("c", 1, "recover from a rejected action")]
    second = type_segments(
        {sub.subtask_id: segment_card(sub, "", 400) for sub in later},
        first.new_types, phase_label="p04:learn", handle=_handle())
    assert second.assignments[later[0].subtask_id] == "type/002"
    assert [row.type_id for row in second.new_types] == ["type/003"]


def test_the_card_writer_copies_the_entry_state():
    subtask_type = SubtaskType(
        type_id=make_type_id(1), name="answer the question",
        entry_state="The agent has a passage that names the answer.",
        phase_label="p02:learn")
    material = Material(strong=[{
        "subtask": _subtask("a", 1, "answer the question"),
        "steps_view": "step 1\naction: Finish[x]\nobservation: ok"}])
    skill = write_card(subtask_type, material, profile="", playbook="",
                       handle=_handle(), params=CardAuthoringParams())
    assert skill.when_to_use == subtask_type.entry_state
    assert len(skill.steps) == 3
    assert skill.summary == "Teach the reader to answer the question."


# ---------------------------------------------------------------------------
# the evolving texts
# ---------------------------------------------------------------------------

def test_the_profile_merges_notes_without_repeating_them():
    handle = _handle()
    first = evolve_text(
        "profile", "", ["It skips verification.", ""], "", handle)
    assert first == "- It skips verification."
    second = evolve_text(
        "profile", first,
        ["It skips verification.", "It answers from memory."], "", handle)
    assert second.splitlines() == [
        "- It skips verification.", "- It answers from memory."]
    assert evolve_text("profile", second, [], "", handle) == second


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
# the two rewrite prompts
# ---------------------------------------------------------------------------

_MARK = REVISION_MARKER + ": name the concrete state before acting."


def _card():
    return make_skill(make_type_id(1), CARD_FIELDS["when_to_use"],
                      steps=["Search first.", "Then answer."],
                      not_applicable_when="never", summary="Answer briefly.")


def test_a_trigger_rewrite_marks_when_to_use_and_keeps_the_summary():
    skill = _card()
    revision = revise_trigger(
        skill, catalog="the catalog", evidence_steps="steps",
        diagnosis="not opened", playbook="", profile="", handle=_handle())
    assert revision.side == "trigger"
    assert revision.fields["when_to_use"].endswith(_MARK)
    assert revision.fields["summary"] == skill.summary


def test_a_content_rewrite_marks_the_end_of_the_content():
    skill = _card()
    revision = revise_content(
        skill, evidence_steps="steps", diagnosis="opened, misses a step",
        playbook="the playbook", profile="", handle=_handle())
    assert revision.side == "content"
    assert revision.fields["steps"] == skill.steps
    assert revision.fields["not_applicable_when"].endswith(_MARK)


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
