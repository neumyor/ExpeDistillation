"""Accumulate per-trial model-limitation notes into the weak-model profile."""

from typing import Any, Sequence

from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import call_model

__all__ = ["update_profile"]


def update_profile(
    *,
    old_profile: str,
    notes: Sequence[str],
    handle: Any,
    usage: Any = None,
    run_dir: Any = None,
) -> str:
    """Return the updated profile; no notes means no model call."""
    cleaned = [note.strip() for note in notes if note and note.strip()]
    if not cleaned:
        return old_profile
    parsed = call_model(
        handle, "PROFILE_UPDATE",
        {
            "old_profile": old_profile.strip() or "(empty)",
            "profile_notes": "\n\n".join(cleaned),
        },
        output_json=True, usage=usage, run_dir=run_dir)
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            "PROFILE_UPDATE: expected a JSON object, got "
            f"{type(parsed).__name__}")
    text = parsed.get("profile_text")
    if not isinstance(text, str):
        raise ModelOutputError(
            "PROFILE_UPDATE: profile_text must be a string")
    return text.strip()
