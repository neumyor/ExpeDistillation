"""Internal protocol prompts used by B1 (llm.call).

They are registered in the B2 registry like every other model-facing text so
their digests are frozen into the manifest (README 1.6): changing either one
is a new experiment, not a no-op.
"""
from expedistill.prompts.registry import PromptSpec, register

register(PromptSpec(
    name="DEFAULT_SYSTEM",
    template="You are a helpful assistant. Follow the instructions exactly.",
    description="Neutral system message for every B1 call unless overridden (B1).",
))

register(PromptSpec(
    name="REPAIR_INSTRUCTION",
    template=(
        "Your previous reply was not valid JSON. Reply with ONLY the JSON, "
        "with no text before or after it."
    ),
    description="Appended when a JSON response needs repair (B1).",
))
