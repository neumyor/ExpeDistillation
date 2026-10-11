"""The per-run context every stage receives.

Built once by the pipeline; stages read it and never mutate it. Field types
are kept loose (Any) so this module stays free of upper-layer imports — the
real types are infra.store.Store, core.config.ExperimentConfig,
infra.usage.UsageRecorder, and llm.ModelHandle.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass
class StageContext:
    run_dir: Path
    store: Any                 # infra.store.Store
    config: Any                # core.config.ExperimentConfig
    usage: Any                 # infra.usage.UsageRecorder
    handles: Dict[str, Any]    # role name -> llm.ModelHandle
    workdir_root: Path          # per-run scratch root for E1 workdirs

    def handle(self, role: str) -> Any:
        """The handle for a role; unknown role raises naming known roles."""
        try:
            return self.handles[role]
        except KeyError:
            known = ", ".join(sorted(self.handles))
            raise KeyError(f"no handle for role {role!r}; known roles: {known}")
