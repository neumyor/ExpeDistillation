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
from expedistill.infra.reliability import ConfigError
from expedistill.stages.fork_run import _check_same_prefix
from expedistill.stages.stage0_freeze import _resolve_phase_methods

_REPO = Path(__file__).resolve().parents[3]
CONFIG_DIR = _REPO / "configs" / "experiments"
_SPEC = importlib.util.spec_from_file_location(
    "build_paper_configs", _REPO / "scripts" / "run" / "build_paper_configs.py")
BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(BUILDER)

BENCHMARKS = BUILDER.BENCHMARKS
BASELINE_BENCHMARKS = BUILDER.BASELINE_BENCHMARKS
NAMES = sorted(BUILDER.generate(
    BUILDER.DEFAULT_D_MODELS, BUILDER.DEFAULT_Q_MODEL))
BASELINES = ("skillopt", "evoskill", "trace2skill", "gepa", "expel",
             "agentbrew")
D_MODEL = BUILDER.DEFAULT_D_MODELS
Q_MODEL = BUILDER.DEFAULT_Q_MODEL
STUDENT_MODEL = {
    "q": Q_MODEL, "d": D_MODEL["alfworld"], "x": BUILDER.DEFAULT_X_MODEL}

#: What separates a test-only config from the reference method.
TEST_ONLY_PHASES = ["phases.baseline", "phases.learn", "phases.practice"]
#: The six roles of the schema-9 role table.
ROLES = ["executor_strong", "executor_weak", "segmenter", "author",
         "rewriter", "judge"]


def _name(bench, stem):
    return f"{bench}_paper_{stem}.yaml"


#: The benchmarks the ablation arms run on, and the one key the reference
#: file does not carry: TB's ablation arms read a card first in the final
#: test only (the reference file keeps its frozen definition).
ABLATION_BENCHES = ("alfworld", "tb21", "swebench")


def _read_rule_diff(bench):
    return ["phases.test.delivery.read_rule"] if bench == "tb21" else []


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

#: The ALFWorld ablation arms and their final tests without the playbook.
ABLATIONS = ("e1_no_skills", "e2_written_once", "e3_body_only",
             "e4_trigger_only", "e5_prefix_authoring", "e6_no_profile",
             "e7_k1")
NOPB = {"e8_ours_nopb": "e1_ours",
        **{f"e8_{stem.split('_', 1)[1]}_nopb": stem
           for stem in ABLATIONS[1:]}}


def test_the_matrix_has_the_expected_files():
    assert {name.partition("_paper_")[2].split("_")[0]
            for name in NAMES} == {"e1", "e2", "e3", "e4", "e5", "e6", "e7",
                                   "e8"}
    per_bench = {bench: sorted(name.partition("_paper_")[2][:-5]
                               for name in NAMES
                               if name.startswith(bench + "_paper_"))
                 for bench in BENCHMARKS}
    external = sorted(f"e1_{baseline}_{variant}" for baseline in BASELINES
                      for variant in ("folder", "whole"))
    assert per_bench["tb21"] == sorted(
        ["e1_ours"] + external + list(ABLATIONS) + list(NOPB))
    assert per_bench["searchqa"] == sorted(["e1_ours", "e1_bare"] + external)
    assert per_bench["alfworld"] == sorted(
        ["e1_ours", "e1_ours_val", "e1_bare_q", "e1_bare_d", "e1_bare_x"]
        + external + list(ABLATIONS) + list(NOPB))
    assert per_bench["swebench"] == sorted(
        ["e1_ours"] + list(ABLATIONS) + list(NOPB))
    assert len(NAMES) == 45 + 3 * (len(ABLATIONS) + len(NOPB))


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
    if bench == "swebench":
        counts = {key: len(value) for key, value in ours["split"].items()}
        assert counts == {"train": 30, "test": 30}
    for name, cfg in mine.items():
        changed = {path.split(".")[0] for path in _diff(cfg, ours)}
        assert changed <= {"sequence", "phases", "models", "token_budgets"}, \
            f"{name} changes a global key: {sorted(changed)}"


def test_only_alfworld_has_a_val_list():
    splits = BUILDER.paper_splits.SPLITS
    for bench in ("tb21", "searchqa", "swebench"):
        assert "val" not in splits[bench]
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
    assert phases["practice"]["tasks"] == "train"
    for name in ("practice", "test"):
        assert phases[name]["playbook"] == "evolved"
    for name in ("baseline", "practice"):
        assert "compare" not in phases[name]


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_the_teacher_fills_every_role_but_the_weak_executor(bench, files):
    ours = files[_name(bench, "e1_ours")]
    assert ours["models"] == {
        role: Q_MODEL if role == "executor_weak" else D_MODEL[bench]
        for role in ROLES}
    for role, entry in ours["token_budgets"]["roles"].items():
        expected = 262144 if ours["models"][role] == Q_MODEL else 524288
        if role.startswith("executor_") and bench == "swebench":
            # SWE-bench's harness caps every executor at 131072.
            expected = 131072
        assert entry == {"max_output_tokens": 65536,
                         "max_input_tokens": expected}, role


def test_only_swebench_and_the_tb_ablation_tests_must_read_a_card(files):
    # SWE-bench: practice and test. TB: only the final test of an ablation
    # arm (the reference file keeps its frozen definition).
    for name, cfg in files.items():
        stem = name.partition("_paper_")[2][:-5]
        tb_ablation = (name.startswith("tb21_")
                       and stem in set(ABLATIONS) | set(NOPB))
        for phase_name, phase in cfg["phases"].items():
            delivery = phase.get("delivery") or {}
            if delivery.get("name") != "folder":
                continue
            must_read = (name.startswith("swebench_")
                         or (tb_ablation and phase["role"] == "test"))
            expected = "mandatory" if must_read else None
            assert delivery.get("read_rule") == expected, (name, phase_name)


def test_ours_val_differs_from_ours_in_the_practice_list_only(files):
    ours = files[_name("alfworld", "e1_ours")]
    val = files[_name("alfworld", "e1_ours_val")]
    assert _diff(val, ours) == ["phases.practice.tasks"]
    assert val["phases"]["practice"]["tasks"] == "val"
    for bench in ("tb21", "searchqa"):
        assert _name(bench, "e1_ours_val") not in NAMES


# ---------------------------------------------------------------------------
# The ablation arms (docs/decisions/paper_ablation_switches_20261119.md)
# ---------------------------------------------------------------------------

_SWITCH_KEYS = ("cards", "revise_scope", "evolve_profile", "trigger_view")


#: The arms whose learn definitions carry a switch key; no other config
#: does, so every other config freezes the manifest it always did.
SWITCHED = {"e1_no_skills", "e3_body_only", "e4_trigger_only",
            "e5_prefix_authoring", "e6_no_profile",
            "e8_body_only_nopb", "e8_trigger_only_nopb",
            "e8_prefix_authoring_nopb", "e8_no_profile_nopb"}


def test_only_the_switched_arms_carry_a_switch_key(files):
    for name, cfg in files.items():
        carried = [key for spec in cfg["phases"].values()
                   if spec["kind"] == "learn"
                   for family in ("review", "author") for key in spec[family]
                   if key in _SWITCH_KEYS]
        assert bool(carried) == (name.partition("_paper_")[2][:-5]
                                 in SWITCHED), name
    # The reference's learn definition has no switch key at all.
    ours = files[_name("alfworld", "e1_ours")]["phases"]["learn"]
    assert set(ours["review"]) == {"name", "K", "retry_limit",
                                   "write_lessons"}
    assert set(ours["author"]) == {"name", "typing_chunk_size",
                                   "max_segments_per_card",
                                   "max_chars_per_segment",
                                   "write_retry_limit"}


@pytest.mark.parametrize("stem, differs", [
    ("e1_no_skills", ["phases.learn.review.cards"]),
    ("e2_written_once", ["phases.practice", "sequence"]),
    ("e3_body_only", ["phases.learn_scoped", "sequence"]),
    ("e4_trigger_only", ["phases.learn_scoped", "sequence"]),
    ("e5_prefix_authoring", ["phases.learn.author.trigger_view",
                             "phases.practice", "sequence"]),
    ("e6_no_profile", ["phases.learn.review.evolve_profile"]),
    ("e7_k1", ["phases.learn.review.K"]),
])
@pytest.mark.parametrize("bench", ABLATION_BENCHES)
def test_an_ablation_differs_from_the_reference_in_its_own_keys(
        bench, stem, differs, files):
    ours = files[_name(bench, "e1_ours")]
    assert _diff(files[_name(bench, stem)], ours) == sorted(
        differs + _read_rule_diff(bench))


@pytest.mark.parametrize("bench", ABLATION_BENCHES)
def test_the_switch_values(bench, files):
    def learn(stem, name="learn"):
        return files[_name(bench, stem)]["phases"][name]

    assert learn("e1_no_skills")["review"]["cards"] is False
    assert learn("e6_no_profile")["review"]["evolve_profile"] is False
    assert learn("e7_k1")["review"]["K"] == 1
    assert learn("e5_prefix_authoring")["author"]["trigger_view"] == "prefix"
    assert learn("e3_body_only", "learn_scoped")["review"][
        "revise_scope"] == "content_only"
    assert learn("e4_trigger_only", "learn_scoped")["review"][
        "revise_scope"] == "trigger_only"


@pytest.mark.parametrize("bench", ABLATION_BENCHES)
@pytest.mark.parametrize("stem", ["e3_body_only", "e4_trigger_only"])
def test_the_scoped_arms_keep_the_reference_learn_for_the_first_round(
        bench, stem, files):
    ours = files[_name(bench, "e1_ours")]
    arm = files[_name(bench, stem)]
    assert arm["phases"]["learn"] == ours["phases"]["learn"]
    assert arm["sequence"] == [
        "baseline", "learn", "practice", "learn_scoped", "practice",
        "learn_scoped", "test"]
    scoped = {key: value for key, value in
              arm["phases"]["learn_scoped"].items()}
    scoped["review"] = {key: value for key, value in scoped["review"].items()
                        if key != "revise_scope"}
    assert scoped == ours["phases"]["learn"]


@pytest.mark.parametrize("bench", ABLATION_BENCHES)
def test_the_short_arms_run_baseline_learn_test(bench, files):
    for stem in ("e2_written_once", "e5_prefix_authoring"):
        cfg = files[_name(bench, stem)]
        assert cfg["sequence"] == ["baseline", "learn", "test"]
        assert cfg["phases"]["test"]["playbook"] == "evolved"
        assert sorted(cfg["phases"]) == ["baseline", "learn", "test"]


@pytest.mark.parametrize("bench", ABLATION_BENCHES)
@pytest.mark.parametrize("stem, base", sorted(NOPB.items()))
def test_a_no_playbook_arm_is_its_base_with_the_final_playbook_off(
        bench, stem, base, files):
    arm = files[_name(bench, stem)]
    reference = files[_name(bench, base)]
    expected = ["phases.test.playbook"]
    if base == "e1_ours":
        expected += _read_rule_diff(bench)
    assert _diff(arm, reference) == sorted(expected)
    assert "playbook" not in arm["phases"]["test"]
    assert reference["phases"]["test"]["playbook"] == "evolved"
    assert arm["sequence"] == reference["sequence"]


def test_the_ablation_arms_skip_searchqa():
    for stem in list(ABLATIONS) + list(NOPB):
        assert _name("searchqa", stem) not in NAMES
        for bench in ABLATION_BENCHES:
            assert _name(bench, stem) in NAMES


def test_the_final_tests_of_the_ablation_arms_read_a_card_first_on_tb(files):
    # TB's reference file keeps the frozen definition (no read rule); the
    # ablation arms follow the mandatory-read fork of the reference run.
    assert "read_rule" not in files[_name("tb21", "e1_ours")][
        "phases"]["test"]["delivery"]
    for stem in list(ABLATIONS) + list(NOPB):
        cfg = files[_name("tb21", stem)]
        assert cfg["phases"]["test"]["delivery"]["read_rule"] == "mandatory"
        # the practice phases stay as the reference run's
        if "practice" in cfg["phases"]:
            assert "read_rule" not in cfg["phases"]["practice"]["delivery"]
    # ALFWorld never gets a read rule
    for stem in list(ABLATIONS) + list(NOPB):
        cfg = files[_name("alfworld", stem)]
        assert "read_rule" not in cfg["phases"]["test"]["delivery"]


# How many phases each arm's run copies from which run with ``fork-run``:
# (source arm, target arm, copied phase count).
FORKS = [
    ("e1_ours", "e1_no_skills", 1),
    ("e1_ours", "e2_written_once", 2),
    ("e1_ours", "e3_body_only", 3),
    ("e1_ours", "e4_trigger_only", 3),
    ("e1_ours", "e5_prefix_authoring", 1),
    ("e1_ours", "e6_no_profile", 1),
    ("e1_ours", "e7_k1", 1),
    # The no-playbook final tests copy everything through the last learn
    # phase of the arm's own run and rerun only the test.
    ("e1_ours", "e8_ours_nopb", 6),
    ("e2_written_once", "e8_written_once_nopb", 2),
    ("e3_body_only", "e8_body_only_nopb", 6),
    ("e4_trigger_only", "e8_trigger_only_nopb", 6),
    ("e5_prefix_authoring", "e8_prefix_authoring_nopb", 2),
    ("e6_no_profile", "e8_no_profile_nopb", 6),
    ("e7_k1", "e8_k1_nopb", 6),
]


def _config_of(stem, bench="alfworld"):
    return load_config(str(CONFIG_DIR / _name(bench, stem)))


@pytest.mark.parametrize("bench", ABLATION_BENCHES)
@pytest.mark.parametrize("source, target, count", FORKS)
def test_fork_run_accepts_every_planned_prefix(bench, source, target, count):
    # The same check ``fork-run`` makes before it copies anything: the
    # first ``count`` phases are defined the same way and everything else
    # that acts on them is equal.
    _check_same_prefix(_config_of(source, bench), _config_of(target, bench),
                       count)


@pytest.mark.parametrize("source, target, count", [
    # A learn phase that differs is not copied.
    ("e1_ours", "e1_no_skills", 2),
    ("e1_ours", "e6_no_profile", 2),
    ("e1_ours", "e7_k1", 2),
    ("e1_ours", "e5_prefix_authoring", 2),
    # The scoped learn phase starts at the fourth phase.
    ("e1_ours", "e3_body_only", 4),
    ("e1_ours", "e4_trigger_only", 4),
    # The short arms have a different third phase.
    ("e1_ours", "e2_written_once", 3),
    # The no-playbook test is the last phase and differs.
    ("e1_ours", "e8_ours_nopb", 7),
])
@pytest.mark.parametrize("bench", ABLATION_BENCHES)
def test_fork_run_refuses_a_prefix_that_would_copy_a_changed_phase(
        bench, source, target, count):
    with pytest.raises(ConfigError):
        _check_same_prefix(_config_of(source, bench),
                           _config_of(target, bench), count)


# ---------------------------------------------------------------------------
# The test-only arms
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bench", BASELINE_BENCHMARKS)
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


@pytest.mark.parametrize("bench", BASELINE_BENCHMARKS)
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


@pytest.mark.parametrize("student", ["q", "d", "x"])
def test_a_bare_arm_is_one_bare_run_with_a_zero_execution_control(
        student, files):
    config = files[_name("alfworld", f"e1_bare_{student}")]
    assert config["sequence"] == ["test"]
    assert len(config["split"]["test"]) == 134
    assert (config["n_attempts_strong"], config["n_attempts_weak"]) == (3, 3)
    assert config["models"]["executor_weak"] == STUDENT_MODEL[student]
    test = config["phases"]["test"]
    assert test["delivery"] == {"name": "whole_bank"}
    assert test["execution"] == {"name": "real"}
    # The run IS the measurement; the pinned placeholder keeps the control
    # arm from repeating the same bare model.
    assert test["compare"]["against"] == "external_campaign"
    assert test["compare"]["external"] == BUILDER.POOLED_CONTROL


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
    # SWE-bench: the bare Qwen test run of benchmark/swebench-eval.
    compare = files[_name("swebench", "e1_ours")]["phases"]["test"][
        "compare"]
    assert compare == {
        "against": "external_campaign",
        "external": {"model_key": "qwen",
                     "path": BUILDER.paper_splits.SWEBENCH_EXTERNAL_PATH,
                     "sha256": BUILDER.paper_splits.SWEBENCH_EXTERNAL_SHA256}}


def test_method_arms_show_the_evolved_playbook_and_test_only_arms_do_not(
        files):
    for name, cfg in files.items():
        has_learn = any(spec.get("kind") == "learn"
                        for spec in cfg["phases"].values())
        without = "_nopb." in name
        for spec in cfg["phases"].values():
            if spec.get("role") in ("practice", "test"):
                if has_learn and not (without and spec["role"] == "test"):
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
