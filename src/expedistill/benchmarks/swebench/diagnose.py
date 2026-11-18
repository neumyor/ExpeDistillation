"""SWE-bench's diagnosis prompts (layer 3): the learn phase's failure
diagnosis reads how the hidden tests judged the submitted patch.

* ``SWEBENCH_TRIAL_FAILURE_ANALYSIS`` replaces the method's
  ``TRIAL_FAILURE_ANALYSIS`` (the prompt pack's ``overrides``). It is the
  method prompt with the two additions Terminal Bench makes, in SWE-bench
  terms: start from the GRADER REPORT when the task section carries one, and
  keep the report's values out of the two notes. The placeholders are the
  method prompt's, as stage 0 requires;
  ``tests/unit/benchmarks/test_swebench_diagnose.py`` pins that the text
  differs from the method prompt by exactly these two additions, so an edit
  to the method prompt fails that test until it is carried over here.
* ``SWEBENCH_DIAGNOSIS_GRADER_REPORT`` is the section the diagnosis appends
  to the task text when the trial recorded a grader report (the prompt
  pack's ``grader_report_prompt``); ``{report}`` is ``plugin.grader_report``'s
  account of the scoring.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["GRADER_REPORT_PROMPT_NAME", "SWEBENCH_DIAGNOSIS_GRADER_REPORT",
           "SWEBENCH_TRIAL_FAILURE_ANALYSIS"]

GRADER_REPORT_PROMPT_NAME = "SWEBENCH_DIAGNOSIS_GRADER_REPORT"


SWEBENCH_TRIAL_FAILURE_ANALYSIS = register(
    PromptSpec(
        name="SWEBENCH_TRIAL_FAILURE_ANALYSIS",
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

The trial failed. When the TASK section ends with a GRADER REPORT, that
report is how the hidden FAIL_TO_PASS and PASS_TO_PASS tests judged the
submitted patch: start from it, then find the step where the agent's work
diverged from what those tests check.

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
- model_note and playbook_note must not repeat test names, values, file
  names or expected outputs from a grader report; state the habit or
  practice behind the failure instead.

{correction_note}Output ONLY JSON:
{{"reason": "<a clear paragraph explaining the failure>",
  "failing_step": <integer from 0 to {last_step}>,
  "scope": "<subtask or general>",
  "model_note": "<one or two sentences, or an empty string>",
  "playbook_note": "<one or two sentences, or an empty string>"}}
""",
        description=(
            "SWE-bench's TRIAL_FAILURE_ANALYSIS: diagnose one failed weak "
            "trial, starting from the grader report when the task section "
            "carries one."
        ),
    )
)


SWEBENCH_DIAGNOSIS_GRADER_REPORT = register(
    PromptSpec(
        name=GRADER_REPORT_PROMPT_NAME,
        template="""GRADER REPORT (how the hidden tests judged the submitted patch: whether it
applied, the FAIL_TO_PASS and PASS_TO_PASS tests that failed, and the last
lines of the test log):
{report}""",
        description=(
            "SWE-bench's grader report section of the failure diagnosis, "
            "appended to the task text; {report} is the plugin's account "
            "of the scoring."
        ),
    )
)
