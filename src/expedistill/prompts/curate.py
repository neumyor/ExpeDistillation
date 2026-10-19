"""Curation prompts (stage 5): G7 similarity/conflict/low-value finding, G8
merging, G9 grouping.

Curation maintains the bank without re-judging skills: merge near-duplicates
into general skills, combine conflicts with explicit condition distinctions,
flag low-value skills (dropped only when the curation's ``drop_low_value``
switch is on) and label capability groups. Merged skills keep the
weak-model-friendly style, generalize the sources' conditions and add no
advice the sources do not support (docs/decisions/
curation_generalize_20261019.md).
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["SIMILARITY", "MERGE", "GROUP"]

SIMILARITY = register(
    PromptSpec(
        name="SIMILARITY",
        template="""You are curating a bank of skills written for a weak model. Find
near-duplicate groups, conflicting pairs and low-value skills in the listing
below. Each entry starts with the skill id.

SKILLS:
{skills_listing}

- similar_groups: groups of skills that teach the same procedure or the same
  lesson. Two skills belong together when one merged skill could teach what
  both teach. Differences in the task they came from, in task-specific error
  messages, file names or concrete values do NOT make them different: judge
  the behavior they teach, not their wording. Every group has at least two
  skill ids.
- conflicts: pairs of skills that give OPPOSITE advice for the same situation.
  Each conflict is a two-element list [skill_id, skill_id].
- low_value: skills that would not help on any other task: advice that only
  makes sense for the one task the skill came from (its specific files,
  values or one-off computation), or advice too vague to act on.

Only group or pair skills of the same kind: procedure skills (whose content
contains the '[When to use]' section header) with procedure skills, failure
references (whose content contains the '[Trigger]' section header) with
failure references -- in similar_groups AND in conflicts.

Do not force anything: return empty lists when there is nothing to merge, no
conflicts and no low-value skill.

Output ONLY JSON with exactly these fields:
{{"similar_groups": [["skill_id", "skill_id"], ...],
  "conflicts": [["skill_id", "skill_id"], ...],
  "low_value": ["skill_id", ...]}}
""",
        description=(
            "G7 similarity: find mergeable near-duplicate groups, "
            "conflicting pairs and low-value skills among listed skills."
        ),
    )
)

MERGE = register(
    PromptSpec(
        name="MERGE",
        template="""You are curating a bank of skills written for a weak model. Merge the
skills below into ONE clean skill.

Generalization comes first. The merged skill must help on OTHER tasks of the
same kind, not only on the tasks the sources came from. Rebuild the skill in a
general form; do not paste the sources' sections one after another.

MERGE MODE: {mode}
- "dedup": the skills teach the same thing. Distill them into one general
  lesson. Where the sources describe the same situation with task-specific
  details (task names, specific error messages, file names, values), write one
  general trigger and general steps that cover all of them. Do not center the
  skill on any single source's scenario.
- "conflict": the skills give opposite advice for the same situation. First
  work out why they conflict: which condition makes one course of action right
  and the other wrong. Then write one skill that states those conditions
  explicitly, so the reader can decide when to follow which course of action.

SKILLS:
{skills_block}

Rules:
- Keep every distinct piece of advice from the sources, stated in general form.
- You may generalize the sources' conditions and steps; do not add advice that
  none of the sources supports.
- Preserve the bracketed section structure of the source skills: the merged
  skill carries the same four sections. Reproduce the bracketed section
  headers exactly as written, each on its own line.
- Keep the weak-model-friendly style: short sentences, one instruction per
  sentence, explicit order, concrete trigger conditions.

Also write a one-line summary of the merged skill: what it is and does, in
one or two sentences. It will be shown as the skill's single line in a
directory of skills, so make it concrete and specific, and keep it on one
line.

Output ONLY JSON with exactly these fields:
{{"content": "<the merged skill, with its bracketed section headers>",
  "summary": "<one line: what the merged skill is and does>"}}
""",
        description=(
            "G8 merge: rebuild a near-duplicate group or a conflicting pair "
            "into one general skill plus its one-line summary."
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
