"""The execute phase's playbook switch on delivery rows (``units.with_playbook``).

Every delivery carries the playbook the same way: the block starts with
the playbook, and a folder manifest carries it as ``playbook_text`` (the
folder runners render it from there) -- an empty bank's manifest
included.
"""

import pytest

from expedistill.core import ids
from expedistill.inject import format_playbook_block
from expedistill.methods import resolve_fixed
from expedistill.methods.delivery import units
from tests.helpers.skills import make_skill

PLAYBOOK = "Check the result against the task text before finishing."


def _bank():
    return [make_skill(ids.make_type_id(index + 1), f"when {index}",
                       summary=f"card {index}") for index in range(2)]


def _rows(name, bank=None):
    driver, params = resolve_fixed("delivery", name)
    return driver.resolve(_bank() if bank is None else bank, ["tb:t9"],
                          params)


@pytest.mark.parametrize("name", ["whole_bank", "none", "folder"])
def test_the_block_starts_with_the_playbook(name):
    plain = _rows(name)
    shown = units.with_playbook(plain, PLAYBOOK)
    head = format_playbook_block(PLAYBOOK)
    assert head.startswith("Global playbook:\n" + PLAYBOOK)
    for before, after in zip(plain, shown):
        assert after.skills_block == head + before.skills_block
        assert after.unit_id == before.unit_id


@pytest.mark.parametrize("bank", [None, []], ids=["cards", "empty"])
def test_a_folder_manifest_carries_the_playbook(bank):
    shown = units.with_playbook(_rows("folder", bank), PLAYBOOK)
    manifest = shown[0].aux["skill_folder"]
    assert manifest["playbook_text"] == PLAYBOOK
    assert "playbook_text" not in _rows("folder", bank)[0].aux[
        "skill_folder"]


def test_an_empty_playbook_leaves_the_rows_as_they_are():
    plain = _rows("folder")
    assert units.with_playbook(plain, "  ") == plain
