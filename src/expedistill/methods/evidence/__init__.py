"""The evidence family's contract: how the add path's evidence is cut.

The evidence family's one method (``segment``, plan section 3's registry
key set) is the add path's evidence: G1+D7 segmentation through K2, still
emitting Subtask objects. The params class moved here from the
segmentation slot package in M3 (the registry's new ``(evidence,
segment)`` entry resolves it); the slot package re-exports it so the v3
config path -- the ``(segmentation, llm)`` entry stage 2 consumes -- is
byte-identical until the slot dies in M4. Driver-less until M4: the
phase wiring lands with the config schema, so today only the params
resolve (and the class's v3-slot error wording rides along unchanged).
"""

from dataclasses import dataclass

from expedistill.core.config import ConfigError

__all__ = ["SegmentationParams"]


@dataclass(frozen=True)
class SegmentationParams:
    """Max segments per trajectory, and K2's re-segmentation budget."""

    K: int
    retry_limit: int = 2

    def __post_init__(self) -> None:
        if self.K < 1:
            raise ConfigError(f"methods.segmentation.K must be >= 1, got {self.K}")
        if self.retry_limit < 0:
            raise ConfigError(
                "methods.segmentation.retry_limit must be >= 0, "
                f"got {self.retry_limit}")
