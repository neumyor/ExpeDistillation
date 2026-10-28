"""R9/R8 (W2-C): the frozen schema-v4 parsing products in core.config.

Pins the phase vocabulary (the five R8 roles, shared with the decision
layer's words), the ``pNN:<name>`` label derivation (the frozen
numbering in core.config: freeze is p00, sequence entry i
is p{i+1:02d}), the parsed phase specs' shape, the load-time checklist
of ``parse_phases`` (plan R9 item 4, every check a loud ConfigError),
the hand-off contract types the phase drivers receive and return, and
the 7 checked-in v3 configs converting through ``upgrade_v3_config``
into shapes the checklist accepts. Pure functions only (AGENTS.md
section 8): no YAML file is loaded from disk, no store is touched.
"""

import copy
from pathlib import Path

import pytest
import yaml

from expedistill.composites.decision import (
    PHASE_BASELINE,
    PHASE_EXAM,
    PHASE_KINDS,
    PHASE_LEARN,
    PHASE_LIVE,
    PHASE_TEST,
)
from expedistill.core.config import (
    DEFAULT_INVALID_REDISPATCH_LIMIT,
    EXECUTE_PHASE_ROLES,
    FRESH_BARE,
    FREEZE_LABEL,
    PHASE_ROLE_BASELINE,
    PHASE_ROLE_EXAM,
    PHASE_ROLE_LEARN,
    PHASE_ROLE_LIVE,
    PHASE_ROLE_TEST,
    CompareSpec,
    ConfigError,
    ExecutePhaseSpec,
    LearnPhaseSpec,
    PhaseInstance,
    PhaseResult,
    parse_phases,
    phase_label,
    phase_spec_to_raw,
    upgrade_v3_config,
    upgrade_v4_config,
    upgrade_v5_config,
    upgrade_v6_config,
)

#: The repository root (the 7 v3 configs live under configs/experiments/).
_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_DIR = _ROOT / "configs" / "experiments"


# ---------------------------------------------------------------------------
# Fixtures: the two canonical converted sequences
# ---------------------------------------------------------------------------

def _v3_raw(*, acceptance="exam", **overrides):
    """A minimal but complete v3 config mapping (the slot spellings the
    golden configs use)."""
    raw = {
        "benchmark": "tb",
        "mode": "split",
        "models": {"executor_strong": "strong-model",
                   "executor_weak": "weak-model"},
        "n_attempts_strong": 1,
        "n_attempts_weak": 2 if acceptance == "live_run" else 1,
        "schema_version": 3,
        "methods": {
            "segmentation": {"K": 5, "retry_limit": 2},
            "curation": {"name": "agentbrew", "token_budget": 4096,
                         "rounds": 3},
        },
    }
    if acceptance == "live_run":
        raw["methods"]["acceptance"] = {
            "name": "live_run", "n_attempts": 2,
            "improvement": "strict_rate_gain",
        }
        raw["methods"]["delivery"] = {"name": "whole_bank"}
    else:
        raw["methods"]["delivery"] = {"name": "retrieval", "top_k": 1,
                                      "max_categories": 1, "min_score": 0.02}
    raw.update(overrides)
    return raw


def _current(raw):
    return upgrade_v6_config(upgrade_v5_config(upgrade_v4_config(raw)))


@pytest.fixture()
def exam_v4():
    return _current(upgrade_v3_config(_v3_raw()))


@pytest.fixture()
def live_v4():
    return _current(upgrade_v3_config(_v3_raw(acceptance="live_run")))


def _parse(v4):
    version = v4.get("schema_version")
    if version == 4:
        v6 = upgrade_v6_config(upgrade_v5_config(upgrade_v4_config(v4)))
    elif version == 5:
        v6 = upgrade_v6_config(upgrade_v5_config(v4))
    elif version == 6:
        v6 = upgrade_v6_config(v4)
    else:
        v6 = v4
    return parse_phases(
        v6["phases"], v6["sequence"],
        n_attempts_weak=v6.get("n_attempts_weak", 1))


# ---------------------------------------------------------------------------
# The vocabulary (R8) and the label derivation
# ---------------------------------------------------------------------------

class TestVocabulary:
    def test_the_five_roles_are_the_decision_layers_words(self):
        # R8: the config declares the phase role with the SAME words the
        # decision entry receives as current_kind / control_kind.
        assert PHASE_ROLE_BASELINE == PHASE_BASELINE
        assert PHASE_ROLE_EXAM == PHASE_EXAM
        assert PHASE_ROLE_LIVE == PHASE_LIVE
        assert PHASE_ROLE_TEST == PHASE_TEST
        assert PHASE_ROLE_LEARN == PHASE_LEARN
        assert EXECUTE_PHASE_ROLES == tuple(sorted(PHASE_KINDS
                                                   - {PHASE_LEARN}))

    def test_the_label_shape_is_what_phase_bank_and_decision_accept(self):
        # The label must satisfy composites.phase_bank's frozen regex
        # (exactly one colon right after the pNN head).
        from expedistill.composites.phase_bank import _PHASE_LABEL_RE
        label = phase_label(0, "baseline")
        assert label == "p01:baseline"
        assert _PHASE_LABEL_RE.fullmatch(label)
        assert FREEZE_LABEL == "p00:freeze"
        assert _PHASE_LABEL_RE.fullmatch(FREEZE_LABEL)
        assert phase_label(11, "a_late.phase") == "p12:a_late.phase"


class TestParsedShapes:
    def test_execute_phase_fields(self, exam_v4):
        specs = _parse(exam_v4)
        d = specs["baseline"]
        assert isinstance(d, ExecutePhaseSpec)
        assert d.kind == "execute"
        assert d.role == PHASE_ROLE_BASELINE
        assert d.tasks == "train"
        assert d.delivery == {"name": "none"}
        assert d.execution == {"name": "real",
                               "arms": ["executor_strong",
                                        "executor_weak"]}
        assert d.compare is None and d.acceptance is None
        a = specs["exam"]
        assert a.role == PHASE_ROLE_EXAM
        assert a.execution == {
            "name": "exam", "max_skill_reads": 3,
            "bare_control": True, "leak_audit": True}
        c = specs["test"]
        assert c.role == PHASE_ROLE_TEST
        assert c.tasks == "test"
        assert c.compare == CompareSpec(against="fresh_bare")

    def test_learn_phase_fields(self, exam_v4):
        specs = _parse(exam_v4)
        learn = specs["learn"]
        assert isinstance(learn, LearnPhaseSpec)
        assert learn.kind == "learn"
        assert learn.role == PHASE_ROLE_LEARN
        assert learn.review["name"] == "review"
        assert learn.review["decision"] == "task_level"
        assert learn.review["K"] == 5
        assert learn.author == {"name": "distill_diagnose"}
        assert learn.curate["name"] == "agentbrew"
        assert learn.curate["merge_check"] == "exam"

    def test_learn_phase_decision_strategy_parses(self, exam_v4):
        raw = copy.deepcopy(exam_v4)
        raw["phases"]["learn"]["review"] = {
            "name": "review", "decision": "skill_level"}
        learn = _parse(raw)["learn"]
        assert learn.review["decision"] == "skill_level"

    def test_learn_phase_attribution_windows_parse(self, exam_v4):
        raw = copy.deepcopy(exam_v4)
        raw["phases"]["learn"]["review"] = {
            "name": "review", "before_steps": 3, "after_steps": 4}
        learn = _parse(raw)["learn"]
        assert learn.review["before_steps"] == 3
        assert learn.review["after_steps"] == 4
        assert phase_spec_to_raw(learn)["review"] == learn.review

    def test_live_sequence_shape(self, live_v4):
        assert live_v4["sequence"] == ["baseline", "learn", "live", "learn", "live", "learn", "test"]
        specs = _parse(live_v4)
        b = specs["live"]
        assert b.role == PHASE_ROLE_LIVE
        assert b.execution == {"name": "real", "n_attempts": 2,
                               "max_injection_tokens": None}
        assert b.compare == CompareSpec(against="baseline",
                                         rule="strict_rate_gain")
        # live_run's merges are not proven on regression exams: the
        # upgrade maps merge_check to 'none' (no exam phase exists).
        assert specs["learn"].curate["merge_check"] == "none"

    def test_round_trip_through_the_freezable_form(self, exam_v4):
        # to_dict -> from_dict stability: the raw form of a parsed spec
        # re-parses to the same spec (the manifest comparison needs it).
        specs = _parse(exam_v4)
        raw = {name: phase_spec_to_raw(spec)
               for name, spec in specs.items()}
        assert parse_phases(raw, exam_v4["sequence"],
                           n_attempts_weak=1) == specs

    def test_hand_off_contract_types(self, exam_v4):
        specs = _parse(exam_v4)
        instance = PhaseInstance(index=0, name="baseline", label="p01:baseline",
                                 spec=specs["baseline"], task_ids=("tb:t1",))
        assert (instance.index, instance.label) == (0, "p01:baseline")
        result = PhaseResult(label=instance.label, summary={"units": 1})
        assert result.summary == {"units": 1}


# ---------------------------------------------------------------------------
# The load-time checklist (every item a ConfigError)
# ---------------------------------------------------------------------------

class TestChecklist:
    def test_empty_sequence_refused(self, exam_v4):
        with pytest.raises(ConfigError, match="sequence must be a"):
            _parse({**exam_v4, "sequence": []})

    def test_colon_in_a_name_refused(self, exam_v4):
        # The hard prerequisite of the pNN:<name> label: no colons.
        with pytest.raises(ConfigError, match="never a colon"):
            _parse({**exam_v4, "sequence": ["baseline:", "learn"]})

    def test_name_without_definition_refused(self, exam_v4):
        with pytest.raises(ConfigError, match="no definition"):
            _parse({**exam_v4, "sequence": ["baseline", "X"]})

    def test_unused_definition_refused(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["X"] = dict(raw["exam"])
        with pytest.raises(ConfigError, match="never runs"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)

    def test_unknown_kind_refused(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["baseline"]["kind"] = "study"
        with pytest.raises(ConfigError, match="'execute' or 'learn'"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)

    def test_field_set_must_match_the_kind(self, exam_v4):
        # A learn key on an execute phase and vice versa: both refused,
        # never silently dropped.
        raw = copy.deepcopy(exam_v4["phases"])
        raw["baseline"]["evidence"] = {"name": "segment", "K": 5}
        with pytest.raises(ConfigError, match="kind 'execute' but carries"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)
        raw = copy.deepcopy(exam_v4["phases"])
        raw["learn"]["delivery"] = {"name": "none"}
        with pytest.raises(ConfigError, match="kind 'learn' but carries"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)

    def test_unknown_role_refused(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["baseline"]["role"] = "maybe"
        with pytest.raises(ConfigError, match="declared, never inferred"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)

    def test_test_role_and_test_tasks_are_one_fact(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["test"]["tasks"] = "train"
        with pytest.raises(ConfigError, match="mixes role 'test'"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)

    def test_exam_role_runs_the_exam_execution(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["exam"]["execution"] = {"name": "real"}
        with pytest.raises(ConfigError, match="that role runs 'exam'"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)
        raw = copy.deepcopy(exam_v4["phases"])
        # B replaces A in the sequence (a live phase runs the benchmark).
        raw["live"] = dict(raw.pop("exam"), role=PHASE_ROLE_LIVE)
        raw["live"]["execution"] = {"name": "exam"}
        sequence = ["baseline", "learn", "live", "learn", "test"]
        with pytest.raises(ConfigError, match="exam_folder|that role runs"):
            parse_phases(raw, sequence, n_attempts_weak=1)

    def test_baseline_arms_are_fixed(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["baseline"]["execution"]["arms"] = ["executor_weak"]
        with pytest.raises(ConfigError, match="must be exactly"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)
        raw = copy.deepcopy(exam_v4["phases"])
        del raw["baseline"]["execution"]["arms"]
        with pytest.raises(ConfigError, match="must be exactly"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)

    def test_baseline_refuses_a_per_arm_depth(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["baseline"]["execution"]["n_attempts"] = 2
        with pytest.raises(ConfigError,
                           match="n_attempts_strong / n_attempts_weak"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)

    def test_only_the_baseline_is_two_armed(self, live_v4):
        raw = copy.deepcopy(live_v4["phases"])
        raw["live"]["execution"]["arms"] = ["executor_weak"]
        with pytest.raises(ConfigError, match="only a baseline"):
            parse_phases(raw, live_v4["sequence"],
                         n_attempts_weak=2)

    def test_an_exam_phase_has_no_sampling_depth(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["exam"]["execution"]["n_attempts"] = 1
        with pytest.raises(ConfigError, match="no sampling depth"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)

    def test_nested_method_mappings_need_a_name(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["baseline"]["delivery"] = {"arms": 1}
        with pytest.raises(ConfigError,
                           match=r"phases.baseline.delivery needs a non-empty"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)
        raw = copy.deepcopy(exam_v4["phases"])
        raw["learn"]["curate"] = "agentbrew"
        with pytest.raises(ConfigError,
                           match="phases.learn.curate must be a mapping"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)

    def test_compare_against_must_be_earlier_or_fresh_bare(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["baseline"]["compare"] = {"against": "test"}
        with pytest.raises(ConfigError, match="does not appear earlier"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)
        raw = copy.deepcopy(exam_v4["phases"])
        raw["baseline"]["compare"] = {"against": FRESH_BARE}
        # fresh_bare is legal anywhere (a fresh bare arm in this phase).
        parse_phases(raw, exam_v4["sequence"],
                     n_attempts_weak=1)

    def test_compare_target_must_be_a_bare_measurement(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["exam"]["compare"] = {"against": "baseline"}
        # D (baseline) is a legal control for A too.
        parse_phases(raw, exam_v4["sequence"],
                     n_attempts_weak=1)
        # A control that appears EARLIER but is not a bare measurement
        # (the learn phase before A) is refused.
        raw["exam"]["compare"] = {"against": "learn"}
        with pytest.raises(ConfigError, match="bare measurement"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)
        # A later phase (the final test) never qualifies as a control.
        raw["exam"]["compare"] = {"against": "test"}
        with pytest.raises(ConfigError, match="does not appear earlier"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)

    def test_a_live_control_is_refused(self, live_v4):
        # A live phase's report rows all carry the injected group, so a
        # live phase is never a bare control: the checklist demands a
        # baseline phase (the learn driver's check used to fire only
        # after the live phase's executions were spent).
        raw = copy.deepcopy(live_v4["phases"])
        raw["B2"] = dict(raw["live"])
        raw["B2"]["compare"] = {"against": "live"}
        sequence = ["baseline", "learn", "live", "learn", "B2", "learn", "test"]
        with pytest.raises(ConfigError, match="baseline phase's bare"):
            parse_phases(raw, sequence, n_attempts_weak=2)

    def test_a_live_phase_needs_a_control(self, live_v4):
        # Without compare (or against fresh_bare) a live phase leaves
        # the decision with no control row to compare against.
        raw = copy.deepcopy(live_v4["phases"])
        del raw["live"]["compare"]
        with pytest.raises(ConfigError, match="without a control"):
            parse_phases(raw, live_v4["sequence"],
                         n_attempts_weak=2)
        raw["live"]["compare"] = {"against": FRESH_BARE}
        with pytest.raises(ConfigError, match="without a control"):
            parse_phases(raw, live_v4["sequence"],
                         n_attempts_weak=2)

    def test_the_sequence_must_start_with_an_execute_phase(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        sequence = ["learn", "baseline"] + exam_v4["sequence"]
        with pytest.raises(ConfigError,
                           match="must start with an execute phase"):
            parse_phases(raw, sequence,
                         n_attempts_weak=1)

    def test_single_arm_depth_never_exceeds_the_control(self, live_v4):
        raw = copy.deepcopy(live_v4["phases"])
        raw["live"]["execution"]["n_attempts"] = 3
        with pytest.raises(ConfigError, match="exceeds its control's"):
            parse_phases(raw, live_v4["sequence"],
                         n_attempts_weak=2)
        # At the control's depth the check passes.
        raw["live"]["execution"]["n_attempts"] = 2
        parse_phases(raw, live_v4["sequence"],
                     n_attempts_weak=2)

    def test_single_arm_depth_must_be_a_positive_int(self, live_v4):
        raw = copy.deepcopy(live_v4["phases"])
        raw["live"]["execution"]["n_attempts"] = 0
        with pytest.raises(ConfigError, match="positive"):
            parse_phases(raw, live_v4["sequence"],
                         n_attempts_weak=2)

    def test_author_exams_switch_has_no_v4_image(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["learn"]["author"] = {"name": "distill_diagnose", "exams": True}
        with pytest.raises(ConfigError, match="no v4 image"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)

    def test_merge_check_exam_needs_an_exam_phase(self, live_v4):
        # The live sequence has no exam phase: merge_check 'exam' is
        # refused (the upgrade maps it to 'none' for this reason).
        raw = copy.deepcopy(live_v4["phases"])
        raw["learn"]["curate"]["merge_check"] = "exam"
        with pytest.raises(ConfigError, match="no exam execute phase"):
            parse_phases(raw, live_v4["sequence"],
                         n_attempts_weak=2)

    def test_acceptance_names_a_rule_or_stays_absent(self, exam_v4):
        raw = copy.deepcopy(exam_v4["phases"])
        raw["exam"]["acceptance"] = "scorer"
        parse_phases(raw, exam_v4["sequence"],
                     n_attempts_weak=1)
        raw["exam"]["acceptance"] = ""
        with pytest.raises(ConfigError, match="verdict rule"):
            parse_phases(raw, exam_v4["sequence"],
                         n_attempts_weak=1)


# ---------------------------------------------------------------------------
# The 7 checked-in v3 configs convert into checklist-passing shapes
# ---------------------------------------------------------------------------

class TestCheckedInConfigs:
    def test_every_v3_config_upgrades_and_parses(self):
        paths = sorted(_CONFIG_DIR.glob("*.yaml"))
        v3 = [p for p in paths
              if yaml.safe_load(p.read_text(encoding="utf-8")).get(
                  "schema_version") == 3]
        assert len(v3) == 7
        for path in v3:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            v4 = upgrade_v3_config(raw)
            v5 = upgrade_v5_config(upgrade_v4_config(v4))
            v6 = upgrade_v6_config(v5)
            specs = _parse(v6)
            assert set(specs) == set(v6["phases"]) == set(v6["sequence"])
            # Every emitted phase carries its R8 role, and the v4-only
            # global is pinned at the default.
            for spec in specs.values():
                assert spec.role in (PHASE_ROLE_BASELINE, PHASE_ROLE_EXAM,
                                     PHASE_ROLE_LIVE, PHASE_ROLE_TEST,
                                     PHASE_ROLE_LEARN)
            assert v6["invalid_redispatch_limit"] == \
                DEFAULT_INVALID_REDISPATCH_LIMIT
            # The whole converted identity loads under the v6 gate: the
            # checklist runs from ExperimentConfig's own __post_init__,
            # role tables and token budgets included.
            from expedistill.core.config import ExperimentConfig
            cfg = ExperimentConfig.from_dict(v6)
            assert cfg.sequence == v6["sequence"]
            # The canonical (all-keys) to_dict round-trips: the frozen
            # manifest of a converted run compares equal on resume.
            assert ExperimentConfig.from_dict(cfg.to_dict()) == cfg
