"""The weak-model profile section shared by the writing prompts.

Every learn phase evolves the profile (``methods/evolve_text``) before it
writes anything, and every writer of that phase -- the card writer, the
two rewrite prompts and the playbook writer -- receives the evolved text
through this section. The first learn phase
starts from an empty profile; the prompt still needs a section in that
slot, because its surrounding instructions refer to the reader's capability
boundary. The neutral section says plainly that no profile is available
and keeps the reader at a generally capable agent of the benchmark's kind.

The section builder is pure: the caller names the reader's role and passes
the profile text; the role picks the header line and an empty profile yields
the neutral section. A non-empty profile is followed by the binding line:
whatever the profile states about the reader is a rule for every node the
writer produces.
"""

from typing import Optional

from expedistill.prompts.learning_context import GENERIC

__all__ = ["profile_section", "PROFILE_ROLES"]

#: Header line per role: who reads what the writer produces.
PROFILE_ROLES = {
    "skill_reader": "THE MODEL THAT WILL READ THE SKILL (weak model profile):",
    "playbook_reader": ("THE MODEL THAT WILL PLAY WITH THIS PLAYBOOK IN VIEW "
                        "(weak model profile):"),
}

#: The no-profile section: no capability claims, no invented weaknesses.
_NO_PROFILE_SECTION = (
    "No weak-model profile is provided. Treat the reader as a generally "
    "capable {agent_kind} agent: every step must be a concrete action it can "
    "carry out, one action per step. Do not assume any specific weakness."
)

#: Appended to every non-empty profile: what the profile says about the
#: reader binds the written procedure just like a capability limit does.
_PROFILE_BINDING = (
    "Everything above is binding for every step, node and rule you write. "
    "A procedure that assumes information the profile says the reader will "
    "not have at that moment is a defect, even when each individual action "
    "is within the reader's capability."
)


def profile_section(role: str, profile: Optional[str],
                    agent_kind: Optional[str] = None) -> str:
    """One prompt's profile section: role header + profile, or the neutral text.

    ``role`` is a key of ``PROFILE_ROLES``. A blank/None profile yields the
    neutral section. ``agent_kind`` is the benchmark's ``agent_kind`` slot
    (a handle's ``prompt_defaults.get("agent_kind")``); None means the
    GENERIC wording, as it does for the slots ``call_model`` fills.
    """
    if role not in PROFILE_ROLES:
        raise ValueError(
            f"unknown profile role {role!r}; known: {sorted(PROFILE_ROLES)}")
    text = (profile or "").strip()
    if not text:
        return _NO_PROFILE_SECTION.format(
            agent_kind=agent_kind or GENERIC.slots["agent_kind"])
    return f"{PROFILE_ROLES[role]}\n{text}\n\n{_PROFILE_BINDING}"
