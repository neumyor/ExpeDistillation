"""Method drivers (layer 6): one driver per (family, name), by-name dispatch.

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

The config speaks the v4 phase families (R8's method families plus the
P04 decision split and attribution windows); resolving a family
validates its params (the family's dataclass) before any driver is
imported.
"""

from expedistill.methods.registry import (
    resolve,
    resolve_fixed,
    resolve_params,
)

__all__ = ["resolve", "resolve_fixed", "resolve_params"]
