"""B1 model calling (layer 2): client, ``call_model`` entry point, providers.

All model calls in the repo go through ``call_model`` (AGENTS.md building-block
rule 4); the only exceptions are E1's local runners, which use
``OpenAICompatClient`` directly, and the TB runner, whose calls happen remotely
in Harbor.
"""
