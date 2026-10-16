"""K4 skill-status determination: what the stored records say about one
skill's state (layer 5).

The skill-status part of the former composites/acceptance.py, split out
mechanically in R0 (docs/decisions/execute_learn_repair_plan_20261016.md):
the stage-4 ledger paths and the record readers that derive a skill's
current state purely from disk -- the attempts of an exam (serial order)
and the resumes each lookup supports (a judged bare answer, a passing
acceptance attempt ON this version, the judged with-skill attempt of one
round, the most recent judged with-skill attempt before a round, and the
next round's number). Read-only; the consumers are the acceptance report reader
(composites/reports.py), the exam phase driver (stages/phase_execute.py)
and the exam evidence collector (methods/evidence/exam_sheet.py), and
the leak verdicts are composites/acceptance_leak.py.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from expedistill.core import ids
from expedistill.core.models import Attempt, Skill
from expedistill.infra.store import Store, read_jsonl

#: Ledger of failed G6 tries, next to LEAK_VERDICTS_LEDGER. One line per try
#: that raised ModelOutputError; the try that exhausts the budget carries
#: ``exhausted: true`` and is what marks its round as consumed. Written by
#: composites/acceptance_submit.py's rewrite round; defined here (next to
#: the reader that counts it) so the writer needs no import cycle.
REWRITE_FAILURE_LEDGER = "rewrite_failures.jsonl"


def _ledger_path(store: Store, name: str) -> Path:
    # stage4_acceptance/ is the parent of the attempts directory.
    return Path(store.dir_for("attempts")).parent / name


def _skill_records(store: Store, name: str, skill_id: str
                   ) -> List[Dict[str, Any]]:
    """This skill's lines of one stage-4 ledger, in append order."""
    return [record for record in read_jsonl(_ledger_path(store, name))
            if record.get("skill_id") == skill_id]


def _serial(attempt: Attempt) -> int:
    # The numeric serial, never the id string: serials >= 100 widen the id,
    # and "att100" sorts before "att99" as text.
    return ids.parse_attempt_id(attempt.attempt_id)[1]


def _exam_attempts(store: Store, exam_id: str) -> List[Attempt]:
    """All stored attempts of one exam, in serial order."""
    attempts = []
    for attempt_id in store.ids("attempts"):
        attempt = store.load("attempts", attempt_id, Attempt)
        if attempt.exam_id == exam_id:
            attempts.append(attempt)
    return sorted(attempts, key=_serial)


def _find_bare(attempts: List[Attempt]) -> Optional[Attempt]:
    """The judged bare attempt of the exam, if an interrupted run left one."""
    for attempt in attempts:
        if (attempt.purpose == "bare" and attempt.skill_id is None
                and attempt.judge_verdict is not None):
            return attempt
    return None


def _find_accepted(attempts: List[Attempt], skill_id: str,
                   skill_version: int) -> Optional[Attempt]:
    """A judged acceptance attempt that passed ON this version.

    The version must match: a passing attempt on another version says
    nothing about the stored content, and accepting on it would let text
    that was never examined into the bank.
    """
    for attempt in attempts:
        if (attempt.purpose == "acceptance" and attempt.skill_id == skill_id
                and attempt.skill_version == skill_version
                and attempt.judge_verdict is True):
            return attempt
    return None


def _find_round_attempt(attempts: List[Attempt], skill_id: str,
                        skill_version: int, retry_round: int
                        ) -> Optional[Attempt]:
    """The judged with-skill attempt for one (version, round) pair, if an
    interrupted run stored it between the exam and the rewrite."""
    for attempt in attempts:
        if (attempt.purpose == "acceptance" and attempt.skill_id == skill_id
                and attempt.skill_version == skill_version
                and attempt.retry_round == retry_round
                and attempt.judge_verdict is not None):
            return attempt
    return None


def _last_with_skill(attempts: List[Attempt], skill_id: str,
                     before_round: int) -> Optional[Attempt]:
    """The most recent judged with-skill attempt of an already completed
    round (rewrite context on resume).

    Only rounds before ``before_round`` count: the resumed round's own
    attempt, if stored, is found again by the loop. Round is the primary
    order; the attempt serial breaks ties.
    """
    judged = [a for a in attempts
              if a.purpose == "acceptance" and a.skill_id == skill_id
              and a.retry_round < before_round
              and a.judge_verdict is not None]
    if not judged:
        return None
    return max(judged, key=lambda a: (a.retry_round, _serial(a)))


def _next_round(store: Store, skill: Skill) -> int:
    """Rounds already closed by a rewrite outcome (the retired
    accept_skill loop's round accounting)."""
    exhausted = [record for record in _skill_records(
                     store, REWRITE_FAILURE_LEDGER, skill.skill_id)
                 if record.get("exhausted")]
    return (skill.version - 1) + len(exhausted)
