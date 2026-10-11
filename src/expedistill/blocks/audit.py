"""D8 leak audit (layer 4): a model judge, one call per (skill, exam).

A skill that reveals the exam's protected answer material has memorized
this question's answer instead of the general ability, so every skill
version must pass this audit before acceptance (stage 4 / K4). The audit
asks the model behind ``handle`` (the "auditor" role) whether the skill
contains, trivially rephrases, or directly reveals the reference answer or
the exam's concrete answer-specific entities; generic method advice,
protocol words, action names and environment feedback strings are not leaks
(prompt ``LEAK_AUDIT_JUDGE``). The previous mechanical substring/threshold
rule is retired: it false-positived in real pilots (an acronym matched
inside a word, quoted protocol tokens like an action name and an
environment feedback string were treated as protected entities), see
docs/decisions/leak_audit_llm_judge_20261011.md.

The judge is nondeterministic, so persistence -- K4's ``leak_verdicts.jsonl``,
first verdict sticks -- is what makes a run reproducible, not this block.

Validation follows the judge pattern of blocks/exam.py: after call_model's
JSON-repair budget, ``leaked`` must be a JSON boolean and ``reason`` a
non-empty string; anything else is a ModelOutputError, never a guess -- a
judge that answered something else has broken its contract, and silently
mapping it to a verdict would corrupt the acceptance decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Tuple, Union

from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model

__all__ = ["audit_leak"]


def audit_leak(
    skill_content: str,
    question_text: str,
    reference_answer: str,
    handle: ModelHandle,
    *,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Tuple[bool, str]:
    """Audit one skill version against one exam; return ``(leaked, reason)``.

    ``leaked`` is True when the judge finds protected answer material in the
    skill; ``reason`` (one to three sentences, never restating the reference
    answer) is the audit report, reused as the rewrite context when K4 voids
    the version. A verdict is one model call: this block never re-asks on
    its own, and never persists anything (K4 owns both).
    """
    parsed = call_model(
        handle, "LEAK_AUDIT_JUDGE",
        {"skill_text": skill_content, "question_text": question_text,
         "reference_answer": reference_answer},
        output_json=True, usage=usage, run_dir=run_dir,
    )
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            f"LEAK_AUDIT_JUDGE: expected a JSON object, "
            f"got {type(parsed).__name__}")
    leaked = parsed.get("leaked")
    if not isinstance(leaked, bool):
        raise ModelOutputError(
            f"LEAK_AUDIT_JUDGE: field 'leaked' must be true or false, "
            f"got {leaked!r}")
    reason = parsed.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ModelOutputError(
            f"LEAK_AUDIT_JUDGE: field 'reason' must be a non-empty string, "
            f"got {reason!r}")
    return leaked, reason.strip()
