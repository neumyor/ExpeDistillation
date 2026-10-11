"""Curation prompts (stage 5): G7 similarity/conflict finding, G8 merging, G9 grouping.

Curation maintains the bank without re-judging skills: merge near-duplicates,
combine conflicts with explicit condition distinctions, and label capability
groups. Merged skills keep the weak-model-friendly style and add no new advice.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["SIMILARITY", "MERGE", "GROUP"]

SIMILARITY = register(
    PromptSpec(
        name="SIMILARITY",
        template="""You are curating a bank of skills written for a weak model. Find
near-duplicate groups and conflicting pairs in the listing below. Each entry
starts with the skill id.

SKILLS:
{skills_listing}

- similar_groups: groups of skills that teach the same thing (same advice, same
  trigger conditions). Group only skills that could be merged into one skill;
  every group has at least two skill ids.
- conflicts: pairs of skills that give OPPOSITE advice for the same situation.
  Each conflict is a two-element list [skill_id, skill_id].

Judge by literal meaning, not surface wording: skills about the same behavior
with different examples still belong to one group. Do not force anything:
return empty lists when there is nothing to merge and no conflicts.

Output ONLY JSON with exactly these fields:
{{"similar_groups": [["skill_id", "skill_id"], ...],
  "conflicts": [["skill_id", "skill_id"], ...]}}
""",
        description=(
            "G7 similarity: find mergeable near-duplicate groups and "
            "conflicting pairs among listed skills."
        ),
    )
)

MERGE = register(
    PromptSpec(
        name="MERGE",
        template="""You are curating a bank of skills written for a weak model. Merge the
skills below into ONE clean skill.

MERGE MODE: {mode}
- "dedup": the skills teach the same thing; merge them into one skill that keeps
  every distinct piece of advice exactly once.
- "conflict": the skills give opposite advice for the same situation; merge them
  into one skill with explicit condition distinctions, so the reader can decide
  when to follow which course of action.

SKILLS:
{skills_block}

Rules:
- Keep every piece of advice from the source skills; lose none.
- Do not invent new advice that none of the sources contains.
- Keep the weak-model-friendly style: short sentences, one instruction per
  sentence, explicit order, concrete trigger conditions.
- In conflict mode, state the distinguishing conditions explicitly.

Output ONLY the merged skill as plain text. No title, no JSON, no commentary.
""",
        description=(
            "G8 merge: combine a near-duplicate group or a conflicting pair "
            "into one clean skill."
        ),
    )
)

GROUP = register(
    PromptSpec(
        name="GROUP",
        template="""You are curating a bank of skills written for a weak model. Assign each
skill to exactly one capability group. Each entry starts with the skill id.

SKILLS:
{skills_listing}

Group by WHAT KIND of ability the skill teaches, not by where it came from. Use
short, consistent group names (two to four words, for example "file inspection"
or "error recovery"), and reuse the same name for skills of the same capability.
Every skill id in the listing must appear exactly once.

Output ONLY JSON with exactly these fields:
{{"groups": {{"skill_id": "<group_name>", ...}}}}
""",
        description=(
            "G9 grouping: assign each listed skill one capability group name."
        ),
    )
)
