"""The weak-model profile section shared by the authoring prompts.

The experiment's ``use_weak_profile`` switch decides whether the profile
text reaches a prompt. When it is off, the stage passes an empty profile;
the prompt still needs a section in that slot, because its surrounding
instructions refer to the reader's capability boundary. The neutral
section says plainly that no profile is available and keeps the reader
at a generally capable agent of the benchmark's kind.

The section builder is pure: the caller names the reader's role and passes
the profile text; the role picks the header line and an empty profile yields
the neutral section. A non-empty
profile is followed by the binding line: whatever the profile states about
the reader -- a capability boundary or the execution context it will have
at each step -- is a rule for every node the writer produces. This one
place reaches every authoring and rewriting prompt, so a new writer prompt
cannot silently drop the constraint.
"""

from typing import Optional

from expedistill.prompts.learning_context import GENERIC

__all__ = ["profile_section", "PROFILE_ROLES"]

#: Header line per role: the two authoring paths and the merge path name the
#: skill's reader, the live attribution path names the task's executor.
PROFILE_ROLES = {
    "skill_reader": "THE MODEL THAT WILL READ THE SKILL (weak model profile):",
    "merged_skill_reader": ("THE MODEL THAT WILL READ THE MERGED SKILL "
                            "(weak model profile):"),
    "task_executor": "THE MODEL THAT EXECUTED THE TASK (weak model profile):",
}

#: The no-profile section: no capability claims, no invented weaknesses.
_NO_WEAK_PROFILE_SECTION = (
    "No weak-model profile is provided. Treat the reader as a generally "
    "capable {agent_kind} agent: every step must be a concrete action it can "
    "carry out, one action per step. Do not assume any specific weakness."
)

#: Appended to every non-empty profile: a profile may state execution
#: constraints (what the reader's context holds at each step), and those
#: bind the written procedure just like a capability limit does.
_PROFILE_BINDING = (
    "Everything above is binding for every step, node and rule you write. "
    "A procedure that assumes information the profile says the reader will "
    "not have at that moment is a defect, even when each individual action "
    "is within the reader's capability."
)


def profile_section(role: str, weak_profile: Optional[str],
                    agent_kind: Optional[str] = None) -> str:
    """One prompt's profile section: role header + profile, or the neutral text.

    ``role`` is a key of ``PROFILE_ROLES``. A blank/None profile is the
    disabled switch's state.
    ``agent_kind`` is the benchmark's ``agent_kind`` slot (a handle's
    ``prompt_defaults.get("agent_kind")``); None means the GENERIC wording,
    as it does for the slots ``call_model`` fills.
    """
    if role not in PROFILE_ROLES:
        raise ValueError(
            f"unknown profile role {role!r}; known: {sorted(PROFILE_ROLES)}")
    text = (weak_profile or "").strip()
    if not text:
        return _NO_WEAK_PROFILE_SECTION.format(
            agent_kind=agent_kind or GENERIC.slots["agent_kind"])
    return f"{PROFILE_ROLES[role]}\n{text}\n\n{_PROFILE_BINDING}"
