from expedistill.stages import stage0_freeze, stage1_baseline, stage2_segmentation  # noqa: F401  (stage 0: freeze; stage 1: dual baseline; stage 2: segmentation)
from expedistill.stages import stage3_generation  # noqa: F401  (stage 3: skills + exams)
from expedistill.stages import stage6_test  # noqa: F401  (stage 6: test)
from expedistill.stages import phase_learn  # noqa: F401  (the learn phase driver; replaces stage 4 / stage 5, R9)
# pipeline is NOT imported here: it still names the retired stage 4 / 5
# fronts until W2-C's second commit deletes it (the R9 flip's known
# intermediate state -- `import expedistill.stages.pipeline` still works
# for its own importers and fails exactly there).
