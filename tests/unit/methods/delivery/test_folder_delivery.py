"""The skill-folder M2 delivery tests: catalog rendering, the manifest,
the whole_bank-parity bookkeeping and the load-time C-role boundary.

The ``folder`` method is a pure ``resolve`` that renders the catalog
text and the folder manifest into the unit rows (the composite-layer
materialization lives in composites/skill_materialize.py and has its own
test file); here the folder play ends at the unit row. Only the bank's
active cards are delivered.
"""

import pytest

from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.config import (
    ConfigError,
    FOLDER_DELIVERY_NAME,
)
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.ids import make_task_id, make_type_id
from expedistill.core.models import Skill
from expedistill.inject.skills import bank_reference
from expedistill.methods.registry import resolve_fixed, resolve_params
from expedistill.methods.delivery.params import FolderParams, WholeBankParams
from expedistill.methods.delivery import folder_delivery as folder_driver
from expedistill.methods.delivery.folder_delivery import (
    CATALOG_FILENAME,
    render_catalog,
    skill_dirname,
    skill_filename,
    skill_folder_manifest,
)
from tests.helpers.skills import make_skill

BENCHMARK = "tb"

MARKED = {
    "when_to_use": "The service rejects connections.\n"
                   "Check the log file before restarting anything.",
    "steps": ["Check the service status."],
    "not_applicable_when": "The status command already reports running.",
}


def _skill(serial: int, *, summary: str = "A service skill.",
           status: str = "active") -> Skill:
    return make_skill(make_type_id(serial), **MARKED, summary=summary,
                      status=status)


def _row(serial: int, **fields) -> dict:
    """A bank row with fields a stored Skill cannot hold (an empty
    summary or trigger): the catalog renders rows as well as skills."""
    return {**_skill(serial).to_dict(), **fields}


def _bank():
    """One full-membership phase artifact: two active, one discarded."""
    active_a = _skill(1, summary="Restart a stuck service safely.")
    active_b = _skill(2, summary="Check the log before restarting.")
    discarded = _skill(3, summary="A discarded skill, never delivered.",
                       status="discarded")
    return [active_a, active_b, discarded], [active_a, active_b]


def _tasks(*raws):
    return [make_task_id(BENCHMARK, raw) for raw in raws]


# ---------------------------------------------------------------------------
# The catalog rendering
# ---------------------------------------------------------------------------

def test_the_catalog_opens_with_the_usage_rules():
    catalog = render_catalog([_skill(1, summary="Restart safely.")])
    lines = catalog.splitlines()
    assert lines[0] == "## Skill folder catalog (fallback)"
    rules = [line for line in lines if line.startswith("- ")]
    # The catalog is a fallback: two usage rules remain (cat on demand,
    # do not open many files at once). The prompt's <available_skills>
    # list is the primary directory.
    assert len(rules) == 2
    assert "cat only the skill file" in rules[0]
    assert "Do not open many skill files at once" in rules[1]
    assert "available_skills" in "\n".join(lines)
    assert catalog.endswith("\n")


def test_one_line_per_skill_in_bank_order():
    (accepted_a, accepted_b) = _bank()[1]
    catalog = render_catalog([accepted_a, accepted_b],
                             description_mode="summary")
    entries = catalog.splitlines()[-2:]
    assert entries == [
        f"{accepted_a.skill_id} | {skill_filename(accepted_a.skill_id)} "
        f"| Restart a stuck service safely.",
        f"{accepted_b.skill_id} | {skill_filename(accepted_b.skill_id)} "
        f"| Check the log before restarting.",
    ]


def test_the_file_name_is_a_skill_directory_with_skill_md():
    assert skill_filename("tb:t1/a00/seg00/skill") == \
        "tb-t1-a00-seg00-skill/SKILL.md"
    assert skill_filename("bank/merge/0001") == "bank-merge-0001/SKILL.md"
    assert skill_filename("plain") == "plain/SKILL.md"
    with pytest.raises(ValueError, match="non-empty string skill_id"):
        skill_filename("")


def test_a_long_skill_id_gets_a_bounded_directory_name():
    name = skill_dirname("x" * 200)
    assert len(name) == 64
    assert name.startswith("x" * 55)
    assert skill_dirname("x" * 200) == name  # deterministic


def test_a_reserved_word_in_the_directory_name_is_refused():
    with pytest.raises(ValueError, match="reserved word"):
        skill_dirname("claude/helper")
    with pytest.raises(ValueError, match="reserved word"):
        skill_dirname("Anthropic/helper")


def test_an_empty_summary_falls_back_to_the_when_to_use_field():
    catalog = render_catalog([_row(1, summary="")],
                             description_mode="summary")
    entry = catalog.splitlines()[-1]
    # The trigger collapsed to one line; the steps never leak into the
    # description.
    assert entry.endswith(
        "| The service rejects connections. Check the log file before "
        "restarting anything.")


def test_no_summary_and_no_trigger_gets_the_explicit_placeholder():
    row = _row(1, summary="", when_to_use="")
    catalog = render_catalog([row], description_mode="summary")
    assert catalog.splitlines()[-1].endswith("| (no summary)")


def test_a_long_description_is_kept_whole_in_the_catalog():
    # A clipped trigger reads as a different rule: the catalog line keeps
    # the whole description (the length budget lives in the G2 prompts).
    long = _skill(1, summary="a" * 450)
    entry = render_catalog([long], description_mode="summary").splitlines()[-1]
    assert entry.endswith("| " + "a" * 450)
    assert "[truncated]" not in entry


def test_the_three_description_modes():
    skill = _skill(1, summary="Restart safely.")
    trigger = "The service rejects connections. Check the log file "\
              "before restarting anything."
    summary_entry = render_catalog(
        [skill], description_mode="summary").splitlines()[-1]
    assert summary_entry.endswith("| Restart safely.")
    trigger_entry = render_catalog(
        [skill], description_mode="when_to_use").splitlines()[-1]
    assert trigger_entry.endswith("| " + trigger)
    combined = render_catalog(
        [skill], description_mode="summary_then_when_to_use").splitlines()[-1]
    assert combined.endswith("| Restart safely. Use when: " + trigger)


# ---------------------------------------------------------------------------
# The manifest and the resolved rows
# ---------------------------------------------------------------------------

def test_the_manifest_carries_the_active_files_and_the_bank_fingerprint():
    bank, accepted = _bank()
    manifest = skill_folder_manifest(bank)
    assert manifest["catalog_filename"] == CATALOG_FILENAME
    assert manifest["catalog_text"] == render_catalog(accepted)
    assert manifest["description_mode"] == "summary_then_when_to_use"
    # The fingerprint is the phase artifact's bank-summary digest: over
    # the FULL bank (discarded included), never a new version number.
    assert manifest["bank_fingerprint"] == bank_reference(bank)
    # The rev-1 ruling: the file list is a NAME MAPPING -- no content
    # travels with the manifest (progressive disclosure survives every
    # stage; the bodies' only source is the phase bank).
    assert manifest["files"] == [
        {"skill_id": skill.skill_id, "version": skill.version,
         "filename": skill_filename(skill.skill_id)}
        for skill in accepted]


def test_the_rows_mirror_whole_banks_active_bookkeeping():
    bank, accepted = _bank()
    tasks = _tasks("t1", "t2")
    folder_rows = folder_driver.resolve(bank, tasks, FolderParams())
    whole_rows = resolve_fixed("delivery", "whole_bank")[0].resolve(
        bank, tasks, WholeBankParams())
    # Same active-set digest, same unit ids, input task order -- the
    # reuse and decision plumbing cannot tell the bookkeeping apart.
    assert [row.unit_id for row in folder_rows] == \
        [row.unit_id for row in whole_rows]
    assert [row.skills_injected for row in folder_rows] == \
        [row.skills_injected for row in whole_rows]
    assert [row.task_ref for row in folder_rows] == tasks
    # ...but the block is the catalog, not the D9 whole-bank block.
    assert all(row.skills_block == render_catalog(accepted)
               for row in folder_rows)
    assert folder_rows[0].skills_block != whole_rows[0].skills_block
    # Every row's aux names the same (phase-level, read-only) manifest.
    manifests = {id(row.aux["skill_folder"]) for row in folder_rows}
    assert len(manifests) == 1
    assert folder_rows[0].aux["skill_folder"] == \
        skill_folder_manifest(bank)


def test_an_empty_active_bank_renders_no_catalog():
    discarded = _skill(3, status="discarded")
    rows = folder_driver.resolve([discarded], _tasks("t1"), FolderParams())
    # The null-state lesson: an empty injection is the empty string,
    # never a header claiming experience exists.
    assert rows[0].skills_injected.startswith("bank:")
    assert rows[0].skills_block == ""
    assert rows[0].aux["skill_folder"]["files"] == []
    assert rows[0].aux["skill_folder"]["catalog_text"] == ""


def test_colliding_directory_names_are_refused_loudly():
    # The safe-alphabet mapping is not injective: two distinct ids can
    # map to one directory, and one file must not silently shadow the
    # other.
    left = _skill(1, summary="Left.")
    left.skill_id = "a:b"
    right = _skill(2, summary="Right.")
    right.skill_id = "a/b"
    with pytest.raises(ValueError, match="silently"):
        skill_folder_manifest([left, right])


def test_a_skill_named_catalog_gets_its_own_directory():
    # The catalog file is catalog.md at the root; a skill id "catalog"
    # maps to catalog/SKILL.md and does not collide with it.
    skill = _skill(1, summary="Clash.")
    skill.skill_id = "catalog"
    manifest = skill_folder_manifest([skill])
    assert manifest["files"][0]["filename"] == "catalog/SKILL.md"


# ---------------------------------------------------------------------------
# The delivery-side id against the frozen key
# ---------------------------------------------------------------------------

def test_folder_unit_ids_follow_the_frozen_key():
    bank, _accepted = _bank()
    rows = folder_driver.resolve(bank, _tasks("t1", "t2"), FolderParams())
    phase_label = "p03:test"
    units = [ExecutionUnitRow(
        unit_id=make_unit_id(phase_label=phase_label, benchmark=BENCHMARK,
                             task_ref=row.task_ref, role="weak",
                             skills_injected=row.skills_injected),
        task_ref=row.task_ref, skills_injected=row.skills_injected,
        skills_block=row.skills_block, aux={})
        for row in rows]
    for unit, row in zip(units, rows):
        # The delivery-side id's digest is exactly the one the full
        # frozen key carries: the phase driver rebuilding the id from
        # this row lands on the same key, so the join holds.
        digest = row.unit_id.rsplit(":", 1)[1]
        assert unit.unit_id == \
            f"p03:test:{BENCHMARK}:{row.task_ref}:weak:{digest}"


# ---------------------------------------------------------------------------
# The registry seam
# ---------------------------------------------------------------------------

def test_the_registry_resolves_the_folder_entry():
    driver, params = resolve_fixed("delivery", FOLDER_DELIVERY_NAME)
    assert driver is folder_driver
    assert params == FolderParams()
    assert resolve_params("delivery", {"name": FOLDER_DELIVERY_NAME}) == \
        FolderParams()


def test_description_mode_is_validated():
    with pytest.raises(ConfigError, match="description_mode"):
        FolderParams(description_mode="unknown")
