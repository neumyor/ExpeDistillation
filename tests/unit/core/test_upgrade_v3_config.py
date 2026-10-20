"""Unit tests for upgrade_v3_config: the v3 -> v4 mapping (M4.2).

The function is a pure data mapping landed AHEAD of the M4.3 core flip:
``CONFIG_SCHEMA_VERSION`` is still 3 and ``load_config`` still refuses
v4, so these tests drive it directly on raw YAML dicts -- exactly what
the one-time converter (plan section 4) will feed it -- and pin the v4
shape of every v3-shaped config in the repository, plus the mapping-side
refusals and the v2 -> v3 -> v4 chain.
"""
import copy
from pathlib import Path

import pytest
import yaml

from expedistill.core.config import (
    ConfigError,
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


#: Section 4's baseline phase -- the double-arm baseline, the same literal in
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

#: Section 4's exam phase -- one exam measurement round (the retry_limit
#: budget is expressed by how many exam phases the sequence carries, so the
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
     ["baseline", "learn", "exam", "learn", "exam", "learn", "exam", "learn", "test"],
     {
         "baseline": D_PHASE,
         "exam": A_PHASE,
         "test": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "retrieval", "top_k": 5,
                            "max_categories": 3, "min_score": 0.05},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "learn": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "exam",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("alfworld_skillopt_split_v3.yaml",
     ["baseline", "learn", "exam", "learn", "exam", "learn", "exam", "learn", "test"],
     {
         "baseline": D_PHASE,
         "exam": A_PHASE,
         "test": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "whole_bank"},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "learn": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "exam",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("searchqa_skillopt_split_v3.yaml",
     ["baseline", "learn", "exam", "learn", "exam", "learn", "exam", "learn", "test"],
     {
         "baseline": D_PHASE,
         "exam": A_PHASE,
         "test": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "whole_bank"},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "learn": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "exam",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("tb21_full_v2_v3.yaml",
     ["baseline", "learn", "exam", "learn", "exam", "learn", "exam", "learn", "test"],
     {
         "baseline": D_PHASE,
         "exam": A_PHASE,
         # full mode: no held-out test set, so C reuses D's weak bare
         # arm (and its accepted holes) instead of running a fresh one.
         "test": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "whole_bank"},
               "execution": {"name": "real"},
               "compare": {"against": "baseline"}},
         "learn": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "exam",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("tb21_split_live_run_v3.yaml",
     ["baseline", "learn", "live", "learn", "live", "learn", "test"],
     {
         "baseline": D_PHASE,
         # live_run's acceptance params move into B; its merges are
         # proven by the round-1 group re-execution, not regression
         # exams (merge_check: none -- the disclosed identity change).
         "live": {"kind": "execute", "role": "live",
               "delivery": {"name": "group_by_task"},
               "execution": {"name": "real", "n_attempts": 3,
                             "max_injection_tokens": None},
               "compare": {"against": "baseline", "rule": "strict_rate_gain"}},
         "test": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "retrieval", "top_k": 5,
                            "max_categories": 3, "min_score": 0.02},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "learn": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "none",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("tb21_split_reuse_v3.yaml",
     ["baseline", "learn", "exam", "learn", "exam", "learn", "exam", "learn", "test"],
     {
         "baseline": D_PHASE,
         "exam": A_PHASE,
         "test": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "retrieval", "top_k": 5,
                            "max_categories": 3, "min_score": 0.02},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "learn": {"kind": "learn", "role": "learn",
               "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
               "author": {"name": "distill_diagnose"},
               "curate": {"name": "agentbrew", "merge_check": "exam",
                          "token_budget": 96000, "rounds": 3}},
     }),
    ("tb21_split_v2_v3.yaml",
     ["baseline", "learn", "exam", "learn", "exam", "learn", "exam", "learn", "test"],
     {
         "baseline": D_PHASE,
         "exam": A_PHASE,
         "test": {"kind": "execute", "role": "test", "tasks": "test",
               "delivery": {"name": "retrieval", "top_k": 5,
                            "max_categories": 3, "min_score": 0.02},
               "execution": {"name": "real"},
               "compare": {"against": "fresh_bare"}},
         "learn": {"kind": "learn", "role": "learn",
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
    def test_completed_slots_upgrade_identically_to_absent_ones(
            self, name, sequence, phases):
        # The v3 loader froze an absent slot as the default method with
        # default params; the upgrade completes the same way, so a raw
        # file and the v3-frozen manifest of the same experiment land on
        # the same v4 identity. (The frozen path itself -- load_config on
        # a v3 file -- died with the v3 loader; the reuse assembler's
        # v3-source walk is Wave 3's to rebuild on this guarantee.)
        raw = _load_raw(name)
        completed = dict(raw)
        completed["methods"] = {
            "segmentation": {"K": 5, "retry_limit": 2},
            "authoring": {"exams": True},
            "acceptance": {"name": "exam", "retry_limit": 2},
            "curation": {"name": "agentbrew", "token_budget": 4096,
                         "rounds": 3},
            "delivery": {"name": "whole_bank"},
        }
        for slot, given in raw.get("methods", {}).items():
            if given:
                completed["methods"][slot] = dict(given)
        from_raw = upgrade_v3_config(raw)
        from_completed = upgrade_v3_config(completed)
        assert from_completed["sequence"] == from_raw["sequence"]
        assert from_completed["phases"] == from_raw["phases"]

    def test_phase_and_field_order_is_pinned(self):
        # The construction order is part of the pinned shape (section
        # 4's field table): reordering a phase's fields must be a
        # conscious change, not a dict-iteration accident.
        exam = upgrade_v3_config(_load_raw("tb21_split_v2_v3.yaml"))
        assert list(exam["phases"]) == ["baseline", "learn", "exam", "test"]
        assert list(exam["phases"]["baseline"]) == [
            "kind", "role", "tasks", "delivery", "execution"]
        assert list(exam["phases"]["exam"]) == [
            "kind", "role", "delivery", "execution"]
        assert list(exam["phases"]["test"]) == [
            "kind", "role", "tasks", "delivery", "execution", "compare"]
        assert list(exam["phases"]["learn"]) == [
            "kind", "role", "evidence", "author", "curate"]
        live = upgrade_v3_config(_load_raw("tb21_split_live_run_v3.yaml"))
        assert list(live["phases"]) == ["baseline", "learn", "live", "test"]
        assert list(live["phases"]["live"]) == [
            "kind", "role", "delivery", "execution", "compare"]

    def test_input_mapping_untouched(self):
        raw = _load_raw("tb21_split_v2_v3.yaml")
        before = copy.deepcopy(raw)
        upgrade_v3_config(raw)
        assert raw == before

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


class TestUpgradeV3MappingRefusals:
    """Mapping-side boundaries the v4 checklist cannot see: a v3 slot
    value with no v4 destination is refused, never silently dropped."""

    def test_unknown_acceptance_name_refused(self):
        with pytest.raises(ConfigError, match="no v4 mapping"):
            upgrade_v3_config(_minimal(acceptance={"name": "oracle"}))

    def test_unmapped_slot_key_refused(self):
        # A key the mapping would silently drop is a changed experiment
        # identity; it is refused instead.
        with pytest.raises(ConfigError, match="no v4 destination"):
            upgrade_v3_config(_minimal(
                acceptance={"name": "exam", "retry_limit": 2,
                            "bogus": 1}))
