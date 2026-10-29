"""RunSettings: the two parallelism limits and how they default."""

from expedistill import cli
from expedistill.stages.context import RunSettings


def test_rollout_follows_workers_unless_set():
    assert RunSettings(workers=128).rollout == 128
    assert RunSettings(workers=128, rollout_workers=64).rollout == 64


def test_the_default_is_the_serial_run():
    settings = RunSettings()
    assert settings.workers == 1 and settings.rollout == 1


def _args(**overrides):
    parser = cli.build_parser() if hasattr(cli, "build_parser") else None
    assert parser is not None
    base = ["run", "--config", "c.yaml", "--run-dir", "r", "--workers", "128"]
    for key, value in overrides.items():
        base += [key, str(value)]
    return parser.parse_args(base)


def test_the_resume_command_keeps_the_rollout_limit_only_when_given():
    without = cli._resume_command(_args())
    with_limit = cli._resume_command(_args(**{"--rollout-workers": 64}))

    assert "--rollout-workers" not in without
    assert "--workers 128" in with_limit and "--rollout-workers 64" in with_limit


def test_the_rollout_flag_defaults_to_unset():
    assert _args().rollout_workers is None
    assert _args(**{"--rollout-workers": 64}).rollout_workers == 64
