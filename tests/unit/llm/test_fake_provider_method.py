"""The fake provider's answers to the paper method's prompts.

Every reply is checked by the REAL consumer of that prompt (the cluster
authoring driver, the exam blocks, the trial-level review, the requirement
writer, the REWRITE block, the two skill-use judges), so a reply that its
strict validator would refuse fails here and not in a smoke. The weak
executor is driven through the real SearchQA plugin, reading cards from a
really materialized skill folder.
"""

import dataclasses
import json
from types import SimpleNamespace

import pytest

from expedistill.benchmarks.alfworld.learning import ALFWORLD_CONTEXT
from expedistill.benchmarks.searchqa.learning import SEARCHQA_CONTEXT
from expedistill.benchmarks.searchqa.plugin import SearchQAPlugin
from expedistill.blocks import adherence, exam
from expedistill.blocks.author import has_skill_sections, rewrite_skill
from expedistill.blocks.segment import segment, validate_segments
from expedistill.composites.evidence_package import EvidencePackage
from expedistill.core.answer_sheet import (
    AnswerSheet,
    CoverageRecord,
    ExecutionRecord,
    GradingRecord,
    PhaseReport,
    UnitSummary,
)
from expedistill.core.ids import make_subtask_id, make_task_id, make_trajectory_id
from expedistill.core.models import Exam, Skill, Step, Subtask
from expedistill.core.trace import TraceRef
from expedistill.infra.reliability import ConfigError
from expedistill.llm.call import ModelHandle
from expedistill.llm.fake_provider import (
    REVISION_MARKER,
    FakeProviderClient,
    load_plan,
)
from expedistill.methods.author import ClusterAuthoringParams
from expedistill.methods.author import cluster_by_trigger as cluster
from expedistill.methods.author.requirement import write_requirement
from expedistill.composites.exam_answering import _aliases
from expedistill.composites.skill_folder import materialize_skill_folder
from expedistill.methods.delivery.folder import (
    render_catalog,
    skill_folder_manifest,
)
from expedistill.methods.review import trial_review
from expedistill.methods.review.contracts import (
    NewSkillRequirement,
    ReviewContext,
)
from tests.helpers.plugins import ensure_plugin
from tests.helpers.searchqa_stub import fixture_items, install_eval_repo_stub
from tests.helpers.skill_folder import CARD_BODY, folder_task_meta
from tests.helpers.skills import make_skill

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
    # must be registered again for the review and executor tests.
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
    import json
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
    body = f"{CARD_BODY}\n{REVISION_MARKER}: concrete" if marker \
        else CARD_BODY
    bank = [make_skill(f"traj/searchqa:{name}/strong/00/seg00", body,
                       summary=f"Card {name}.", status="pending")
            for name in ("alpha", "beta")]
    manifest = skill_folder_manifest(
        bank, "summary_then_when_to_use", accepted_only=False)
    materialize_skill_folder(manifest, bank, tmp_path / "f")
    locations = [entry["filename"] for entry in manifest["files"]]
    if folder:
        task.meta.update(folder_task_meta(manifest, tmp_path / "f"))
    client = _client(plan)
    handle = _handle(client, role="executor_weak", model=model)
    outcome = SearchQAPlugin().run(task, handle, skills_block="",
                                   workdir=tmp_path)
    return outcome, client, locations


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
# cluster authoring
# ---------------------------------------------------------------------------

def _subtask(task, segment_index, *, source="strong", success=True):
    trajectory_id = make_trajectory_id(make_task_id("searchqa", task),
                                       source, 0)
    return Subtask(
        subtask_id=make_subtask_id(trajectory_id, segment_index),
        task_id=make_task_id("searchqa", task),
        trajectory_id=trajectory_id, source=source,
        trajectory_success=success, segment_index=segment_index,
        step_range=(segment_index, segment_index + 1),
        label="answer the question", description="submit the answer",
        success=success, context_summary="the search is done")


def _experience(task, segment_index, of=2, **kwargs):
    return {
        "subtask": _subtask(task, segment_index, **kwargs),
        "steps_view": "step 0\naction: Finish[x]\nobservation: ok",
        "mode": "distill",
        "segment_position": f"segment {segment_index + 1} of {of} in its "
                            "trajectory; it covers original steps 0 to 0",
    }


def test_a_failed_segment_is_a_when_a_command_fails_moment():
    result = cluster.classify_trigger(
        _experience("a", 1, success=False, source="weak"),
        handle=_handle(), max_chars=400)
    assert result["family"] == "when_command_fails"


@pytest.mark.parametrize("segment_index, of, family", [
    (0, 2, "at_task_start"), (1, 2, "before_submitting"),
    (0, 1, "before_submitting")])
def test_a_successful_segment_is_classified_by_its_position(
        segment_index, of, family):
    result = cluster.classify_trigger(
        _experience("a", segment_index, of=of), handle=_handle(),
        max_chars=400)
    assert result["family"] == family
    assert result["family"] in cluster.TRIGGER_FAMILIES


def test_families_are_clustered_in_pairs_without_the_fallback():
    experiences = [_experience(task, 1) for task in "abcde"]
    clusters = cluster.cluster_family(
        "before_submitting", experiences, handle=_handle(),
        params=ClusterAuthoringParams())
    assert sorted(len(item["subtask_ids"]) for item in clusters) == [2, 3]
    assert not any(item.get("fallback") for item in clusters)
    assert sorted(sid for item in clusters for sid in item["subtask_ids"]) \
        == sorted(item["subtask"].subtask_id for item in experiences)


def test_a_lone_subtask_is_a_cluster_of_one():
    experiences = [_experience("a", 1)]
    clusters = cluster.cluster_family(
        "before_submitting", experiences, handle=_handle(),
        params=ClusterAuthoringParams())
    assert [len(item["subtask_ids"]) for item in clusters] == [1]
    assert not clusters[0].get("fallback")


@pytest.mark.parametrize("context", [SEARCHQA_CONTEXT, ALFWORLD_CONTEXT])
@pytest.mark.parametrize("family", list(cluster.TRIGGER_FAMILIES))
def test_the_cluster_skill_opens_with_the_benchmarks_phrase(context, family):
    experiences = [_experience(task, 1) for task in "ab"]
    handle = _handle(context=context)
    clusters = cluster.cluster_family(
        family, experiences, handle=handle,
        params=ClusterAuthoringParams())
    by_id = {item["subtask"].subtask_id: item for item in experiences}
    skill = cluster.write_cluster_skill(
        clusters[0], by_id, weak_profile="", handle=handle,
        params=ClusterAuthoringParams())
    phrase = context.variables()[f"phrase_{family}"]
    assert skill.content.startswith(f"[When to use] {phrase}")
    assert has_skill_sections(skill.content)
    assert skill.summary and "\n" not in skill.summary
    assert skill.source_subtasks == sorted(clusters[0]["subtask_ids"])


# ---------------------------------------------------------------------------
# the generalization exam
# ---------------------------------------------------------------------------

def _skills(count=3):
    return [make_skill(f"traj/searchqa:t{index}/strong/00/seg00",
                       f"[When to use] when {index}\n[Steps]\n1. do it\n"
                       "[Do not use when] never",
                       summary=f"card {index}", status="pending")
            for index in range(count)]


def _catalog(skills):
    return render_catalog(skills)


def test_the_exam_scene_is_built_from_the_task_prompt_and_the_prefix():
    scene = exam.write_scene(
        f"## Question\n{QUESTION}\n\n## Transcript so far\n",
        "step 0\naction: Search[x]\nobservation: a passage about it",
        "step 1\naction: Finish[y]\nobservation: ok",
        "[When to use] x", "P02 cluster", _handle())
    assert QUESTION in scene["scene_text"]
    assert "a passage about it" in scene["scene_text"]
    assert all(scene[field].strip() for field in exam.SCENE_FIELDS)


def test_the_exam_scene_without_a_prefix_has_no_observation_to_quote():
    scene = exam.write_scene(
        f"## Question\n{QUESTION}", "", "step 0\naction: Search[x]",
        "[When to use] x", "n", _handle())
    assert "latest observation" not in scene["scene_text"]


def _known_names(skills):
    """Every spelling answer_exam hands choose_skills for these cards."""
    manifest = skill_folder_manifest(
        skills, "summary_then_when_to_use", accepted_only=False)
    return manifest, list(_aliases(manifest))


def test_the_trigger_names_catalog_cards_within_the_read_limit():
    skills = _skills(10)
    manifest, known = _known_names(skills)
    spellings = set()
    picks = []
    for index in range(40):
        selected, reason = exam.choose_skills(
            f"scene number {index}", manifest["catalog_text"], known, 3,
            _handle())
        assert set(selected) <= set(known)
        assert len(selected) <= 3 and reason
        spellings.update("id" if name.startswith("traj/") else "dir"
                         for name in selected)
        picks.append(tuple(selected))
    assert () in picks and any(len(pick) >= 2 for pick in picks)
    assert len({pick for pick in picks}) > 3
    assert spellings == {"id", "dir"}   # both catalog spellings are used


def test_the_trigger_respects_a_read_limit_of_one_and_an_empty_catalog():
    skills = _skills(3)
    manifest, known = _known_names(skills)
    for index in range(20):
        selected, _ = exam.choose_skills(
            f"scene {index}", manifest["catalog_text"], known, 1, _handle())
        assert len(selected) <= 1
    selected, _ = exam.choose_skills("scene", "", [], 3, _handle())
    assert selected == []


def test_the_plan_quotes_an_opened_skill_in_both_shapes():
    skills = _skills(1)
    plans = {exam.write_plan(f"scene {index}", skills[0].content, "why",
                             _handle()) for index in range(12)}
    assert all("Apply the opened skill" in plan for plan in plans)
    assert any("\n" in plan for plan in plans)      # a list of steps
    assert any("\n" not in plan for plan in plans)  # one numbered string
    bare = exam.write_plan("scene", "", "", _handle())
    assert "Apply the opened skill" not in bare


def test_the_judge_fails_a_plan_that_opened_nothing_and_varies_otherwise():
    verdicts = {}
    for index in range(30):
        success, reason = exam.judge(
            f"scene {index}", "behave", "reference", _catalog(_skills()),
            "cat dir/SKILL.md", "why", f"plan {index}", _handle())
        assert reason.strip()
        verdicts[index] = success
    assert set(verdicts.values()) == {True, False}
    success, reason = exam.judge(
        "scene", "behave", "reference", "", "", "", "plan", _handle())
    assert success is False and "without opening any skill" in reason
    again, _ = exam.judge(
        "scene 0", "behave", "reference", "", "cat x/SKILL.md", "",
        "plan 0", _handle())
    assert again == verdicts[0]


# ---------------------------------------------------------------------------
# trial-level review
# ---------------------------------------------------------------------------

SKILL, OTHER = _skills(2)


def _unit(unit_id, skill, task_ref, phase):
    return UnitSummary(
        unit_id=unit_id, phase_label=phase, task_ref=task_ref, role="weak",
        arm=None, expected_attempts=1,
        injected_skills=[{"skill_id": skill.skill_id, "version": 1}],
        valid_count=1, success_count=0, status="normal", cause="")


def _sheet(unit_id, phase, execution_fields, verdict="failure"):
    execution = ExecutionRecord(
        sheet_id=f"{unit_id}/att00", unit_id=unit_id, phase_label=phase,
        role="weak", arm=None, model_role="weak", model_name="fake-weak",
        sample_index=0, **execution_fields)
    return AnswerSheet(
        sheet_id=execution.sheet_id, phase_label=phase,
        coverage=CoverageRecord(unit_id=unit_id, attempt_index=0,
                                present=True, status="present"),
        execution=execution,
        grading=GradingRecord(verdict=verdict, basis="test",
                              rule_name="test"))


def _folder(*skills):
    return {
        "files": [{"skill_id": skill.skill_id, "version": 1,
                   "filename": skill.skill_id.replace(":", "-").replace(
                       "/", "-") + "/SKILL.md"} for skill in skills],
        "catalog_text": _catalog(skills),
    }


def _exam_sheet(skill, opened):
    folder = _folder(SKILL, OTHER)
    names = {entry["skill_id"]: entry["filename"]
             for entry in folder["files"]}
    cats = "\n".join(f"cat {names[item.skill_id]}" for item in opened)
    unit = f"exam-{skill.skill_id}"
    return unit, _sheet(unit, "p03:exam", dict(
        task_ref=f"{skill.skill_id}/v01/exam",
        skills_injected=[{"skill_id": skill.skill_id, "version": 1}],
        steps=[Step(action=cats or "# no skill opened",
                    observation="\n\n".join(i.content for i in opened)),
               Step(action="restart it", observation="")],
        answer="restart it",
        metrics={"scene_text": "A reader is answering a question.",
                 "expected_behavior": "search, check, answer"},
        aux={"judge_reason": "The plan submits an answer without checking "
                             "the passage against the question."},
        artifacts={"skill_folder": folder}, kind="trajectory"))


def _context(**changes):
    base = ReviewContext(
        phase_kind="exam", phase_label="p04:learn",
        source_phase_label="p03:exam", checks_left=1, round=0,
        delivery="exam_folder", compare_rule="strict_rate_gain",
        n_attempts_weak=1, benchmark="searchqa", weak_profile="",
        task_select_rule="any_weak_failed", trajectory_select="all",
        segment_select="strong_success_weak_failed", workers=1)
    return dataclasses.replace(base, **changes)


def _exam_store():
    scene = TraceRef("p01:baseline", "real-sheet", 1)
    return SimpleNamespace(load=lambda kind, object_id, cls=None: Exam(
        exam_id=object_id, skill_id=SKILL.skill_id, skill_version=1,
        scene_text="s", expected_behavior="e", reference_answer="r",
        scene=scene))


def _exam_report(opened_by_skill):
    sheets, units = [], []
    for skill, opened in opened_by_skill:
        unit, sheet = _exam_sheet(skill, opened)
        sheets.append(sheet)
        units.append(_unit(unit, skill, f"{skill.skill_id}/v01/exam",
                           "p03:exam"))
    return PhaseReport(phase_kind="exam", learning_usable=True,
                       sheets=sheets, units=units)


def _review(report, context, bank, client=None):
    return trial_review.run_trial_review(
        current=report, bank=bank, context=context,
        handles={"author": _handle(client)}, store=_exam_store())


def test_an_exam_review_decides_every_skill_and_writes_the_requirement():
    report = _exam_report([(SKILL, []), (OTHER, [SKILL])])
    result = _review(report, _context(), [SKILL, OTHER])

    assert not result.failed_targets and not result.model_errors
    assert len(result.trial_findings) == 2
    assert {item.row.target_id for item in result.items} \
        == {SKILL.skill_id, OTHER.skill_id}
    assert {item.row.action for item in result.items} <= {
        "keep", "revise", "delete"}
    # An exam skill is always the first of its finding's skills.
    for finding, (skill, _) in zip(
            result.trial_findings, [(SKILL, []), (OTHER, [SKILL])]):
        assert finding.skill_ids[0] == skill.skill_id
    sheet_ids = {finding.sheet_id for finding in result.trial_findings}
    for requirement in result.new_skill_requirements:
        assert set(requirement.source_sheet_ids) <= sheet_ids
        assert requirement.title and requirement.description
    assert result.profile_update.strip()
    assert "\n\n" not in result.profile_update


def test_the_exam_review_is_deterministic():
    def once():
        result = _review(_exam_report([(SKILL, []), (OTHER, [SKILL])]),
                         _context(), [SKILL, OTHER])
        return ([(item.row.target_id, item.row.action)
                 for item in result.items],
                [finding.reason for finding in result.trial_findings],
                result.profile_update,
                [item.title for item in result.new_skill_requirements])
    assert once() == once()


def test_the_exam_review_exercises_every_action_across_skills():
    skills = _skills(12)
    report = _exam_report([(skill, []) for skill in skills])
    result = _review(report, _context(), skills)
    actions = {item.row.action for item in result.items}
    assert {"revise", "delete"} <= actions


def _live_review(monkeypatch, steps):
    """One failed live trial through the review; the task prompt lookup is
    replaced (it would load the real benchmark)."""
    from expedistill.methods.review import trial
    monkeypatch.setattr(trial.attribution, "task_instruction_for",
                        lambda benchmark, task: "task")
    folder = _folder(SKILL)
    unit = "live-unit"
    sheet = _sheet(unit, "p03:live", dict(
        task_ref="searchqa:t1", skills_injected=[{
            "skill_id": SKILL.skill_id, "version": 1}],
        steps=steps(folder), raw_correctness=False,
        artifacts={"skill_folder": folder}))
    report = PhaseReport(
        phase_kind="live", learning_usable=True, sheets=[sheet],
        units=[_unit(unit, SKILL, "searchqa:t1", "p03:live")])
    return trial_review.run_trial_review(
        current=report, bank=[SKILL],
        context=_context(phase_kind="live", source_phase_label="p03:live",
                         delivery="folder_by_task"),
        handles={"author": _handle()})


def test_a_live_trial_review_names_the_unread_delivered_skill(monkeypatch):
    result = _live_review(monkeypatch, lambda folder: [
        Step(action="Search[q]", observation="a passage"),
        Step(action="Finish[wrong]", observation="INCORRECT")])
    finding = result.trial_findings[0]
    assert finding.skill_ids == [SKILL.skill_id]
    assert (finding.step_start, finding.step_end) == (0, 2)
    assert [item.row.target_id for item in result.items] \
        == [SKILL.skill_id]
    assert result.items[0].row.action in ("revise", "delete")


def test_a_live_trial_that_opened_the_card_points_at_the_read_step(
        monkeypatch):
    result = _live_review(monkeypatch, lambda folder: [
        Step(action="Search[q]", observation="a passage"),
        Step(action=f"ReadSkill[{folder['files'][0]['filename']}]",
             observation=SKILL.content),
        Step(action="Finish[wrong]", observation="INCORRECT")])
    assert result.trial_findings[0].step_start == 1




def test_the_profile_merges_notes_without_repeating_them():
    from expedistill.methods.review import profile
    handle = _handle()
    first = profile.update_profile(
        old_profile="", notes=["It skips verification.", ""],
        handle=handle)
    assert first == "- It skips verification."
    second = profile.update_profile(
        old_profile=first,
        notes=["It skips verification.", "It answers from memory."],
        handle=handle)
    assert second.splitlines() == [
        "- It skips verification.", "- It answers from memory."]
    assert profile.update_profile(
        old_profile=second, notes=[], handle=handle) == second


# ---------------------------------------------------------------------------
# the requirement skill
# ---------------------------------------------------------------------------

def test_the_requirement_skill_carries_the_trigger_and_three_sections():
    subtask = _subtask("a", 0, source="weak", success=False)
    requirement = NewSkillRequirement(
        requirement_id="req/abc", title="Verify the passage",
        description="verify it before replying",
        trigger="Before answering, when a passage was retrieved.",
        evidence=EvidencePackage(
            target="req/abc", facts=[],
            segments=[{"sheet_id": "s1", "step_range": [0, 1]}],
            source_experiences=[{
                "subtask": subtask, "steps_view": "step 0",
                "mode": "diagnose", "segment_position": "trial window"}]),
        source_sheet_ids=["s1"])
    skill = write_requirement(
        requirement, weak_profile="- it skips checks",
        source_context="the real steps", handle=_handle())
    assert isinstance(skill, Skill) and has_skill_sections(skill.content)
    assert skill.content.startswith(
        "[When to use] Before answering, when a passage was retrieved.")
    assert "verify the passage" in skill.summary.lower()
    assert skill.status == "pending"


# ---------------------------------------------------------------------------
# REWRITE and its scope notice
# ---------------------------------------------------------------------------

def _rewrite(skill, scope):
    return rewrite_skill(
        skill, "question", "weak answer", "what was wrong", "", _handle(),
        source_context="context", scope=scope)


def _sections(content):
    from expedistill.core.skill_sections import split_sections
    return split_sections(content)


def test_a_rewrite_without_a_scope_marks_the_end_of_the_skill():
    skill = _skills(1)[0]
    new = _rewrite(skill, "both")
    assert new["content"].endswith(REVISION_MARKER
                                   + ": name the concrete state before "
                                     "acting.")
    assert new["summary"] != skill.summary


def test_a_body_scope_keeps_the_trigger_and_the_summary_after_the_splice():
    skill = _skills(1)[0]
    new = _rewrite(skill, "body")
    old, now = _sections(skill.content), _sections(new["content"])
    assert now["[When to use]"] == old["[When to use]"]
    assert REVISION_MARKER in now["[Do not use when]"]
    assert "OUT-OF-SCOPE-EDIT" not in new["content"]
    assert new["summary"] == skill.summary


def test_a_trigger_scope_keeps_the_body_and_rewrites_the_summary():
    skill = _skills(1)[0]
    new = _rewrite(skill, "trigger")
    old, now = _sections(skill.content), _sections(new["content"])
    assert REVISION_MARKER in now["[When to use]"]
    assert now["[Steps]"] == old["[Steps]"]
    assert now["[Do not use when]"] == old["[Do not use when]"]
    assert "OUT-OF-SCOPE-EDIT" not in new["content"]
    assert new["summary"] != skill.summary


def test_the_unspliced_scope_replies_do_carry_the_forbidden_edit():
    skill = _skills(1)[0]
    body_prompt_client = _client()
    user = ("A skill written for a weak model failed its acceptance exam.\n"
            "SKILL (current version):\n" + skill.content +
            "\n\nEXAM QUESTION:\nq\n\n"
            "SCOPE OF THIS REWRITE - the body only. The trigger is fixed.\n")
    reply = body_prompt_client.complete("fake-strong", "s", user)
    assert "OUT-OF-SCOPE-EDIT" in reply


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
    card = ("[When to use] at start\n[Steps]\n1. one\n2. two\n3. three\n"
            "4. four\n5. five\n[Do not use when] never")
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
    user = ("Judge whether the plan below handles the scene correctly.\n"
            "no sections at all")
    with pytest.raises(AssertionError, match="anchor"):
        _client().complete("fake-strong", "s", user)
