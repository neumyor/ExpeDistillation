"""Unit tests for scripts/run/build_paper_configs.py.

The script renders the paper's experiment matrix (docs/decisions/
paper_experiments_code_20261030.md) into configs/experiments/. These tests
pin three things: every checked-in file loads through the real loader and
its methods resolve exactly as stage 0 resolves them; every arm differs
from its reference in exactly the intended keys (the guard that an
ablation changes one factor); and the generator is idempotent and reports
stale files.
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
LOCAL = BUILDER.LOCAL_BENCHMARKS
BARE_STEMS = ("e1_bare_q", "e1_bare_d", "e1_bare_x")
#: The full matrix pins one frozen bare control per student model; those
#: files exist only after the three bare runs finish. Until then the module
#: skips (the bare configs themselves are tested in
#: test_build_bare_configs.py), so the transition cannot be mistaken for a
#: passing suite.
BARE_READY = all(
    (BUILDER.SEED_DIR / name).is_file()
    for name in BUILDER.BARE_CONTROL_FILES.values())
pytestmark = pytest.mark.skipif(
    not BARE_READY,
    reason="frozen bare control files not built yet: run e1_bare_q/d/x")
NAMES = sorted(BUILDER.generate(
    CONFIG_DIR, BUILDER.DEFAULT_D_MODELS, BUILDER.DEFAULT_Q_MODEL,
    stems=None if BARE_READY else BARE_STEMS))
BASELINES = ("skillopt", "evoskill", "trace2skill", "gepa", "expel",
             "agentbrew")
D_MODEL = BUILDER.DEFAULT_D_MODELS
Q_MODEL = BUILDER.DEFAULT_Q_MODEL

#: What separates a test-only config from the full method.
TEST_ONLY_PHASES = ["phases.baseline", "phases.exam", "phases.learn",
                    "phases.learn_add", "phases.learn_trial"]


def _name(bench, stem):
    if bench == "alfworld":
        return f"alfworld_split_paper_v3_{stem}.yaml"
    return f"{bench}_split_paper_{stem}.yaml"


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
    return sorted(BUILDER.diff_paths(a, b, leaves=("curate",)))


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
    a = {"phase": {"curate": {"name": "none", "token_budget": None}}}
    b = {"phase": {"curate": {"name": "agentbrew", "rounds": 3}}}
    assert BUILDER.diff_paths(a, b) == [
        "phase.curate.name", "phase.curate.token_budget",
        "phase.curate.rounds"]
    assert BUILDER.diff_paths(a, b, leaves=("curate",)) == ["phase.curate"]


# ---------------------------------------------------------------------------
# The matrix and the loader
# ---------------------------------------------------------------------------

def test_the_matrix_has_the_expected_files():
    by_prefix = {}
    for name in NAMES:
        bench, _, rest = name.partition("_split_paper_")
        rest = rest.removeprefix("v3_")
        by_prefix.setdefault(rest.split("_")[0], []).append((bench, name))
    assert {key: len(value) for key, value in by_prefix.items()} == {
        "e1": 43, "e2": 13, "e3": 6, "e4": 18, "e5": 9, "e6": 3}
    assert len(NAMES) == 92
    # TB bare is the external campaign control, so only the local
    # benchmarks get a bare config.
    assert _name("tb21", "e1_bare") not in NAMES
    assert _name("searchqa", "e1_bare") in NAMES
    # ALFWorld freezes one bare run per student model; each is its own arm.
    for student in ("q", "d", "x"):
        assert _name("alfworld", f"e1_bare_{student}") in NAMES
    assert _name("alfworld", "e1_bare") not in NAMES
    # The X = qwen3.8-27b teacher arm is ALFWorld only.
    assert _name("alfworld", "e2_x2q") in NAMES
    for bench in ("searchqa", "tb21"):
        assert _name(bench, "e2_x2q") not in NAMES


def test_explicit_cache_models_are_a_per_model_option(files):
    # 2026-11-04: the generic client reads cache_mode from model_options;
    # the generator must be able to opt a model in without touching any
    # other model's options.
    base = files[_name("alfworld", "e1_ours")]
    assert "cache_mode" not in base["model_options"].get(
        BUILDER.DEFAULT_Q_MODEL, {})
    generated = BUILDER.generate(
        CONFIG_DIR, BUILDER._parse_d_models([]), BUILDER.DEFAULT_Q_MODEL,
        stems=["e1_ours"],
        explicit_cache_models=[BUILDER.DEFAULT_Q_MODEL],
        cache_prefix_tokens=1500)
    cfg = generated[_name("alfworld", "e1_ours")].config
    assert cfg["model_options"][BUILDER.DEFAULT_Q_MODEL] == {
        "cache_mode": "explicit", "cache_prefix_tokens": 1500}
    assert cfg["model_options"][BUILDER.DEFAULT_D_MODELS["alfworld"]] == {
        "reasoning_effort": "max"}


@pytest.mark.parametrize("name", NAMES)
def test_every_config_loads_and_resolves_its_methods(name):
    config = load_config(str(CONFIG_DIR / name))
    # Stage 0's own resolution: every family of every phase, the
    # trial_level input-budget check and the external control file.
    _resolve_phase_methods(config)
    # The ALFWorld arms hand the writer the reader's execution context
    # (2026-11-03); the other benchmarks pass no profile.
    if name.startswith("alfworld_"):
        assert config.use_weak_profile is True
        assert "unified protocol" in config.weak_profile
        assert "Opened skill cards" in config.weak_profile
    else:
        assert config.use_weak_profile is False
        assert config.weak_profile == ""
    assert config.mode == "split"
    assert (config.n_attempts_strong, config.n_attempts_weak) == (3, 3)
    assert config.sequence[-1] == "test"
    assert config.phases["test"].role == "test"
    split = config.split
    assert not set(split["train"]) & set(split["test"])
    assert not set(split.get("reserved", [])) & set(split["test"])


@pytest.mark.parametrize("student,key", [("d", "dpsk"), ("q", "qwen")])
def test_tb_control_block_parses_for_the_students_column(student, key):
    config = load_config(str(CONFIG_DIR / _name("tb21", "e2_d2" + student)))
    external = config.phases["test"].compare.external
    assert external.model_key == key
    source = yaml.safe_load(
        (CONFIG_DIR / BUILDER.SOURCE_CONFIGS["tb21"]).read_text("utf-8"))
    pinned = source["phases"]["test"]["compare"]["external"]
    assert external.sha256 == pinned["sha256"]
    assert Path(external.path).name == Path(pinned["path"]).name


def test_no_config_carries_the_dropped_retune_switch():
    for name in NAMES:
        assert "retune" not in (CONFIG_DIR / name).read_text("utf-8")


# ---------------------------------------------------------------------------
# What is shared by every arm of a benchmark
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bench", BENCHMARKS)
def test_split_and_global_keys_are_identical_across_arms(bench, files):
    source = yaml.safe_load(
        (CONFIG_DIR / BUILDER.SOURCE_CONFIGS[bench]).read_text("utf-8"))
    mine = {name: cfg for name, cfg in files.items()
            if name.startswith(bench + "_split_paper_")}
    ours = mine[_name(bench, "e1_ours")]
    assert ours["split"] == source["split"]
    assert ours["models"] == source["models"]
    assert ours["model_options"] == source["model_options"]
    assert ours["rules"] == source["rules"]
    if bench == "tb21":
        assert ours["tasks"] == source["tasks"]
        assert ours["token_budgets"] == source["token_budgets"]
        counts = {key: len(value) for key, value in ours["split"].items()}
        assert counts == {"train": 20, "test": 39, "reserved": 30}
    else:
        assert "tasks" not in ours
    for name, cfg in mine.items():
        changed = {path.split(".")[0] for path in _diff(cfg, ours)}
        # The coverage arms (e5, 2026-11-04) are the one arm family that
        # also overrides the material rules; everything else stays global.
        assert changed <= {"sequence", "phases", "models", "token_budgets",
                           "rules"}, \
            f"{name} changes a global key: {sorted(changed)}"


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_trial_level_budget_is_declared_for_author_and_rewriter(bench, files):
    for stem in ("e1_ours", "e3_k1", "e4_a1_body", "e4_staged9"):
        roles = files[_name(bench, stem)]["token_budgets"]["roles"]
        for role in ("author", "rewriter"):
            assert roles[role]["max_input_tokens"] >= 524288


# ---------------------------------------------------------------------------
# Experiment 1: the main table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bench", BENCHMARKS)
def test_the_reference_method_is_exam_plus_trial_level(bench, files):
    ours = files[_name(bench, "e1_ours")]
    assert ours["sequence"] == [
        "baseline", "learn_add", "exam", "learn", "exam", "learn_trial",
        "test"]
    phases = ours["phases"]
    assert phases["learn_add"]["review"]["decision"] == "skill_level"
    assert phases["learn_add"]["review"]["K"] == 5
    for name in ("learn", "learn_trial"):
        assert phases[name]["review"]["decision"] == "trial_level"
    for name in ("learn_add", "learn", "learn_trial"):
        assert phases[name]["author"]["name"] == "cluster_by_trigger"
        assert phases[name]["curate"]["name"] == "agentbrew"
        for switch in ("revise_scope", "accept_new", "new_skills"):
            assert switch not in phases[name]["review"]
    assert phases["exam"]["delivery"] == {
        "description_mode": "summary_then_when_to_use",
        "name": "exam_folder"}
    assert phases["exam"]["execution"] == {
        "bare_control": False, "leak_audit": False, "max_skill_reads": 3,
        "name": "exam"}
    assert phases["test"]["delivery"] == {
        "description_mode": "summary_then_when_to_use", "name": "folder"}
    assert phases["test"]["execution"] == {"name": "real_folder"}


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_baseline_configs_run_the_test_phase_only(bench, files):
    ours = files[_name(bench, "e1_ours")]
    for baseline in BASELINES:
        folder = files[_name(bench, f"e1_{baseline}_folder")]
        whole = files[_name(bench, f"e1_{baseline}_whole")]
        assert _diff(folder, ours) == sorted(["sequence"] + TEST_ONLY_PHASES)
        assert folder["sequence"] == ["test"]
        assert folder["phases"]["test"] == ours["phases"]["test"]
        assert _diff(whole, ours) == sorted(
            ["sequence", "phases.test.delivery.description_mode",
             "phases.test.delivery.name", "phases.test.delivery.verbatim",
             "phases.test.execution.name"]
            + TEST_ONLY_PHASES)
        assert whole["sequence"] == ["test"]
        assert whole["phases"]["test"]["delivery"] == {
            "name": "whole_bank", "verbatim": True}
        assert whole["phases"]["test"]["execution"] == {"name": "real"}
        assert (whole["phases"]["test"]["compare"]
                == ours["phases"]["test"]["compare"])


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
    assert test["compare"] == {"against": "fresh_bare", "rule": None}
    # `delivery: none` + fresh_bare collides on unit ids at runtime.
    assert test["delivery"] == {"name": "whole_bank"}


@pytest.mark.parametrize("student", ["q", "d", "x"])
def test_each_alfworld_bare_arm_runs_an_empty_bank_once(student, files):
    bare = files[_name("alfworld", f"e1_bare_{student}")]
    assert bare["sequence"] == ["test"]
    assert bare["phases"]["test"]["delivery"] == {"name": "whole_bank"}
    assert bare["phases"]["test"]["execution"] == {"name": "real"}
    # The control arm is a zero-execution placeholder: the bare arm IS the
    # measurement and must not re-run the same bare model.
    compare = bare["phases"]["test"]["compare"]
    assert compare["against"] == "external_campaign"


def test_the_control_follows_the_benchmark_and_the_student(files):
    for stem in ("e1_ours", "e1_gepa_folder", "e1_gepa_whole"):
        compare = files[_name("tb21", stem)]["phases"]["test"]["compare"]
        assert compare["against"] == "external_campaign"
        assert compare["external"]["model_key"] == "qwen"
        local = files[_name("searchqa", stem)]["phases"]["test"]["compare"]
        assert local == {"against": "fresh_bare", "rule": None}
    # ALFWorld: every with-bank arm pins its student's frozen bare file.
    for stem, student in (("e1_ours", "q"), ("e2_d2d", "d"),
                          ("e2_q2d", "d"), ("e2_d2q", "q")):
        external = files[_name("alfworld", stem)]["phases"]["test"][
            "compare"]["external"]
        assert external["model_key"] == {
            "q": Q_MODEL, "d": D_MODEL["alfworld"]}[student]
        assert external["path"] == \
            f"../seed/{BUILDER.BARE_CONTROL_FILES[student]}"
        assert len(external["sha256"]) == 64


# ---------------------------------------------------------------------------
# Experiment 2: teacher x student
# ---------------------------------------------------------------------------

TEACHER_ROLES = ["author", "curator", "executor_strong", "rewriter",
                 "segmenter"]
STUDENT_ROLES = ["answerer", "executor_weak"]
FIXED_ROLES = ["auditor", "examiner", "judge"]


def _expected_cell_diff(roles, *, control_paths):
    paths = [f"models.{role}" for role in roles]
    paths += [f"token_budgets.roles.{role}.max_input_tokens"
              for role in roles]
    paths += list(control_paths)
    return sorted(paths)


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_teacher_student_cells_differ_in_models_budgets_and_column(
        bench, files):
    d2d = files[_name(bench, "e2_d2d")]
    assert _diff(files[_name(bench, "e2_d2q")], d2d) == \
        _expected_cell_diff(
            STUDENT_ROLES, control_paths=_control_paths(bench, "e2_d2q",
                                                        "e2_d2d"))
    assert _diff(files[_name(bench, "e2_q2d")], d2d) == \
        _expected_cell_diff(
            TEACHER_ROLES, control_paths=_control_paths(bench, "e2_q2d",
                                                        "e2_d2d"))
    assert _diff(files[_name(bench, "e2_q2q")], d2d) == \
        _expected_cell_diff(
            TEACHER_ROLES + STUDENT_ROLES,
            control_paths=_control_paths(bench, "e2_q2q", "e2_d2d"))


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_teacher_student_cells_assign_the_roles(bench, files):
    d, q = D_MODEL[bench], Q_MODEL
    for teacher, student in (("d", "d"), ("d", "q"), ("q", "d"),
                             ("q", "q")):
        models = files[_name(bench, f"e2_{teacher}2{student}")]["models"]
        letters = {"d": d, "q": q}
        for role in TEACHER_ROLES:
            assert models[role] == letters[teacher], (teacher, student, role)
        for role in STUDENT_ROLES:
            assert models[role] == letters[student], (teacher, student, role)
        for role in FIXED_ROLES:
            assert models[role] == d, (teacher, student, role)


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_the_input_budget_follows_the_model(bench, files):
    for stem in ("e2_d2d", "e2_d2q", "e2_q2d", "e2_q2q"):
        cfg = files[_name(bench, stem)]
        for role, entry in cfg["token_budgets"]["roles"].items():
            expected = 262144 if cfg["models"][role] == Q_MODEL else 524288
            assert entry["max_input_tokens"] == expected, (stem, role)


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_teacher_student_method_is_add_then_exam_then_test(bench, files):
    ours = files[_name(bench, "e1_ours")]
    cell = files[_name(bench, "e2_d2q")]
    # D -> Q is the reference teacher and student: models and budgets
    # match e1_ours, only the method shape differs.
    assert _diff(cell, ours) == sorted([
        "sequence", "phases.learn", "phases.learn_trial",
        "phases.learn_add.curate", "phases.learn_add.review.write_lessons",
        "phases.learn_add.review.accept_new"])
    assert cell["sequence"] == ["baseline", "learn_add", "exam", "test"]
    review = cell["phases"]["learn_add"]["review"]
    assert review["write_lessons"] is False
    assert review["accept_new"] is True
    assert cell["phases"]["learn_add"]["curate"] == {
        "name": "none", "token_budget": None}
    assert cell["phases"]["learn_add"]["author"]["name"] == \
        "cluster_by_trigger"
    assert cell["phases"]["exam"] == ours["phases"]["exam"]
    assert cell["phases"]["test"] == ours["phases"]["test"]


# ---------------------------------------------------------------------------
# Experiments 3 and 4: one factor each
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bench", BENCHMARKS)
def test_k1_changes_only_the_add_phase_segment_count(bench, files):
    ours = files[_name(bench, "e1_ours")]
    k1 = files[_name(bench, "e3_k1")]
    assert _diff(k1, ours) == ["phases.learn_add.review.K"]
    assert k1["phases"]["learn_add"]["review"]["K"] == 1
    assert ours["phases"]["learn_add"]["review"]["K"] == 5


@pytest.mark.parametrize("bench", BENCHMARKS)
@pytest.mark.parametrize("k", (5, 3, 1))
def test_coverage_arm_switches_only_the_material_rules(bench, k, files):
    # 2026-11-04: the coverage ablation keeps the task gate
    # (any_weak_failed) and changes only which trajectories and segments
    # reach the writer; K is the same knob as e3.
    ours = files[_name(bench, "e1_ours")]
    cov = files[_name(bench, f"e5_allcover_k{k}")]
    expected = ["rules.segment_select", "rules.trajectory_select"]
    if k != 5:
        expected.append("phases.learn_add.review.K")
    assert _diff(cov, ours) == sorted(expected)
    assert cov["rules"] == {
        **ours["rules"],
        "segment_select": "all",
        "trajectory_select": "all",
    }
    assert cov["rules"]["task_select"] == "any_weak_failed"
    assert cov["phases"]["learn_add"]["review"]["K"] == k


def test_playbook_arm_adds_only_the_delivery_playbook(files):
    # 2026-11-04 P0: the reference method plus the global playbook on the
    # folder/exam_folder deliveries. Rules and K stay the reference's.
    ours = files[_name("alfworld", "e1_ours")]
    p0 = files[_name("alfworld", "e6_playbook_k5")]
    assert _diff(p0, ours) == sorted([
        "phases.exam.delivery.playbook_file",
        "phases.exam.delivery.playbook_sha256",
        "phases.test.delivery.playbook_file",
        "phases.test.delivery.playbook_sha256",
    ])
    for phase in ("exam", "test"):
        delivery = p0["phases"][phase]["delivery"]
        assert delivery["playbook_file"] == BUILDER.PLAYBOOK_FILE
        assert len(delivery["playbook_sha256"]) == 64
    assert p0["rules"] == ours["rules"]


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_a0_stops_after_the_add_phase(bench, files):
    ours = files[_name(bench, "e1_ours")]
    a0 = files[_name(bench, "e4_a0")]
    assert _diff(a0, ours) == sorted([
        "sequence", "phases.exam", "phases.learn", "phases.learn_trial",
        "phases.learn_add.curate", "phases.learn_add.review.accept_new"])
    assert a0["sequence"] == ["baseline", "learn_add", "test"]
    assert a0["phases"]["learn_add"]["review"]["accept_new"] is True
    assert a0["phases"]["learn_add"]["curate"]["name"] == "none"
    # A0 keeps the method's K and lessons: only the later phases go.
    assert a0["phases"]["learn_add"]["review"]["K"] == 5
    assert a0["phases"]["learn_add"]["review"]["write_lessons"] is True


SCOPE_PATHS = ["phases.learn.review.revise_scope",
               "phases.learn_trial.review.revise_scope"]
NEW_SKILLS_PATHS = ["phases.learn.review.new_skills",
                    "phases.learn_trial.review.new_skills"]
COMPARE_PATHS = ["phases.test.compare.against",
                 "phases.test.compare.external"]


def _control_paths(bench, first, second):
    """The compare-block paths when two arms pin different controls.

    ALFWorld pins one frozen bare file per student model, so arms with the
    same student match (all e4 arms) and a student change moves the file
    (path, sha256 and model_key). TB's campaign file is shared, so only the
    column changes. SearchQA always uses a fresh in-run control.
    """
    if bench not in ("tb21", "alfworld"):
        return []
    students = {arm.stem: arm.student for arm in BUILDER.arms()}
    if students[first] == students[second]:
        return []
    if bench == "tb21":
        return ["phases.test.compare.external.model_key"]
    return ["phases.test.compare.external.model_key",
            "phases.test.compare.external.path",
            "phases.test.compare.external.sha256"]


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_a3_joint_differs_from_the_full_method_in_new_skills_only(
        bench, files):
    ours = files[_name(bench, "e1_ours")]
    a3 = files[_name(bench, "e4_a3_joint")]
    assert _diff(a3, ours) == sorted(
        NEW_SKILLS_PATHS + _control_paths(bench, "e4_a3_joint", "e1_ours"))
    for name in ("learn", "learn_trial"):
        review = a3["phases"][name]["review"]
        assert review["new_skills"] is False
        # The scope stays at its default (both): a3 spells no revise_scope.
        assert "revise_scope" not in review
    assert "new_skills" not in a3["phases"]["learn_add"]["review"]
    assert a3["sequence"] == ours["sequence"]


@pytest.mark.parametrize("bench", BENCHMARKS)
@pytest.mark.parametrize("stem,scope", [("e4_a1_body", "body"),
                                        ("e4_a2_trigger", "trigger")])
def test_single_scope_arms_differ_from_a3_in_the_revise_scope_only(
        bench, stem, scope, files):
    a3 = files[_name(bench, "e4_a3_joint")]
    ours = files[_name(bench, "e1_ours")]
    arm = files[_name(bench, stem)]
    assert _diff(arm, a3) == sorted(
        SCOPE_PATHS + _control_paths(bench, stem, "e4_a3_joint"))
    for name in ("learn", "learn_trial"):
        review = arm["phases"][name]["review"]
        assert review["revise_scope"] == scope
        assert review["new_skills"] is False
    assert "revise_scope" not in arm["phases"]["learn_add"]["review"]
    assert arm["sequence"] == ours["sequence"] == a3["sequence"]
    # Against the full method an arm changes both factors, nothing else
    # (plus the control block when only one of the two is a pooled arm).
    assert _diff(arm, ours) == sorted(
        SCOPE_PATHS + NEW_SKILLS_PATHS
        + _control_paths(bench, stem, "e1_ours"))


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_the_other_e4_arms_leave_new_skills_at_its_default(bench, files):
    for stem in ("e4_a0", "e4_joint9", "e4_staged9"):
        for phase in files[_name(bench, stem)]["phases"].values():
            if phase["kind"] == "learn":
                assert "new_skills" not in phase["review"], stem


@pytest.mark.parametrize("bench", BENCHMARKS)
def test_the_nine_phase_arms_share_a_sequence_and_differ_in_two_scopes(
        bench, files):
    ours = files[_name(bench, "e1_ours")]
    joint = files[_name(bench, "e4_joint9")]
    staged = files[_name(bench, "e4_staged9")]
    nine = ["baseline", "learn_add", "exam", "learn", "exam", "learn_trial",
            "exam", "learn_final", "test"]
    assert joint["sequence"] == staged["sequence"] == nine
    assert _diff(joint, ours) == sorted([
        "sequence", "phases.learn_final",
        "phases.learn.review.revise_scope",
        "phases.learn_trial.review.revise_scope"]
        + _control_paths(bench, "e4_joint9", "e1_ours"))
    assert _diff(staged, joint) == [
        "phases.learn.review.revise_scope",
        "phases.learn_trial.review.revise_scope"]
    scopes = {name: joint["phases"][name]["review"]["revise_scope"]
              for name in ("learn", "learn_trial", "learn_final")}
    assert scopes == {"learn": "both", "learn_trial": "both",
                      "learn_final": "both"}
    scopes = {name: staged["phases"][name]["review"]["revise_scope"]
              for name in ("learn", "learn_trial", "learn_final")}
    assert scopes == {"learn": "body", "learn_trial": "trigger",
                      "learn_final": "both"}
    # learn_final is the third trial_level definition: same body as
    # learn_trial, so P06 and P08 can carry different scopes.
    assert joint["phases"]["learn_final"] == joint["phases"]["learn_trial"]


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
        (tmp_path / _name("alfworld", "e2_q2d")).read_text("utf-8"))
    assert config["models"]["examiner"] == "my-d"
    assert config["models"]["author"] == "my-q"
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
    by_file = {line.split("|")[4].strip(): line for line in lines[2:]}
    assert sorted(by_file) == NAMES
    assert "reference" in by_file[_name("tb21", "e1_ours")]
    assert "phases.learn_add.review.K" in by_file[_name("tb21", "e3_k1")]


def test_the_index_names_the_reference_of_every_e4_arm(capsys):
    assert BUILDER.main(["--print-index"]) == 0
    rows = {}
    for line in capsys.readouterr().out.strip().splitlines()[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows[cells[3]] = {"reference": cells[4], "differs": cells[5]}
    expected = {"e4_a0": "e1_ours", "e4_a3_joint": "e1_ours",
                "e4_a1_body": "e4_a3_joint", "e4_a2_trigger": "e4_a3_joint",
                "e4_joint9": "e1_ours", "e4_staged9": "e4_joint9"}
    for bench in BENCHMARKS:
        for stem, reference in expected.items():
            assert rows[_name(bench, stem)]["reference"] == reference
        assert rows[_name(bench, "e4_a1_body")]["differs"] == \
            ", ".join(sorted(
                SCOPE_PATHS
                + _control_paths(bench, "e4_a1_body", "e4_a3_joint")))
        assert rows[_name(bench, "e4_a3_joint")]["differs"] == \
            ", ".join(sorted(
                NEW_SKILLS_PATHS
                + _control_paths(bench, "e4_a3_joint", "e1_ours")))
        assert rows[_name(bench, "e1_ours")] == {
            "reference": "-", "differs": "reference"}
