"""The per-run context every stage receives.

Built once by the pipeline; stages read it and never mutate it (the
dataclass is frozen -- the historical "read-only" docstring is now
enforced). Field types are kept loose (Any) so this module stays free of
upper-layer imports -- the real types are infra.store.Store,
core.config.ExperimentConfig, infra.usage.UsageRecorder, and
llm.ModelHandle.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = ["RunSettings", "StageContext"]


@dataclass(frozen=True)
class RunSettings:
    """Operational run settings (AGENTS.md section 5 rule 3): they change
    how hard a run tries, never what it measures, so they live outside
    ExperimentConfig and the frozen manifest, and a resume may change them
    freely. Typed here so the stages read one field instead of getattr
    chains on the context -- the workers/stagger/shortfall plumbing used
    to be pasted into every stage file.

    The pipeline validates the values (``_check_run_settings``) before
    constructing the context; tests constructing contexts directly are
    deliberate callers and bypass that on purpose.
    """

    #: Per-batch parallelism (B5 thread count) the stages request for work
    #: that only calls the LLM endpoint (authoring, review, curation, exam).
    workers: int = 1
    #: Parallelism of benchmark rollouts (K1 dispatch of real executions);
    #: ``None`` follows ``workers``. A rollout holds a sandbox on the
    #: benchmark host for minutes, so its limit is the host's, not the
    #: endpoint's.
    rollout_workers: Optional[int] = None
    #: Per-unit random startup jitter (seconds) K1 applies before dispatch.
    k1_stagger: float = 0.0
    #: Whether NEW infra holes are accepted (the accepted set is durable
    #: per run and every later call honours it with or without the flag).
    allow_infra_shortfall: bool = False

    @property
    def rollout(self) -> int:
        """The rollout parallelism in effect."""
        return self.workers if self.rollout_workers is None \
            else self.rollout_workers


@dataclass(frozen=True)
class StageContext:
    run_dir: Path
    store: Any                 # infra.store.Store
    config: Any                # core.config.ExperimentConfig
    usage: Any                 # infra.usage.UsageRecorder
    handles: Dict[str, Any]    # role name -> llm.ModelHandle
    workdir_root: Path         # per-run scratch root for E1 workdirs
    #: The operational settings above; defaults are the serial run.
    run_settings: RunSettings = field(default_factory=RunSettings)

    def handle(self, role: str) -> Any:
        """The handle for a role; unknown role raises naming known roles."""
        try:
            return self.handles[role]
        except KeyError:
            known = ", ".join(sorted(self.handles))
            raise KeyError(f"no handle for role {role!r}; known roles: {known}")
