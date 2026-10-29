"""RemoteLink in local mode: the driver runs on the benchmark host."""

import os
import shutil

import pytest

from expedistill.benchmarks.tb.remote import (
    ENV_HOST,
    ENV_KEY,
    ENV_ROOT,
    LOCAL_HOST,
    RemoteLink,
)
from expedistill.infra.reliability import ConfigError, InfrastructureError


@pytest.fixture()
def link(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_HOST, LOCAL_HOST)
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.setenv(ENV_ROOT, str(tmp_path))
    return RemoteLink()


def test_local_mode_needs_no_key(link):
    assert link.local is True and link.key_path == ""


def test_an_empty_host_is_still_a_config_error(monkeypatch):
    monkeypatch.setenv(ENV_HOST, "")
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.setenv(ENV_ROOT, "/data2/niuyiming/x")
    with pytest.raises(ConfigError, match=ENV_HOST):
        RemoteLink()


def test_a_remote_host_still_requires_the_key(monkeypatch):
    monkeypatch.setenv(ENV_HOST, "user@node29")
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.setenv(ENV_ROOT, "/data2/niuyiming/x")
    with pytest.raises(ConfigError, match=ENV_KEY):
        RemoteLink()


def test_run_returns_stdout_stderr_and_the_commands_own_exit_code(link):
    out, err, rc = link.run("echo hello; echo oops >&2; exit 3")

    assert (out, err, rc) == ("hello\n", "oops\n", 3)


def test_run_uses_a_clean_environment_like_a_login_session(link, monkeypatch):
    monkeypatch.setenv("EXPEDISTILL_LLM_API_KEY", "driver-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "driver-secret")

    out, _, rc = link.run("env")

    assert rc == 0
    assert "driver-secret" not in out
    assert "HOME=" in out and "PATH=" in out


def test_run_does_not_see_the_drivers_virtualenv_first_on_path(link):
    import sys

    out, _, _ = link.run('printf %s "$PATH"')

    assert os.path.join(sys.prefix, "bin") not in out.split(os.pathsep)


def test_run_starts_in_the_home_directory(link):
    out, _, _ = link.run("pwd")

    assert out.strip() == os.path.expanduser("~")


def test_run_timeout_is_an_infrastructure_error_without_the_command(link):
    with pytest.raises(InfrastructureError) as excinfo:
        link.run("sleep 5; echo secret-command-text", timeout=0.2)

    assert "secret-command-text" not in str(excinfo.value)
    assert "timed out" in str(excinfo.value)


def test_put_and_get_copy_files(link, tmp_path):
    source = tmp_path / "in.txt"
    source.write_text("payload")
    stored = tmp_path / "remote.txt"
    fetched = tmp_path / "out.txt"

    link.put(str(source), str(stored))
    link.get(str(stored), str(fetched))

    assert stored.read_text() == "payload" and fetched.read_text() == "payload"


def test_get_of_a_missing_file_is_an_infrastructure_error(link, tmp_path):
    with pytest.raises(InfrastructureError, match="local copy failed"):
        link.get(str(tmp_path / "missing"), str(tmp_path / "out"))


@pytest.mark.skipif(shutil.which("setsid") is None,
                    reason="setsid(1) is a Linux tool (the benchmark host)")
def test_a_detached_command_survives_its_launcher(link, tmp_path):
    """The plugin starts the runner with ``nohup setsid ... & echo $!``."""
    marker = tmp_path / "done"
    out, _, rc = link.run(
        f"nohup setsid bash -c 'sleep 0.3; touch {marker}' "
        ">/dev/null 2>&1 < /dev/null & echo $!")

    assert rc == 0 and out.strip().isdigit()
    import time
    for _ in range(40):
        if marker.exists():
            break
        time.sleep(0.1)
    assert marker.exists()
