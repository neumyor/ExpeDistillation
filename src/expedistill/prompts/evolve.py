"""The learn phase's two global texts: the model profile and the playbook.

Both are rewritten once per learn phase from the phase's diagnosis notes
(``methods/evolve_text``). A benchmark may replace ``PLAYBOOK_UPDATE`` with
its own registered prompt through its prompt pack's ``overrides``; the
replacement fills exactly the same placeholders.

A rejected reply is asked again with a ``correction_note``:
``EVOLVE_CORRECTION`` names a reply of the wrong shape,
``PLAYBOOK_PARTS_CORRECTION`` lists the playbook parts the benchmark
requires and the reply left out.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["EVOLVE_CORRECTION", "PLAYBOOK_PARTS_CORRECTION",
           "PLAYBOOK_UPDATE", "PROFILE_UPDATE"]


PROFILE_UPDATE = register(
    PromptSpec(
        name="PROFILE_UPDATE",
        template="""You maintain the accumulated profile of a weaker {agent_kind}-agent model on {benchmark_name}.
The profile describes the model as a student: what it reliably understands,
where it repeatedly fails, and what kind of teaching it needs. The writers of
skill cards and of the playbook read it; the agent never does. It is not a
task list, it must not contain task-specific answers, and it does not restate
the environment protocol below.

ENVIRONMENT PROTOCOL (fixed facts every writer already knows):
{protocol}

CURRENT PROFILE:
{old_profile}

NEW PROFILE NOTES FROM THIS PHASE:
{profile_notes}

Merge the notes into the current profile. Keep every durable observation
that still helps future writers. Remove repetition and contradictions. If
the notes add nothing durable, return the current profile unchanged. Keep
the result concise and concrete: short lines, one observation each.

{correction_note}Output ONLY JSON:
{{"profile_text": "<the updated profile>"}}
""",
        description=(
            "Learn step 4: merge the phase's model notes into the evolving "
            "weak-model profile."
        ),
    )
)


PLAYBOOK_UPDATE = register(
    PromptSpec(
        name="PLAYBOOK_UPDATE",
        template="""You maintain the GLOBAL PLAYBOOK of a weaker {agent_kind} agent on {benchmark_name}.
The agent sees the playbook for the whole task, next to the list of skill
cards it can open. A skill card teaches what to do in one kind of situation;
the playbook holds only the working practice that helps on EVERY task.

ENVIRONMENT PROTOCOL (fixed; the agent already receives it, so never repeat
or contradict it):
{protocol}

CURRENT PLAYBOOK ("(empty)" before the first version):
{old_playbook}

FACTS (in the first learning round: baseline facts measured on training
tasks without any skills; in every later round: the trigger-evidence
counts, i.e. which skill cards the agent had in its catalog but did not
open before the step where a failed trial went wrong, and how often):
{facts}

NOTES FROM THIS ROUND'S FAILURE DIAGNOSES:
{playbook_notes}

{profile_section}

Update the playbook:
- Keep every rule of the current playbook that the notes and facts do not
  contradict, add what they show is missing, and drop a rule only when the
  evidence shows that it misleads.
- Write only task-general practice: how to work, check and recover on any
  task. Never write a step-by-step recipe for one kind of task, and never
  name objects, files, values or tasks from the evidence: those belong in
  skill cards.
- Tell the agent to look at its skill list whenever its situation matches a
  card's "when to use" line, and to open that card before acting. The
  playbook complements the cards; it never replaces them.
- Every rule must be checkable by the agent at the moment it applies, from
  what the protocol says it can see.
- Use short sentences, one instruction each. Plain Markdown, no code fences.
  Aim for 1,000 to 4,000 characters; the limit is 20,000.

{correction_note}End your reply with the playbook in exactly this shape, and write
nothing after the closing tag:

<playbook>
(the whole playbook)
</playbook>
""",
        description=(
            "Learn step 4: rewrite the global playbook from the phase's "
            "playbook notes and facts (the baseline facts in the first "
            "learn phase, the trigger-evidence counts afterwards)."
        ),
    )
)


EVOLVE_CORRECTION = register(
    PromptSpec(
        name="EVOLVE_CORRECTION",
        template=("Your previous reply was rejected: {problem}. Write it "
                  "again in exactly the shape given below.\n\n"),
        description=(
            "Learn step 4: the correction note of a profile or playbook "
            "reply of the wrong shape."
        ),
    )
)


PLAYBOOK_PARTS_CORRECTION = register(
    PromptSpec(
        name="PLAYBOOK_PARTS_CORRECTION",
        template=("Your previous playbook was rejected: the playbook is "
                  "missing: {missing}. Write it again and make sure every "
                  "one of those parts is present and explicit:\n"
                  "{missing_parts}\n\n"),
        description=(
            "Learn step 4: the correction note of a playbook missing parts "
            "the benchmark requires; {missing_parts} lists each as "
            "'- <name>: <description>'."
        ),
    )
)
