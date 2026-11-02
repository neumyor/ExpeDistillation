"""Judge prompts for how a trial used a delivered skill card.

Two questions are asked after a trial is finished, both of a model that reads
the stored trajectory:

* ``SKILL_APPLICABILITY_JUDGE``: for every card the agent was shown, did the
  situation named by the card's trigger ever arise in this trial? The answer
  comes from the trajectory alone, never from the outcome, so it does not
  depend on whether the agent opened the card.
* ``SKILL_ADHERENCE_JUDGE``: for a card the agent did open, which of the
  card's numbered steps did the agent carry out afterwards?

The wording is benchmark-neutral on purpose: the same prompts judge every
benchmark's trials.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["SKILL_ADHERENCE_JUDGE", "SKILL_APPLICABILITY_JUDGE"]

SKILL_APPLICABILITY_JUDGE = register(
    PromptSpec(
        name="SKILL_APPLICABILITY_JUDGE",
        template="""An agent worked on one task. Before starting it was shown a list of skill
cards. Each card has a description that says what the card teaches and when it
applies. The agent could open a card at any step to read it.

For every card, decide whether the situation named by the card's trigger
actually arose at some point of the trial below.

TASK GIVEN TO THE AGENT:
{task_instruction}

CARD LIST, exactly as the agent saw it:
{skill_listing}

THE TRIAL (the agent's actions and what each action returned, step by step):
{trajectory_view}

How to judge:
- A card applies when, at some step, the situation its description names was
  really present in the trial, so that opening the card at that step would have
  been justified by the description.
- A card does not apply when its topic only sounds related to the task, or when
  the situation it names never arose.
- Judge from what the trial shows. Do not use how the trial ended, and do not
  use whether the agent opened the card or followed it. A step that only opens
  or reads a card is not the situation the card names.
- first_step is the number of the earliest step at which the situation was
  present, using the step numbers printed in the trial.

Return one entry for EVERY card in the list, in the same order, with the
skill_id copied exactly as written in the list. Do not add cards that are not
in the list and do not leave a listed card out.

Output ONLY JSON with exactly this shape:
{{"skills": [
  {{"skill_id": "<id copied from the list>",
   "applicable": <true or false>,
   "first_step": <step number, or null when the card does not apply>,
   "reason": "<one sentence: the situation that arose, or why none did>"}}
]}}
""",
        description=(
            "Applicability judge: for each listed skill card, whether its "
            "trigger condition was met anywhere in a finished trial, judged "
            "from the trajectory alone."
        ),
    )
)

SKILL_ADHERENCE_JUDGE = register(
    PromptSpec(
        name="SKILL_ADHERENCE_JUDGE",
        template="""An agent opened one skill card during a trial. Judge, for each numbered
item in the card's [Steps] section, whether the agent carried it out after
opening the card.

THE CARD:
{card_text}

THE TRIAL FROM THE STEP WHERE THE CARD WAS OPENED ON (the agent's actions and
what each action returned; step numbers are those of the whole trial):
{trajectory_view}

The card's [Steps] section has {n_steps} numbered item(s). Give each item one
status:
- followed: the agent did what the item asks. Equivalent actions count; the
  wording and the exact form of the action may differ.
- partial: the agent did only part of what the item asks, or did it in a way
  that drops an essential part of it.
- skipped: the situation the item is for came up (or the trial moved past that
  point), and the agent did not do what the item asks.
- not_reached: the situation the item is for never came up in the trial, for
  example because the trial ended first or went a different way, so the agent
  had no occasion to do it.

Judge only from the actions and results shown. Do not credit an intention the
agent only stated, and do not use how the trial ended.

Return one entry per item, numbered as in the card, with a one-line evidence
quote: the action or result that shows the status (leave it empty for
not_reached).

Output ONLY JSON with exactly this shape:
{{"steps": [
  {{"number": 1,
   "status": "<followed|partial|skipped|not_reached>",
   "evidence": "<one line quoting the action or result>"}}
]}}
""",
        description=(
            "Adherence judge: for one opened skill card, the status of each "
            "numbered [Steps] item (followed / partial / skipped / "
            "not_reached) in the rest of the trial."
        ),
    )
)
