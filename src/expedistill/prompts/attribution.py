"""Live-run acceptance prompts: G10 group attribution, G11 skill revision.

Stage-4 live_run mode (docs/decisions/acceptance_live_run_20261014.md
section 2.3): when a task's skill group fails to beat its stage-1 baseline
in real execution, G10 (author role) produces one group-level diagnosis and
a keep / revise / delete action per skill, and G11 (rewriter role) rewrites
each skill the plan marked for revision from its problem statement plus
excerpts of the failed attempts. Both prompts write for the weak model in
the student-aware style of prompts/author.py: short imperative steps,
concrete triggers, transferable command forms.

Deliberately NOT imported by ``prompts/__init__.py``. METHOD_PROMPT_NAMES
is an import-time snapshot of the registry, and every in-flight exam run
resumes by byte-comparing the manifest against that frozen name set, so a
new name appearing there would break resume (decision section 0). The two
prompts below are therefore registered lazily: stage 0 includes their
digests into the manifest only in live_run mode, and
``blocks/attribution.py`` defensively imports this module so any caller
that bypasses stage 0 (unit tests, scripts) still finds both names
registered instead of hitting PromptNotFoundError.

Design note on ``task_instruction`` (decision section 2.3, reviewer R3-M5):
the variable is the RENDERED ``task_prompt(task)`` product -- the execution
prompt the student model actually saw, not the Task record's instruction
field. Under TB that field is just the task name, so the full instruction
reaches the attributor only indirectly, through the first-step context of
``live_views``. That limitation is recorded here rather than papered over
(open question: whether TB can recover the full remote instruction text).
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["LIVE_RUN_ATTRIBUTION", "LIVE_SKILL_REVISION",
           "LIVE_SKILL_RETRIGGER", "LIVE_USAGE_ATTRIBUTION"]

LIVE_RUN_ATTRIBUTION = register(
    PromptSpec(
        name="LIVE_RUN_ATTRIBUTION",
        template="""You are the attribution step of a live acceptance round. A weak model
executed the SAME task twice: first bare (the baseline), then with a group of
skills injected into its execution prompt. The run with skills did NOT do
better than the baseline. Diagnose why, and decide what happens to each
skill.

TASK (the execution prompt the weak model actually saw):
{task_instruction}

BASELINE (the weak model's bare attempts at the same task, one status line
per attempt):
{baseline_summary}

RUN WITH SKILLS (every attempt of this round, successful and failed ones
alike):
{live_views}

SKILLS (the group that was injected; each skill's id, then its full text):
{skills_listing}

{profile_section}

Work in two steps:

1. Diagnose. Compare the baseline with the run-with-skills and say why the
   real execution did not get better. 1 to 3 sentences, concrete: name the
   skill that misfired or went unused, what the weak model did instead, and
   where the injected text changed its behavior for the worse. Do not
   restate the attempt counts; say what actually happened.

2. Decide, per skill. Emit exactly one action for EVERY skill in the group,
   each skill exactly once, identified by its skill_id exactly as listed:
   - "keep": the skill is not part of the problem.
   - "revise": the skill is part of the problem and fixable. The problem
     must be concrete and about the skill AS WRITTEN: a trigger the weak
     model cannot recognize during real execution, a step it cannot carry
     out, an ordering that misleads it, a case the skill leaves uncovered.
   - "delete": the skill is harmful or useless in this group. The problem
     must say why, concretely.

   Revision discipline, binding on every "revise" problem: the fix must be
   phrased as a TRANSFERABLE skill -- general patterns and command forms
   with generic placeholders (for example <config-file>, <port>), never
   this task's file names, concrete values, or entity names. Every library
   skill is retrieved and injected into OTHER tasks, where this task's
   specifics are wrong.

Output ONLY JSON with exactly these fields:
{{"diagnosis": "<1 to 3 sentences: why the real execution did not get better>",
  "skill_actions": [
    {{"skill_id": "<the skill's id, exactly as listed>",
      "action": "<keep|revise|delete>",
      "problem": "<empty string for keep; for revise/delete the concrete
      problem, phrased transferably>"}}
  ]}}
""",
        description=(
            "G10 live-run attribution: diagnose why the skill run did not "
            "beat the baseline and assign keep/revise/delete to every skill "
            "in the group."
        ),
    )
)

LIVE_SKILL_REVISION = register(
    PromptSpec(
        name="LIVE_SKILL_REVISION",
        template="""A skill written for a weak model was injected into the weak model's real
execution of a task, and the execution failed. Rewrite the skill.

SKILL (current version):
{skill_text}

ATTRIBUTION REPORT (the group's diagnosis and this skill's problem):
{attribution_report}

WHAT THE FAILED EXECUTION ACTUALLY DID (excerpts from the failed attempts):
{failure_view}

{profile_section}

Work in two steps:

1. Classify why the weak model did not succeed WITH this skill. Choose
   exactly one category:
   - "misread": the weak model misunderstood the skill or the situation it
     applies to;
   - "missing_step": the skill skipped an intermediate step that real
     execution needed spelled out;
   - "wrong_trigger": the condition for when to apply the skill was
     unclear, so the weak model applied it wrongly or at the wrong moment;
   - "other": none of the above.

2. Rewrite the skill to fix the classified problem and the attribution
   report. Change the wording, granularity, or trigger conditions of the
   skill -- not its underlying idea. The rewritten skill must keep the
   four bracket sections of the original skill ([When to use]/[Steps]/
   [Verify]/[Do not use when]): rewrite each section's content, never collapse them into prose.
   Reproduce the bracketed section headers exactly as written, each on its
   own line. Then write a fresh one-line summary of the REWRITTEN skill
   (what it is and does, one sentence): the content changed, so the
   skill's directory line must describe what it now says, not what it
   used to say. Keep the summary on one line.

Hard constraint: do NOT copy the specific values, file names, entity names,
or other concrete details of THIS task's trajectory into the skill. The
rewritten skill must stay transferable: general patterns and command forms
with generic placeholders (for example <config-file>, <port>). Library
skills are retrieved and injected into OTHER tasks, where this task's
specifics are wrong.

Output ONLY JSON with exactly these fields:
{{"failure_category": "<misread|missing_step|wrong_trigger|other>",
  "skill_text": "<the rewritten skill, plain text>",
  "revision_reason": "<one or two sentences: what you changed and why>",
  "summary": "<one line: what the rewritten skill is and does>"}}
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
     WRITTEN or with its selection, phrased transferably. Empty when there
     is no problem.

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
