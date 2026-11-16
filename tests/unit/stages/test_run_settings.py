"""RunSettings: the two parallelism limits and how they default."""

from expedistill.stages.context import RunSettings


def test_rollout_follows_workers_unless_set():
    assert RunSettings().rollout == 1
    assert RunSettings(workers=128).rollout == 128
    assert RunSettings(workers=128, rollout_workers=64).rollout == 64
