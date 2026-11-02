"""The weak-model profile section shared by the authoring prompts.

The experiment's ``use_weak_profile`` switch decides whether the profile
text reaches a prompt. When it is off, the stage passes an empty profile;
the prompt still needs a section in that slot, because its surrounding
instructions refer to the reader's capability boundary. The neutral
section says plainly that no profile is available and keeps the reader
at a generally capable agent of the benchmark's kind.

The section builder is pure: the caller passes the prompt's header and
the profile text; an empty profile yields the neutral section.
"""

from typing import Optional

from expedistill.prompts.learning_context import GENERIC

__all__ = ["profile_section"]

#: The no-profile section: no capability claims, no invented weaknesses.
_NO_WEAK_PROFILE_SECTION = (
    "No weak-model profile is provided. Treat the reader as a generally "
    "capable {agent_kind} agent: every step must be a concrete action it can "
    "carry out, one action per step. Do not assume any specific weakness."
)


def profile_section(header: str, weak_profile: Optional[str],
                    agent_kind: Optional[str] = None) -> str:
    """One prompt's profile section: header + profile, or the neutral text.

    ``header`` is the prompt's fixed line (the two authoring paths name
    the skill's reader, the live attribution path names the task's
    executor). A blank/None profile is the disabled switch's state.
    ``agent_kind`` is the benchmark's ``agent_kind`` slot (a handle's
    ``prompt_defaults.get("agent_kind")``); None means the GENERIC wording,
    as it does for the slots ``call_model`` fills.
    """
    text = (weak_profile or "").strip()
    if not text:
        return _NO_WEAK_PROFILE_SECTION.format(
            agent_kind=agent_kind or GENERIC.slots["agent_kind"])
    return f"{header}\n{text}"
