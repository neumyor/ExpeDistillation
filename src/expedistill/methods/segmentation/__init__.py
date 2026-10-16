"""The segmentation slot's contract (stage 2, K2 + G1 + D7) -- v3 shim.

The params class lives in the evidence family package since M3 (plan
section 3's registry key set); this slot package re-exports it so the v3
config path (the registry's ``(segmentation, llm)`` entry, stage 2's
direct consumption) is byte-identical until the slot dies in M4.

Driver-less for now: the only segmentation method is the LLM splitter,
and stage 2 consumes these params directly. The slot exists so the
second segmentation method (fixed windows, none-for-ablation) registers
beside it without touching the stage.
"""

from expedistill.methods.evidence import SegmentationParams

__all__ = ["SegmentationParams"]
