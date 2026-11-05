"""Unit tests for stages/remerge_bank.py (fake model, temp run dirs).

The tool re-runs a finished run's recorded merge groups under the current
MERGE prompt and reader profile and writes the result as a test-only run's
accepted bank. These tests pin: the product is replaced in place (same id,
same version), every other bank row is byte-identical, the profile reaches
the MERGE prompt, and the provenance record names the source phase.
"""

import copy
import json

import pytest

from expedistill.core.config import ExperimentConfig, phase_label
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import Store
from expedistill.llm import json_script, make_fake_handle
from expedistill.stages.remerge_bank import REMERGED_NAME, remerge_bank
from tests.helpers.skills import make_skill

_SEQUENCE = ["baseline", "learn", "test"]
_PHASES = {
    "baseline": {"kind": "execute", "role": "baseline", "tasks": "train",
                 "delivery": {"name": "none"},
                 "execution": {"name": "real",
                               "arms": ["executor_strong", "executor_weak"]}},
    "learn": {"kind": "learn",
              "review": {"name": "review", "K": 4},
              "author": {"name": "distill_diagnose"},
              "curate": {"name": "agentbrew", "token_budget": 4096,
                         "rounds": 3, "merge_check": "none"}},
    "test": {"kind": "execute", "role": "test", "tasks": "test",
             "delivery": {"name": "folder"},
             "execution": {"name": "real_folder"},
             "compare": {"against": "fresh_bare"}},
}


def _config() -> ExperimentConfig:
    return ExperimentConfig(
        benchmark="alfworld",
        mode="split",
        models={"executor_strong": "big-model", "executor_weak": "weak-7b"},
        weak_profile="the reader sees two steps",
        n_attempts_strong=1,
        n_attempts_weak=2,
        sequence=list(_SEQUENCE),
        phases=copy.deepcopy(_PHASES),
        split={"train": ["t1", "t2"], "test": ["t3"]},
        rules={"task_select": "all_valid_failed",
               "trajectory_select": "all"},
    )


def _make_source_run(tmp_path):
    run = tmp_path / "source"
    store = Store(run)
    config = _config()
    (run / "manifest.json").write_text(json.dumps({"config": config.to_dict()}))
    label = phase_label(_SEQUENCE.index("learn"), "learn")
    phase_dir = run / "phases" / label.replace(":", "_")
    phase_dir.mkdir(parents=True)

    left = make_skill("traj/t/left/seg00", content="left skill",
                      status="accepted", summary="left summary")
    right = make_skill("traj/t/right/seg00", content="right skill",
                       status="accepted", summary="right summary")
    other = make_skill("traj/t/other/seg00", content="other skill",
                       status="accepted", summary="other summary")
    store.save(left, "skills")
    store.save(right, "skills")
    store.save(other, "skills")

    product = make_skill(
        "bank/merge/0000", content="old merged text", status="accepted",
        summary="old merged summary", merged_from=[left.skill_id, right.skill_id])
    rows = [product.to_dict(), other.to_dict()]
    (phase_dir / "skill_bank.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n")
    (phase_dir / "curation_log.json").write_text(json.dumps({
        "merge_records": [{
            "mode": "dedup",
            "skill_id": product.skill_id,
            "sources": [left.skill_id, right.skill_id],
            "outcome": "kept",
        }],
    }))
    return run, product.skill_id, other.skill_id


def test_remerge_replaces_only_the_merge_product(tmp_path):
    source, product_id, other_id = _make_source_run(tmp_path)
    handle = make_fake_handle(script=json_script({
        "content": "new merged text",
        "summary": "new merged summary"}))

    summary = remerge_bank(
        source_run=source, run_dir=tmp_path / "target",
        weak_profile="the reader sees two steps", curator_handle=handle)

    assert summary["merges"] == 1
    assert summary["bank_size"] == 2
    assert len(handle.client.calls) == 1
    assert "the reader sees two steps" in handle.client.calls[0]["user"]
    assert "binding for every step" in handle.client.calls[0]["user"]

    rows = [json.loads(line) for line in
            (tmp_path / "target" / "skill_bank.jsonl").read_text().splitlines()]
    by_id = {row["skill_id"]: row for row in rows}
    assert by_id[product_id]["content"] == "new merged text"
    assert by_id[product_id]["summary"] == "new merged summary"
    assert by_id[product_id]["version"] == 1
    # Every other row is unchanged.
    source_rows = [json.loads(line) for line in
                   (source / "phases" / phase_label(
                       _SEQUENCE.index("learn"), "learn").replace(":", "_")
                    / "skill_bank.jsonl").read_text().splitlines()]
    assert by_id[other_id] == next(row for row in source_rows
                                   if row["skill_id"] == other_id)

    remerged = (tmp_path / "target" / REMERGED_NAME).read_text()
    assert "p02:learn" in remerged
    assert product_id in remerged
    # The skill object landed in the target store too.
    assert (tmp_path / "target" / "stage3_generation" / "skills").exists()


def test_remerge_refuses_a_target_that_already_holds_a_run(tmp_path):
    source, _product_id, _other_id = _make_source_run(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / "manifest.json").write_text("{}")
    handle = make_fake_handle(script=json_script({
        "content": "x", "summary": "y"}))
    with pytest.raises(ConfigError, match="not empty"):
        remerge_bank(source_run=source, run_dir=target,
                     weak_profile="", curator_handle=handle)
