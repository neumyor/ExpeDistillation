"""The exam learn landing: where a revised skill's next exam scene comes from."""

from types import SimpleNamespace

from expedistill.composites.evidence_package import EvidencePackage
from expedistill.core import ids
from expedistill.core.answer_sheet import UnitSummary
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Exam
from expedistill.infra.store import Store, read_jsonl
from expedistill.stages import learn_exam
from tests.helpers.skills import make_skill

SKILL = make_skill("traj/tb:t1/strong/00/seg03", "validate the outputs",
                   summary="validate outputs")
OTHER = make_skill("traj/tb:t1/strong/00/seg01", "inspect the inputs",
                   summary="inspect inputs")


def _exam(skill, scene_step):
    return Exam(
        exam_id=ids.make_exam_id(skill.skill_id, 1), skill_id=skill.skill_id,
        skill_version=1, scene_text=f"scene at step {scene_step}",
        expected_behavior="act", reference_answer="act",
        source_ref={"phase_label": "p01:baseline", "unit_id": "u",
                    "scene_step": scene_step})


def _unit(unit_id, skill):
    return UnitSummary(
        unit_id=unit_id, phase_label="p03:exam",
        task_ref=ids.make_exam_id(skill.skill_id, 1), role="weak", arm=None,
        expected_attempts=1,
        injected_skills=[{"skill_id": skill.skill_id, "version": 1}],
        valid_count=1, success_count=0, status="normal", cause="")


def test_a_revision_records_the_scene_of_its_basis_exam(tmp_path):
    store = Store(tmp_path / "run")
    store.save(SKILL, "skills")
    store.save(_exam(SKILL, 6), "exams")
    store.save(_exam(OTHER, 4), "exams")
    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    row = TaskListRow(target_kind="skill", target_id=SKILL.skill_id,
                      action="revise", reason="widen the trigger",
                      basis_unit="exam-own")
    outcome = SimpleNamespace(
        skill_id=SKILL.skill_id, outcome="revised", content="new body",
        summary="validate outputs", failure_category="",
        revision_reason="the trigger missed the final stage")
    package = EvidencePackage(target=SKILL.skill_id, old_skill=SKILL)

    learn_exam._land_revision(
        SimpleNamespace(store=store), SimpleNamespace(label="p04:learn"),
        row, package, outcome, phase_dir,
        [_unit("exam-other", OTHER), _unit("exam-own", SKILL)])

    origin = read_jsonl(phase_dir / "skill_origins.jsonl")[0]
    assert origin["version"] == 2
    assert origin["source_ref"]["scene_step"] == 6
