"""The per-window attribution driver: folder invocations and injected windows."""


from expedistill.composites.answer_sheets import execution_from_trajectory
from expedistill.composites.live_measurement import round_dir
from expedistill.core.answer_sheet import AnswerSheet, CoverageRecord
from expedistill.core.ids import make_task_id, make_trajectory_id
from expedistill.core.models import Step, Trajectory
from expedistill.infra.store import Store
from expedistill.methods.attribution import AttributionParams, skill_attempts
from tests.helpers.skills import make_skill

TASK = make_task_id("tb", "t1")
TRAJ_ID = make_trajectory_id(TASK, "weak", 0)
SKILL_A = make_skill("traj/tb:t1/strong/00/seg00",
                     content="[When to use] a\n[Steps]\n1. do\n"
                             "[Do not use when] c")
SKILL_B = make_skill("traj/tb:t1/strong/00/seg01",
                     content="[When to use] d\n[Steps]\n1. do\n"
                             "[Do not use when] f")
FOLDER = {"files": [
    {"skill_id": SKILL_A.skill_id, "filename": "a/SKILL.md"},
    {"skill_id": SKILL_B.skill_id, "filename": "b/SKILL.md"},
]}


def _trajectory(actions):
    return Trajectory(
        trajectory_id=TRAJ_ID, task_id=TASK, benchmark="tb",
        model_role="weak", model_name="w", attempt_index=0,
        steps=[Step(action=action, observation="obs")
               for action in actions],
        correctness=False)


def _store(tmp_path, actions):
    run = tmp_path / "run"
    store = Store(run)
    Store(round_dir(store, 0)).save(_trajectory(actions), "trajectories")
    return store


def _fake_attribution(record):
    def fake(**kwargs):
        record.append(kwargs)
        return {skill.skill_id: {
            "trigger_context": "trigger",
            "behavior_after_trigger": "behavior",
            "improvement": "improve"}
            for skill in kwargs["skills"]}
    return fake


def test_folder_uses_each_cat_event_and_batches_the_window(tmp_path,
                                                           monkeypatch):
    store = _store(tmp_path, [
        "cat a/SKILL.md\ncat b/SKILL.md", "do something", "done"])
    usage = [{
        "task_id": TASK, "attempt": 0, "delivery_status": "ok",
        "read_skill_ids": [SKILL_A.skill_id, SKILL_B.skill_id],
        "unread_skill_ids": [],
        "reads": [
            {"skill_id": SKILL_A.skill_id, "step_index": 0,
             "filename": "a/SKILL.md", "command": "cat a/SKILL.md"},
            {"skill_id": SKILL_B.skill_id, "step_index": 0,
             "filename": "b/SKILL.md", "command": "cat b/SKILL.md"},
        ],
        "outcome": "failure",
    }]
    calls = []
    monkeypatch.setattr(skill_attempts, "attribute_attempts",
                        _fake_attribution(calls))

    evidence = skill_attempts.collect_skill_evidence(
        task_id=TASK, skills=[SKILL_A, SKILL_B], store=store, round_=0,
        n_attempts=1, task_instruction="do it", delivery="folder",
        folder=FOLDER, usage_rows=usage, weak_profile="w",
        author_handle=object(), params=AttributionParams(before_steps=0,
                                                        after_steps=0))

    assert len(calls) == 1
    assert {skill.skill_id for skill in calls[0]["skills"]} == {
        SKILL_A.skill_id, SKILL_B.skill_id}
    aliases = calls[0]["skill_aliases"]
    assert aliases["a"] == SKILL_A.skill_id
    assert aliases["a/SKILL.md"] == SKILL_A.skill_id
    assert aliases["b"] == SKILL_B.skill_id
    assert len(evidence[SKILL_A.skill_id].windows) == 1
    assert evidence[SKILL_A.skill_id].windows[0].kind == "invocation"
    assert evidence[SKILL_A.skill_id].windows[0].trigger_steps == [0]


def test_attribution_uses_only_the_task_description(tmp_path, monkeypatch):
    trajectory = _trajectory(["cat a/SKILL.md", "do something"])
    trajectory.meta["initial_prompt"] = (
        "You are an AI assistant tasked with solving command-line tasks.\n"
        "Return JSON with analysis/plan/commands.\n\n"
        "Task Description:\nFix the widget without changing main.tex.\n\n"
        "Before running any task command:\n"
        "Read the skill directory in <available_skills>.\n")
    store = Store(tmp_path / "run")
    Store(round_dir(store, 0)).save(trajectory, "trajectories")
    usage = [{
        "task_id": TASK, "attempt": 0, "delivery_status": "ok",
        "read_skill_ids": [SKILL_A.skill_id],
        "unread_skill_ids": [SKILL_B.skill_id],
        "reads": [{"skill_id": SKILL_A.skill_id, "step_index": 0,
                   "filename": "a/SKILL.md", "command": "cat a/SKILL.md"}],
        "outcome": "failure",
    }]
    calls = []
    monkeypatch.setattr(skill_attempts, "attribute_attempts",
                        _fake_attribution(calls))

    skill_attempts.collect_skill_evidence(
        task_id=TASK, skills=[SKILL_A], store=store, round_=0,
        n_attempts=1, task_instruction="fallback", delivery="folder",
        folder=FOLDER, usage_rows=usage, weak_profile="w",
        author_handle=object(), params=AttributionParams(before_steps=0,
                                                        after_steps=0))

    assert calls[0]["task_instruction"] == (
        "Fix the widget without changing main.tex.")
    assert "analysis/plan/commands" not in calls[0]["task_instruction"]


def test_folder_marks_an_unread_skill_with_no_windows(tmp_path, monkeypatch):
    store = _store(tmp_path, ["cat a/SKILL.md", "do something"])
    usage = [{
        "task_id": TASK, "attempt": 0, "delivery_status": "ok",
        "read_skill_ids": [SKILL_A.skill_id],
        "unread_skill_ids": [SKILL_B.skill_id],
        "reads": [{"skill_id": SKILL_A.skill_id, "step_index": 0,
                   "filename": "a/SKILL.md", "command": "cat a/SKILL.md"}],
        "outcome": "failure",
    }]
    calls = []
    monkeypatch.setattr(skill_attempts, "attribute_attempts",
                        _fake_attribution(calls))

    evidence = skill_attempts.collect_skill_evidence(
        task_id=TASK, skills=[SKILL_A, SKILL_B], store=store, round_=0,
        n_attempts=1, task_instruction="do it", delivery="folder",
        folder=FOLDER, usage_rows=usage, weak_profile="w",
        author_handle=object(), params=AttributionParams(before_steps=0,
                                                        after_steps=0))

    assert len(evidence[SKILL_A.skill_id].windows) == 1
    assert evidence[SKILL_B.skill_id].windows == []


def test_injected_delivery_uses_start_and_end_windows(tmp_path, monkeypatch):
    store = _store(tmp_path, [f"step {i}" for i in range(30)])
    calls = []
    monkeypatch.setattr(skill_attempts, "attribute_attempts",
                        _fake_attribution(calls))

    evidence = skill_attempts.collect_skill_evidence(
        task_id=TASK, skills=[SKILL_A, SKILL_B], store=store, round_=0,
        n_attempts=1, task_instruction="do it", delivery="whole_bank",
        folder=None, usage_rows=[], weak_profile="w",
        author_handle=object(), params=AttributionParams(before_steps=5,
                                                        after_steps=5))

    assert len(calls) == 2
    kinds = [call["window_kind"] for call in calls]
    assert kinds == ["start", "end"]
    assert len(evidence[SKILL_A.skill_id].windows) == 2
    assert evidence[SKILL_A.skill_id].windows[0].step_start == 0
    assert evidence[SKILL_A.skill_id].windows[1].step_end == 30


def _sheet(actions, *, folder=FOLDER, delivery_trajectory=True):
    trajectory = _trajectory(actions)
    execution = execution_from_trajectory(
        trajectory, unit_id="unit-1", phase_label="p03:live",
        arm=None, folder=folder if delivery_trajectory else None)
    coverage = CoverageRecord(
        unit_id="unit-1", attempt_index=0, present=True,
        status="present")
    return AnswerSheet(sheet_id=execution.sheet_id, phase_label="p03:live",
                       coverage=coverage, execution=execution)


def test_answer_sheet_is_the_folder_attribution_input(monkeypatch):
    sheet = _sheet(["cat a/SKILL.md\ncat b/SKILL.md", "do something"])
    calls = []
    monkeypatch.setattr(skill_attempts, "attribute_attempts",
                        _fake_attribution(calls))

    evidence = skill_attempts.collect_skill_evidence(
        task_id=TASK, skills=[SKILL_A, SKILL_B], sheets=[sheet],
        n_attempts=1, task_instruction="do it", delivery="folder",
        weak_profile="w", author_handle=object(),
        params=AttributionParams(before_steps=0, after_steps=0))

    assert len(calls) == 1
    assert {skill.skill_id for skill in calls[0]["skills"]} == {
        SKILL_A.skill_id, SKILL_B.skill_id}
    assert len(evidence[SKILL_A.skill_id].windows) == 1
    assert len(evidence[SKILL_B.skill_id].windows) == 1


def test_answer_sheet_is_the_injected_attribution_input(monkeypatch):
    sheet = _sheet([f"step {i}" for i in range(30)], folder=None,
                   delivery_trajectory=False)
    calls = []
    monkeypatch.setattr(skill_attempts, "attribute_attempts",
                        _fake_attribution(calls))

    evidence = skill_attempts.collect_skill_evidence(
        task_id=TASK, skills=[SKILL_A], sheets=[sheet],
        n_attempts=1, task_instruction="do it", delivery="whole_bank",
        weak_profile="w", author_handle=object(),
        params=AttributionParams(before_steps=5, after_steps=5))

    assert [call["window_kind"] for call in calls] == ["start", "end"]
    assert len(evidence[SKILL_A.skill_id].windows) == 2
