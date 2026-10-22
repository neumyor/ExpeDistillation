"""Unit tests for scripts/run/build_local_live_config.py.

The script renders the TB21 live-run method for a local benchmark. These
tests pin the generated shape (schema v5, sequence, phases, split), the
split extraction's count and duplicate checks, and that the checked-in
configs load through the real loader.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

from expedistill.core.config import load_config

_REPO = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "build_local_live_config",
    _REPO / "scripts" / "run" / "build_local_live_config.py")
BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(BUILDER)


def _write_split(root: Path, dirname: str, counts, items) -> None:
    split_dir = root / dirname
    split_dir.mkdir(parents=True)
    (split_dir / "split_manifest.json").write_text(
        json.dumps({"counts": counts}), encoding="utf-8")
    for split, rows in items.items():
        (split_dir / split).mkdir()
        (split_dir / split / "items.json").write_text(
            json.dumps(rows), encoding="utf-8")


def _searchqa_manifest(root: Path) -> None:
    _write_split(
        root, "searchqa_id_split", {"train": 2, "test": 1},
        {"train": [{"id": "q1"}, {"id": "q2"}],
         "test": [{"id": "q3"}]})


def _alfworld_manifest(root: Path) -> None:
    _write_split(
        root, "alfworld_path_split", {"train": 1, "test": 1},
        {"train": [{"gamefile": "json_2.1.1/train/a/game.tw-pddl"}],
         "test": [{"gamefile": "json_2.1.1/valid_unseen/b/game.tw-pddl"}]})


def test_extract_ids_reads_both_benchmarks(tmp_path):
    _searchqa_manifest(tmp_path)
    _alfworld_manifest(tmp_path)

    searchqa = BUILDER.extract_ids(tmp_path, "searchqa")
    assert searchqa == {"train": ["q1", "q2"], "test": ["q3"]}
    alfworld = BUILDER.extract_ids(tmp_path, "alfworld")
    assert alfworld == {
        "train": ["json_2.1.1/train/a/game.tw-pddl"],
        "test": ["json_2.1.1/valid_unseen/b/game.tw-pddl"],
    }


def test_count_mismatch_is_fatal(tmp_path):
    _write_split(
        tmp_path, "searchqa_id_split", {"train": 3, "test": 1},
        {"train": [{"id": "q1"}, {"id": "q2"}],
         "test": [{"id": "q3"}]})
    with pytest.raises(SystemExit, match="carries 2 items"):
        BUILDER.extract_ids(tmp_path, "searchqa")


def test_duplicate_ids_are_fatal(tmp_path):
    _write_split(
        tmp_path, "searchqa_id_split", {"train": 2, "test": 1},
        {"train": [{"id": "q1"}, {"id": "q1"}],
         "test": [{"id": "q3"}]})
    with pytest.raises(SystemExit, match="duplicate ids"):
        BUILDER.extract_ids(tmp_path, "searchqa")


def test_render_config_matches_the_live_run_method(tmp_path):
    _searchqa_manifest(tmp_path)
    ids = BUILDER.extract_ids(tmp_path, "searchqa")
    config = yaml.safe_load(BUILDER.render_config("searchqa", ids))

    assert config["schema_version"] == 5
    assert config["benchmark"] == "searchqa"
    assert config["mode"] == "split"
    assert config["sequence"] == [
        "baseline", "learn", "live", "learn", "live", "learn", "test"]
    assert config["rules"] == {
        "segment_select": "strong_success_weak_failed",
        "task_select": "any_weak_failed",
        "trajectory_select": "strong1_weak1",
    }
    assert config["models"]["executor_strong"] == "deepseek-v4-flash-0731-tencent"
    assert config["models"]["executor_weak"] == "qwen3.6-flash-distill"
    assert config["n_attempts_strong"] == 3
    assert config["n_attempts_weak"] == 3
    assert config["use_weak_profile"] is False

    phases = config["phases"]
    assert phases["baseline"]["execution"]["arms"] == [
        "executor_strong", "executor_weak"]
    assert phases["learn"]["evidence"] == {
        "name": "segment", "K": 5, "retry_limit": 2,
        "write_lessons": True}
    assert phases["learn"]["curate"]["rounds"] == 3
    assert phases["learn"]["curate"]["grouping_input"] == "full"
    assert phases["learn"]["curate"]["similarity_input"] == "summary"
    assert phases["live"]["delivery"] == {"name": "group_by_task"}
    assert phases["live"]["execution"]["n_attempts"] == 3
    assert phases["live"]["compare"] == {
        "against": "baseline", "rule": "strict_rate_gain"}
    # Local benchmarks have no file-system action: the test phase injects
    # the whole accepted bank (curation keeps it small) and compares
    # against a fresh bare run.
    assert phases["test"]["delivery"] == {"name": "whole_bank"}
    assert phases["test"]["execution"] == {"name": "real"}
    assert phases["test"]["compare"] == {
        "against": "fresh_bare", "rule": None}
    assert config["split"] == {"train": ["q1", "q2"], "test": ["q3"]}


def test_render_is_deterministic(tmp_path):
    _searchqa_manifest(tmp_path)
    ids = BUILDER.extract_ids(tmp_path, "searchqa")
    assert BUILDER.render_config("searchqa", ids) \
        == BUILDER.render_config("searchqa", ids)


def test_generated_config_loads_through_the_real_loader(tmp_path):
    _searchqa_manifest(tmp_path)
    ids = BUILDER.extract_ids(tmp_path, "searchqa")
    path = tmp_path / "searchqa_live.yaml"
    path.write_text(BUILDER.render_config("searchqa", ids), encoding="utf-8")

    config = load_config(str(path))
    assert config.schema_version == 5
    assert config.benchmark == "searchqa"
    assert config.sequence == [
        "baseline", "learn", "live", "learn", "live", "learn", "test"]


def test_check_mode_detects_drift(tmp_path, monkeypatch, capsys):
    _searchqa_manifest(tmp_path)
    out = tmp_path / "cfg.yaml"
    monkeypatch.setattr(sys, "argv", [
        "build_local_live_config", "--benchmark", "searchqa",
        "--skillopt-data", str(tmp_path), "--out", str(out),
        "--no-verify"])
    BUILDER.main()

    monkeypatch.setattr(sys, "argv", [
        "build_local_live_config", "--benchmark", "searchqa",
        "--skillopt-data", str(tmp_path), "--out", str(out),
        "--check", "--no-verify"])
    BUILDER.main()  # succeeds on the file just written

    out.write_text(out.read_text(encoding="utf-8") + "# drift\n",
                   encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "build_local_live_config", "--benchmark", "searchqa",
        "--skillopt-data", str(tmp_path), "--out", str(out),
        "--check", "--no-verify"])
    with pytest.raises(SystemExit, match="does not byte-match"):
        BUILDER.main()


def test_checked_in_configs_load():
    for name in ("searchqa_split_live_run_20261020.yaml",
                 "alfworld_split_live_run_20261020.yaml"):
        config = load_config(
            str(_REPO / "configs" / "experiments" / name))
        assert config.schema_version == 5
        assert config.sequence == [
            "baseline", "learn", "live", "learn", "live", "learn", "test"]
        assert config.use_weak_profile is False
