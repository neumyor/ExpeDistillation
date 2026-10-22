"""The skill-folder M2 delivery tests: catalog rendering, the manifest,
the whole_bank-parity bookkeeping and the load-time C-role boundary.

Milestone map (the 2026-10-17 alignment ruling): M1 added the Skill
``summary`` field; M2 delivers the ``folder`` method -- a pure
``resolve`` that renders the catalog text and the folder manifest into
the unit rows (the composite-layer materialization lives in
composites/skill_folder.py and has its own test file). The M3 pairing
(real_folder execution, plugin capability bits) is not in the table
yet; here the folder play ends at the unit row.
"""

import pytest

from expedistill.benchmarks.base import Task
from expedistill.composites.decision import reuse_key, reusable_units
from expedistill.composites.verdicts import make_unit_id
from expedistill.core.config import (
    ConfigError,
    FOLDER_DELIVERY_NAME,
    PHASE_ROLE_TEST,
    parse_phases,
)
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.ids import (
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill
from expedistill.inject import bank_reference
from expedistill.methods import resolve_fixed, resolve_params
from expedistill.methods.delivery import FolderParams, WholeBankParams
from expedistill.methods.delivery import folder as folder_driver
from expedistill.methods.delivery.folder import (
    CATALOG_FILENAME,
    render_catalog,
    skill_filename,
    skill_folder_manifest,
)
from tests.helpers.skills import make_skill

BENCHMARK = "tb"

MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "Check the log file before restarting anything.",
    "[Steps]",
    "1. Check the service status.",
    "[Verify] The status command reports running.",
    "[Do not use when] The status command already reports running.",
])


def _skill(task_raw: str, *, summary: str = "", status: str = "accepted",
           content: str = MARKED) -> Skill:
    task_id = make_task_id(BENCHMARK, task_raw)
    subtask_id = make_subtask_id(make_trajectory_id(task_id, "strong", 0), 0)
    return make_skill(subtask_id, content, summary=summary, status=status)


def _bank():
    """One full-membership phase artifact: two accepted, one pending."""
    accepted_a = _skill("t1", summary="Restart a stuck service safely.")
    accepted_b = _skill("t2", summary="Check the log before restarting.")
    pending = _skill("t3", summary="A pending skill, never delivered.",
                     status="pending")
    return [accepted_a, accepted_b, pending], [accepted_a, accepted_b]


def _tasks(*raws):
    return [make_task_id(BENCHMARK, raw) for raw in raws]


# ---------------------------------------------------------------------------
# The catalog rendering
# ---------------------------------------------------------------------------

def test_the_catalog_opens_with_the_usage_rules():
    catalog = render_catalog([_skill("t1", summary="Restart safely.")])
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


def test_an_empty_summary_falls_back_to_the_when_to_use_section():
    skill = _skill("t1", summary="")  # the v2-migration shape
    catalog = render_catalog([skill], description_mode="summary")
    entry = catalog.splitlines()[-1]
    # The section body (marker-line remainder plus continuation lines)
    # collapsed to one line; [Steps] never leaks into the description.
    assert entry.endswith(
        "| The service rejects connections. Check the log file before "
        "restarting anything.")


def test_a_retired_failure_reference_gets_the_explicit_placeholder():
    """The 2026-10-19 ruling: only [When to use] is a fallback; a
    pre-ruling [Trigger] skill is not migrated and reads as markerless."""
    content = "\n".join([
        "[Trigger] The model restarts the service blindly.",
        "[Why it fails] The lock file is still held.",
        "[Do instead] Check the lock file, then restart.",
        "[Do not use when] The status command already reports running.",
    ])
    skill = _skill("t1", summary="", content=content)
    entry = render_catalog([skill], description_mode="summary").splitlines()[-1]
    assert entry.endswith("| (no summary)")


def test_a_markerless_skill_gets_the_explicit_placeholder():
    skill = _skill("t1", content="Just some prose with no sections.")
    catalog = render_catalog([skill], description_mode="summary")
    assert catalog.splitlines()[-1].endswith("| (no summary)")


def test_a_k5_length_summary_is_clipped_and_marked():
    long = _skill("t1", summary="a" * 250)
    entry = render_catalog([long], description_mode="summary").splitlines()[-1]
    # The clip is explicit, never silent: 200 chars plus the mark.
    assert entry.endswith("a" * 200 + " [truncated]")
    exact = _skill("t1", summary="a" * 200)
    assert render_catalog(
        [exact], description_mode="summary").splitlines()[-1].endswith(
            "| " + "a" * 200)
    short = _skill("t1", summary="a" * 201)
    assert render_catalog(
        [short], description_mode="summary").splitlines()[-1].endswith(
            "a" * 200 + " [truncated]")


def test_the_three_description_modes():
    skill = _skill("t1", summary="Restart safely.")
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

def test_the_manifest_carries_the_accepted_files_and_the_bank_fingerprint():
    bank, accepted = _bank()
    manifest = skill_folder_manifest(bank)
    assert manifest["catalog_filename"] == CATALOG_FILENAME
    assert manifest["catalog_text"] == render_catalog(accepted)
    assert manifest["description_mode"] == "summary_then_when_to_use"
    # The fingerprint is the phase artifact's bank-summary digest: over
    # the FULL bank (pending included), never a new version number.
    assert manifest["bank_fingerprint"] == bank_reference(bank)
    # The rev-1 ruling: the file list is a NAME MAPPING -- no content
    # travels with the manifest (progressive disclosure survives every
    # stage; the bodies' only source is the phase bank).
    assert manifest["files"] == [
        {"skill_id": skill.skill_id, "version": skill.version,
         "filename": skill_filename(skill.skill_id)}
        for skill in accepted]


def test_the_rows_mirror_whole_banks_accepted_bookkeeping():
    bank, accepted = _bank()
    tasks = _tasks("t1", "t2")
    folder_rows = folder_driver.resolve(bank, tasks, FolderParams())
    whole_rows = resolve_fixed("delivery", "whole_bank")[0].resolve(
        bank, tasks, WholeBankParams())
    # Same accepted-set digest, same unit ids, input task order -- the
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


def test_an_empty_accepted_bank_renders_no_catalog():
    pending = _skill("t3", status="pending")
    rows = folder_driver.resolve([pending], _tasks("t1"), FolderParams())
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
    left = _skill("t1", summary="Left.")
    left.skill_id = "a:b"
    right = _skill("t2", summary="Right.")
    right.skill_id = "a/b"
    with pytest.raises(ValueError, match="silently"):
        skill_folder_manifest([left, right])


def test_a_skill_named_catalog_gets_its_own_directory():
    # The catalog file is catalog.md at the root; a skill id "catalog"
    # maps to catalog/SKILL.md and does not collide with it.
    skill = _skill("t1", summary="Clash.")
    skill.skill_id = "catalog"
    manifest = skill_folder_manifest([skill])
    assert manifest["files"][0]["filename"] == "catalog/SKILL.md"


# ---------------------------------------------------------------------------
# The join with the reuse plumbing (end to end)
# ---------------------------------------------------------------------------

def test_folder_unit_ids_join_the_reuse_plumbing():
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
        assert reuse_key(unit).startswith(row.task_ref)
    # The same folder again reuses: nothing re-executes under rule 1.
    assert reusable_units(units, units) == \
        {unit.unit_id: unit.unit_id for unit in units}
    # A changed folder does not: a different catalog block is a
    # different measurement.
    changed = [ExecutionUnitRow(
        unit_id=units[0].unit_id, task_ref=units[0].task_ref,
        skills_injected=units[0].skills_injected,
        skills_block="a changed catalog", aux={})]
    assert reusable_units(changed, units) == {}


# ---------------------------------------------------------------------------
# The load-time boundary: folder is a test-phase (test role) delivery
# ---------------------------------------------------------------------------

_LEARN = {
    "kind": "learn",
    "evidence": {"name": "segment", "K": 5, "retry_limit": 2},
    "author": {"name": "distill_diagnose"},
    "curate": {"name": "none", "token_budget": 4096},
}


def _execute(role, tasks, delivery, execution=None):
    raw = {"kind": "execute", "role": role, "tasks": tasks,
           "delivery": delivery,
           "execution": execution
           or {"name": "exam" if role == "exam" else "real"}}
    if role == "baseline":
        raw["execution"]["arms"] = ["executor_strong", "executor_weak"]
    return raw


def _parse_with(role_phase, sequence):
    phases = {"baseline": _execute("baseline", "train", {"name": "none"}),
              "learn": _LEARN}
    phases.update(role_phase)
    return parse_phases(phases, sequence, n_attempts_weak=1)


def test_a_test_role_phase_may_deliver_the_folder():
    # Since M3 the folder delivery pairs with the real_folder execution
    # (checklist item 8's pairing rule), so the test phase parses with it.
    specs = _parse_with(
        {"test": _execute("test", "test", {"name": FOLDER_DELIVERY_NAME},
                       execution={"name": "real_folder"})},
        ["baseline", "learn", "test"])
    assert specs["test"].delivery["name"] == FOLDER_DELIVERY_NAME
    assert specs["test"].role == PHASE_ROLE_TEST


@pytest.mark.parametrize("role,tasks", [
    ("exam", "train"),       # A
    ("live", "train"),       # B
    ("baseline", "train"),   # D
])
def test_the_other_roles_refuse_the_folder(role, tasks):
    with pytest.raises(ConfigError,
                       match=r"legal only for role 'test' \(the test phase"):
        _parse_with(
            {"X": _execute(role, tasks, {"name": FOLDER_DELIVERY_NAME})},
            ["baseline", "learn", "X"])


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


def test_inline_catalog_is_no_longer_accepted():
    with pytest.raises(ConfigError, match="unknown parameter"):
        resolve_params("delivery", {"name": FOLDER_DELIVERY_NAME,
                                    "inline_catalog": True})


def test_an_unknown_delivery_name_lists_the_folder():
    with pytest.raises(ConfigError, match="known: \\['whole_bank'"):
        resolve_params("delivery", {"name": "folders"})
    error = ""
    try:
        resolve_params("delivery", {"name": "shelf"})
    except ConfigError as exc:
        error = str(exc)
    assert FOLDER_DELIVERY_NAME in error


def test_folder_resolves_task_objects_too():
    # The phase driver hands Task objects over; the catalog stays the
    # same fixed pair while the task refs come from the objects.
    bank, _accepted = _bank()
    tasks = [Task(task_id="t1", instruction="restart the service"),
             Task(task_id="t2", instruction="check the log")]
    rows = folder_driver.resolve(bank, tasks, FolderParams())
    assert [row.task_ref for row in rows] == ["t1", "t2"]
