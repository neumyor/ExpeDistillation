"""Phase definitions for experiment-config tests (schema 9).

One definition site for the method shapes config-level tests build on:
the reference method of design spec 2.4 and the smallest legal method
(baseline, one learn phase, a final test). Each call returns fresh
copies, so a test may mutate what it gets.
"""

import copy

__all__ = ["MINIMAL_SEQUENCE", "REFERENCE_SEQUENCE", "minimal_method",
           "reference_method"]

#: The reference method's sequence (design spec 2.4).
REFERENCE_SEQUENCE = ("baseline", "learn", "practice", "learn", "practice",
                      "learn", "test")

#: The smallest legal sequence: the baseline, one learn phase, the test.
MINIMAL_SEQUENCE = ("baseline", "learn", "test")

_BASELINE = {"kind": "execute", "role": "baseline", "tasks": "train",
             "delivery": {"name": "none"},
             "execution": {"name": "real",
                           "arms": ["executor_strong", "executor_weak"]}}

_LEARN = {"kind": "learn",
          "review": {"name": "review", "K": 5, "retry_limit": 2},
          "author": {"name": "subtask_cards"}}

_PRACTICE = {"kind": "execute", "role": "practice", "tasks": "train",
             "delivery": {"name": "folder"},
             "execution": {"name": "real_folder", "attempts": 3},
             "playbook": "evolved"}

_FOLDER_TEST = {"kind": "execute", "role": "test", "tasks": "test",
                "delivery": {"name": "folder"},
                "execution": {"name": "real_folder"},
                "playbook": "evolved",
                "compare": {"against": "fresh_bare"}}

_WHOLE_BANK_TEST = {"kind": "execute", "role": "test", "tasks": "test",
                    "delivery": {"name": "whole_bank"},
                    "execution": {"name": "real"},
                    "compare": {"against": "fresh_bare"}}


def reference_method() -> dict:
    """``{"sequence": [...], "phases": {...}}`` of the reference method:
    practice on train, folder delivery and the evolved playbook."""
    return {"sequence": list(REFERENCE_SEQUENCE),
            "phases": copy.deepcopy({"baseline": _BASELINE,
                                     "learn": _LEARN,
                                     "practice": _PRACTICE,
                                     "test": _FOLDER_TEST})}


def minimal_method() -> dict:
    """``{"sequence": [...], "phases": {...}}`` of the smallest legal
    method: baseline, one learn phase, a whole-bank test (playbook off)."""
    return {"sequence": list(MINIMAL_SEQUENCE),
            "phases": copy.deepcopy({"baseline": _BASELINE,
                                     "learn": _LEARN,
                                     "test": _WHOLE_BANK_TEST})}
