"""Skill-folder M3, the TB sandbox leg: the folder rides the per-run
skills directory into the sandbox. The task-level SKILL.md carries the
guidance; each skill is its own ``<skill>/SKILL.md`` directory, and the
catalog stays in catalog.md as a fallback. The recorded trajectory
keeps the agent's cat commands for M4's parsing.

Everything goes through the fake links of tests/helpers/tb_fake_remote.py (no
network, no ssh). The remote contract this pins: the plugin uploads the
task-level SKILL.md + catalog.md + every manifest file into
``<TBENCH_SKILL_ROOT>/<run_id>/<task_name>/`` (and the dashed alias
directory for dotted task names), which the vendored
TencentTaskSkillE2BEnvironment mounts into the sandbox read-only at
``/opt/openclaw-skills/<task_name>/``. Harbor's Terminus agent scans
``<task>/<skill>/SKILL.md`` itself and renders the
``<available_skills>`` list.
"""

import json
from pathlib import Path

import pytest

from expedistill.benchmarks.base import Task
from expedistill.benchmarks.tb.protocol import MOUNT_PLACEHOLDER
from tests.helpers.tb_fake_remote import (
    HANDLE,
    ROOT,
    TRIAL,
    FakeRemoteLink,
    JobsRemoteLink,
    make_plugin,
)
from tests.helpers.tb_folder import (
    CAT_TRAJECTORY,
    CATALOG,
    MANIFEST,
    materialized,
    trial_files_with_cat,
)


def _folder_task(tmp_path, task_name="train-fasttext"):
    return Task(task_id=task_name, instruction=task_name,
                meta={"task_name": task_name, "attempt_token": "tok",
                      "skill_folder": MANIFEST,
                      "skill_folder_dir": str(materialized(tmp_path))})


def test_folder_unit_ships_the_folder_and_the_placeholder(tmp_path):
    link = FakeRemoteLink(
        polls=[f"RESULT {TRIAL}/result.json\n"],
        remote_files=trial_files_with_cat())
    plugin = make_plugin(link)
    workdir = tmp_path / "work"
    folder_dir = materialized(tmp_path)

    outcome = plugin.run(_folder_task(tmp_path), HANDLE, CATALOG, workdir)

    # The task-level SKILL.md is the mount placeholder, never the catalog.
    skill_remote = f"{ROOT}/skills/ed_train-fasttext_tok/train-fasttext/SKILL.md"
    local_skill = workdir / "skills" / "train-fasttext" / "SKILL.md"
    assert local_skill.read_text(encoding="utf-8") == MOUNT_PLACEHOLDER
    assert (skill_remote, ) == tuple(
        remote for local, remote in link.puts if remote == skill_remote)
    # The catalog and every manifest file ride the same directory as ONE
    # tar over one scp (the phase-once cache; the manifest carries names
    # only -- the bodies came from the bank through the composite), and
    # the per-unit staging is a single remote cp from that cache: the
    # simulator's file tree then holds the task directory's files
    # byte-identical to the materialized folder.
    tar_puts = [(local, remote) for local, remote in link.puts
                if remote.startswith(f"{ROOT}/skills_cache/")
                and remote.endswith(".tar")]
    assert len(tar_puts) == 1
    import io
    import tarfile as _tarfile
    # The local tar is deleted right after the put and the remote copy
    # by the ship's cleanup rm; the put-time snapshot keeps the bytes.
    shipped = dict(link.uploads)[tar_puts[0][1]]
    with _tarfile.open(fileobj=io.BytesIO(shipped)) as tar:
        members = {member.name: tar.extractfile(member).read()
                   for member in tar.getmembers() if member.isfile()}
    expected = {
        name: (folder_dir / name).read_bytes()
        for name in ([entry["filename"] for entry in MANIFEST["files"]]
                     + [MANIFEST["catalog_filename"]])}
    assert members == expected
    staged = f"{ROOT}/skills/ed_train-fasttext_tok/train-fasttext"
    for name, payload in expected.items():
        assert link.remote_files[f"{staged}/{name}"] == payload
    # The runner is pointed at the per-run skills root in skills mode.
    submit = next(cmd for cmd in link.commands if "nohup" in cmd)
    assert "TBENCH_EVAL_MODE=skills" in submit
    assert (f"TBENCH_SKILL_ROOT={ROOT}/skills/ed_train-fasttext_tok"
            in submit)
    # The outcome's meta records the folder facts and the card rule.
    assert outcome.meta["eval_mode"] == "skills"
    assert outcome.meta["skills_injected"] is True
    assert outcome.meta["prompt_name"] == "TB_SKILL_READ"
    assert outcome.meta["skill_folder"] == {
        "local_dir": str(folder_dir),
        "bank_fingerprint": MANIFEST["bank_fingerprint"],
        "catalog_filename": MANIFEST["catalog_filename"],
        "description_mode": MANIFEST["description_mode"],
        "files": [entry["filename"] for entry in MANIFEST["files"]],
    }
    # The trajectory keeps the agent's cat commands verbatim -- M4's
    # parsing input, unintercepted and unbudgeted by execution.
    actions = "\n".join(step["action"] for step in outcome.steps)
    assert "cat /opt/openclaw-skills/train-fasttext/catalog.md" in actions
    assert ("cat /opt/openclaw-skills/train-fasttext/"
            "type-001-skill/SKILL.md") in actions


def test_folder_unit_uploads_the_dashed_alias_too(tmp_path):
    # The same spelling rule SKILL.md follows (the env looks the
    # directory up by the environment name, whose authoring export
    # normalizes dots to dashes): a dotted task name ships the whole
    # folder to BOTH spellings.
    link = FakeRemoteLink(
        polls=[f"RESULT {ROOT}/jobs/ed_install-windows-3.11_tok/"
               f"install-windows-3.11__AbCdEf1/result.json\n"],
        remote_files={
            f"{ROOT}/jobs/ed_install-windows-3.11_tok/"
            f"install-windows-3.11__AbCdEf1/result.json":
                Path("tests/fixtures/tb/result_success.json").read_text(
                    encoding="utf-8"),
            f"{ROOT}/jobs/ed_install-windows-3.11_tok/"
            f"install-windows-3.11__AbCdEf1/agent/trajectory.json":
                json.dumps(CAT_TRAJECTORY),
            f"{ROOT}/jobs/ed_install-windows-3.11_tok/"
            f"install-windows-3.11__AbCdEf1/verifier/test-stdout.txt":
                Path("tests/fixtures/tb/verifier_collected.txt").read_text(
                    encoding="utf-8"),
        })
    plugin = make_plugin(link)
    task = _folder_task(tmp_path, task_name="install-windows-3.11")

    plugin.run(task, HANDLE, CATALOG, tmp_path / "work")

    # The staging cp names BOTH spellings (the same rule SKILL.md's
    # alias upload follows), and the simulator's tree holds the folder
    # files under both directories; SKILL.md itself rides each spelling
    # by its own scp, as before.
    remotes = [remote for _, remote in link.puts]
    assert (f"{ROOT}/skills/ed_install-windows-3.11_tok/"
            f"install-windows-3.11/SKILL.md") in remotes
    assert (f"{ROOT}/skills/ed_install-windows-3.11_tok/"
            f"install-windows-3-11/SKILL.md") in remotes
    copies = [cmd for cmd in link.commands if "cp -r " in cmd]
    assert len(copies) == 1
    assert "install-windows-3.11/" in copies[0]
    assert "install-windows-3-11/" in copies[0]
    for name in ([entry["filename"] for entry in MANIFEST["files"]]
                 + [MANIFEST["catalog_filename"]]):
        payload = materialized(tmp_path).joinpath(name).read_bytes()
        assert link.remote_files[
            f"{ROOT}/skills/ed_install-windows-3.11_tok/"
            f"install-windows-3.11/{name}"] == payload
        assert link.remote_files[
            f"{ROOT}/skills/ed_install-windows-3.11_tok/"
            f"install-windows-3-11/{name}"] == payload


def test_folder_unit_refuses_an_unmaterialized_directory(tmp_path):
    plugin = make_plugin(FakeRemoteLink())
    task = Task(task_id="train-fasttext", instruction="train-fasttext",
                meta={"task_name": "train-fasttext",
                      "attempt_token": "tok",
                      "skill_folder": MANIFEST})
    with pytest.raises(Exception, match="skill_folder_dir"):
        plugin.run(task, HANDLE, CATALOG, tmp_path / "work")


def test_a_bare_unit_uploads_no_folder_and_no_suffix(tmp_path):
    # The control arm's bare unit: no manifest on the task, an empty
    # skills block, and therefore no upload at all -- the pre-folder
    # behaviour, byte for byte.
    link = FakeRemoteLink(
        polls=[f"RESULT {TRIAL}/result.json\n"],
        remote_files=trial_files_with_cat())
    plugin = make_plugin(link)
    task = Task(task_id="train-fasttext", instruction="train-fasttext",
                meta={"task_name": "train-fasttext", "attempt_token": "tok"})

    outcome = plugin.run(task, HANDLE, "", tmp_path / "work")

    assert outcome.meta["eval_mode"] == "bare"
    assert outcome.meta["skills_injected"] is False
    assert outcome.meta["prompt_name"] == "TB_TERMINUS_TEMPLATE"
    assert "skill_folder" not in outcome.meta
    assert not any("skills/" in remote for _, remote in link.puts)


def test_two_folder_units_share_one_cache_upload(tmp_path):
    """The transport fix's heart: the folder ships ONCE per folder
    content, no matter how many units ride it. The second unit finds
    the cache (test -d), stages with its own cp, and never puts a
    second tar -- the 2026-10-18 storm was 84 units x 141 per-file
    scp connections; this shape is one scp plus one cp ssh per unit."""
    second_trial = (f"{ROOT}/jobs/ed_crack-7z-hash_tok/"
                    f"crack-7z-hash__AbCdEf1")
    files = trial_files_with_cat()
    for name, text in (
            ("result.json", Path("tests/fixtures/tb/result_success.json")
             .read_text(encoding="utf-8")),
            ("agent/trajectory.json", json.dumps(CAT_TRAJECTORY)),
            ("verifier/test-stdout.txt",
             Path("tests/fixtures/tb/verifier_collected.txt")
             .read_text(encoding="utf-8"))):
        files[f"{second_trial}/{name}"] = text
    link = FakeRemoteLink(
        polls=[f"RESULT {TRIAL}/result.json\n",
               f"RESULT {second_trial}/result.json\n"],
        remote_files=files)
    plugin = make_plugin(link)
    first = plugin.run(_folder_task(tmp_path), HANDLE, CATALOG,
                       tmp_path / "work1")
    tars_after_first = [remote for _, remote in link.puts
                        if remote.startswith(f"{ROOT}/skills_cache/")
                        and remote.endswith(".tar")]
    assert len(tars_after_first) == 1

    second = plugin.run(_folder_task(tmp_path, task_name="crack-7z-hash"),
                        HANDLE, CATALOG, tmp_path / "work2")

    # Exactly one tar ever; the second unit staged by cp alone.
    tars = [remote for _, remote in link.puts
            if remote.startswith(f"{ROOT}/skills_cache/")
            and remote.endswith(".tar")]
    assert tars == tars_after_first
    copies = [cmd for cmd in link.commands if "cp -r " in cmd]
    assert len(copies) == 2
    assert "crack-7z-hash" in copies[1]
    # Both task directories hold the folder's exact bytes.
    folder_dir = materialized(tmp_path)
    for task in ("train-fasttext", "crack-7z-hash"):
        for name in ([entry["filename"] for entry in MANIFEST["files"]]
                     + [MANIFEST["catalog_filename"]]):
            expected = folder_dir.joinpath(name).read_bytes()
            assert link.remote_files[
                f"{ROOT}/skills/ed_{task}_tok/{task}/{name}"] == expected
    assert second.meta["skill_folder"] == first.meta["skill_folder"]


def test_the_gate_covers_the_probe_and_fresh_launch(tmp_path, monkeypatch):
    """The rolling admission covers every connection-building step: the
    pre-submit probe, then a fresh launch's upload+submit, each take a
    gate slot. A resumed unit (state=result) still probes under the gate
    but performs no upload -- the long poll wait holds no slot."""
    from expedistill.benchmarks.tb import plugin as tb_plugin

    events = []

    class RecordingGate:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, *args):
            events.append("exit")

    monkeypatch.setattr(tb_plugin, "_UPLOAD_GATE", RecordingGate())
    # The stateful fake: the first run's finished trial stays visible to
    # the second run's probe, so the second call really resumes.
    link = JobsRemoteLink()
    plugin = make_plugin(link)
    task = _folder_task(tmp_path)

    first = plugin.run(task, HANDLE, CATALOG, tmp_path / "work")
    puts_after_first = list(link.puts)
    # Probe, then upload+submit.
    assert events == ["enter", "exit", "enter", "exit"]
    # The finished trial is reused on the second call: the probe is
    # gated, the upload is not performed.
    second = plugin.run(task, HANDLE, CATALOG, tmp_path / "work")
    assert events == ["enter", "exit", "enter", "exit", "enter", "exit"]
    assert second.meta["resumed"] == "result"
    assert first.meta["resumed"] is None
    # Resume re-uploads nothing and returns the same verdict and folder facts.
    assert link.puts == puts_after_first
    assert second.correctness == first.correctness is True
    assert second.meta["skill_folder"] == first.meta["skill_folder"]
