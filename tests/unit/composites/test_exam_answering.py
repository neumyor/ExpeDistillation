"""Exam answering catalog aliases, including synthetic requirement skills."""

from expedistill.composites import exam_answering


def test_requirement_skill_aliases_cover_mixed_separators():
    skill_id = "req/abc123/seg00/skill"
    folder = {"files": [{
        "skill_id": skill_id,
        "filename": "req-abc123-seg00-skill/SKILL.md",
    }]}

    aliases = exam_answering._aliases(folder)

    assert aliases["req/abc123-seg00-skill"] == skill_id
    assert aliases["req-abc123-seg00-skill"] == skill_id
    assert aliases[skill_id] == skill_id
