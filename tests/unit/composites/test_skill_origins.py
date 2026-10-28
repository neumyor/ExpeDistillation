"""composites.skill_origins: the per-phase skill-origin ledger.

Covers the write (one row per ``(skill_id, version)``, directory created
on demand), the newest-phase-first lookup, and the ``source_ref`` pointer
shape. No models, no network (AGENTS.md section 8).
"""

import json

import pytest

from expedistill.composites.skill_origins import (
    SKILL_ORIGINS_NAME,
    append_origin,
    find_origin,
    source_ref,
)

SKILL = "traj/tb:t1/strong/00/seg00/skill"


def _origin(version=2, phase_label="p02:learn", note="first version"):
    return dict(
        skill_id=SKILL,
        version=version,
        phase_label=phase_label,
        change_note=note,
        context_text="the weak run missed the cd step",
        source_ref=source_ref(phase_label, unit_id="u-1", scene_step=3),
    )


def _ledger(phase_dir):
    return phase_dir / SKILL_ORIGINS_NAME


class TestAppendOrigin:
    def test_writes_one_row_and_creates_the_directory(self, tmp_path):
        phase_dir = tmp_path / "phases" / "p02_learn"
        append_origin(phase_dir, **_origin())
        lines = _ledger(phase_dir).read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {
            "skill_id": SKILL,
            "version": 2,
            "phase_label": "p02:learn",
            "change_note": "first version",
            "context_text": "the weak run missed the cd step",
            "source_ref": {"phase_label": "p02:learn", "unit_id": "u-1",
                           "scene_step": 3},
        }

    def test_same_skill_version_is_a_noop(self, tmp_path):
        phase_dir = tmp_path / "phases" / "p02_learn"
        append_origin(phase_dir, **_origin(note="first version"))
        append_origin(phase_dir, **_origin(note="a later rewrite"))
        rows = [json.loads(line) for line in
                _ledger(phase_dir).read_text().splitlines()]
        assert len(rows) == 1
        # The first row is the kept evidence, never the re-run's rewrite.
        assert rows[0]["change_note"] == "first version"

    def test_a_new_version_appends_and_other_skills_do_not_collide(self,
                                                                   tmp_path):
        phase_dir = tmp_path / "phases" / "p02_learn"
        append_origin(phase_dir, **_origin(version=1))
        append_origin(phase_dir, **_origin(version=2))
        other = _origin(version=1)
        other["skill_id"] = f"{SKILL}2"
        append_origin(phase_dir, **other)
        assert len(_ledger(phase_dir).read_text().splitlines()) == 3

    def test_existing_rows_survive_a_resume(self, tmp_path):
        phase_dir = tmp_path / "phases" / "p02_learn"
        _ledger(phase_dir).parent.mkdir(parents=True)
        _ledger(phase_dir).write_text('{"skill_id": "other", "version": 1}\n')
        append_origin(phase_dir, **_origin())
        rows = [json.loads(line) for line in
                _ledger(phase_dir).read_text().splitlines()]
        assert rows[0] == {"skill_id": "other", "version": 1}
        assert rows[1]["skill_id"] == SKILL


class TestFindOrigin:
    def test_returns_the_recorded_row(self, tmp_path):
        append_origin(tmp_path / "phases" / "p02_learn", **_origin())
        row = find_origin(tmp_path, SKILL, 2)
        assert row is not None
        assert row["change_note"] == "first version"
        assert row["source_ref"]["scene_step"] == 3

    def test_unknown_skill_or_version_is_none(self, tmp_path):
        append_origin(tmp_path / "phases" / "p02_learn", **_origin())
        assert find_origin(tmp_path, SKILL, 99) is None
        assert find_origin(tmp_path, "some/other/skill", 2) is None

    def test_missing_phases_tree_is_none(self, tmp_path):
        assert find_origin(tmp_path / "run", SKILL, 1) is None

    def test_newest_phase_wins_by_numeric_directory_order(self, tmp_path):
        # p10 must sort after p09 (numeric, not lexicographic); both carry
        # the same (skill_id, version) to prove which one is read first.
        append_origin(tmp_path / "phases" / "p09_learn", **_origin(
            phase_label="p09:learn", note="the stale row"))
        append_origin(tmp_path / "phases" / "p10_learn", **_origin(
            phase_label="p10:learn", note="the fresh row"))
        row = find_origin(tmp_path, SKILL, 2)
        assert row["phase_label"] == "p10:learn"
        assert row["change_note"] == "the fresh row"

    def test_skips_a_newer_phase_without_the_version(self, tmp_path):
        append_origin(tmp_path / "phases" / "p02_learn", **_origin())
        # The newest phase recorded another skill version only.
        append_origin(tmp_path / "phases" / "p03_learn",
                      **_origin(version=7, phase_label="p03:learn"))
        assert find_origin(tmp_path, SKILL, 2)["phase_label"] == "p02:learn"

    def test_directory_outside_the_pnn_shape_sorts_last(self, tmp_path):
        append_origin(tmp_path / "phases" / "p01_learn", **_origin(
            phase_label="p01:learn", note="numbered"))
        append_origin(tmp_path / "phases" / "scratch", **_origin(
            phase_label="scratch", note="unnumbered"))
        assert find_origin(tmp_path, SKILL, 2)["change_note"] == "numbered"


class TestSourceRef:
    def test_keeps_every_given_pointer(self):
        assert source_ref("p11:test", unit_id="u-1", sheet_id="s-2",
                          scene_step=4) == {
            "phase_label": "p11:test", "unit_id": "u-1", "sheet_id": "s-2",
            "scene_step": 4}

    def test_omits_none_keys_and_defaults_step_to_zero(self):
        assert source_ref("p11:test") == {
            "phase_label": "p11:test", "scene_step": 0}
        assert source_ref("p11:test", sheet_id="s-2") == {
            "phase_label": "p11:test", "sheet_id": "s-2", "scene_step": 0}

    @pytest.mark.parametrize("step", [-1, 1.5, "3", None, True])
    def test_rejects_anything_but_a_non_negative_int(self, step):
        with pytest.raises(ValueError, match="scene_step"):
            source_ref("p11:test", scene_step=step)
