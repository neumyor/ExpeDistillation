# R9: pipeline.py is gone -- the CLI (cli.run_experiment) loops the
# config's phase sequence and calls the phase drivers (phase_execute.py /
# phase_learn.py, Wave 2's other two agents).
from expedistill.stages import stage0_freeze  # noqa: F401  (freeze: p00)
from expedistill.stages import stage4_acceptance, stage5_curation  # noqa: F401  (dead code under v4; retires with the wave2/b merge)
from expedistill.stages import phase_execute  # noqa: F401  (the execute phases, R9/R8)
