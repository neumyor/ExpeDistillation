"""G2 skill writing, G3 exam generation, G6 skill rewrite (layer 4).

Pure computation plus model calls (AGENTS.md block rules): the blocks know no
stage, write no disk, and take the segment steps as an already-rendered text
view (D5 output), never as live objects. Every model call goes through
``llm.call.call_model`` with a named prompt; the block's own job is only to
assemble the template variables from the Subtask/Skill, validate the declared
output, and build the resulting object. IDs come from ``core.ids``, never from
model output.

G6 deliberately returns a plain dict instead of a new Skill and never mutates
its argument: version bumps and ``rewrite_history`` appends are bookkeeping,
which belongs to the composites (K4), not to this block. The rewrite-history
entry shape is pinned as ``{content, error_category, reason}`` where
``content`` is the PREVIOUS version's text.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from expedistill.core.ids import make_exam_id, make_skill_id
from expedistill.core.models import Exam, Skill, Subtask
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model

__all__ = [
    "FAILURE_CATEGORIES",
    "gen_exam",
    "rewrite_skill",
    "write_skill",
]

#: Why the weak model failed its acceptance exam (README stage 4). Pinned by
#: the REWRITE prompt's output contract; changing the set means changing the
#: prompt, which is a new experiment.
FAILURE_CATEGORIES = ("misread", "missing_step", "wrong_trigger", "other")


def write_skill(
    subtask: Subtask,
    steps_view: str,
    weak_profile: str,
    mode: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Skill:
    """Write a v1 skill for one subtask (G2).

    ``mode`` picks the writing style (README stage 3): "distill" for strong
    model segments (what worked / what the lesson was), "diagnose" for weak
    model segments (where the weak model went wrong and what to do instead).
    Any other mode is a caller bug and raises ValueError immediately, before
    any model call.
    """
    if mode == "distill":
        prompt_name = "SKILL_DISTILL"
        variables = {
            "label": subtask.label,
            "description": subtask.description,
            "steps_view": steps_view,
            # The prompt states the subtask's outcome in prose ("true"/"false").
            "success": str(subtask.success).lower(),
            "weak_profile": weak_profile,
        }
    elif mode == "diagnose":
        prompt_name = "SKILL_DIAGNOSE"
        variables = {
            "label": subtask.label,
            "description": subtask.description,
            "steps_view": steps_view,
            "context_summary": subtask.context_summary,
            "weak_profile": weak_profile,
        }
    else:
        raise ValueError(
            f'mode must be "distill" or "diagnose", got {mode!r}')

    content = call_model(
        handle, prompt_name, variables, usage=usage, run_dir=run_dir
    )
    return Skill(
        skill_id=make_skill_id(subtask.subtask_id),
        content=content.strip(),
        subtask_id=subtask.subtask_id,
        version=1,
        status="pending",
        rewrite_history=[],
    )


def gen_exam(
    subtask: Subtask,
    steps_view: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Exam:
    """Generate the exam question for one subtask (G3).

    The exam is written from the subtask only, never from a skill (README
    section 2). The JSON shape is part of the EXAM_GEN prompt contract;
    a parsed response missing keys or carrying empty values is a
    ModelOutputError.
    """
    variables = {
        "label": subtask.label,
        "description": subtask.description,
        "steps_view": steps_view,
    }
    parsed = call_model(
        handle, "EXAM_GEN", variables,
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"EXAM_GEN: expected a JSON object, got {type(parsed).__name__}")

    question_text = parsed.get("question_text")
    reference_answer = parsed.get("reference_answer")
    for name, value in (
        ("question_text", question_text),
        ("reference_answer", reference_answer),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ModelOutputError(
                f"EXAM_GEN: field {name!r} must be a non-empty string, "
                f"got {value!r}")

    return Exam(
        exam_id=make_exam_id(subtask.subtask_id),
        question_text=question_text.strip(),
        reference_answer=reference_answer.strip(),
        subtask_id=subtask.subtask_id,
    )


def rewrite_skill(
    skill: Skill,
    question_text: str,
    weak_answer: str,
    error_description: str,
    weak_profile: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, str]:
    """Rewrite a failed skill (G6); returns the new version's fields.

    No reference answer is ever passed in (README stage 4). The returned dict
    is ``{"content", "failure_category", "rewrite_reason"}``; the caller (K4)
    owns bumping ``skill.version`` and appending to ``rewrite_history``. The
    ``Skill`` argument is left untouched.
    """
    variables = {
        "skill_text": skill.content,
        "question_text": question_text,
        "weak_answer": weak_answer,
        "error_description": error_description,
        "weak_profile": weak_profile,
    }
    parsed = call_model(
        handle, "REWRITE", variables,
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"REWRITE: expected a JSON object, got {type(parsed).__name__}")

    failure_category = parsed.get("failure_category")
    if failure_category not in FAILURE_CATEGORIES:
        raise ModelOutputError(
            f"REWRITE: failure_category must be one of {FAILURE_CATEGORIES}, "
            f"got {failure_category!r}")
    skill_text = parsed.get("skill_text")
    if not isinstance(skill_text, str) or not skill_text.strip():
        raise ModelOutputError(
            f"REWRITE: field 'skill_text' must be a non-empty string, "
            f"got {skill_text!r}")
    rewrite_reason = parsed.get("rewrite_reason")
    if not isinstance(rewrite_reason, str):
        raise ModelOutputError(
            f"REWRITE: field 'rewrite_reason' must be a string, "
            f"got {rewrite_reason!r}")

    return {
        "content": skill_text.strip(),
        "failure_category": failure_category,
        "rewrite_reason": rewrite_reason,
    }
