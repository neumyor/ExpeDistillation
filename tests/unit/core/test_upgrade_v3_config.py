"""Unit tests for upgrade_v3_config: the v3 -> v4 mapping (M4.2).

The function is a pure data mapping landed AHEAD of the M4.3 core flip:
``CONFIG_SCHEMA_VERSION`` is still 3 and ``load_config`` still refuses
v4, so these tests drive it directly on raw YAML dicts -- exactly what
the one-time converter (plan section 4) will feed it -- and pin the v4
shape of every v3-shaped config in the repository, plus the mapping
table's conversion and refusal behaviour on synthetic inputs.
"""
import copy
from pathlib import Path

import pytest
import yaml

from expedistill.core.config import (
    ConfigError,
    load_config,
    upgrade_v2_config,
    upgrade_v3_config,
)

EXPERIMENTS = Path(__file__).resolve().parents[3] / "configs" / "experiments"

#: The seven v3-shaped configs -- the converter's whole input set (plan
#: section 4's s-6: the four ``*_v3.yaml`` plus the three
#: ``*_v2_v3.yaml`` conversions).
V3_CONFIGS = (
    "alfworld_skillopt_split_v2_v3.yaml",
    "alfworld_skillopt_split_v3.yaml",
    "searchqa_skillopt_split_v3.yaml",
    "tb21_full_v2_v3.yaml",
    "tb21_split_live_run_v3.yaml",
    "tb21_split_reuse_v3.yaml",
    "tb21_split_v2_v3.yaml",
)


def _load_raw(name):
    with open(EXPERIMENTS / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


#: Section 4's D phase -- the double-arm baseline, the same literal in
#: every sequence the upgrade emits (the two-arm depths are the global
#: n_attempts_strong / n_attempts_weak, never per-phase).
D_PHASE = {
    "kind": "execute",
    "role": "baseline",
    "tasks": "train",
    "delivery": {"name": "none"},
    "execution": {"name": "real",
                  "arms": ["executor_strong", "executor_weak"]},
}

#: Section 4's A phase -- one exam measurement round (the retry_limit
#: budget is expressed by how many A phases the sequence carries, so the
#: phase itself has no parameters).
A_PHASE = {
    "kind": "execute",
    "role": "exam",
    "delivery": {"name": "per_skill"},
    "execution": {"name": "exam"},
}

#: (file, expected sequence, expected phases) -- plan section 4's mapping
#: table applied by hand, every phase of every config field by field.
#: Only D and A are shared constants: section 4 fixes them for every
#: sequence, and each row still asserts its config's phases against
#: them. Everything a config varies is spelled out in its own row.
EXPECTED = [
    ("alfworld_skillopt_split_v2_v3.yaml",
     ["D", "L", "A", "L", "A", "L", "A", "L", "C"],
     {
         "D": D_PHASE,
         "A": A_PHASE,
         "C": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "retrieval", "top_k": 5,
                            "max_categories": 3, "min_score": 0.05},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "L": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "exam",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("alfworld_skillopt_split_v3.yaml",
     ["D", "L", "A", "L", "A", "L", "A", "L", "C"],
     {
         "D": D_PHASE,
         "A": A_PHASE,
         "C": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "whole_bank"},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "L": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "exam",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("searchqa_skillopt_split_v3.yaml",
     ["D", "L", "A", "L", "A", "L", "A", "L", "C"],
     {
         "D": D_PHASE,
         "A": A_PHASE,
         "C": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "whole_bank"},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "L": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "exam",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("tb21_full_v2_v3.yaml",
     ["D", "L", "A", "L", "A", "L", "A", "L", "C"],
     {
         "D": D_PHASE,
         "A": A_PHASE,
         # full mode: no held-out test set, so C reuses D's weak bare
         # arm (and its accepted holes) instead of running a fresh one.
         "C": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "whole_bank"},
               "execution": {"name": "real"},
               "compare": {"against": "D"}},
         "L": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "exam",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("tb21_split_live_run_v3.yaml",
     ["D", "L", "B", "L", "B", "L", "C"],
     {
         "D": D_PHASE,
         # live_run's acceptance params move into B; its merges are
         # proven by the round-1 group re-execution, not regression
         # exams (merge_check: none -- the disclosed identity change).
         "B": {"kind": "execute", "role": "live",
               "delivery": {"name": "group_by_task"},
               "execution": {"name": "real", "n_attempts": 3,
                             "max_injection_tokens": None},
               "compare": {"against": "D", "rule": "strict_rate_gain"}},
         "C": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "retrieval", "top_k": 5,
                            "max_categories": 3, "min_score": 0.02},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "L": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "none",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("tb21_split_reuse_v3.yaml",
     ["D", "L", "A", "L", "A", "L", "A", "L", "C"],
     {
         "D": D_PHASE,
         "A": A_PHASE,
         "C": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "retrieval", "top_k": 5,
                            "max_categories": 3, "min_score": 0.02},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "L": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "exam",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("tb21_split_v2_v3.yaml",
     ["D", "L", "A", "L", "A", "L", "A", "L", "C"],
     {
         "D": D_PHASE,
         "A": A_PHASE,
         "C": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "retrieval", "top_k": 5,
                            "max_categories": 3, "min_score": 0.02},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "L": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "exam",
                          "token_budget": 96000, "rounds": 3}},
     }),
]

_IDS = [row[0] for row in EXPECTED]


class TestUpgradeV3ConfigShapes:
    """The seven v3-shaped configs (the converter's whole input set)
    upgrade to their pinned v4 shapes."""

    @pytest.mark.parametrize("name,sequence,phases", EXPECTED, ids=_IDS)
    def test_upgrades_to_the_pinned_shape(self, name, sequence, phases):
        v4 = upgrade_v3_config(_load_raw(name))
        assert v4["schema_version"] == 4
        assert "methods" not in v4
        assert v4["sequence"] == sequence
        assert v4["phases"] == phases
        assert set(v4["phases"]) == set(sequence)
        # The v4-only global is emitted at its default so a converted
        # identity never depends on the default's value.
        assert v4["invalid_redispatch_limit"] == 1

    @pytest.mark.parametrize("name,sequence,phases", EXPECTED, ids=_IDS)
    def test_globals_pass_through_untouched(self, name, sequence, phases):
        raw = _load_raw(name)
        v4 = upgrade_v3_config(raw)
        # Zero loss, zero invention: the output keeps every key the
        # mapping does not own verbatim, drops exactly ``methods``, adds
        # exactly ``sequence``/``phases``/``invalid_redispatch_limit``
        # and rewrites only ``schema_version``.
        assert set(v4) == (set(raw) - {"methods"}) | {
            "sequence", "phases", "invalid_redispatch_limit"}
        for key in set(raw) - {"methods", "schema_version"}:
            assert v4[key] == raw[key], key

    @pytest.mark.parametrize("name,sequence,phases", EXPECTED, ids=_IDS)
    def test_frozen_manifest_path_agrees_with_the_raw_path(
            self, name, sequence, phases):
        # The reuse assembler upgrades a frozen manifest's config entry
        # (to_dict: slots completed, role table filled); it must land on
        # the same phases as the converter's raw-file path.
        frozen = load_config(str(EXPERIMENTS / name)).to_dict()
        from_raw = upgrade_v3_config(_load_raw(name))
        from_frozen = upgrade_v3_config(frozen)
        assert from_frozen["sequence"] == from_raw["sequence"]
        assert from_frozen["phases"] == from_raw["phases"]

    def test_phase_and_field_order_is_pinned(self):
        # The construction order is part of the pinned shape (section
        # 4's field table): reordering a phase's fields must be a
        # conscious change, not a dict-iteration accident.
        exam = upgrade_v3_config(_load_raw("tb21_split_v2_v3.yaml"))
        assert list(exam["phases"]) == ["D", "L", "A", "C"]
        assert list(exam["phases"]["D"]) == [
            "kind", "role", "tasks", "delivery", "execution"]
        assert list(exam["phases"]["A"]) == [
            "kind", "role", "delivery", "execution"]
        assert list(exam["phases"]["C"]) == [
            "kind", "role", "tasks", "delivery", "execution", "compare"]
        assert list(exam["phases"]["L"]) == [
            "kind", "role", "evidence", "author", "curate"]
        live = upgrade_v3_config(_load_raw("tb21_split_live_run_v3.yaml"))
        assert list(live["phases"]) == ["D", "L", "B", "C"]
        assert list(live["phases"]["B"]) == [
            "kind", "role", "delivery", "execution", "compare"]

    def test_input_mapping_untouched(self):
        raw = _load_raw("tb21_split_v2_v3.yaml")
        before = copy.deepcopy(raw)
        upgrade_v3_config(raw)
        assert raw == before


def _minimal(**slot_overrides):
    """A v3 config skeleton: the five slots plus the one global the
    mapping reads (mode); every other global is the caller's business --
    the upgrade passes unknown keys through untouched."""
    methods = {
        "segmentation": {"K": 5, "retry_limit": 2},
        "authoring": {"exams": True},
        "acceptance": {"name": "exam", "retry_limit": 2},
        "curation": {"name": "agentbrew", "token_budget": 96000,
                     "rounds": 3},
        "delivery": {"name": "whole_bank"},
    }
    methods.update(slot_overrides)
    return {"benchmark": "tb", "mode": "split", "methods": methods}


def _live_slots(**overrides):
    slots = {
        "acceptance": {"name": "live_run", "n_attempts": 3,
                       "improvement": "strict_rate_gain"},
        "curation": {"name": "none", "token_budget": 96000},
    }
    slots.update(overrides)
    return slots


class TestUpgradeV3Refusals:
    """The mapping table's loud-failure boundaries on synthetic inputs."""

    def test_v2_input_refused(self):
        # A v2 config has no ``methods`` mapping; the error names the
        # v2 -> v3 step of the chain (the assembler's version walk).
        with pytest.raises(ConfigError, match="upgrade_v2_config"):
            upgrade_v3_config(_load_raw("tb21_split_v2.yaml"))

    def test_already_v4_input_refused(self):
        with pytest.raises(ConfigError, match="sequence"):
            upgrade_v3_config({"methods": {}, "sequence": ["D"]})
        with pytest.raises(ConfigError, match="phases"):
            upgrade_v3_config({"methods": {}, "phases": {}})

    def test_non_mapping_input_refused(self):
        with pytest.raises(TypeError, match="mapping"):
            upgrade_v3_config(["not", "a", "config"])

    def test_missing_methods_refused(self):
        with pytest.raises(ConfigError, match="schema-v3"):
            upgrade_v3_config({"benchmark": "tb", "mode": "split"})

    def test_unknown_acceptance_name_refused(self):
        with pytest.raises(ConfigError, match="no v4 mapping"):
            upgrade_v3_config(_minimal(acceptance={"name": "oracle"}))

    def test_nameless_acceptance_refused(self):
        with pytest.raises(ConfigError, match="needs a name"):
            upgrade_v3_config(_minimal(acceptance={"retry_limit": 1}))

    def test_unmapped_slot_key_refused(self):
        # A key the mapping would silently drop is a changed experiment
        # identity; it is refused instead.
        with pytest.raises(ConfigError, match="no v4 destination"):
            upgrade_v3_config(_minimal(
                acceptance={"name": "exam", "retry_limit": 2,
                            "bogus": 1}))

    def test_live_run_missing_required_params_refused(self):
        with pytest.raises(ConfigError, match="n_attempts"):
            upgrade_v3_config(_minimal(acceptance={"name": "live_run"}))


class TestUpgradeV3SyntheticShapes:
    """The mapping table's conversions on synthetic inputs."""

    def test_exam_retry_limit_counts_the_a_phases(self):
        v4 = upgrade_v3_config(
            _minimal(acceptance={"name": "exam", "retry_limit": 0}))
        assert v4["sequence"] == ["D", "L", "A", "L", "C"]

    def test_empty_methods_complete_to_the_defaults(self):
        # An absent or empty slot is METHOD_DEFAULTS (the v3 loader's
        # own completion), so the upgrade must produce the v4 identity
        # of what the same file loads as -- and spelling the defaults
        # out lands on the identical output.
        from_defaults = upgrade_v3_config(
            {"benchmark": "tb", "mode": "split", "methods": {}})
        explicit = upgrade_v3_config(_minimal(
            acceptance={"name": "exam", "retry_limit": 2},
            curation={"name": "agentbrew", "token_budget": 4096,
                      "rounds": 3}))
        assert from_defaults == explicit

    def test_exams_false_with_exam_acceptance_refused(self):
        with pytest.raises(ConfigError, match="ablation"):
            upgrade_v3_config(_minimal(authoring={"exams": False}))

    def test_exams_false_ablation_pair_converts_identically(self):
        # exams: false is legal only as live_run + curation none, and a
        # live sequence has no A phase either way -- so the ablation
        # pair upgrades to the identical shape as exams: true.
        base = _minimal(**_live_slots())
        ablated = _minimal(**_live_slots())
        ablated["methods"]["authoring"] = {"exams": False}
        assert upgrade_v3_config(ablated) == upgrade_v3_config(base)

    def test_live_run_params_move_into_the_b_phase(self):
        v4 = upgrade_v3_config(_minimal(
            **_live_slots(acceptance={
                "name": "live_run", "n_attempts": 5,
                "improvement": "strict_rate_gain",
                "max_injection_tokens": 4096})))
        b = v4["phases"]["B"]
        assert b["kind"] == "execute"
        assert b["delivery"] == {"name": "group_by_task"}
        assert b["execution"] == {"name": "real", "n_attempts": 5,
                                  "max_injection_tokens": 4096}
        assert b["compare"] == {"against": "D",
                                "rule": "strict_rate_gain"}
        assert v4["phases"]["L"]["curate"] == {
            "name": "none", "token_budget": 96000}
        assert v4["sequence"] == ["D", "L", "B", "L", "B", "L", "C"]
        assert "A" not in v4["phases"]

    def test_explicit_merge_check_is_preserved(self):
        # The curation slot may carry merge_check (M3); an explicit value
        # is the config's identity choice and survives the upgrade.
        v4 = upgrade_v3_config(_minimal(
            curation={"name": "agentbrew", "token_budget": 96000,
                      "rounds": 3, "merge_check": "none"}))
        assert v4["phases"]["L"]["curate"]["merge_check"] == "none"

    def test_explicit_merge_check_exam_under_live_run_refused(self):
        # merge_check: exam under live_run has no v4 image: the live
        # sequence generates no exams to check merges against.
        with pytest.raises(ConfigError, match="merge_check"):
            upgrade_v3_config(_minimal(
                **_live_slots(curation={
                    "name": "agentbrew", "token_budget": 96000,
                    "rounds": 3, "merge_check": "exam"})))

    def test_delivery_copies_name_and_params_verbatim(self):
        v4 = upgrade_v3_config(_minimal(delivery={"name": "per_skill"}))
        assert v4["phases"]["C"]["delivery"] == {"name": "per_skill"}

    def test_v2_source_chains_through_upgrade_v2(self):
        # The assembler's version walk (plan section 7): a v2 source
        # manifest chains v2 -> v3 -> v4 and lands on the same phases
        # as the repository's own v3 conversion of the same experiment.
        chained = upgrade_v3_config(
            upgrade_v2_config(_load_raw("tb21_split_v2.yaml")))
        direct = upgrade_v3_config(_load_raw("tb21_split_v2_v3.yaml"))
        assert chained["schema_version"] == 4
        assert chained["sequence"] == direct["sequence"]
        assert chained["phases"] == direct["phases"]
