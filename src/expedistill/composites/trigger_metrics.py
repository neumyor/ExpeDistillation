"""Trigger and adherence metrics over judged trials (layer 5, pure).

Input: one record per judged trial,

    {"task": str, "attempt": int,
     "delivered": [skill_id, ...],          # the cards the agent was shown
     "opened": [skill_id, ...],             # the cards it actually opened
     "applicable": {skill_id: bool},        # one entry per delivered card
     "adherence": {skill_id: [status, ...]},# per OPENED card, one per step
     "correct": True | False | None}        # the trial's outcome

``applicable`` is the applicability judge's reading of the trajectory (did
the situation named by the card's trigger arise), ``adherence`` the
adherence judge's step statuses (``followed`` / ``partial`` / ``skipped`` /
``not_reached``). Extra keys are ignored. A record whose parts contradict
each other (an opened card that was not delivered, ``applicable`` not
covering exactly the delivered cards, an unknown status) is a caller bug and
raises ValueError: no metric is computed from it.

``trigger_metrics`` returns the pre-registered definitions below, in a plain
dict. Each metric carries its numerator and denominator, so any reader can
recompute it; a ratio whose denominator is zero is None, never 0. The
definitions are fixed before any result is looked at: do not add an
alternative here to make a number look better, add a new run instead.

* ``trigger_recall`` -- opened-and-applicable / applicable. Counted over
  (trial, card) pairs, pooled across trials; only a trial with at least one
  applicable card contributes (an applicable pair exists only there). It
  answers: when a delivered card applied, how often did the agent open it?
* ``misfire_rate`` -- opened-and-not-applicable / opened. Counted over
  (trial, card) pairs, pooled; only a trial that opened at least one card
  contributes. It answers: of the cards the agent opened, how many did not
  apply to what it was doing?
* ``open_rate`` -- the share of trials in which at least one card was
  opened (a rate per trial, not per card).
* ``adherence_rate`` -- the mean, over opened (trial, card) pairs, of the
  card's step score: followed = 1, partial = 0.5, skipped = 0, with
  ``not_reached`` steps left out of the card's mean. An opened card with no
  countable step (every step not_reached, or no judged step) is not scored
  and does not enter the mean; ``cards_opened`` and ``cards_scored`` say how
  many did.
* ``success_by_group`` -- the success rate of trials in three disjoint
  groups: ``applicable_opened`` (at least one applicable card was opened),
  ``applicable_not_opened`` (a card applied but no applicable card was
  opened), ``nothing_applicable`` (no delivered card applied). Trials whose
  ``correct`` is None are counted in ``n`` but not in the rate's
  denominator (``n_known``).

``replay_metrics`` aggregates exam-replay rows (one per re-answered exam):

* ``recall`` -- P(target card opened): trials where the exam's own skill was
  among the opened cards / answered exams.
* ``misfire`` -- the mean number of non-target cards opened per exam.
* ``plan_pass_rate`` -- exams whose plan the judge passed / answered exams.
* ``pass_given_target_opened`` / ``pass_given_target_not_opened`` -- the
  same pass rate within the exams where the target was / was not opened.
"""

from typing import Any, Dict, Iterable, List, Mapping, Optional

__all__ = ["GROUPS", "replay_metrics", "trigger_metrics"]

#: Step score of an adherence status; ``not_reached`` has no score.
STEP_SCORES = {"followed": 1.0, "partial": 0.5, "skipped": 0.0}
_NOT_REACHED = "not_reached"

GROUP_APPLICABLE_OPENED = "applicable_opened"
GROUP_APPLICABLE_NOT_OPENED = "applicable_not_opened"
GROUP_NOTHING_APPLICABLE = "nothing_applicable"
GROUPS = (GROUP_APPLICABLE_OPENED, GROUP_APPLICABLE_NOT_OPENED,
          GROUP_NOTHING_APPLICABLE)


def _ratio(numerator: float, denominator: float) -> Optional[float]:
    return numerator / denominator if denominator else None


def _check(record: Mapping[str, Any]) -> None:
    where = f"record {record.get('task')!r}/{record.get('attempt')!r}"
    delivered = record.get("delivered")
    opened = record.get("opened")
    applicable = record.get("applicable")
    adherence = record.get("adherence")
    if not isinstance(delivered, (list, tuple)) \
            or not isinstance(opened, (list, tuple)) \
            or not isinstance(applicable, Mapping) \
            or not isinstance(adherence, Mapping):
        raise ValueError(
            f"{where}: needs list 'delivered' and 'opened' and mapping "
            "'applicable' and 'adherence'")
    if set(applicable) != set(delivered):
        raise ValueError(
            f"{where}: 'applicable' must have exactly the delivered cards "
            f"{sorted(delivered)}, got {sorted(applicable)}")
    if not all(isinstance(flag, bool) for flag in applicable.values()):
        raise ValueError(f"{where}: 'applicable' values must be booleans")
    if len(set(opened)) != len(opened) or not set(opened) <= set(delivered):
        raise ValueError(
            f"{where}: 'opened' must be distinct delivered cards, got "
            f"{list(opened)} for delivered {sorted(delivered)}")
    if not set(adherence) <= set(opened):
        raise ValueError(
            f"{where}: 'adherence' may only cover opened cards, got "
            f"{sorted(adherence)} for opened {list(opened)}")
    for statuses in adherence.values():
        unknown = [s for s in statuses
                   if s not in STEP_SCORES and s != _NOT_REACHED]
        if unknown:
            raise ValueError(f"{where}: unknown adherence status {unknown}")
    if record.get("correct") not in (True, False, None):
        raise ValueError(
            f"{where}: 'correct' must be True, False or None")


def trigger_metrics(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """The pre-registered trigger / adherence metrics (module docstring)."""
    records = list(records)
    for record in records:
        _check(record)

    applicable_pairs = opened_applicable = 0
    opened_pairs = opened_not_applicable = 0
    trials_with_applicable = trials_with_open = 0
    cards_opened = 0
    card_scores: List[float] = []
    step_counts = {status: 0 for status in (*STEP_SCORES, _NOT_REACHED)}
    groups = {name: {"n": 0, "n_known": 0, "successes": 0}
              for name in GROUPS}

    for record in records:
        opened = set(record["opened"])
        applicable = {card for card, flag in record["applicable"].items()
                      if flag}
        applicable_pairs += len(applicable)
        opened_applicable += len(applicable & opened)
        opened_pairs += len(opened)
        opened_not_applicable += len(opened - applicable)
        trials_with_applicable += bool(applicable)
        trials_with_open += bool(opened)
        cards_opened += len(opened)

        for statuses in record["adherence"].values():
            for status in statuses:
                step_counts[status] += 1
            scores = [STEP_SCORES[s] for s in statuses if s in STEP_SCORES]
            if scores:
                card_scores.append(sum(scores) / len(scores))

        if not applicable:
            group = GROUP_NOTHING_APPLICABLE
        elif applicable & opened:
            group = GROUP_APPLICABLE_OPENED
        else:
            group = GROUP_APPLICABLE_NOT_OPENED
        entry = groups[group]
        entry["n"] += 1
        if record["correct"] is not None:
            entry["n_known"] += 1
            entry["successes"] += bool(record["correct"])

    for entry in groups.values():
        entry["rate"] = _ratio(entry["successes"], entry["n_known"])
    return {
        "n_trials": len(records),
        "trigger_recall": {
            "value": _ratio(opened_applicable, applicable_pairs),
            "opened_applicable": opened_applicable,
            "applicable": applicable_pairs,
            "trials": trials_with_applicable,
        },
        "misfire_rate": {
            "value": _ratio(opened_not_applicable, opened_pairs),
            "opened_not_applicable": opened_not_applicable,
            "opened": opened_pairs,
            "trials": trials_with_open,
        },
        "open_rate": {
            "value": _ratio(trials_with_open, len(records)),
            "trials_opened": trials_with_open,
            "trials": len(records),
        },
        "adherence_rate": {
            "value": _ratio(sum(card_scores), len(card_scores)),
            "cards_opened": cards_opened,
            "cards_scored": len(card_scores),
            "steps": step_counts,
        },
        "success_by_group": groups,
    }


def replay_metrics(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """The exam-replay aggregates (module docstring).

    Each row is one re-answered exam: ``{"target_opened": bool,
    "other_opened": int, "verdict": "success" | "failure"}``.
    """
    rows = list(rows)
    for row in rows:
        if not isinstance(row.get("target_opened"), bool) \
                or isinstance(row.get("other_opened"), bool) \
                or not isinstance(row.get("other_opened"), int) \
                or row.get("verdict") not in ("success", "failure"):
            raise ValueError(f"malformed exam-replay row: {dict(row)!r}")
    opened = [row for row in rows if row["target_opened"]]
    unopened = [row for row in rows if not row["target_opened"]]

    def passed(group: List[Mapping[str, Any]]) -> int:
        return sum(row["verdict"] == "success" for row in group)

    def pass_rate(group: List[Mapping[str, Any]]) -> Dict[str, Any]:
        return {"value": _ratio(passed(group), len(group)),
                "passed": passed(group), "n": len(group)}

    return {
        "n": len(rows),
        "recall": {"value": _ratio(len(opened), len(rows)),
                   "target_opened": len(opened), "n": len(rows)},
        "misfire": {"value": _ratio(
            sum(row["other_opened"] for row in rows), len(rows)),
            "other_opened": sum(row["other_opened"] for row in rows),
            "n": len(rows)},
        "plan_pass_rate": pass_rate(rows),
        "pass_given_target_opened": pass_rate(opened),
        "pass_given_target_not_opened": pass_rate(unopened),
    }
