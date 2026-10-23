"""The shared attribution driver: one group call, fact-only packages."""

from expedistill.composites.live_measurement import round_dir
from expedistill.composites.skill_usage import SKILL_USAGE_NAME
from expedistill.core.contracts import AcceptanceReportRow
from expedistill.core.ids import make_task_id, make_trajectory_id
from expedistill.core.models import Step, Trajectory
from expedistill.infra.store import Store, write_jsonl
from expedistill.llm import json_script, make_fake_handle
from expedistill.methods.attribution.group import collect_group_attribution
from tests.helpers.skills import make_skill

TASK = make_task_id("tb", "t1")
TRAJ = make_trajectory_id(TASK, "strong", 0)
SKILLS = [make_skill(f"{TRAJ}/seg0{index}", content="[When to use] x\n"
                     "[Steps]\n1. do\n[Verify] y\n"
                     "[Do not use when] z")
          for index in range(2)]


def _trajectory(attempt, role="weak"):
    return Trajectory(
        trajectory_id=make_trajectory_id(TASK, role, attempt),
        task_id=TASK, benchmark="tb", model_role=role,
        model_name="m", attempt_index=attempt,
        steps=[Step(action=f"do {attempt}", observation="obs")],
        correctness=False)


def test_collects_fact_only_packages_and_asks_once(tmp_path):
    run = tmp_path / "run"
    store = Store(run)
    for attempt in range(2):
        store.save(_trajectory(attempt, "strong"), "trajectories")
        store.save(_trajectory(attempt, "weak"), "trajectories")
    round_store = Store(round_dir(store, 0))
    for attempt in range(2):
        round_store.save(_trajectory(attempt), "trajectories")
    write_jsonl(round_dir(store, 0) / SKILL_USAGE_NAME, [{
        "task_id": TASK, "round": 0, "attempt": attempt,
        "delivery_status": "ok",
        "available_skill_ids": [skill.skill_id for skill in SKILLS],
        "read_skill_ids": [SKILLS[0].skill_id],
        "unread_skill_ids": [SKILLS[1].skill_id],
        "reads": [{"skill_id": SKILLS[0].skill_id,
                   "filename": "a/SKILL.md", "step_index": 0,
                   "command": "cat a/SKILL.md"}],
        "outcome": "failure",
    } for attempt in range(2)])
    report = AcceptanceReportRow(
        unit_id="p03:live:tb:tb:t1:weak:deadbeef",
        phase_label="p03:live", role="weak",
        injected_skills=[{"skill_id": skill.skill_id, "version": 1}
                         for skill in SKILLS],
        runs=[], valid_count=2, success_count=0, status="normal",
        cause="", artifacts={})
    handle = make_fake_handle("author", [json_script({
        "diagnosis": "one was read and ignored",
        "skills": [
            {"skill_id": SKILLS[0].skill_id, "relevance": "relevant",
             "application": "not_applied", "evidence_steps": [0],
             "problem": "the steps were ignored"},
            {"skill_id": SKILLS[1].skill_id, "relevance": "relevant",
             "application": "unclear", "evidence_steps": [],
             "problem": "never read"},
        ]})])

    packages = collect_group_attribution(
        task_id=TASK, members=SKILLS, report_row=report, store=store,
        round_=0, n_attempts=2, task_instruction="restart the service",
        weak_profile="weak", author_handle=handle)

    assert len(packages) == 2
    first = packages[0]
    assert first.fact("group_diagnosis") == "one was read and ignored"
    assert first.fact("skill_read_attempts") == "2"
    assert first.fact("skill_relevance") == "relevant"
    assert first.fact("skill_application") == "not_applied"
    assert first.fact("task_instruction") == "restart the service"
    assert len(handle.client.calls) == 1
