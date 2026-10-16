# R9: pipeline.py is gone -- the CLI (cli.run_experiment) loops the
# config's phase sequence and calls the phase drivers (phase_execute.py /
# phase_learn.py, Wave 2's other two agents). The stage1-6 modules are
# dead code under schema v4 (their config slots no longer exist) and are
# deleted by their owning agents; they stay importable here until then.
from expedistill.stages import stage0_freeze  # noqa: F401  (freeze: p00)
from expedistill.stages import stage1_baseline, stage2_segmentation  # noqa: F401  (dead code under v4; W2-A deletes)
from expedistill.stages import stage3_generation  # noqa: F401  (dead code under v4; W2-A deletes)
from expedistill.stages import stage4_acceptance  # noqa: F401  (dead code under v4; W2-B deletes)
from expedistill.stages import stage5_curation, stage6_test  # noqa: F401  (dead code under v4; W2-A/B delete)
