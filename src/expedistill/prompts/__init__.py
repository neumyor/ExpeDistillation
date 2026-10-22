"""B2 prompt registry (layer 1): every method prompt as a named constant.

Importing this package imports every prompt module, which registers its
prompts at import time -- including the live attribution/revision prompts
and the usage-aware attribution/retrigger prompts, whose v2-era lazy
registration died with the v3 schema switch (the manifest now freezes
the digests of ALL registered method prompts plus the run's task prompt;
the pre-registered static test pins that every prompts/*.py module is in
this import list, so a prompt cannot exist outside the frozen surface).
Benchmark task-execution prompts are NOT registered here; they live in
the benchmark plugins (E1 runners).
"""

from expedistill.prompts.registry import (
    PROMPTS,
    DuplicatePromptError,
    PromptNotFoundError,
    PromptSpec,
    digest,
    digests,
    get,
    register,
)

# Importing the modules registers their prompts in PROMPTS.
from expedistill.prompts import (  # noqa: F401
    audit,
    attribution,
    author,
    curate,
    exam,
    llm,
    profile,
    segment,
)

#: Names of the method prompts (everything this package registers). Exact by
#: import order: a plugin can only call ``register`` after importing this
#: package, so nothing else is in PROMPTS when this line runs.
METHOD_PROMPT_NAMES = frozenset(PROMPTS)

__all__ = [
    "METHOD_PROMPT_NAMES",
    "PROMPTS",
    "DuplicatePromptError",
    "PromptNotFoundError",
    "PromptSpec",
    "digest",
    "digests",
    "get",
    "register",
]
