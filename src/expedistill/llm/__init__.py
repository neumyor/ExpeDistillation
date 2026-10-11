"""B1 model calling (layer 2): client, call_model entry point, and test double.

All model calls in the repo go through ``call_model`` (README 1.1 rule 4); the
only exceptions are E1's local runners, which use ``OpenAICompatClient``
directly, and the TB runner, whose calls happen remotely in Harbor.
"""

from expedistill.llm.call import (
    DEFAULT_SYSTEM,
    REPAIR_INSTRUCTION,
    ModelHandle,
    call_model,
    extract_json,
    make_handle,
)
from expedistill.llm.client import (
    API_KEY_ENV,
    ENDPOINT_ENV,
    CompletionResult,
    OpenAICompatClient,
)
from expedistill.llm.fake import FakeClient, json_script, make_fake_handle

__all__ = [
    "API_KEY_ENV",
    "DEFAULT_SYSTEM",
    "ENDPOINT_ENV",
    "REPAIR_INSTRUCTION",
    "CompletionResult",
    "FakeClient",
    "ModelHandle",
    "OpenAICompatClient",
    "call_model",
    "extract_json",
    "json_script",
    "make_fake_handle",
    "make_handle",
]
