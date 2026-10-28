"""Prompts for the trial-level review and its requirement/profile agents.

Five prompts make the review path:

* ``TRIAL_FAILURE_ANALYSIS`` -- explain one failed executed trial in free
  text;
* ``TRIAL_EXAM_ANALYSIS`` -- explain one failed paper exam from the judge's
  reason (the plan is the answer format, nothing was executed);
* ``TRIAL_REQUIREMENT_AGGREGATION`` -- merge all findings into final
  skill actions and the global new-skill requirement document;
* ``PROFILE_UPDATE`` -- merge profile notes into the accumulated model
  profile;
* ``REQUIREMENT_SKILL`` -- write one three-section skill for one aggregated
  requirement.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = [
    "PROFILE_UPDATE",
    "REQUIREMENT_SKILL",
    "TRIAL_EXAM_ANALYSIS",
    "TRIAL_FAILURE_ANALYSIS",
    "TRIAL_REQUIREMENT_AGGREGATION",
]


TRIAL_FAILURE_ANALYSIS = register(
    PromptSpec(
        name="TRIAL_FAILURE_ANALYSIS",
        template="""You analyze ONE failed trial of a weaker terminal agent on Terminal Bench
V2.1. The trial is selected only because its acceptance verdict is failure.
Your job is diagnosis, not action selection.

TASK INSTRUCTION:
{task_instruction}

TRIAL STEPS:
{trial_steps}

SKILLS ACTUALLY DELIVERED TO THIS TRIAL:
{available_skills}

ALL SKILLS IN THE CURRENT BANK:
{all_skills}

Read the trial first, then the delivered skills. Decide why the trial failed
and write the answer in ordinary prose. Do not force the cause into a fixed
category. Distinguish, when the evidence supports it:

- the model read a relevant, correct skill and still failed because of its
  own terminal behavior, planning, speed, or comprehension limits;
- a relevant skill existed but the model never opened or applied it;
- the model opened a skill but the skill's trigger, steps, or guard was
  wrong or incomplete;
- no skill in the bank addresses this failure, so a new capability is
  needed.

Before writing the reason, check every skill under SKILLS ACTUALLY DELIVERED.
If a delivered skill matched the situation but was not opened, or was opened
but not followed, include it in `skills` with the concrete issue. Do not omit
a relevant delivered skill from that list.

Use only evidence visible in the steps and skills. If the cause is unclear,
say what is unclear instead of inventing certainty.

Output ONLY JSON:
{{"reason": "<a clear paragraph explaining the failure>",
  "profile_note": "<what this says about the model as a student, or an empty string>",
  "skills": [
    {{"skill_id": "<exact bank skill id or empty string>",
      "issue": "<what this skill did or failed to do>",
      "step_start": <the 0-based step where this skill should have been
                     opened, or where its guidance went wrong; for a skill
                     the model never opened, use the step where the failure
                     first became visible>,
      "step_end": <exclusive end step>}}
  ],
  "new_skill_note": "<what new capability is missing, or an empty string>"}}
""",
        description=(
            "Trial review: explain one failed trial and separate model limits "
            "from skill defects and missing capabilities."
        ),
    )
)


TRIAL_EXAM_ANALYSIS = register(
    PromptSpec(
        name="TRIAL_EXAM_ANALYSIS",
        template="""You analyze ONE failed paper exam of a weaker terminal agent on Terminal
Bench V2.1. Your job is diagnosis, not action selection.

How the exam works: the scene below was cut from a real task at one point in
time. The examined model saw only the scene and the skill catalog, chose up to
a few skills to open, read them, and wrote a plan for what to do from this
point. Nothing was executed. A plan in prose with no command output is the
required answer format, so never give "the model did not execute commands"
or "the trial stopped after a plan" as a cause. A judge compared the plan with
the expected behavior and marked it failed for the reason given below.

SCENE:
{scene_text}

EXPECTED BEHAVIOR (the grading standard the judge used):
{expected_behavior}

JUDGE'S REASON FOR THE FAILURE:
{judge_reason}

SKILL UNDER EXAMINATION (this exam was written for it):
SKILL_ID: {exam_skill_id}
{exam_skill_content}

SKILL CATALOG (exactly what the examined model saw when choosing):
{skill_catalog}

SKILLS THE MODEL OPENED:
{opened_skills}

THE MODEL'S PLAN:
{plan}

Explain the defect the judge named. Take the judge's reason as the fact to
explain; if the scene itself contradicts it, say so plainly.

Only the stage this scene is about matters. A skill meant for an earlier or
later stage is not applicable here, so not opening it is never a defect.

For the skill under examination, decide which of these holds:
- it was not opened, and its catalog line should have matched this scene:
  its trigger or description is defective;
- it was not opened, and its catalog line rightly does not match this
  scene: the model chose wrongly, or the scene is outside the skill's stage;
- it was opened, and it covers what the judge says is missing or wrong:
  the model did not follow a correct skill (a model limitation);
- it was opened, and it does not cover the defect, or it misleads: its
  content is defective.

List other skills only when they apply to this scene and contributed to the
failure (opened and misleading, or matching but not opened).

Use only evidence visible above. If the cause is unclear, say what is
unclear instead of inventing certainty.

Output ONLY JSON:
{{"reason": "<a clear paragraph explaining the failure>",
  "exam_skill_issue": "<which case above holds for the skill under examination, and the concrete evidence>",
  "profile_note": "<what this says about the model as a student, or an empty string>",
  "skills": [
    {{"skill_id": "<exact bank skill id of another applicable skill>",
      "issue": "<what this skill did or failed to do in this scene>"}}
  ],
  "new_skill_note": "<what new capability is missing, or an empty string>"}}
""",
        description=(
            "Trial review on a paper exam: explain one failed exam plan from "
            "the judge's reason, separating skill defects from model limits."
        ),
    )
)


TRIAL_REQUIREMENT_AGGREGATION = register(
    PromptSpec(
        name="TRIAL_REQUIREMENT_AGGREGATION",
        template="""You merge per-trial failure diagnoses into ONE phase review.

TRIAL FINDINGS:
{findings}

CURRENT SKILL BANK:
{skills}

MEASURED SKILLS (one action is required for every id below):
{measured_skills}

Do two jobs.

1. Skill actions.
For every skill in MEASURED SKILLS, choose exactly one action:
keep, revise, or delete. Merge all trials that point at the same skill into
one row. A missing invocation is a reason to revise the skill's trigger or
description. A defective skill is a reason to revise its content. A correct
skill the model still could not use stays kept; its evidence belongs in the
profile, not in a rewrite. If no finding mentions a measured skill, choose
keep and say explicitly that no finding implicated it. Use ordinary prose
for reasons. Never emit two rows for one skill, and never omit a measured
skill.

2. New capability requirements.
Aggregate all ``new_skill_note`` values into a small set of capability
requirements. A requirement is about a reusable capability, not about one
task or one trial. One requirement will become one new skill. Source sheet
ids are provenance only.

Output ONLY JSON:
{{"skill_actions": [
  {{"skill_id": "<exact bank skill id>",
    "action": "keep|revise|delete",
    "reason": "<prose>",
    "source_sheet_ids": ["<sheet id>", "..."]}}
],
 "new_skill_requirements": [
  {{"title": "<short capability title>",
    "description": "<what the new skill must teach>",
    "trigger": "<when the skill should be opened>",
    "source_sheet_ids": ["<sheet id>", "..."]}}
]}}
""",
        description=(
            "Trial review: merge findings into per-skill actions and an "
            "aggregated new-skill requirement document."
        ),
    )
)


PROFILE_UPDATE = register(
    PromptSpec(
        name="PROFILE_UPDATE",
        template="""You maintain the accumulated profile of a weaker terminal-agent model.
The profile describes the model as a student: what it reliably understands,
where it repeatedly fails, and what kind of teaching it needs. It is not a
task list and it must not contain task-specific answers.

CURRENT PROFILE:
{old_profile}

NEW PROFILE NOTES FROM THIS PHASE:
{profile_notes}

Merge the notes into the current profile. Keep every durable observation
that still helps future skill authors. Remove repetition and contradictions.
If the notes add nothing durable, return the current profile unchanged.
Keep the result concise and concrete.

Output ONLY JSON:
{{"profile_text": "<the updated profile>"}}
""",
        description=(
            "Trial review: accumulate per-trial model-limitation notes into "
            "the evolving weak-model profile."
        ),
    )
)


REQUIREMENT_SKILL = register(
    PromptSpec(
        name="REQUIREMENT_SKILL",
        template="""You write ONE reusable three-section skill for a weaker terminal agent on
Terminal Bench V2.1. The skill answers a capability gap found across several
failed trials. It will be injected into a DIFFERENT task from the same
capability family, not the task that produced the evidence.

REQUIREMENT TITLE: {label}
REQUIREMENT DESCRIPTION: {description}
REQUIREMENT TRIGGER: {segment_position}
FAILURE EVIDENCE:
{steps_view}

{profile_section}

Write a concrete, executable card for the weak reader. Use the three sections
and the one-line summary required by the JSON contract. The trigger must be
observable at the moment named by the requirement. Do not mention trial ids,
sheet ids, file names, or task names in the reusable text.

Output ONLY JSON:
{{"summary": "<one line: what this skill teaches>",
  "when_to_use": "<the trigger and observable signs>",
  "steps": ["<ordered executable step>", "..."],
  "not_applicable_when": "<when this skill is wrong>"}}
""",
        description=(
            "Trial review: write one new skill for one aggregated capability "
            "requirement."
        ),
    )
)
