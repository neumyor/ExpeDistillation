"""Method drivers (layer 6): one driver per (family, name), by-name dispatch.

A method is the DRIVER -- the function that takes the run context,
orchestrates composites and disposes failures (R1 of the multi-method
plan, docs/decisions/multi_method_framework_20261015.md section 2); the
composites it calls stay in composites/ and never move here. The
name -> driver table lives in ``registry`` as plain data: registration
imports nothing, and a name the config asks for that is not in the table
fails loudly. Resolving a family validates its params (the family's
dataclass) before any driver is imported.
"""
