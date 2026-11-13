"""The phase sequence's parsing products in core.config.

Pins the phase vocabulary (baseline / practice / test / learn), the
``pNN:<name>`` label derivation (freeze is p00, sequence entry i is
p{i+1:02d}), the parsed phase specs' shape, the load-time checklist of
``parse_phases`` (design spec 2.3, every item a loud ConfigError with one
negative case each), the hand-off contract types the phase drivers receive
and return, and every checked-in experiment config loading under the
current schema.
"""

import copy
from pathlib import Path

import pytest

from expedistill.core.config import (
    CONFIG_SCHEMA_VERSION,
    EXECUTE_PHASE_ROLES,
    FRESH_BARE,
    FREEZE_LABEL,
    LEARNABLE_PHASE_ROLES,
    PHASE_ROLE_BASELINE,
    PHASE_ROLE_LEARN,
    PHASE_ROLE_PRACTICE,
    PHASE_ROLE_TEST,
    CompareSpec,
    ConfigError,
    ExecutePhaseSpec,
    ExperimentConfig,
    LearnPhaseSpec,
    PhaseInstance,
    PhaseResult,
    load_config,
    parse_phases,
    phase_label,
    phase_spec_to_raw,
)
from expedistill.core.ids import phase_tag
from tests.helpers.configs import REFERENCE_SEQUENCE, reference_method

_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_DIR = _ROOT / "configs" / "experiments"

# ---------------------------------------------------------------------------
# Fixture: the reference method
# ---------------------------------------------------------------------------

@pytest.fixture()
def method():
    return reference_method()


def _parse(method):
    return parse_phases(method["phases"], method["sequence"])


def _refused(method, match):
    with pytest.raises(ConfigError, match=match):
        _parse(method)


# ---------------------------------------------------------------------------
# The vocabulary and the label derivation
# ---------------------------------------------------------------------------

class TestVocabulary:
    def test_the_roles(self):
        assert EXECUTE_PHASE_ROLES == (PHASE_ROLE_BASELINE,
                                       PHASE_ROLE_PRACTICE, PHASE_ROLE_TEST)
        assert LEARNABLE_PHASE_ROLES == (PHASE_ROLE_BASELINE,
                                         PHASE_ROLE_PRACTICE)
        assert PHASE_ROLE_LEARN not in EXECUTE_PHASE_ROLES

    def test_the_label_shape_is_what_the_id_rules_accept(self):
        label = phase_label(0, "baseline")
        assert label == "p01:baseline"
        assert phase_tag(label) == "p01_baseline"
        assert phase_tag(FREEZE_LABEL) == "p00_freeze"
        assert phase_label(11, "a_late.phase") == "p12:a_late.phase"


class TestParsedShapes:
    def test_execute_phase_fields(self, method):
        specs = _parse(method)
        baseline = specs["baseline"]
        assert isinstance(baseline, ExecutePhaseSpec)
        assert baseline.kind == "execute"
        assert baseline.role == PHASE_ROLE_BASELINE
        assert baseline.tasks == "train"
        assert baseline.delivery == {"name": "none"}
        assert baseline.execution == {
            "name": "real", "arms": ["executor_strong", "executor_weak"]}
        assert baseline.compare is None and baseline.acceptance is None
        assert baseline.playbook == "off"
        practice = specs["practice"]
        assert practice.role == PHASE_ROLE_PRACTICE
        assert practice.delivery == {"name": "folder"}
        assert practice.execution == {"name": "real_folder", "attempts": 3}
        assert practice.playbook == "evolved"
        assert practice.compare is None
        test = specs["test"]
        assert test.role == PHASE_ROLE_TEST
        assert test.tasks == "test"
        assert test.compare == CompareSpec(against="fresh_bare")

    def test_learn_phase_fields(self, method):
        learn = _parse(method)["learn"]
        assert isinstance(learn, LearnPhaseSpec)
        assert learn.kind == "learn"
        assert learn.role == PHASE_ROLE_LEARN
        assert learn.review == {"name": "review", "K": 5, "retry_limit": 2}
        assert learn.author == {"name": "subtask_cards"}
        assert set(phase_spec_to_raw(learn)) == {
            "review", "author", "kind", "role"}

    def test_practice_may_run_the_val_list(self, method):
        method["phases"]["practice"]["tasks"] = "val"
        assert _parse(method)["practice"].tasks == "val"

    def test_round_trip_through_the_freezable_form(self, method):
        # The raw form of a parsed spec re-parses to the same spec (the
        # manifest comparison needs it).
        specs = _parse(method)
        raw = {name: phase_spec_to_raw(spec)
               for name, spec in specs.items()}
        assert parse_phases(raw, method["sequence"]) == specs

    def test_hand_off_contract_types(self, method):
        specs = _parse(method)
        instance = PhaseInstance(index=0, name="baseline",
                                 label="p01:baseline",
                                 spec=specs["baseline"], task_ids=("tb:t1",))
        assert (instance.index, instance.label) == (0, "p01:baseline")
        result = PhaseResult(label=instance.label, summary={"units": 1})
        assert result.summary == {"units": 1}


# ---------------------------------------------------------------------------
# The load-time checklist (design spec 2.3; every item a ConfigError)
# ---------------------------------------------------------------------------

class TestItem1Sequence:
    def test_empty_sequence_refused(self, method):
        _refused({**method, "sequence": []}, "sequence must be a")

    def test_colon_in_a_name_refused(self, method):
        # The hard prerequisite of the pNN:<name> label: no colons.
        _refused({**method, "sequence": ["baseline:", "learn"]},
                 "never a colon")

    def test_name_without_definition_refused(self, method):
        _refused({**method, "sequence": ["baseline", "X"]}, "no definition")

    def test_unused_definition_refused(self, method):
        method["phases"]["X"] = copy.deepcopy(method["phases"]["practice"])
        _refused(method, "never runs")


class TestItem2FieldSets:
    def test_unknown_kind_refused(self, method):
        method["phases"]["baseline"]["kind"] = "study"
        _refused(method, "'execute' or 'learn'")

    def test_an_execute_key_on_a_learn_phase_is_refused(self, method):
        method["phases"]["learn"]["delivery"] = {"name": "none"}
        _refused(method, "kind 'learn' but carries")

    def test_a_learn_key_on_an_execute_phase_is_refused(self, method):
        method["phases"]["baseline"]["review"] = {"name": "review"}
        _refused(method, "kind 'execute' but carries")

    def test_the_curate_key_is_gone(self, method):
        method["phases"]["learn"]["curate"] = {"name": "none"}
        _refused(method, "kind 'learn' but carries")

    def test_nested_method_mappings_need_a_name(self, method):
        method["phases"]["baseline"]["delivery"] = {"arms": 1}
        _refused(method, r"phases.baseline.delivery needs a non-empty")

    def test_nested_method_mappings_are_mappings(self, method):
        method["phases"]["learn"]["author"] = "subtask_cards"
        _refused(method, "phases.learn.author must be a mapping")


class TestItem3RoleConsistency:
    def test_unknown_role_refused(self, method):
        method["phases"]["baseline"]["role"] = "exam"
        _refused(method, "declared, never inferred")

    def test_the_test_role_runs_the_test_list(self, method):
        method["phases"]["test"]["tasks"] = "train"
        _refused(method, "role 'test' with tasks 'train'")

    @pytest.mark.parametrize("phase", ["baseline", "practice"])
    def test_only_the_test_role_runs_the_test_list(self, method, phase):
        method["phases"][phase]["tasks"] = "test"
        _refused(method, f"role '{phase}' with tasks 'test'")

    def test_the_baseline_runs_train_only(self, method):
        method["phases"]["baseline"]["tasks"] = "val"
        _refused(method, "role 'baseline' with tasks 'val'")

    def test_an_unknown_task_set_is_refused(self, method):
        method["phases"]["practice"]["tasks"] = "reserved"
        _refused(method, "tasks must be one of")

    def test_the_baseline_uses_the_none_delivery(self, method):
        method["phases"]["baseline"]["delivery"] = {"name": "whole_bank"}
        _refused(method, "the baseline is the bare run")

    def test_the_baseline_uses_the_real_execution(self, method):
        method["phases"]["baseline"]["delivery"] = {"name": "folder"}
        method["phases"]["baseline"]["execution"] = {
            "name": "real_folder",
            "arms": ["executor_strong", "executor_weak"]}
        _refused(method, "the baseline is the bare run")

    def test_the_baseline_never_shows_a_playbook(self, method):
        method["phases"]["baseline"]["playbook"] = "evolved"
        _refused(method, "baseline phase runs without")

    def test_baseline_arms_are_fixed(self, method):
        method["phases"]["baseline"]["execution"]["arms"] = ["executor_weak"]
        _refused(method, "must be exactly")

    def test_baseline_arms_are_required(self, method):
        del method["phases"]["baseline"]["execution"]["arms"]
        _refused(method, "must be exactly")

    @pytest.mark.parametrize("phase", ["practice", "test"])
    def test_only_the_baseline_is_two_armed(self, method, phase):
        method["phases"][phase]["execution"]["arms"] = ["executor_weak"]
        _refused(method, "only a baseline")

    def test_practice_uses_the_folder_delivery(self, method):
        method["phases"]["practice"]["delivery"] = {"name": "whole_bank"}
        method["phases"]["practice"]["execution"] = {"name": "real"}
        _refused(method, "a practice phase runs the way the final test")

    def test_an_unknown_execution_name_is_refused(self, method):
        method["phases"]["test"]["execution"] = {"name": "exam"}
        _refused(method, "execution name 'exam' is not one of")

    def test_acceptance_names_a_rule_or_stays_absent(self, method):
        method["phases"]["practice"]["acceptance"] = "scorer"
        _parse(method)
        method["phases"]["practice"]["acceptance"] = ""
        _refused(method, "verdict rule")


class TestItem4BaselineFirst:
    def test_a_practice_phase_cannot_come_first(self, method):
        method["sequence"] = ["practice", "baseline", "learn", "test"]
        _refused(method, "must start with the 'baseline'")

    def test_a_learn_phase_cannot_come_first(self, method):
        method["sequence"] = ["learn"] + method["sequence"]
        _refused(method, "must start with the 'baseline'")

    def test_a_test_only_sequence_needs_no_baseline(self, method):
        method["sequence"] = ["test"]
        method["phases"] = {"test": method["phases"]["test"]}
        del method["phases"]["test"]["playbook"]
        assert set(_parse(method)) == {"test"}

    def test_the_baseline_cannot_follow_a_test(self, method):
        method["sequence"] = ["test", "baseline"]
        method["phases"] = {name: method["phases"][name]
                            for name in ("test", "baseline")}
        del method["phases"]["test"]["playbook"]
        _refused(method, "only the first entry may be the baseline")

    def test_only_the_first_entry_is_the_baseline(self, method):
        method["sequence"] = ["baseline", "learn", "practice", "baseline",
                              "learn", "test"]
        _refused(method, "only the first entry may be the baseline")


class TestItem5Compare:
    @pytest.mark.parametrize("phase", ["baseline", "practice"])
    def test_only_a_test_phase_has_a_control(self, method, phase):
        method["phases"][phase]["compare"] = {"against": FRESH_BARE}
        _refused(method, "only a 'test' phase")

    @pytest.mark.parametrize("against", ["baseline", "learn", "test", None])
    def test_compare_against_names_a_control_source(self, method, against):
        method["phases"]["test"]["compare"] = {"against": against}
        _refused(method, "against must be one of")

    def test_compare_refuses_unknown_keys(self, method):
        method["phases"]["test"]["compare"] = {"against": FRESH_BARE,
                                               "rule": "x"}
        _refused(method, "unknown key")


class TestItem6LearnReadsLearnable:
    def test_a_learn_phase_after_the_test_is_refused(self, method):
        method["sequence"] = list(REFERENCE_SEQUENCE) + ["learn"]
        _refused(method, "role 'test'")

    def test_learn_right_after_the_baseline_is_legal(self, method):
        method["sequence"] = ["baseline", "learn", "test"]
        del method["phases"]["practice"]
        assert set(_parse(method)) == {"baseline", "learn", "test"}


class TestItem7FolderPairing:
    def test_a_folder_delivery_needs_real_folder(self, method):
        method["phases"]["test"]["execution"] = {"name": "real"}
        _refused(method, "pairs with the 'real_folder' execution")

    def test_real_folder_needs_a_folder_delivery(self, method):
        method["phases"]["test"]["delivery"] = {"name": "whole_bank"}
        _refused(method, "pairs with the 'real_folder' execution")

    def test_a_test_phase_may_run_whole_bank_with_real(self, method):
        method["phases"]["test"]["delivery"] = {"name": "whole_bank"}
        method["phases"]["test"]["execution"] = {"name": "real"}
        assert _parse(method)["test"].execution == {"name": "real"}


class TestItem8PlaybookSwitch:
    def test_the_switch_defaults_to_off(self, method):
        for name in ("practice", "test"):
            del method["phases"][name]["playbook"]
        specs = _parse(method)
        assert {specs[name].playbook
                for name in ("baseline", "practice", "test")} == {"off"}

    def test_practice_and_test_may_show_the_evolved_playbook(self, method):
        specs = _parse(method)
        assert specs["practice"].playbook == specs["test"].playbook \
            == "evolved"
        assert phase_spec_to_raw(specs["test"])["playbook"] == "evolved"

    def test_an_unknown_switch_value_is_refused(self, method):
        method["phases"]["test"]["playbook"] = "learned"
        _refused(method, "playbook must be one of")

    def test_an_evolved_playbook_needs_a_learn_phase_before_it(self, method):
        method["sequence"] = ["baseline", "test"]
        del method["phases"]["learn"]
        del method["phases"]["practice"]
        _refused(method, "no learn phase runs before")


# ---------------------------------------------------------------------------
# Every checked-in experiment config loads under the current schema
# ---------------------------------------------------------------------------

class TestCheckedInConfigs:
    def test_every_config_loads_and_round_trips(self):
        paths = sorted(_CONFIG_DIR.glob("*.yaml"))
        assert paths, f"no experiment configs under {_CONFIG_DIR}"
        for path in paths:
            cfg = load_config(str(path))
            assert cfg.schema_version == CONFIG_SCHEMA_VERSION, path.name
            assert set(cfg.phases) == set(cfg.sequence), path.name
            for spec in cfg.phases.values():
                assert spec.role in EXECUTE_PHASE_ROLES + (
                    PHASE_ROLE_LEARN,)
            # The canonical (all-keys) to_dict round-trips: the frozen
            # manifest compares equal on resume.
            assert ExperimentConfig.from_dict(cfg.to_dict()) == cfg, \
                path.name
