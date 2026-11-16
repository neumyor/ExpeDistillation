"""Unit / answer-sheet identity rules' unit tests (composites/unit_ids.py).

The unit id is the join key between a UnitSpec, a coverage record and a
UnitSummary.  These tests pin the raw injection shapes, the deterministic id format,
and the two convenience ids.
"""

import hashlib
import urllib.parse

from expedistill.composites import unit_ids
from expedistill.core.models import Step, Trajectory


def test_canonical_skills_keeps_none_and_a_bank_reference_apart():
    assert unit_ids.canonical_skills(None) == "none"
    assert unit_ids.canonical_skills("digest-abc") == "bank:digest-abc"


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
    # Every injection shape is a distinct identity.
    assert len({base_id, _base(skills_injected="bank-digest"),
                _base(skills_injected="bank-digest-2")}) == 3


def test_make_gap_sheet_id_encodes_the_unit_and_attempt():
    assert unit_ids.make_gap_sheet_id("unit-1", 0) == "gap:unit-1:0"
    assert unit_ids.make_gap_sheet_id("unit-1", 3) == "gap:unit-1:3"
    assert unit_ids.make_gap_sheet_id("unit-1", None) == "gap:unit-1:None"


def test_a_long_gap_sheet_id_still_fits_a_filename():
    exam_id = ("traj/alfworld:json_2.1.1/train/"
               "pick_and_place_simple-ToiletPaper-None-ToiletPaperHanger-415/"
               "trial_T20190908_050443_333939/game.tw-pddl/strong/00/"
               "seg01/skill/v01/exam")
    unit = unit_ids.make_unit_id(
        phase_label="p03:exam", benchmark="alfworld", task_ref=exam_id,
        role="weak", skills_injected=None)
    gap = unit_ids.make_gap_sheet_id(unit, 0)

    name = urllib.parse.quote(gap, safe="") + ".json"
    assert len(name.encode()) + 14 <= 255  # the atomic-write temp suffix
    # Same pair, same id; a different attempt is a different id.
    assert unit_ids.make_gap_sheet_id(unit, 0) == gap
    assert unit_ids.make_gap_sheet_id(unit, 1) != gap
    # Two long units that share a head do not collide.
    other = unit_ids.make_unit_id(
        phase_label="p03:exam", benchmark="alfworld",
        task_ref=exam_id.replace("seg01", "seg02"),
        role="weak", skills_injected=None)
    assert unit_ids.make_gap_sheet_id(other, 0) != gap


def test_unit_id_for_trajectory_reads_task_role_and_injection():
    trajectory = Trajectory(
        trajectory_id="traj/tb:task-1/weak/00",
        task_id="tb:task-1",
        benchmark="tb",
        model_role="weak",
        model_name="model-a",
        attempt_index=0,
        skills_injected="bank:s1v2",
        steps=[Step(action="ls", observation="ok")],
        correctness=True,
    )
    assert unit_ids.unit_id_for_trajectory(
        phase_label="p03:live", benchmark="tb", trajectory=trajectory) == \
        unit_ids.make_unit_id(
            phase_label="p03:live", benchmark="tb",
            task_ref="tb:task-1", role="weak",
            skills_injected="bank:s1v2")
