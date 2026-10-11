"""B2 prompt registry (layer 1): every method prompt as a named constant.

Importing this package imports the four prompt modules, which register their
prompts at import time. Benchmark task-execution prompts are NOT registered
here; they live in the benchmark plugins (E1 runners).
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
from expedistill.prompts import author, curate, exam, llm, segment  # noqa: F401

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
