"""G4 answering and G5 judging (exam blocks, layer 4).

Both blocks are thin by design: prompt text lives in the B2 registry (ANSWER,
JUDGE) and every model call goes through B1 (``llm.call.call_model``), so this
module only wires variables and validates the judge's output. The verdict check
follows the reference repo's judge-validation pattern (ExpeToSkill-merged,
evaluation/claim_check.py): after JSON parsing, the categorical field is checked
against a closed vocabulary and anything outside it is a ModelOutputError, not
a guess -- a judge that answered something else has broken its contract, and
silently mapping it to True/False would corrupt the acceptance decision.

The skills block arrives here already formatted by D9 (composites call
``inject.format_skills_block``); bare and with-skill answering share the same
ANSWER template and differ only in whether that block is empty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Tuple, Union

from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model

__all__ = ["answer", "judge"]

#: The only verdicts the JUDGE prompt may emit; anything else is a format error.
_VERDICT_TO_BOOL = {"correct": True, "incorrect": False}


def answer(
    question_text: str,
    skills_block: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> str:
    """Answer one exam question, bare or with an injected skills block.

    ``skills_block`` is the ALREADY-FORMATTED D9 block (empty string for the
    bare control); the same ANSWER template is used either way, so acceptance
    time and test time cannot drift apart. Returns the answer text, stripped.
    """
    text = call_model(
        handle,
        "ANSWER",
        {"question_text": question_text, "skills_block": skills_block},
        usage=usage,
        run_dir=run_dir,
    )
    return text.strip()


def judge(
    question_text: str,
    reference_answer: str,
    weak_answer: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Tuple[bool, str]:
    """Grade one answer: return ``(verdict, error_description)``.

    The reference answer is a grading standard only; neither return value may
    carry it -- the error description comes verbatim from the judge, whose
    prompt forbids restating the correct answer, and this block never echoes
    the reference itself. ``error_description`` is "" when the answer is
    correct. A verdict outside {"correct", "incorrect"}, a non-object JSON
    response, or a non-string error description raises ModelOutputError.
    """
    parsed = call_model(
        handle,
        "JUDGE",
        {
            "question_text": question_text,
            "reference_answer": reference_answer,
            "weak_answer": weak_answer,
        },
        output_json=True,
        usage=usage,
        run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"JUDGE must return a JSON object, got {type(parsed).__name__}"
        )
    verdict = parsed.get("verdict")
    if not isinstance(verdict, str) or verdict not in _VERDICT_TO_BOOL:
        raise ModelOutputError(
            f"JUDGE verdict must be 'correct' or 'incorrect', got {verdict!r}"
        )
    error_description = parsed.get("error_description", "")
    if not isinstance(error_description, str):
        raise ModelOutputError(
            "JUDGE error_description must be a string, got "
            f"{type(error_description).__name__}"
        )
    return _VERDICT_TO_BOOL[verdict], error_description
