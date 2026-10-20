"""The weak-model profile section shared by the authoring prompts.

The experiment's ``use_weak_profile`` switch decides whether the profile
text reaches a prompt. When it is off, the stage passes an empty profile;
the prompt still needs a section in that slot, because its surrounding
instructions refer to the reader's capability boundary. The neutral
section says plainly that no profile is available and keeps the reader
at a generally capable terminal agent.

The section builder is pure: the caller passes the prompt's header and
the profile text; an empty profile yields the neutral section.
"""

from typing import Optional

__all__ = ["NO_WEAK_PROFILE_SECTION", "profile_section"]

#: The no-profile section: no capability claims, no invented weaknesses.
NO_WEAK_PROFILE_SECTION = (
    "No weak-model profile is provided. Treat the reader as a generally "
    "capable terminal agent: every step must be a concrete action it can "
    "carry out, one action per step. Do not assume any specific weakness."
)


def profile_section(header: str, weak_profile: Optional[str]) -> str:
    """One prompt's profile section: header + profile, or the neutral text.

    ``header`` is the prompt's fixed line (the two authoring paths name
    the skill's reader, the live attribution path names the task's
    executor). A blank/None profile is the disabled switch's state.
    """
    text = (weak_profile or "").strip()
    if not text:
        return NO_WEAK_PROFILE_SECTION
    return f"{header}\n{text}"
