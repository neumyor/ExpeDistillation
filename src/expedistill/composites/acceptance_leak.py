"""Leak determination: the D8 leak audit and its first-verdict ledger
(layer 5) -- the acceptance module's leak member since R1 (boundaries doc
§3.4: everything that needs a model to judge belongs to acceptance; a
leaked version's measurement is void, which maps onto the acceptance
vocabulary's ``not_testable`` / ``leak``).

The leak-audit part of the former composites/acceptance.py, split out
mechanically in R0 (docs/decisions/execute_learn_repair_plan_20261016.md).
The leak audit is a model judge (D8, ``blocks.audit.audit_leak``) made
through the caller's auditor handle.

Leak-verdict ledger ``stage4_acceptance/leak_verdicts.jsonl``: one JSON row
``{"skill_id": str, "skill_version": int, "leaked": bool, "reason": str}``
per (skill, version), the FIRST verdict the judge gave for that version.
The row is appended BEFORE the round acts on the verdict. The judge is
nondeterministic, so first verdict sticks: a resumed run looks the verdict
up and never asks the judge again for the same (skill, version) -- resume
must equal an uninterrupted run, which a re-ask could not guarantee.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from expedistill.blocks.audit import audit_leak
from expedistill.composites.acceptance_skill_status import (
    _ledger_path,
    _skill_records,
)
from expedistill.core.models import Exam
from expedistill.infra.store import Store, append_jsonl
from expedistill.llm.call import ModelHandle

__all__ = ["LEAK_VERDICTS_LEDGER", "find_leak_verdict", "leak_verdict_for"]

#: Ledger of leak-audit verdicts, written into stage4_acceptance/ next to
#: the attempts: one row per (skill, version), the first verdict the judge
#: gave for it (schema in the module docstring). It doubles as the discard
#: bookkeeping: every leaked=true row is a voided version.
LEAK_VERDICTS_LEDGER = "leak_verdicts.jsonl"


def find_leak_verdict(store: Store, skill_id: str,
                      skill_version: int) -> Optional[Dict[str, Any]]:
    """The stored judge verdict for one (skill, version), if any.

    The first matching row wins; only one row per (skill, version) is ever
    appended, but reading the first keeps the lookup honest even if a
    hand-edited ledger ever holds duplicates. Public since R1: the
    acceptance report reader reads the leak state through this lookup
    instead of a private cross-module name.
    """
    for record in _skill_records(store, LEAK_VERDICTS_LEDGER, skill_id):
        if record.get("skill_version") == skill_version:
            return record
    return None


def leak_verdict_for(
    *,
    store: Store,
    skill_id: str,
    skill_version: int,
    skill_content: str,
    exam: Exam,
    auditor_handle: ModelHandle,
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """One (skill, version)'s leak verdict: the stored first verdict if
    one exists, else one D8 judge call whose row is appended BEFORE it
    is acted on.

    Extracted verbatim from the retired accept_skill's round body in R0
    (same lookup, same call, same append). ``leaked=True`` voids the
    version: the round is consumed without a with-skill attempt for it,
    and the judge's reason travels into the rewrite's error description
    (the exam phase driver's job).
    """
    verdict = find_leak_verdict(store, skill_id, skill_version)
    if verdict is None:
        leaked, reason = audit_leak(
            skill_content, exam.question_text, exam.reference_answer,
            auditor_handle, usage=usage, run_dir=run_dir)
        verdict = {"skill_id": skill_id,
                   "skill_version": skill_version,
                   "leaked": leaked, "reason": reason}
        append_jsonl(_ledger_path(store, LEAK_VERDICTS_LEDGER), verdict)
    return verdict
