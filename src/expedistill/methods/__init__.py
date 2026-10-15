"""Method drivers (layer 6): one driver per (slot, name), by-name dispatch.

A method is the DRIVER -- the function that takes the run context,
orchestrates composites and disposes failures (R1 of the multi-method
plan, docs/decisions/multi_method_framework_20261015.md section 2); the
composites it calls stay in composites/ and never move here. The
name -> driver table lives in ``registry`` as plain data: registration
imports nothing, so the dispatch never loads the live driver module;
a name the config asks for that is not in the table fails loudly. (The
frozen prompt-name surface is the import-time snapshot, not the module
graph: importing ``composites`` as a package has always pulled the live
blocks in through ``composites/__init__`` -- the guarded invariant is
the name set, not which modules happen to be loaded.)

P1a keeps the table metadata-free: the config still speaks the v2 shape
(core.config.acceptance_mode / skill_delivery), and the slots resolve
the driver by that mode string. Params dataclasses and the check() hook
arrive with the v3 schema switch (P1b).
"""

from expedistill.methods.registry import get_method, resolve, resolve_params

__all__ = ["get_method", "resolve", "resolve_params"]
