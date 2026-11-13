"""The learn phase's two rewrite prompts, one per side of a skill.

``REVISE_TRIGGER`` rewrites the trigger (``summary``, ``when_to_use``) of a
card the agent did not open before the step where its trial went wrong
(trigger evidence); ``REVISE_CONTENT`` rewrites the content (``steps``,
``not_applicable_when``) of a card the agent opened and still failed with
(content evidence). Each returns only its own fields. Both receive the
diagnoses of their evidence as ``REVISION_DIAGNOSIS`` renders each one.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["REVISE_CONTENT", "REVISE_TRIGGER", "REVISION_DIAGNOSIS"]


REVISE_TRIGGER = register(
    PromptSpec(
        name="REVISE_TRIGGER",
        template="""A skill card written for a weaker {agent_kind} agent on {benchmark_name} was not opened
when it was needed: in the real trials below the agent was doing the
subtask the card teaches, never opened the card before the step where the
trial went wrong, and failed. Rewrite ONLY the trigger: the one-line summary
and the "when to use" text. The agent sees one line per card in its skill
catalog, made of these two fields, and decides from that line alone whether
to open the card. The steps of the card stay exactly as they are.

ENVIRONMENT PROTOCOL (what the agent sees and may do at each step):
{protocol}

CURRENT SUMMARY: {current_summary}
CURRENT WHEN TO USE: {current_when_to_use}

THE SKILL CATALOG THE AGENT SAW:
{skill_catalog}

THE PLAYBOOK THE AGENT SAW AT EVERY STEP ("(none)" when it had none):
{playbook}

THE REAL STEPS (from the start of the subtask to the step where the trial
went wrong, per failed trial):
{evidence_steps}

DIAGNOSES OF THE FAILURES:
{diagnosis}

{profile_section}

Write a new summary and when_to_use so that an agent in these situations,
and in any situation of the same kind, recognizes this card and opens it,
an agent in an unrelated situation does not, and the line stays
distinguishable from the other lines in the catalog.
- summary: one sentence, a verb and an object, saying what the card
  teaches. No trigger, no task or subtask names.
- when_to_use: the observable entry state of the subtask, named by the
  signs the agent can see at that moment ({observable_signs}). Do not
  describe a moment in time.
- Do not copy names, values or entities from the trials: describe the kind
  of situation.
- Summary and when_to_use together stay under about 400 characters and never
  over 1,000. Write only complete sentences.

Output ONLY JSON:
{{"summary": "<one line: what the card teaches>",
  "when_to_use": "<the observable entry state>",
  "revision_reason": "<one sentence: what you changed and why>"}}
""",
        description=(
            "Learn step 5: rewrite only the trigger (summary, when_to_use) "
            "of a card with trigger evidence."
        ),
    )
)


REVISE_CONTENT = register(
    PromptSpec(
        name="REVISE_CONTENT",
        template="""A skill card written for a weaker {agent_kind} agent on {benchmark_name} did not help: in
the real trials below the agent opened it before the step where the trial
went wrong, and still failed. This is {skill_description}; it will be used
on other tasks, so task-specific details must not enter it. Rewrite ONLY
the content: the steps and the "do not use when" text. The summary and the
"when to use" text stay exactly as they are.

ENVIRONMENT PROTOCOL (what the agent sees and may do at each step):
{protocol}

THE CARD (current version):
{skill_card}

THE PLAYBOOK THE AGENT SEES AT EVERY STEP ("(none)" when it has none):
{playbook}

THE REAL STEPS (from where the agent opened the card to just after the step
where the trial went wrong, per failed trial):
{evidence_steps}

DIAGNOSES OF THE FAILURES:
{diagnosis}

{profile_section}

Rewrite the content so that an agent following it, in these situations and
in any situation of the same kind, does not make the mistakes the diagnoses
name. Read the profile first: guard against the weaknesses it names.
- steps: 3 to 7 steps, as a list without numbers. A step is one decision:
  "When <observable condition>, do <general action>; verify <pass signal>;
  if it fails, <fallback>." Compress instead of appending.
{executable_rules}
- weave the discipline into the verify and fallback parts of the steps:
  {discipline_examples}
- not_applicable_when: the observable signs that this card is wrong for the
  situation.
- Add only what the playbook does not already give; never repeat a
  playbook rule.
- Keep the card's idea; change wording, order, granularity or missing steps.
  Never copy names, values or entities from the trials: they become
  <placeholders>.
- Keep the whole card under 1,500 characters.

Output ONLY JSON:
{{"steps": ["<one decision: condition, action, verify, fallback>", "..."],
  "not_applicable_when": "<observable conditions under which this card is wrong>",
  "revision_reason": "<one sentence: what you changed and why>"}}
""",
        description=(
            "Learn step 5: rewrite only the content (steps, "
            "not_applicable_when) of a card with content evidence."
        ),
    )
)


REVISION_DIAGNOSIS = register(
    PromptSpec(
        name="REVISION_DIAGNOSIS",
        template="[failed trial {number}, failing at step {failing_step}]\n"
                 "{reason}",
        description=(
            "Learn step 5: one evidence's entry of the {diagnosis} text "
            "both rewrite prompts receive: the trial's number, its failing "
            "step and the diagnosis reason."
        ),
    )
)
