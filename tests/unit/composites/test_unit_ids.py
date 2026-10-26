"""Unit / answer-sheet identity rules' unit tests (composites/unit_ids.py).

The unit id is the join key between a UnitSpec, a coverage record and a
UnitSummary.  These tests pin the four raw injection shapes, the canonical
JSON form (dict key order is not identity), the deterministic id format,
and the two convenience ids.
"""

import hashlib
import json

from expedistill.composites import unit_ids
from expedistill.core.models import Step, Trajectory


def test_canonical_skills_keeps_none_empty_list_string_and_list_apart():
    assert unit_ids.canonical_skills(None) == "none"
    assert unit_ids.canonical_skills([]) == "list:[]"
    assert unit_ids.canonical_skills("digest-abc") == "bank:digest-abc"
    assert unit_ids.canonical_skills([{"skill_id": "s1", "version": 1}]) == \
        'list:[{"skill_id":"s1","version":1}]'
    assert len({
        unit_ids.canonical_skills(None),
        unit_ids.canonical_skills([]),
        unit_ids.canonical_skills("digest-abc"),
        unit_ids.canonical_skills([{"skill_id": "s1", "version": 1}]),
    }) == 4


def test_canonical_skills_ignores_dict_key_order_inside_a_member():
    left = [{"skill_id": "s1", "version": 2},
            {"skill_id": "s2", "version": 1}]
    right = [{"version": 2, "skill_id": "s1"},
             {"version": 1, "skill_id": "s2"}]
    assert unit_ids.canonical_skills(left) == \
        unit_ids.canonical_skills(right)


def test_canonical_skills_keeps_list_member_order_as_identity():
    """A group's member order is part of its identity (the same ruling the
    acceptance-side definition pins): only key order is normalised."""
    left = [{"skill_id": "s1", "version": 1},
            {"skill_id": "s2", "version": 1}]
    right = list(reversed(left))
    assert unit_ids.canonical_skills(left) != \
        unit_ids.canonical_skills(right)


def test_canonical_skills_preserves_non_ascii_text():
    canonical = unit_ids.canonical_skills(["技能-一"])
    assert "技能-一" in canonical
    assert json.dumps(["技能-一"], ensure_ascii=False,
                      separators=(",", ":")) in canonical


def _base(**overrides):
    base = dict(
        phase_label="p03:live",
        benchmark="tb",
        task_ref="tb:task-1",
        role="weak",
        skills_injected=None,
    )
    base.update(overrides)
    return unit_ids.make_unit_id(**base)


def test_make_unit_id_is_deterministic_and_has_the_frozen_format():
    unit_id = _base()
    assert unit_id == _base()
    assert unit_id == (
        "p03:live:tb:tb:task-1:weak:"
        + hashlib.sha256(b"none").hexdigest()[:8])
    # The four axes then an eight-hex-char digest (task_ref may itself
    # carry the benchmark's colon, so check the prefix and the tail).
    prefix = "p03:live:tb:tb:task-1:weak:"
    assert unit_id.startswith(prefix)
    digest = unit_id[len(prefix):]
    assert len(digest) == 8
    int(digest, 16)


def test_make_unit_id_changes_with_every_axis():
    base_id = _base()
    assert _base(phase_label="p04:live") != base_id
    assert _base(benchmark="searchqa") != base_id
    assert _base(task_ref="tb:task-2") != base_id
    assert _base(role="strong") != base_id
    assert _base(skills_injected=[]) != base_id
    assert _base(skills_injected="bank-digest") != base_id
    assert _base(skills_injected=[{"skill_id": "s1", "version": 1}]) != \
        base_id


def test_make_gap_sheet_id_encodes_the_unit_and_attempt():
    assert unit_ids.make_gap_sheet_id("unit-1", 0) == "gap:unit-1:0"
    assert unit_ids.make_gap_sheet_id("unit-1", 3) == "gap:unit-1:3"
    assert unit_ids.make_gap_sheet_id("unit-1", None) == "gap:unit-1:None"


def test_unit_id_for_trajectory_reads_task_role_and_injection():
    trajectory = Trajectory(
        trajectory_id="traj/tb:task-1/weak/00",
        task_id="tb:task-1",
        benchmark="tb",
        model_role="weak",
        model_name="model-a",
        attempt_index=0,
        skills_injected=[{"skill_id": "s1", "version": 2}],
        steps=[Step(action="ls", observation="ok")],
        correctness=True,
    )
    assert unit_ids.unit_id_for_trajectory(
        phase_label="p03:live", benchmark="tb", trajectory=trajectory) == \
        unit_ids.make_unit_id(
            phase_label="p03:live", benchmark="tb",
            task_ref="tb:task-1", role="weak",
            skills_injected=[{"skill_id": "s1", "version": 2}])


def test_unit_ids_keeps_every_raw_shape_a_distinct_identity():
    ids_ = set()
    for skills in (None, [], "digest-abc",
                   [{"skill_id": "s1", "version": 1}],
                   [{"skill_id": "s2", "version": 3},
                    {"skill_id": "s1", "version": 1}]):
        ids_.add(unit_ids.make_unit_id(
            phase_label="p03:live", benchmark="tb",
            task_ref="tb:task-1", role="weak", skills_injected=skills))
    assert len(ids_) == 5
