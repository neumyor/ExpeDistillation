"""Layer 1: business-agnostic run support (B3-B7).

Storage, freezing, parallelism, reliability, locking and usage accounting
for a run directory. Depends only on the standard library and
``expedistill.core``; knows nothing about stages, benchmarks or prompts.
"""

from expedistill.infra.manifest import (
    FrozenProtocolChanged,
    freeze,
    load,
    load_manifest,
    load_or_none,
)
from expedistill.infra.parallel import (
    MAX_WORKERS,
    ParallelError,
    effective_workers,
    run_parallel,
)
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
    ModelOutputError,
    ProviderRefusalError,
    ReliabilityError,
    UnitFailureRecorder,
    with_retries,
)
from expedistill.infra.runlock import RunLock, RunLockedError
from expedistill.infra.store import (
    KIND_DIRS,
    Store,
    append_jsonl,
    kind_for,
    object_id,
    read_json,
    read_jsonl,
    write_json,
)
from expedistill.infra.usage import UsageRecorder

__all__ = [
    "CodeBugError",
    "ConfigError",
    "FrozenProtocolChanged",
    "InfrastructureError",
    "KIND_DIRS",
    "MAX_WORKERS",
    "ModelOutputError",
    "ParallelError",
    "ProviderRefusalError",
    "ReliabilityError",
    "RunLock",
    "RunLockedError",
    "Store",
    "UnitFailureRecorder",
    "UsageRecorder",
    "append_jsonl",
    "effective_workers",
    "freeze",
    "kind_for",
    "load",
    "load_manifest",
    "load_or_none",
    "object_id",
    "read_json",
    "read_jsonl",
    "run_parallel",
    "with_retries",
    "write_json",
]
