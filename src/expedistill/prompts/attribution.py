"""Live-run acceptance prompts: usage attribution, revision, retrigger.

``LIVE_USAGE_ATTRIBUTION`` asks the author model for per-skill relevance,
application and problem facts; it never returns an action.
``LIVE_SKILL_REVISION`` rewrites one skill's body from those facts and
failure views. ``LIVE_SKILL_RETRIGGER`` is the revision writer's internal
sub-mode for a relevant skill that was never read: it changes only the
summary and the ``[When to use]`` section.

All three prompts write for the weak model in the student-aware style of
prompts/author.py: short imperative steps, concrete triggers, transferable
command forms. They register at import of ``prompts/__init__`` like every
other method prompt, so the manifest freezes their digests.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["LIVE_SKILL_REVISION",
           "LIVE_SKILL_RETRIGGER", "LIVE_USAGE_ATTRIBUTION"]

LIVE_SKILL_REVISION = register(
    PromptSpec(
        name="LIVE_SKILL_REVISION",
        template="""A skill written for a weak model was injected into the weak model's real
execution of a task, and the execution failed. Rewrite it only if the
failure reveals a general defect. This is a Terminal Bench V2.1 skill; the
card will be used on other tasks, so task-specific details must not enter
it.

SKILL (current version):
{skill_text}

ATTRIBUTION REPORT (the group's diagnosis and this skill's problem):
{attribution_report}

WHAT THE FAILED EXECUTION ACTUALLY DID (excerpts from the failed attempts):
{failure_view}

{profile_section}

Work in three steps:

1. Classify why the weak model did not succeed WITH this skill. Choose
   exactly one category:
   - "misread": the weak model misunderstood the skill or the situation it
     applies to;
   - "missing_step": the skill skipped an intermediate step that real
     execution needed spelled out;
   - "wrong_trigger": the condition for when to apply the skill was
     unclear, so the weak model applied it wrongly or at the wrong moment;
   - "other": none of the above.

2. Decide whether the failure is a general defect in the skill or a
   task-specific issue.
   - General defect: a missing intermediate step, a trigger the reader
     cannot observe, a step the reader cannot execute, or a misleading
     order. Rewrite the skill.
   - Task-specific issue: a fact about this task's files, values, tools or
     trajectory. Do not encode it. Return the skill unchanged and say in
     revision_reason that the failure is task-specific.

3. If you rewrite, keep the four bracket sections of the original skill
   ([When to use]/[Steps]/[Verify]/[Do not use when]): rewrite each
   section's content, never collapse them into prose. Reproduce
   the bracketed section headers exactly as written, each on its own line.
   Change the wording, granularity, or trigger conditions of the skill --
   not its underlying idea.
   Rewrite to 5 to 9 decision nodes and 1,500 to 2,500 characters. Compress
   instead of appending. Every node must remain a general decision rule;
   task-specific values become <placeholders>. A script may appear only as
   its input, output, and invocation; never include its implementation, and
   never use a code block longer than three lines.
   Then write a fresh capability-level summary of the REWRITTEN skill: the
   reusable capability it teaches (verb + object) and its observable
   trigger, with no task or subtask names. Keep it on one line.

Hard constraint: do NOT copy the specific values, file names, entity names,
or other concrete details of THIS task's trajectory into the skill. The
rewritten skill must stay transferable: general patterns and command forms
with generic placeholders. Library skills are retrieved and injected into
OTHER tasks, where this task's specifics are wrong.

Transfer audit before output:
1. Would the rewritten nodes still make sense if this task's language,
   tools, file names and constants changed?
2. Is every trigger observable from the task, not from its name?
3. Is every task-specific token a <placeholder>?
4. Are there 5 to 9 decision nodes and 1,500 to 2,500 characters?
5. Does the verify part name the exact pass signal and forbid submission
   before it passes?
If any check fails, rewrite again.

Output ONLY JSON with exactly these fields:
{{"failure_category": "<misread|missing_step|wrong_trigger|other>",
  "skill_text": "<the rewritten skill, plain text>",
  "revision_reason": "<one or two sentences: what you changed and why, or why
  the failure is task-specific and the skill is unchanged>",
  "summary": "<one line: the reusable capability the rewritten skill teaches
  (verb + object) and its observable trigger; no task or subtask names>"}}
""",
        description=(
            "G11 live-run skill revision: classify the live failure "
            "category and rewrite one skill, keeping its four bracket "
            "sections, without leaking this task's specifics."
        ),
    )
)


LIVE_USAGE_ATTRIBUTION = register(
    PromptSpec(
        name="LIVE_USAGE_ATTRIBUTION",
        template="""You are the usage-attribution step of a live acceptance round. A weak
model executed the SAME task twice: first bare (the baseline), then with a
group of skills available in a folder. The run with skills did NOT do better
than the baseline.

TASK (the execution prompt the weak model actually saw):
{task_instruction}

BASELINE (the weak model's bare attempts at the same task, one status line
per attempt):
{baseline_summary}

RUN WITH SKILLS (every attempt of this round, successful and failed ones
alike):
{live_views}

SKILLS (the group that was available; each skill's id, then its full text):
{skills_listing}

SKILL USAGE (hard facts parsed from the recorded sessions; "read" means the
model catted that skill's SKILL.md, "unread" means it never did):
{usage_table}

{profile_section}

Work in two steps:

1. Diagnose. Compare the baseline with the run-with-skills and say why the
   real execution did not get better. 1 to 3 sentences, concrete: name the
   skill that misfired or went unread, what the weak model did instead, and
   where the available text changed its behavior for the worse. Do not
   restate the attempt counts.

2. Report per-skill facts. Emit exactly one entry for EVERY skill in the
   group, each skill exactly once, identified by its skill_id exactly as
   listed. For each skill report:
   - "relevance": "relevant" when the skill's rules apply to this task,
     "irrelevant" when they do not, "unclear" when the trajectory does not
     settle it;
   - "application": "applied" when the model's actions follow the skill,
     "partially_applied" when some steps are followed, "not_applied" when
     the model read it but ignored it, "contradicted" when the model did the
     opposite or the skill clearly hurt, "unclear" when the view is too thin;
   - "evidence_steps": the step numbers that support the judgment (an empty
     list when there is no evidence);
   - "problem": a concrete statement of what is wrong with the skill AS
     WRITTEN or with its selection, phrased transferably. Required and
     non-empty when "application" is "not_applied" or "contradicted";
     empty otherwise when there is no problem.

Do not propose actions. The learning phase decides what to do with these
facts. Do not claim that a skill caused success or failure from the group
outcome alone; use the recorded actions.

Output ONLY JSON with exactly these fields:
{{"diagnosis": "<1 to 3 sentences: why the real execution did not get better>",
  "skills": [
    {{"skill_id": "<the skill's id, exactly as listed>",
      "relevance": "<relevant|irrelevant|unclear>",
      "application": "<applied|partially_applied|not_applied|contradicted|unclear>",
      "evidence_steps": [<step numbers>],
      "problem": "<concrete, transferable problem or empty string>"}}
  ]}}
""",
        description=(
            "Usage-aware live attribution: diagnose the group and report "
            "per-skill relevance/application facts from the recorded "
            "sessions."
        ),
    )
)


LIVE_SKILL_RETRIGGER = register(
    PromptSpec(
        name="LIVE_SKILL_RETRIGGER",
        template="""A skill was available to a weak model during a real task, but the model
never read it. The skill's body may still be useful; its trigger did not make
the model select it. Rewrite ONLY the trigger and the one-line summary.

SKILL (current version):
{skill_text}

CURRENT SUMMARY:
{old_summary}

SIBLING SKILLS (the descriptions the same model saw at the same time; the new
trigger must be distinguishable from these):
{sibling_descriptions}

TASK:
{task_instruction}

WHAT THE MODEL DID INSTEAD (the first actions before any skill read):
{first_actions}

USAGE FACTS:
{usage_report}

{profile_section}

Work in two steps:

1. Explain why this skill should have been selected for this task, using
   concrete cues from the task and the skill body. If the skill truly does
   not apply, say so in the reason and keep the trigger conservative.
2. Write a fresh one-line summary and a fresh [When to use] trigger. The
   trigger must name the observable situation in which the skill helps, and
   must be distinguishable from the sibling descriptions. Do not copy this
   task's file names, entity names, or concrete values into the trigger.

Hard constraint: change ONLY the summary and the [When to use] section. The
[Steps], [Verify] and [Do not use when] sections must stay byte-identical to
the current skill.

Output ONLY JSON with exactly these fields:
{{"summary": "<one line: what the skill is and does>",
  "when_to_use": "<the new [When to use] section body>",
  "reason": "<one or two sentences: why this trigger should select better>"}}
""",
        description=(
            "Rewrite only a live skill's summary and [When to use] section "
            "after the model failed to select it."
        ),
    )
)
