"""Phase world (layer 7): the freeze step and the phase drivers.

The CLI's loop (``cli.run_experiment``) walks the config's phase sequence and
imports ``phase_execute`` / ``phase_learn`` lazily per dispatch
(``cli._phase_driver``); a ``sys.modules`` entry is the tests' injection seam
for fake drivers, so this package must not import them eagerly.
"""
