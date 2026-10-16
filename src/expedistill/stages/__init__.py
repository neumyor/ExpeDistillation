# R9: pipeline.py and the stage1-6 fronts are gone -- the CLI
# (cli.run_experiment) loops the config's phase sequence and calls the
# phase drivers (phase_execute.py / phase_learn.py).
from expedistill.stages import stage0_freeze  # noqa: F401  (freeze: p00)
from expedistill.stages import phase_execute  # noqa: F401  (the execute phases, R9/R8)
from expedistill.stages import phase_learn  # noqa: F401  (the learn phase; replaces stage 4 / stage 5, R9/R8)
