"""Every learn phase ends with its own loadable bank artifact.

Covers the ``phases/<label>/`` layout and its label validation, the
artifact :func:`write_phase_bank` writes (every stored skill, discarded
ones included, in skill-id order, with a summary that agrees with the
rows), the isolation of the run-level ``skill_bank.jsonl`` and the second
call being a pure rewrite.
"""

import pytest

from expedistill.composites.phase_bank import (
    load_phase_bank,
    load_phase_bank_summary,
    phase_bank_dir,
    phase_bank_path,
    write_phase_bank,
)
from expedistill.core import ids
from expedistill.core.models import Skill
from expedistill.infra.reliability import ConfigError as InfraConfigError
from expedistill.infra.store import Store, read_jsonl, write_jsonl
from expedistill.inject.skills import bank_reference
from tests.helpers.skills import make_skill


def _add(store: Store, serial: int, status: str = "active") -> Skill:
    skill = make_skill(ids.make_type_id(serial), f"card {serial}",
                       status=status)
    store.save(skill, "skills")
    return skill


# ---------------------------------------------------------------------------
# Layout and label validation
# ---------------------------------------------------------------------------

def test_phase_dir_maps_the_label_to_a_directory(tmp_path):
    assert phase_bank_dir(tmp_path, "p01:learn") == (tmp_path / "phases"
                                                     / "p01_learn")
    assert phase_bank_dir(tmp_path, "p00:freeze") == (tmp_path / "phases"
                                                      / "p00_freeze")
    assert phase_bank_path(tmp_path, "p02:x") == (tmp_path / "phases" / "p02_x"
                                                 / "skill_bank.jsonl")
    # The colon rewrite cannot make two legal labels collide.
    assert phase_bank_dir(tmp_path, "p1:a") != phase_bank_dir(tmp_path, "p1:a_a")
    assert phase_bank_dir(tmp_path, "p2:a") != phase_bank_dir(tmp_path, "p1:a")


@pytest.mark.parametrize("label", ["", "..", ".", "a/b", "a b", "p1:name!",
                                   "p1:\\", "p1\n", "p1", "p1:",
                                   "p1:a:b", "x1:learn", "p1_a"])
def test_unsafe_phase_labels_are_refused(tmp_path, label):
    # The label must be exactly 'pNN:<name>': a bare 'pNN' or the
    # colonless 'p1_a' would collide with the legal 'p1:a' once the
    # colon is mapped, so both are refused up front.
    with pytest.raises(InfraConfigError, match="pNN:<name>"):
        phase_bank_dir(tmp_path, label)


def test_loading_a_missing_artifact_names_the_phase(tmp_path):
    with pytest.raises(InfraConfigError, match="p99:never"):
        load_phase_bank(tmp_path, "p99:never")
    with pytest.raises(InfraConfigError, match="p99:never"):
        load_phase_bank_summary(tmp_path, "p99:never")


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------

def test_the_artifact_carries_every_stored_skill_in_id_order(tmp_path):
    store = Store(tmp_path)
    second = _add(store, 2)
    first = _add(store, 1, status="discarded")
    summary = write_phase_bank(store, tmp_path, "p02:learn")
    rows = load_phase_bank(tmp_path, "p02:learn")
    assert [row["skill_id"] for row in rows] == [first.skill_id,
                                                 second.skill_id]
    assert [row["status"] for row in rows] == ["discarded", "active"]
    assert rows == [first.to_dict(), second.to_dict()]
    assert summary == load_phase_bank_summary(tmp_path, "p02:learn")
    assert summary == {"members": [first.skill_id, second.skill_id],
                       "size": 2, "bank_reference": bank_reference(rows)}


def test_an_empty_store_still_writes_an_artifact(tmp_path):
    summary = write_phase_bank(Store(tmp_path), tmp_path, "p02:learn")
    assert load_phase_bank(tmp_path, "p02:learn") == []
    assert summary["members"] == [] and summary["size"] == 0


def test_a_second_call_is_a_pure_rewrite(tmp_path):
    store = Store(tmp_path)
    _add(store, 1)
    write_phase_bank(store, tmp_path, "p02:learn")
    path = phase_bank_path(tmp_path, "p02:learn")
    before = path.read_bytes()
    write_phase_bank(store, tmp_path, "p02:learn")
    assert path.read_bytes() == before


def test_each_learn_phase_keeps_the_bank_of_its_own_round(tmp_path):
    store = Store(tmp_path)
    _add(store, 1)
    write_phase_bank(store, tmp_path, "p02:learn")
    _add(store, 2)
    write_phase_bank(store, tmp_path, "p04:learn")
    assert len(load_phase_bank(tmp_path, "p02:learn")) == 1
    assert len(load_phase_bank(tmp_path, "p04:learn")) == 2


def test_the_run_level_bank_is_left_alone(tmp_path):
    store = Store(tmp_path)
    run_level = tmp_path / "skill_bank.jsonl"
    write_jsonl(run_level, [{"skill_id": "import/x/1"}])
    _add(store, 1)
    write_phase_bank(store, tmp_path, "p02:learn")
    assert read_jsonl(run_level) == [{"skill_id": "import/x/1"}]
