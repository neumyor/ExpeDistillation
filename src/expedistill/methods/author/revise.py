"""The shared P04/P06 revision writer.

The decision module has already chosen ``revise``. This module only writes
the new content from the new ``SkillAttemptEvidence`` package; it has no
keep/delete policy and does not read full trajectories.
"""

import json
from pathlib import Path
from typing import Any, Optional, Union

from expedistill.blocks.attribution import revise_from_attempts
from expedistill.blocks.author import has_skill_sections
from expedistill.core.models import Skill
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
)
from expedistill.llm.call import ModelHandle
from expedistill.methods.author.write import RevisionOutcome

__all__ = ["revise_one"]

_REVISION_ATTEMPTS = 2


def revise_one(
    skill: Skill,
    evidence: Any,
    *,
    weak_profile: str,
    handle: ModelHandle,
    attempts: int = _REVISION_ATTEMPTS,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> RevisionOutcome:
    """Write one skill from all of its per-window evidence."""
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")
    windows = getattr(evidence, "windows", None)
    if windows is None:
        raise CodeBugError(
            f"evidence for {skill.skill_id!r} is not SkillAttemptEvidence")
    if not windows:
        return RevisionOutcome(
            skill_id=skill.skill_id, decision="revise",
            outcome="unchanged")
    serialized = json.dumps(evidence.to_dict(), ensure_ascii=False,
                            indent=2)
    for _ in range(attempts):
        try:
            result = revise_from_attempts(
                skill_text=skill.content,
                attempt_evidence=serialized,
                weak_profile=weak_profile,
                handle=handle,
                usage=usage, run_dir=run_dir)
        except InfrastructureError:
            return RevisionOutcome(
                skill_id=skill.skill_id, decision="revise",
                outcome="infra_failed")
        except ModelOutputError:
            continue
        new_text = result["skill_text"]
        if has_skill_sections(skill.content) \
                and not has_skill_sections(new_text):
            # A structural failure rides the same retry budget as a
            # format failure.
            continue
        if new_text == skill.content:
            return RevisionOutcome(
                skill_id=skill.skill_id, decision="revise",
                outcome="unchanged")
        return RevisionOutcome(
            skill_id=skill.skill_id, decision="revise",
            outcome="revised", content=new_text,
            failure_category=None,
            revision_reason=result["revision_reason"],
            summary=result["summary"])
    return RevisionOutcome(skill_id=skill.skill_id, decision="revise",
                           outcome="unchanged")
