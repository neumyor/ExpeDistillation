"""B2 prompt registry: named prompt constants with digests for manifest freezing.

Every prompt sent to a model is a named constant registered here. Prompt text is
part of the experiment protocol: its sha256 digest goes into the run manifest
(B4), and any text change after a run started means a new experiment.

The generic core registry (``core/registry.py``) is intentionally not used yet:
this module only needs a plain dict plus digest helpers, and keeping it
self-contained lets the prompts layer land independently.
"""

import hashlib
from dataclasses import dataclass
from typing import Dict

__all__ = [
    "DuplicatePromptError",
    "PromptNotFoundError",
    "PromptSpec",
    "PROMPTS",
    "digest",
    "digests",
    "get",
    "register",
]


class DuplicatePromptError(ValueError):
    """Raised when registering a prompt name that is already taken."""


class PromptNotFoundError(KeyError):
    """Raised when looking up a prompt name that is not registered."""


@dataclass(frozen=True)
class PromptSpec:
    """One named prompt: a str.format template plus a one-line description.

    ``description`` states the purpose and the owning block (e.g. "G1: ...").
    ``template`` uses ``str.format`` placeholders; literal braces in the text
    (JSON examples) must be doubled.
    """

    name: str
    template: str
    description: str


PROMPTS: Dict[str, PromptSpec] = {}


def register(spec: PromptSpec) -> PromptSpec:
    """Add ``spec`` to the registry; a duplicate name raises."""
    if spec.name in PROMPTS:
        raise DuplicatePromptError(f"prompt already registered: {spec.name}")
    PROMPTS[spec.name] = spec
    return spec


def _spec(name: str) -> PromptSpec:
    try:
        return PROMPTS[name]
    except KeyError:
        known = ", ".join(sorted(PROMPTS))
        raise PromptNotFoundError(
            f"unknown prompt: {name}; known prompts: {known}"
        ) from None


def get(name: str) -> str:
    """Return the template of prompt ``name``; unknown names raise."""
    return _spec(name).template


def digest(name: str) -> str:
    """sha256 hexdigest of prompt ``name``'s template (for manifest freezing)."""
    return hashlib.sha256(_spec(name).template.encode("utf-8")).hexdigest()


def digests() -> Dict[str, str]:
    """Digest of every registered prompt, keyed by name."""
    return {
        name: hashlib.sha256(spec.template.encode("utf-8")).hexdigest()
        for name, spec in sorted(PROMPTS.items())
    }
