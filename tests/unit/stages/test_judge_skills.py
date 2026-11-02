"""stages/judge_skills.py over a tiny finished test phase.

The run is built by tests/helpers/skill_use_runs.py: three with-bank trials
(one opens a card that applies, one opens nothing although a card applies,
one opens an applicable and an unrelated card) and a control trial that must
never be judged. The judge model is scripted.
"""

import json
import shutil
from types import SimpleNamespace

import pytest

from expedistill.benchmarks.base import ViewRules
from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ProviderRefusalError,
)
from expedistill.infra.store import read_json, read_jsonl
from expedistill.llm.fake import make_fake_handle
from expedistill.stages import judge_skills
from expedistill.stages.analysis_common import PINNED_NAME
from tests.helpers.skill_use_runs import (
    card_id,
    make_bank,
    read_action,
    skill_use_judge_script,
    snapshot,
    write_learn_phase,
    write_test_phase,
)

LABEL = "p03:test"

TRIALS = [
    {"task": "t1", "attempt": 0, "correct": True,
     "actions": ["ls", read_action("alpha"), "do alpha step 1",
                 "do alpha step 2"]},
    {"task": "t1", "attempt": 1, "correct": False,
     "actions": ["ls", "run"]},
    {"task": "t2", "attempt": 0, "correct": False,
     "actions": ["ls", read_action("beta"), read_action("gamma"), "x"]},
    {"task": "t1", "attempt": 0, "correct": True, "arm": "control",
     "actions": ["ls", "run"]},
]


def _applicable(user, listed):
    """t1's situation matches alpha, t2's matches beta."""
    return [card_id("alpha")] if "INSTR skillbench:t1" in user \
        else [card_id("beta")]


def _instruction(task_ref):
    return f"INSTR {task_ref}"


@pytest.fixture
def run_dir(tmp_path):
    run = tmp_path / "run"
    bank = make_bank()
    write_learn_phase(run, "p02:learn", bank)
    write_test_phase(run, bank, TRIALS)
    return run


def _judge(run_dir, out_dir, script=None, *, name="judge-model", **kwargs):
    handle = make_fake_handle(
        "judge", script or skill_use_judge_script(_applicable), name=name)
    summary = judge_skills.judge_phase(
        run_dir, LABEL, out_dir, handle, view_rules=ViewRules(),
        instruction_for=_instruction, **kwargs)
    return summary, handle


def _rows(out_dir):
    return read_jsonl(out_dir / judge_skills.JUDGMENTS_NAME)


def test_every_with_bank_trial_is_judged_and_the_control_is_not(
        run_dir, tmp_path):
    summary, _ = _judge(run_dir, tmp_path / "out")

    assert summary["expected_trials"] == summary["judged_trials"] == 3
    assert summary["failed_trials"] == []
    rows = _rows(tmp_path / "out")
    assert [(row["task"], row["attempt"]) for row in rows] == [
        ("skillbench:t1", 0), ("skillbench:t1", 1), ("skillbench:t2", 0)]
    first, second, third = rows
    assert first["opened"] == [card_id("alpha")]
    assert first["applicable"][card_id("alpha")] is True
    assert first["adherence"] == {card_id("alpha"): ["followed", "skipped"]}
    assert first["correct"] is True
    assert second["opened"] == [] and second["adherence"] == {}
    assert second["correct"] is False
    assert third["opened"] == [card_id("beta"), card_id("gamma")]
    assert all(row["delivered"] == [card_id(name) for name in
                                    ("alpha", "beta", "gamma")]
               for row in rows)


def test_the_aggregates_are_the_registered_metrics_over_the_rows(
        run_dir, tmp_path):
    summary, _ = _judge(run_dir, tmp_path / "out")
    metrics = summary["metrics"]

    # applicable pairs: alpha, alpha, beta; opened among them: alpha, beta.
    assert metrics["trigger_recall"]["value"] == pytest.approx(2 / 3)
    # opened: alpha, beta, gamma; gamma did not apply.
    assert metrics["misfire_rate"]["value"] == pytest.approx(1 / 3)
    assert metrics["open_rate"]["value"] == pytest.approx(2 / 3)
    assert metrics["adherence_rate"]["value"] == 0.5
    groups = metrics["success_by_group"]
    assert groups["applicable_opened"]["rate"] == 0.5
    assert groups["applicable_not_opened"]["rate"] == 0.0
    assert groups["nothing_applicable"]["n"] == 0
    assert read_json(tmp_path / "out" / judge_skills.SUMMARY_NAME) == summary


def test_calls_are_one_applicability_plus_one_adherence_per_opened_card(
        run_dir, tmp_path):
    calls = []
    _judge(run_dir, tmp_path / "out",
           skill_use_judge_script(_applicable, calls=calls))

    assert sorted(calls) == ["adherence"] * 3 + ["applicability"] * 3


def test_the_judge_reads_the_card_list_and_the_trajectory_from_the_read(
        run_dir, tmp_path):
    _, handle = _judge(run_dir, tmp_path / "out")
    prompts = [call["user"] for call in handle.client.calls]
    applicability = [p for p in prompts if "CARD LIST" in p]
    adherence = [p for p in prompts if "opened one skill card" in p]

    # The listing carries the id and the description the folder showed.
    assert ("- skill_id: " + card_id("alpha")
            + "\n  description: Card alpha teaches alpha. "
            "Use when: When alpha matters.") in applicability[0]
    assert "INSTR skillbench:t1" in applicability[0]
    assert "step 0\naction: ls" in applicability[0]
    # Adherence starts at the step of the read, numbered as in the trial.
    first = [p for p in adherence if "do alpha step 1" in p][0]
    assert "step 1\naction: " + read_action("alpha") in first
    assert "step 0\naction: ls" not in first
    assert "step 2\naction: do alpha step 1" in first
    assert "1. do step 1 of alpha" in first


def test_the_manifest_may_ride_on_the_sheets_instead_of_the_unit_rows(
        tmp_path):
    run = tmp_path / "run"
    bank = make_bank()
    write_learn_phase(run, "p02:learn", bank)
    write_test_phase(run, bank, TRIALS, manifest_on_sheets=True)
    (run / "phases" / "p03_test" / "units.jsonl").unlink()

    summary, _ = _judge(run, tmp_path / "out")

    assert summary["judged_trials"] == 3


def test_a_resume_after_a_crash_matches_a_straight_run(run_dir, tmp_path):
    straight, _ = _judge(run_dir, tmp_path / "straight")

    # Calls: trial 1 = 2 (applicability, adherence), trial 2 = 1, trial 3's
    # applicability = the fourth; the fifth (trial 3's first adherence call)
    # crashes, so trial 3 writes nothing and the resume owes only that trial.
    with pytest.raises(RuntimeError, match="scripted crash"):
        _judge(run_dir, tmp_path / "out",
               skill_use_judge_script(_applicable, crash_after=4))
    assert len(_rows(tmp_path / "out")) == 2

    calls = []
    resumed, _ = _judge(run_dir, tmp_path / "out",
                        skill_use_judge_script(_applicable, calls=calls))

    assert sorted(calls) == ["adherence", "adherence", "applicability"]
    assert resumed == straight
    assert _rows(tmp_path / "out") == _rows(tmp_path / "straight")


def test_a_finished_analysis_is_not_judged_again(run_dir, tmp_path):
    _judge(run_dir, tmp_path / "out")
    calls = []

    again, _ = _judge(run_dir, tmp_path / "out",
                      skill_use_judge_script(_applicable, calls=calls))

    assert calls == []
    assert again["judged_trials"] == 3


def test_the_run_directory_is_not_touched(run_dir, tmp_path):
    before = snapshot(run_dir)

    _judge(run_dir, tmp_path / "out")

    assert snapshot(run_dir) == before


def test_the_output_may_not_be_inside_the_run(run_dir):
    with pytest.raises(ConfigError, match="inside the run directory"):
        _judge(run_dir, run_dir / "analysis")

    assert not (run_dir / "analysis").exists()


def test_a_trial_whose_reply_breaks_the_contract_is_reported_not_written(
        run_dir, tmp_path):
    good = skill_use_judge_script(_applicable)

    def script(system, user):
        card = user.split("THE TRIAL FROM")[0]
        if "opened one skill card" in user and "gamma matters" in card:
            return '```json\n{"steps": [{"number": 1, "status": "done", ' \
                   '"evidence": ""}]}\n```'
        return good(system, user)

    summary, _ = _judge(run_dir, tmp_path / "out", script)

    assert summary["expected_trials"] == 3 and summary["judged_trials"] == 2
    assert ["skillbench:t2:weak:" in item["sheet_id"]
            for item in summary["failed_trials"]] == [True]
    assert "'status'" in summary["failed_trials"][0]["error"]
    assert summary["failed_trials"][0]["error_class"] == "ModelOutputError"
    assert {row["task"] for row in _rows(tmp_path / "out")} == {
        "skillbench:t1"}


def test_a_refused_request_fails_only_its_trial(run_dir, tmp_path):
    good = skill_use_judge_script(_applicable)

    def script(system, user):
        if "INSTR skillbench:t2" in user:
            raise ProviderRefusalError("request too large")
        return good(system, user)

    summary, _ = _judge(run_dir, tmp_path / "out", script)

    assert summary["judged_trials"] == 2
    assert [(item["error_class"], "too large" in item["error"])
            for item in summary["failed_trials"]] == [
        ("ProviderRefusalError", True)]


def test_an_infrastructure_error_leaves_the_trial_to_a_resume(
        run_dir, tmp_path):
    good = skill_use_judge_script(_applicable)
    flaky = {"on": True}

    def script(system, user):
        if flaky["on"] and "INSTR skillbench:t2" in user:
            raise InfrastructureError("endpoint down")
        return good(system, user)

    with pytest.raises(InfrastructureError, match="resume"):
        _judge(run_dir, tmp_path / "out", script)
    # The other trials were judged and written despite it.
    assert len(_rows(tmp_path / "out")) == 2

    flaky["on"] = False
    summary, _ = _judge(run_dir, tmp_path / "out", script)

    assert summary["judged_trials"] == 3


def test_a_resume_with_another_judge_model_is_refused(run_dir, tmp_path):
    _judge(run_dir, tmp_path / "out")

    with pytest.raises(ConfigError, match="fresh output directory"):
        _judge(run_dir, tmp_path / "out", name="another-judge")
    assert json.loads(
        (tmp_path / "out" / PINNED_NAME).read_text())["judge_model"] \
        == "judge-model"


def test_the_bank_is_the_latest_artifact_before_the_phase(run_dir, tmp_path):
    later = make_bank()
    later[0].content = later[0].content.replace("do step 1", "LATER step 1")
    write_learn_phase(run_dir, "p04:learn", later)

    summary, handle = _judge(run_dir, tmp_path / "out")

    assert summary["bank_phase_label"] == "p02:learn"
    assert not any("LATER" in call["user"] for call in handle.client.calls)


def test_a_manifest_the_bank_no_longer_carries_is_refused(tmp_path):
    run = tmp_path / "run"
    write_test_phase(run, make_bank(), TRIALS)
    revised = make_bank()
    revised[0].version = 2
    write_learn_phase(run, "p02:learn", revised)

    with pytest.raises(ConfigError, match="version 1"):
        _judge(run, tmp_path / "out")


def test_a_phase_that_delivered_no_card_has_nothing_to_judge(tmp_path):
    run = tmp_path / "run"
    bank = make_bank(status="pending")
    write_learn_phase(run, "p02:learn", bank)
    write_test_phase(run, bank, TRIALS)

    with pytest.raises(ConfigError, match="nothing to judge"):
        _judge(run, tmp_path / "out")


def test_a_phase_with_no_answer_sheets_is_refused(run_dir, tmp_path):
    shutil.rmtree(run_dir / "phases" / "p03_test" / "answer_sheets")

    with pytest.raises(ConfigError, match="no trial-level answer sheets"):
        _judge(run_dir, tmp_path / "out")


def test_the_benchmark_supplies_the_view_rules_and_instruction_by_default(
        run_dir, tmp_path, monkeypatch):
    (run_dir / "manifest.json").write_text(
        json.dumps({"config": {"benchmark": "skillbench"}}))
    monkeypatch.setattr(
        judge_skills, "resolve_plugin",
        lambda name: SimpleNamespace(
            view_rules=ViewRules(max_obs_chars=30, head_keep=5,
                                 tail_keep=5)))
    monkeypatch.setattr(
        judge_skills.attribution, "task_instruction_for",
        lambda benchmark, ref: f"PLUGIN[{benchmark}] {ref}")
    handle = make_fake_handle("judge", skill_use_judge_script(_applicable))

    judge_skills.judge_phase(run_dir, LABEL, tmp_path / "out", handle)

    prompt = handle.client.calls[0]["user"]
    assert "PLUGIN[skillbench] skillbench:t1" in prompt
    assert "characters truncated" in prompt
