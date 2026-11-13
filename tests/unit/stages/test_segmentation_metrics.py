"""Unit tests for stages/segmentation_metrics.py.

The metric definitions are pure functions over stored artifacts, so the
tests build the smallest artifacts that exercise each definition: anchors
that resolve and do not, active and discarded cards, trials whose reads
come before/after the first environment action.
"""

from pathlib import Path

import pytest

from expedistill.core.models import Subtask
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import Store
from expedistill.stages.segmentation_metrics import (
    _parse_arm,
    anchor_distribution,
    mid_task_open_metrics,
    read_facts,
    render,
    run,
    skill_anchor,
)


def _subtask(run_dir: Path, subtask_id: str, start: int, end: int) -> None:
    Store(run_dir).save(Subtask(
        subtask_id=subtask_id,
        phase_label="p02:learn",
        task_id="task-1",
        trajectory_id="traj/p01_baseline/task-1/strong/00",
        source="strong",
        trajectory_success=True,
        segment_index=0,
        step_range=(start, end),
        label="segment",
        description="segment",
        success=True,
    ), "subtasks")


def test_skill_anchor_is_the_earliest_source_start(tmp_path):
    _subtask(tmp_path, "traj/t/strong/00/seg00", 0, 6)
    _subtask(tmp_path, "traj/t/strong/00/seg02", 9, 14)
    store = Store(tmp_path)
    skill = {"source_subtasks": ["traj/t/strong/00/seg02",
                                 "traj/t/strong/00/seg00"]}
    assert skill_anchor(skill, store) == 0
    mid = {"source_subtasks": ["traj/t/strong/00/seg02"]}
    assert skill_anchor(mid, store) == 9


def test_a_card_without_sources_has_no_anchor(tmp_path):
    store = Store(tmp_path)
    # An imported card names no segment.
    assert skill_anchor({"subtask_type": "", "source_subtasks": []},
                        store) is None
    assert skill_anchor({"source_subtasks": ["missing"]}, store) is None


def test_anchor_distribution_counts_unknown_separately(tmp_path):
    _subtask(tmp_path, "traj/a/strong/00/seg00", 0, 3)
    _subtask(tmp_path, "traj/b/strong/00/seg02", 7, 9)
    store = Store(tmp_path)
    result = anchor_distribution([
        {"status": "active", "source_subtasks": ["traj/a/strong/00/seg00"]},
        {"status": "active", "source_subtasks": ["traj/b/strong/00/seg02"]},
        {"status": "active",
         "source_subtasks": ["traj/ghost/strong/00/seg00"]},
    ], store)
    assert result["skills"] == 3
    assert result["discarded"] == 0
    assert result["known"] == 2
    assert result["unknown"] == 1
    assert result["at_task_start"] == 1
    assert result["mid_task"] == 1
    assert result["mid_task_share"] == 0.5
    assert result["anchor_steps"] == [0, 7]


def test_anchor_distribution_counts_only_the_active_cards(tmp_path):
    _subtask(tmp_path, "traj/a/strong/00/seg00", 0, 3)
    _subtask(tmp_path, "traj/b/strong/00/seg02", 7, 9)
    store = Store(tmp_path)
    result = anchor_distribution([
        {"status": "active", "source_subtasks": ["traj/b/strong/00/seg02"]},
        {"status": "discarded",
         "source_subtasks": ["traj/a/strong/00/seg00"]},
    ], store)
    assert (result["skills"], result["discarded"]) == (1, 1)
    assert result["anchor_steps"] == [7]
    assert result["at_task_start"] == 0


def test_read_facts_separates_start_reads_from_mid_task_reads():
    start_only = [
        {"action": "ReadSkill[a/SKILL.md]", "observation": "card"},
        {"action": "go to desk 1", "observation": "you arrive"},
        {"action": "look", "observation": "you see"},
    ]
    assert read_facts(start_only) == {
        "opened": True, "reads": 1, "mid_task_open": False,
        "first_env_step": 1}

    mid = [
        {"action": "go to desk 1", "observation": "you arrive"},
        {"action": "ReadSkill[a/SKILL.md]", "observation": "card"},
        {"action": "take mug 1 from desk 1", "observation": "you take"},
    ]
    assert read_facts(mid)["mid_task_open"] is True

    no_read = [{"action": "look", "observation": "you see"}]
    assert read_facts(no_read) == {
        "opened": False, "reads": 0, "mid_task_open": False,
        "first_env_step": 0}

    only_reads = [{"action": "ReadSkill[a/SKILL.md]", "observation": "card"}]
    assert read_facts(only_reads)["mid_task_open"] is False


def test_mid_task_open_metrics_are_trial_rates():
    trials = [
        [{"action": "go to desk 1"}, {"action": "ReadSkill[a/SKILL.md]"}],
        [{"action": "ReadSkill[a/SKILL.md]"}, {"action": "look"}],
        [{"action": "look"}],
    ]
    metrics = mid_task_open_metrics(trials)
    assert metrics == {"trials": 3, "opened": 2, "mid_task_open": 1,
                       "open_rate": pytest.approx(2 / 3),
                       "mid_task_open_rate": pytest.approx(1 / 3)}


def test_render_has_one_column_per_arm():
    arms = [{
        "label": "K=5", "run_dir": "/r5", "test_phase": "p07:test",
        "control_source": "external_campaign", "tasks": 134, "trials": 402,
        "avg_at_n": 0.5, "pass_at_n": 0.7,
        "infra_missing": {}, "bank_phases": ["p02:learn", "p06:learn"],
        "initial_bank": {"phase": "p02:learn", "skills": 10, "discarded": 0,
                         "known": 10,
                         "unknown": 0, "at_task_start": 2, "mid_task": 8,
                         "mid_task_share": 0.8, "anchor_steps": [0, 3]},
        "final_bank": {"phase": "p06:learn", "skills": 10, "discarded": 2,
                       "known": 10,
                       "unknown": 0, "at_task_start": 2, "mid_task": 8,
                       "mid_task_share": 0.8, "anchor_steps": [0, 3]},
        "reads": {"trials": 402, "opened": 100, "mid_task_open": 50,
                  "open_rate": 100 / 402, "mid_task_open_rate": 50 / 402},
    }]
    table, payload = render(arms)
    assert "| 指标 | K=5 |" in table
    assert "| avg@3 | 50.0% |" in table
    assert "| 任务中途打开率 | 12.4% |" in table
    assert "| 最终库·已删除 | 2 |" in table
    assert payload["arms"][0]["label"] == "K=5"


def test_parse_arm_splits_at_the_last_equals():
    assert _parse_arm("K=5=runs/x") == ("K=5", Path("runs/x"))
    assert _parse_arm("K=1=runs/y") == ("K=1", Path("runs/y"))
    with pytest.raises(ConfigError, match="LABEL=RUN_DIR"):
        _parse_arm("no-separator")


def test_run_refuses_to_write_inside_a_run(tmp_path):
    with pytest.raises(ConfigError, match="inside the run"):
        run(["K=5=" + str(tmp_path)], out_md=tmp_path / "table.md")
