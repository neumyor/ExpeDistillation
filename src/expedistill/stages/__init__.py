# R9: pipeline.py and the stage1-6 fronts are gone -- the CLI
# (cli.run_experiment) loops the config's phase sequence and calls the
# phase drivers (phase_execute.py / phase_learn.py).
from expedistill.stages import stage0_freeze  # noqa: F401  (freeze: p00)
# The two phase drivers are deliberately NOT imported here. The CLI's
# loop imports them lazily per dispatch (cli._phase_driver), and a
# sys.modules entry is the tests' injection seam for fake drivers
# (tests/unit/test_w2c_run_loop.py): an eager import binds the real
# modules as package attributes and routes `from expedistill.stages
# import phase_execute` around the seam (the W2 integration finding --
# W2-A/W2-B had added these imports, W2-C's loop tests went red).
