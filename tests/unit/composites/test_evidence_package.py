"""The evidence-package shape and its fact-only guard (R6).

The package is the fifth cross-module hand-off (its field set is frozen
in prose by the boundaries doc section 2.5; core/contracts.py stays
closed this wave). These tests pin the shape and the structural guard
the acceptance criterion leans on: no action anywhere in a package.
"""

import pytest

from expedistill.core.models import Skill
from expedistill.composites.evidence_package import (
    ACTION_FACT_KINDS,
    EvidencePackage,
    ensure_fact_only,
)


def make_skill():
    return Skill(skill_id="traj/fakebench:t1/strong/00/seg00/skill",
                 content="restart the service", subtask_id="seg00",
                 source_subtasks=["seg00"])


def test_package_defaults_and_readers():
    package = EvidencePackage(target="traj/fakebench:t1/strong/00/seg00/skill",
                             old_skill=make_skill())
    assert package.facts == [] and package.segments == []
    assert package.source_experiences == []
    assert package.fact("skill_problem") is None
    assert not package.has_fact("skill_problem")

    package.facts.append({"kind": "skill_problem", "text": "trigger vague"})
    assert package.fact("skill_problem") == "trigger vague"
    assert package.has_fact("skill_problem")


def test_fact_only_guard_accepts_a_clean_package():
    package = EvidencePackage(
        target="traj/fakebench:t1/strong/00/seg00/skill",
        facts=[{"kind": "baseline_run", "text": "attempt 0: failure"},
               {"kind": "group_diagnosis", "text": "the skill misfired"},
               {"kind": "skill_problem", "text": "the trigger never fires"}],
        segments=[{"trajectory_id": "traj/fakebench:t1/weak/00",
                   "step_range": [0, 4], "status": "failure"}],
        old_skill=make_skill())
    ensure_fact_only(package)   # no raise


def test_fact_only_guard_rejects_an_action_key():
    package = EvidencePackage(
        target="s", facts=[{"kind": "group_diagnosis", "text": "d"}],
        segments=[{"trajectory_id": "t", "action": "revise"}])
    with pytest.raises(ValueError, match="action key"):
        ensure_fact_only(package)


def test_fact_only_guard_rejects_an_action_fact_kind():
    for word in ("keep", "revise", "delete", "add"):
        package = EvidencePackage(
            target="s", facts=[{"kind": word, "text": "d"}])
        with pytest.raises(ValueError, match="never name an action"):
            ensure_fact_only(package)


def test_fact_only_guard_rejects_an_action_nested_in_the_old_skill():
    skill = make_skill()
    skill.rewrite_history.append({"content": "x", "action": "revise"})
    package = EvidencePackage(target="s", old_skill=skill)
    with pytest.raises(ValueError, match="action key"):
        ensure_fact_only(package)


def test_action_fact_kinds_are_the_loop_vocabulary():
    assert ACTION_FACT_KINDS == ("keep", "revise", "delete", "add")
