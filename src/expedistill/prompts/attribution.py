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

__all__ = ["LIVE_RUN_ATTRIBUTION", "LIVE_SKILL_REVISION"]

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
