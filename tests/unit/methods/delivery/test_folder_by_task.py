"""The live folder delivery: one manifest per pending task group."""

import pytest

from expedistill.core.ids import (
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.methods.delivery import FolderParams
from expedistill.methods.delivery import folder_by_task

BENCHMARK = "tb"
TASK_ID = make_task_id(BENCHMARK, "t1")

MARKED = "\n".join([
    "[When to use] The service rejects connections.",
    "[Steps]",
    "1. Check the service status.",
    "[Do not use when] The status command already reports running.",
])


def _skill(segment):
    subtask_id = make_subtask_id(
        make_trajectory_id(TASK_ID, "strong", 0), segment)
    return Skill(skill_id=make_skill_id(subtask_id), content=MARKED,
                 summary=f"Skill {segment}.", subtask_id=subtask_id,
                 source_subtasks=[subtask_id], status="pending")


def test_one_folder_per_task_keeps_the_pending_group():
    skills = [_skill(0), _skill(1)]
    rows = folder_by_task.resolve(
        {TASK_ID: skills}, [TASK_ID], FolderParams(
            description_mode="when_to_use"))

    assert len(rows) == 1
    row = rows[0]
    manifest = row.aux["skill_folder"]
    assert [entry["skill_id"] for entry in manifest["files"]] == \
        [skill.skill_id for skill in skills]
    assert all(entry["skill_id"] in manifest["catalog_text"]
               for entry in manifest["files"])
    assert row.skills_block == manifest["catalog_text"]


def test_an_empty_group_is_refused():
    with pytest.raises(CodeBugError, match="no pending skill group"):
        folder_by_task.resolve({}, [TASK_ID], FolderParams())
