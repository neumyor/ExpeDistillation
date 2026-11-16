"""Layer 1: D9 skill injection (pure functions).

The single source of "skills go into the prompt": every executor prompt that
carries the bank inline (E1, via the ``whole_bank`` delivery) renders the block
here, so every phase injects through the same code path by construction. No
disk IO, no model calls.
"""
