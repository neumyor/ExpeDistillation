"""The learn phase's diagnosis prompt: one call per failed trial.

* ``TRIAL_FAILURE_ANALYSIS`` -- one failed weak trial of a baseline or a
  practice phase: why it failed, the decisive failing step, whether the
  failure belongs to one subtask or is general, and the two notes the
  profile and the playbook evolve from;
* ``DIAGNOSIS_CORRECTION`` -- the ``correction_note`` the prompt carries
  after a reply the diagnosis could not use.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["DIAGNOSIS_CORRECTION", "TRIAL_FAILURE_ANALYSIS"]


TRIAL_FAILURE_ANALYSIS = register(
    PromptSpec(
        name="TRIAL_FAILURE_ANALYSIS",
        template="""You analyze ONE failed trial of a weaker {agent_kind} agent on {benchmark_name}.
Your job is diagnosis: say why the trial failed and where.

ENVIRONMENT PROTOCOL (what the agent sees and may do at each step; it is
fixed, and every executor already receives it):
{protocol}

TASK:
{task_instruction}

PLAYBOOK THE AGENT SAW (shown at every step; "(none)" when it had none):
{playbook}

SKILL CATALOG THE AGENT SAW (one line per card it could open; "(none)" when
it ran without skills):
{skill_catalog}

SKILL CARDS THE AGENT OPENED (the full text it read; "(none)" when it opened
none):
{opened_skills}

THE TRIAL (the agent's {action_noun}s and what each returned, step by step,
numbered from 0; opening a skill card is a step too):
{trial_steps}

The trial failed.

Explain why it failed. Use only evidence visible above; if the cause is
unclear, say what is unclear instead of inventing certainty.

Then give:
- failing_step: the number of the ONE step where the trial went decisively
  wrong (the first step after which success was no longer reachable without
  undoing something). It must be an integer from 0 to {last_step}.
- scope: "subtask" when the mistake belongs to one part of the task (finding
  something, transforming it, checking a result, recovering from a rejected
  {action_noun}); "general" when it belongs to no part in particular (a
  wrong reply format, repeating the same {action_noun} without progress,
  giving up early).
- model_note: a weakness of the model this failure shows (a habit, a
  misreading, a missing check), stated so that it holds beyond this task.
  Empty when the failure says nothing general about the model.
- playbook_note: a working practice that would have prevented this failure
  and holds for every task of the benchmark. Not a step recipe for one kind
  of task, not a fact of this task, and nothing the protocol already states.
  Empty when no such practice exists.

{correction_note}Output ONLY JSON:
{{"reason": "<a clear paragraph explaining the failure>",
  "failing_step": <integer from 0 to {last_step}>,
  "scope": "<subtask or general>",
  "model_note": "<one or two sentences, or an empty string>",
  "playbook_note": "<one or two sentences, or an empty string>"}}
""",
        description=(
            "Learn step 1: diagnose one failed weak baseline or practice "
            "trial into a reason, the failing step, its scope and the "
            "profile and playbook notes."
        ),
    )
)


DIAGNOSIS_CORRECTION = register(
    PromptSpec(
        name="DIAGNOSIS_CORRECTION",
        template="Your previous answer {problem}. Answer again.\n\n",
        description=(
            "Learn step 1: the correction note of a diagnosis whose reply "
            "was rejected; {problem} completes the sentence."
        ),
    )
)
