"""Unit tests for scripts/run/build_paper_configs.py.

The script renders the paper's main table (experiment e1, docs/decisions/
evidence_driven_evolution_20261113.md section 6) into configs/experiments/.
These tests pin three things: every checked-in file loads through the real
loader and its methods resolve exactly as stage 0 resolves them; every arm
differs from its reference in exactly the intended keys; and the generator
is idempotent and reports stale files.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

from expedistill.core.config import load_config
from expedistill.stages.stage0_freeze import _resolve_phase_methods

_REPO = Path(__file__).resolve().parents[3]
CONFIG_DIR = _REPO / "configs" / "experiments"
_SPEC = importlib.util.spec_from_file_location(
    "build_paper_configs", _REPO / "scripts" / "run" / "build_paper_configs.py")
BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(BUILDER)

BENCHMARKS = BUILDER.BENCHMARKS
BARE_STEMS = ("e1_bare_q", "e1_bare_d", "e1_bare_x")
#: The with-bank ALFWorld arms pin the student's frozen bare control; those
#: files exist only after the bare runs finish. Until then the module skips
#: (the bare configs themselves are tested in test_build_bare_configs.py),
#: so the transition cannot be mistaken for a passing suite.
BARE_READY = all(
    (BUILDER.SEED_DIR / name).is_file()
    for name in BUILDER.BARE_CONTROL_FILES.values())
pytestmark = pytest.mark.skipif(
    not BARE_READY,
    reason="frozen bare control files not built yet: run e1_bare_q")
NAMES = sorted(BUILDER.generate(
    BUILDER.DEFAULT_D_MODELS, BUILDER.DEFAULT_Q_MODEL,
    stems=None if BARE_READY else BARE_STEMS))
BASELINES = ("skillopt", "evoskill", "trace2skill", "gepa", "expel",
             "agentbrew")
D_MODEL = BUILDER.DEFAULT_D_MODELS
Q_MODEL = BUILDER.DEFAULT_Q_MODEL

#: What separates a test-only config from the reference method.
TEST_ONLY_PHASES = ["phases.baseline", "phases.learn", "phases.practice"]
#: The six roles of the schema-9 role table.
ROLES = ["executor_strong", "executor_weak", "segmenter", "author",
         "rewriter", "judge"]


def _name(bench, stem):
    return f"{bench}_paper_{stem}.yaml"


@pytest.fixture(scope="module")
def files():
    """Every generated config as read from disk (what a run would load)."""
    out = {}
    for name in NAMES:
        path = CONFIG_DIR / name
        assert path.exists(), f"{name} is not generated; run the script"
        with open(path, "r", encoding="utf-8") as handle:
            out[name] = yaml.safe_load(handle)
    return out


def _diff(a, b):
    return sorted(BUILDER.diff_paths(a, b))


# ---------------------------------------------------------------------------
# The diff helper that every arm test leans on
# ---------------------------------------------------------------------------

def test_diff_paths_reports_values_and_one_sided_keys():
    a = {"x": 1, "p": {"q": 1, "r": 2}, "only_a": 1, "seq": [1, 2]}
    b = {"x": 1, "p": {"q": 9, "r": 2}, "only_b": 1, "seq": [1, 3]}
    assert sorted(BUILDER.diff_paths(a, b)) == [
        "only_a", "only_b", "p.q", "seq"]
    assert BUILDER.diff_paths(a, a) == []


def test_diff_paths_compares_a_leaf_key_whole():
    a = {"phase": {"author": {"name": "x", "k": 1}}}
    b = {"phase": {"author": {"name": "y", "j": 2}}}
    assert BUILDER.diff_paths(a, b) == [
        "phase.author.name", "phase.author.k", "phase.author.j"]
    assert BUILDER.diff_paths(a, b, leaves=("author",)) == ["phase.author"]


# ---------------------------------------------------------------------------
# The matrix and the loader
# ---------------------------------------------------------------------------

def test_the_matrix_has_the_expected_files():
    assert {name.partition("_paper_")[2].split("_")[0]
            for name in NAMES} == {"e1"}
    per_bench = {bench: sorted(name.partition("_paper_")[2][:-5]
                               for name in NAMES
                               if name.startswith(bench + "_paper_"))
                 for bench in BENCHMARKS}
    external = sorted(f"e1_{baseline}_{variant}" for baseline in BASELINES
                      for variant in ("folder", "whole"))
    assert per_bench["tb21"] == sorted(["e1_ours"] + external)
    assert per_bench["searchqa"] == sorted(["e1_ours", "e1_bare"] + external)
    assert per_bench["alfworld"] == sorted(
        ["e1_ours", "e1_ours_val", "e1_bare_q", "e1_bare_d", "e1_bare_x"]
        + external)
    assert len(NAMES) == 44


@pytest.mark.parametrize("name", NAMES)
def test_every_config_loads_and_resolves_its_methods(name):
    config = load_config(str(CONFIG_DIR / name))
    # Stage 0's own resolution: every family of every phase, the playbook
    # capability and the external control file.
    _resolve_phase_methods(config)
    assert config.schema_version == 9
    assert (config.n_attempts_strong, config.n_attempts_weak) == (3, 3)
    assert config.sequence[-1] == "test"
    assert config.phases["test"].role == "test"
    assert sorted(config.models) == sorted(ROLES)
    lists = {key: set(value) for key, value in config.split.items()}
    for first in lists:
        for second in lists:
            if first < second:
                assert not lists[first] & lists[second], (first, second)


def test_explicit_cache_models_are_a_per_model_option(files):
    base = files[_name("alfworld", "e1_ours")]
    assert "cache_mode" not in base["model_options"].get(Q_MODEL, {})
    generated = BUILDER.generate(
        BUILDER._parse_d_models([]), Q_MODEL, stems=["e1_ours"],
        explicit_cache_models=[Q_MODEL], cache_prefix_tokens=1500)
    cfg = generated[_name("alfworld", "e1_ours")].config
    assert cfg["model_options"][Q_MODEL] == {
        "cache_mode": "explicit", "cache_prefix_tokens": 1500}
    assert cfg["model_options"][D_MODEL["alfworld"]] == {
        "reasoning_effort": "max"}


# ---------------------------------------------------------------------------
# What is shared by every arm of a benchmark
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bench", BENCHMARKS)
def test_split_and_global_keys_are_identical_across_arms(bench, files):
    mine = {name: cfg for name, cfg in files.items()
            if name.startswith(bench + "_paper_")}
    ours = mine[_name(bench, "e1_ours")]
    assert ours["split"] == BUILDER.paper_splits.SPLITS[bench]
    if bench == "tb21":
        assert ours["tasks"] == BUILDER.paper_splits.TASKS["tb21"]
        counts = {key: len(value) for key, value in ours["split"].items()}
        assert counts == {"train": 20, "test": 39, "reserved": 30}
    else:
        assert "tasks" not in ours
    for name, cfg in mine.items():
        changed = {path.split(".")[0] for path in _diff(cfg, ours)}
        assert changed <= {"sequence", "phases", "models", "token_budgets"}, \
            f"{name} changes a global key: {sorted(changed)}"


def test_only_alfworld_has_a_val_list():
    splits = BUILDER.paper_splits.SPLITS
    assert "val" not in splits["tb21"] and "val" not in splits["searchqa"]
    val = splits["alfworld"]["val"]
    assert len(val) == len(set(val)) == 39
    assert all(task.startswith("json_2.1.1/valid_seen/") for task in val)
    assert not set(val) & set(splits["alfworld"]["train"])
    assert not set(val) & set(splits["alfworld"]["test"])

    def per_type(tasks):
        counts = {}
        for task in tasks:
            kind = task.split("/")[2].split("-")[0]
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    assert per_type(val) == per_type(splits["alfworld"]["train"])


# ---------------------------------------------------------------------------
# The reference method
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bench", BENCHMARKS)
def test_the_reference_method_is_the_seven_phase_sequence(bench, files):
    ours = files[_name(bench, "e1_ours")]
    assert ours["sequence"] == [
        "baseline", "learn", "practice", "learn", "practice", "learn",
        "test"]
    phases = ours["phases"]
    assert sorted(phases) == ["baseline", "learn", "practice", "test"]
    assert phases["learn"]["review"] == {
        "name": "review", "K": 5, "retry_limit": 2, "write_lessons": True}
    assert phases["learn"]["author"] == {
        "name": "subtask_cards", "typing_chunk_size": 100,
        "max_segments_per_card": 8, "max_chars_per_segment": 1200,
        "write_retry_limit": 2}
    practice = phases["practice"]
    assert practice["tasks"] == "train"
    assert practice["delivery"] == {
        "description_mode": "summary_then_when_to_use", "name": "folder"}
    assert practice["execution"] == {
        "attempts": {"tb21": 2, "alfworld": 3, "searchqa": 1}[bench],
        "name": "real_folder"}
    for name in ("practice", "test"):
        assert phases[name]["playbook"] == "evolved"
    for name in ("baseline", "practice"):
        assert "compare" not in phases[name]
    assert phases["test"]["delivery"] == {
        "description_mode": "summary_then_when_to_use", "name": "folder"}
    assert phases["test"]["execution"] == {"name": "real_folder"}


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_the_teacher_fills_every_role_but_the_weak_executor(bench, files):
    ours = files[_name(bench, "e1_ours")]
    assert ours["models"] == {
        role: Q_MODEL if role == "executor_weak" else D_MODEL[bench]
        for role in ROLES}
    for role, entry in ours["token_budgets"]["roles"].items():
        expected = 262144 if ours["models"][role] == Q_MODEL else 524288
        assert entry == {"max_output_tokens": 65536,
                         "max_input_tokens": expected}, role


def test_ours_val_differs_from_ours_in_the_practice_list_only(files):
    ours = files[_name("alfworld", "e1_ours")]
    val = files[_name("alfworld", "e1_ours_val")]
    assert _diff(val, ours) == ["phases.practice.tasks"]
    assert val["phases"]["practice"]["tasks"] == "val"
    for bench in ("tb21", "searchqa"):
        assert _name(bench, "e1_ours_val") not in NAMES


# ---------------------------------------------------------------------------
# The test-only arms
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bench", BENCHMARKS)
def test_baseline_configs_run_the_test_phase_only(bench, files):
    # A test-only arm has no learn phase that could evolve a playbook, so
    # its test phase is the reference test phase with the playbook off.
    ours = files[_name(bench, "e1_ours")]
    for baseline in BASELINES:
        folder = files[_name(bench, f"e1_{baseline}_folder")]
        whole = files[_name(bench, f"e1_{baseline}_whole")]
        assert _diff(folder, ours) == sorted(
            ["sequence", "phases.test.playbook"] + TEST_ONLY_PHASES)
        assert folder["sequence"] == ["test"]
        assert folder["phases"]["test"] == {
            key: value for key, value in ours["phases"]["test"].items()
            if key != "playbook"}
        assert _diff(whole, ours) == sorted(
            ["sequence", "phases.test.delivery.description_mode",
             "phases.test.delivery.name", "phases.test.delivery.verbatim",
             "phases.test.execution.name", "phases.test.playbook"]
            + TEST_ONLY_PHASES)
        assert whole["phases"]["test"]["delivery"] == {
            "name": "whole_bank", "verbatim": True}
        assert whole["phases"]["test"]["execution"] == {"name": "real"}


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_the_six_baselines_differ_only_by_file_name(bench, files):
    for variant in ("folder", "whole"):
        configs = [files[_name(bench, f"e1_{baseline}_{variant}")]
                   for baseline in BASELINES]
        assert all(cfg == configs[0] for cfg in configs)


def test_searchqa_bare_is_the_whole_bank_test_with_a_fresh_control(files):
    bare = files[_name("searchqa", "e1_bare")]
    whole = files[_name("searchqa", "e1_skillopt_whole")]
    # Bare differs from a baseline whole arm in the verbatim flag only: an
    # empty bank has nothing to flatten.
    assert _diff(bare, whole) == ["phases.test.delivery.verbatim"]
    test = bare["phases"]["test"]
    assert test["compare"] == {"against": "fresh_bare"}
    assert test["delivery"] == {"name": "whole_bank"}


def test_the_control_follows_the_benchmark_and_the_student(files):
    for stem in ("e1_ours", "e1_gepa_folder", "e1_gepa_whole"):
        compare = files[_name("tb21", stem)]["phases"]["test"]["compare"]
        assert compare["against"] == "external_campaign"
        assert compare["external"]["model_key"] == "qwen"
        local = files[_name("searchqa", stem)]["phases"]["test"]["compare"]
        assert local == {"against": "fresh_bare"}
    # ALFWorld: every with-bank arm pins student Q's frozen bare file,
    # never the pooled placeholder of the bare arms.
    for stem in ("e1_ours", "e1_ours_val", "e1_gepa_folder"):
        external = files[_name("alfworld", stem)]["phases"]["test"][
            "compare"]["external"]
        assert external["model_key"] == Q_MODEL
        assert external["path"] == "../seed/alfworld_bare_q_20261102.json"
        assert external != BUILDER.POOLED_CONTROL
        assert len(external["sha256"]) == 64


def test_method_arms_show_the_evolved_playbook_and_test_only_arms_do_not(
        files):
    for name, cfg in files.items():
        has_learn = any(spec.get("kind") == "learn"
                        for spec in cfg["phases"].values())
        for spec in cfg["phases"].values():
            if spec.get("role") in ("practice", "test"):
                if has_learn:
                    assert spec.get("playbook") == "evolved", name
                else:
                    assert "playbook" not in spec, name


# ---------------------------------------------------------------------------
# The generator itself
# ---------------------------------------------------------------------------

def test_generation_is_idempotent_and_matches_the_checked_in_files(
        tmp_path, capsys):
    assert BUILDER.main(["--out-dir", str(tmp_path)]) == 0
    first = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert BUILDER.main(["--out-dir", str(tmp_path)]) == 0
    second = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert first == second
    assert sorted(first) == NAMES
    assert BUILDER.main(["--check", "--out-dir", str(tmp_path)]) == 0
    for name in NAMES:
        assert first[name] == (CONFIG_DIR / name).read_bytes(), name
    assert BUILDER.main(["--check"]) == 0
    assert "0 problem(s)" in capsys.readouterr().out


def test_check_reports_missing_differing_and_stale_files(tmp_path, capsys):
    BUILDER.main(["--out-dir", str(tmp_path)])
    capsys.readouterr()
    (tmp_path / _name("tb21", "e9_retired")).write_text("x\n")
    (tmp_path / NAMES[0]).unlink()
    victim = tmp_path / NAMES[1]
    victim.write_text(victim.read_text() + "# hand edit\n")
    assert BUILDER.main(["--check", "--out-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert f"stale: {_name('tb21', 'e9_retired')}" in out
    assert f"missing: {NAMES[0]}" in out
    assert f"differs: {NAMES[1]}" in out


def test_writing_warns_about_a_stale_file_without_deleting_it(
        tmp_path, capsys):
    stale = tmp_path / _name("alfworld", "e9_retired")
    stale.write_text("x\n")
    assert BUILDER.main(["--out-dir", str(tmp_path)]) == 0
    assert "stale file not produced any more" in capsys.readouterr().err
    assert stale.exists()


def test_model_ids_are_parameters(tmp_path):
    assert BUILDER.main([
        "--out-dir", str(tmp_path), "--d-model", "alfworld=my-d",
        "--q-model", "my-q"]) == 0
    config = yaml.safe_load(
        (tmp_path / _name("alfworld", "e1_ours")).read_text("utf-8"))
    assert config["models"]["author"] == "my-d"
    assert config["models"]["executor_weak"] == "my-q"
    assert config["model_options"] == {"my-d": {"reasoning_effort": "max"}}
    other = yaml.safe_load(
        (tmp_path / _name("searchqa", "e1_ours")).read_text("utf-8"))
    assert other["models"]["executor_strong"] == D_MODEL["searchqa"]
    assert other["models"]["executor_weak"] == "my-q"


def test_a_malformed_model_argument_is_refused(tmp_path):
    assert BUILDER.main(["--out-dir", str(tmp_path),
                         "--d-model", "nonsense"]) == 2
    assert not list(tmp_path.iterdir())


def test_the_index_lists_every_file_with_its_computed_difference(capsys):
    assert BUILDER.main(["--print-index"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == len(NAMES) + 2
    rows = {}
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows[cells[3]] = {"reference": cells[4], "differs": cells[5]}
    assert sorted(rows) == NAMES
    assert rows[_name("tb21", "e1_ours")] == {
        "reference": "-", "differs": "reference"}
    assert rows[_name("alfworld", "e1_ours_val")] == {
        "reference": "e1_ours", "differs": "phases.practice.tasks"}
