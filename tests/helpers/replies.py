"""Scripted model replies for the curation (G7 / G8 / G9) and judge
contracts: one definition per output shape, so a contract change (the
2026-10-19 low_value field, the JSON merge output) is edited here only."""

from expedistill.llm import json_script

__all__ = ["correct", "grouping", "incorrect", "merged", "similarity"]


def similarity(groups=(), conflicts=(), low_value=()):
    """A G7 (SIMILARITY) reply."""
    return json_script({
        "similar_groups": [list(group) for group in groups],
        "conflicts": [list(pair) for pair in conflicts],
        "low_value": list(low_value),
    })


def merged(content, summary="merged skill summary"):
    """A G8 (MERGE) reply: the merged skill and its one-line summary."""
    return json_script({"content": content, "summary": summary})


def grouping(mapping):
    """A G9 (GROUP) reply: skill_id -> capability group name."""
    return json_script({"groups": mapping})


def correct():
    """An EXAM_JUDGE reply grading the plan correct."""
    return json_script({"verdict": "success", "reason": ""})


def incorrect():
    """An EXAM_JUDGE reply grading the plan incorrect."""
    return json_script({"verdict": "failure", "reason": "wrong"})
