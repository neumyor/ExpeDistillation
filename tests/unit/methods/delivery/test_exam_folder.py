"""The exam_folder delivery: the generalization exam's read-only catalog.

The exam (docs/decisions/generalization_exam_20261027.md) shows the
weak model a catalog of the WHOLE bank and lets it open skills itself,
so the delivery differs from ``folder`` in two pinned ways: every member
(pending included) appears in the catalog, and nothing is materialized
on disk. The catalog TEXT is deliberately the same layer-1 rendering
``folder`` produces, so a skill is described with the same words in
both plays.
"""

import pytest

from expedistill.core.ids import (
    make_exam_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.inject import bank_reference
from expedistill.inject.skill_text import DEFAULT_DESCRIPTION_MODE
from expedistill.methods.delivery import ExamFolderParams, FolderParams
from expedistill.methods.delivery import exam_folder as exam_folder_driver
from expedistill.methods.delivery import folder as folder_driver
from expedistill.methods.delivery.folder import (
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
    "[Do not use when] The status command already reports running.",
])


def _skill(task_raw: str, *, summary: str = "", status: str = "accepted"):
    task_id = make_task_id(BENCHMARK, task_raw)
    subtask_id = make_subtask_id(make_trajectory_id(task_id, "strong", 0), 0)
    return make_skill(subtask_id, MARKED, summary=summary, status=status)


def _bank():
    """One phase artifact: two accepted members and one pending."""
    return [_skill("t1", summary="Restart a stuck service safely."),
            _skill("t2", summary="Check the log before restarting."),
            _skill("t3", summary="A pending skill the exam must show.",
                   status="pending")]


def _exams(*skills):
    return [make_exam_id(skill.skill_id, skill.version) for skill in skills]


def test_one_row_per_exam_in_input_order():
    bank = _bank()
    exams = _exams(*bank)
    rows = exam_folder_driver.resolve(bank, exams, ExamFolderParams())
    assert [row.task_ref for row in rows] == exams
    # One row per exam, each with its own join key.
    assert len({row.unit_id for row in rows}) == len(exams)


def test_the_catalog_covers_accepted_and_pending_members():
    bank = _bank()
    rows = exam_folder_driver.resolve(bank, _exams(bank[0]),
                                      ExamFolderParams())
    manifest = rows[0].aux["skill_folder"]
    # accepted_only=False: the exam's universe is the whole bank, in
    # bank order, pending included.
    assert [entry["skill_id"] for entry in manifest["files"]] == \
        [skill.skill_id for skill in bank]
    for skill in bank:
        assert skill.skill_id in rows[0].skills_block


def test_the_catalog_text_is_the_folder_catalogs():
    bank = _bank()
    rows = exam_folder_driver.resolve(bank, _exams(bank[0]),
                                      ExamFolderParams())
    manifest = rows[0].aux["skill_folder"]
    assert manifest["catalog_text"] == render_catalog(bank)
    assert manifest["catalog_text"] == \
        skill_folder_manifest(bank, accepted_only=False)["catalog_text"]
    # ...and a bank with nothing pending renders exactly the block the
    # folder delivery renders for the same skills.
    accepted = bank[:2]
    folder_rows = folder_driver.resolve(accepted, ["task-a"],
                                        FolderParams())
    exam_rows = exam_folder_driver.resolve(
        accepted, _exams(accepted[0]), ExamFolderParams())
    assert exam_rows[0].skills_block == folder_rows[0].skills_block
    assert [entry["filename"] for entry in manifest["files"]] == \
        [skill_filename(skill.skill_id) for skill in bank]


def test_skills_injected_summarises_the_full_bank():
    bank = _bank()
    rows = exam_folder_driver.resolve(bank, _exams(bank[0]),
                                      ExamFolderParams())
    # The digest names the FULL bank (the same one the phase's bank
    # summary writes), never the accepted subset.
    assert rows[0].skills_injected == bank_reference(bank)
    assert rows[0].skills_injected != bank_reference(bank[:2])


def test_every_row_shares_the_phase_level_manifest():
    bank = _bank()
    rows = exam_folder_driver.resolve(bank, _exams(*bank),
                                      ExamFolderParams())
    assert len({id(row.aux["skill_folder"]) for row in rows}) == 1
    assert rows[0].aux["skill_folder"] == \
        skill_folder_manifest(bank, accepted_only=False)


def test_the_description_mode_reaches_the_catalog():
    bank = _bank()
    rows = exam_folder_driver.resolve(
        bank, _exams(bank[0]),
        ExamFolderParams(description_mode="summary"))
    manifest = rows[0].aux["skill_folder"]
    assert manifest["description_mode"] == "summary"
    entry = render_catalog(bank, description_mode="summary").splitlines()[-3]
    assert entry.endswith("| Restart a stuck service safely.")
    assert "Use when:" not in rows[0].skills_block


def test_the_params_default_to_the_folder_catalog_mode():
    assert ExamFolderParams().description_mode == DEFAULT_DESCRIPTION_MODE


def test_an_unknown_description_mode_is_refused():
    # One validation rule for both catalogs: the folder module's.
    with pytest.raises(ValueError, match="description_mode"):
        ExamFolderParams(description_mode="unknown")
