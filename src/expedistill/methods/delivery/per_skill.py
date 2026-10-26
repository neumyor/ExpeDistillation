"""The per_skill delivery driver: one skill, one single-skill block.

The v4 exam-phase delivery (plan section 3): the exam execution driver
answers the exam with the ONE skill under test injected, exactly the
way K4 renders acceptance-time blocks today. In exam mode delivery
receives only the deterministic exam IDENTIFIER (R5: ``<subtask_id>/
exam``): the exam itself is generated on the spot at execution time and
the reference answer stays on the stored Exam the answer sheet points
at -- delivery resolves identifiers, it never writes exams.

Frozen signature (R5): ``resolve(bank, tasks, params)`` with ``bank``
the skills under test (the exam-phase universe is one exam per skill),
``tasks`` the phase's exam refs (``<subtask_id>/exam``), and ``params``
the empty PerSkillParams. One row per exam ref: the exam's task_ref,
the skill's reference list and its D9 block. The block comes from the
same layer-1 renderer K4 uses, so acceptance-time and test-time
injection stay one rendering by construction.
"""

from typing import Any, Sequence

from expedistill.core.contracts import ExecutionUnitList
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError
from expedistill.inject import format_skills_block
from expedistill.methods.delivery import PerSkillParams
from expedistill.methods.delivery import units

__all__ = ["resolve"]

#: The exam-ref suffix (``ids.make_exam_id``'s tail); not imported from
#: core.ids because the module exports builders, not their parts.
_EXAM_SUFFIX = "/exam"


def _subtask_of_exam(ref: str) -> str:
    """The subtask an exam ref names; a malformed ref is a CodeBugError."""
    if not ref.endswith(_EXAM_SUFFIX) or len(ref) <= len(_EXAM_SUFFIX):
        raise CodeBugError(
            f"task {ref!r} is not an exam ref (expected "
            "'<subtask_id>/exam'): per_skill delivery resolves exam "
            "identifiers, and the exam-phase tasks are all exams")
    return ref[:-len(_EXAM_SUFFIX)]


def resolve(bank: Sequence[Skill], tasks: Any,
            params: PerSkillParams) -> ExecutionUnitList:
    """resolve(bank, tasks, params) -> ExecutionUnitList  [frozen, R5]:
    one row per exam ref, the single skill that subtask distils.

    The frozen signature only: the legacy ``resolve(ctx, params, skill)``
    compat shape never had a production caller (K4 keeps rendering
    through ``inject.format_skills_block`` directly) and was removed
    rather than kept as dead dispatch machinery.
    """
    by_subtask = {skill.subtask_id: skill for skill in bank}
    rows = []
    for task in tasks:
        ref = units.task_ref(task)
        subtask_id = _subtask_of_exam(ref)
        skill = by_subtask.get(subtask_id)
        if skill is None:
            raise CodeBugError(
                f"exam {ref!r} has no skill in the bank: the exam-phase "
                "universe is one exam per skill under test")
        rows.append(units.make_row(
            ref, [units.skill_reference(skill)],
            format_skills_block([skill])))
    return rows
