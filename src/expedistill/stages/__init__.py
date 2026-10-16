from expedistill.stages import stage0_freeze  # noqa: F401  (stage 0: freeze -- the one pre-phase step every run keeps)
from expedistill.stages import stage4_acceptance  # noqa: F401  (stage 4: K4 acceptance -- retires with phase_learn, W2-B)
from expedistill.stages import stage5_curation  # noqa: F401  (stage 5: bank curation -- retires with phase_learn, W2-B)
from expedistill.stages import phase_execute  # noqa: F401  (the execute phases, R9/R8)
# The stage-0..6 pipeline module is NOT imported here in the Wave 2 split's
# intermediate state: it still names the stage 1/2/3/6 modules this wave
# deleted, and pulling it in would break every importer of ANY stages
# module (cli and its tests go red through it -- the known cross-domain
# red; the v4 CLI loop retires it, W2-C).
