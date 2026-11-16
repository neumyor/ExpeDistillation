"""Layer 1: business-agnostic run support (B3-B7).

Storage, freezing, parallelism, reliability, locking and usage accounting
for a run directory. Depends only on the standard library and
``expedistill.core``; knows nothing about stages, benchmarks or prompts.
"""
