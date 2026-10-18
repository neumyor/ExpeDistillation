"""Skill-folder M3, the TB sandbox leg: the folder rides the per-run
skills directory into the sandbox, the guidance (never the catalog) is
the injected SKILL.md, and the recorded trajectory keeps the agent's
cat commands for M4's parsing.

Everything goes through the fake links of test_tb_plugin.py (no
network, no ssh). The remote contract this pins: the plugin uploads
SKILL.md + catalog.md + every manifest file into
``<TBENCH_SKILL_ROOT>/<run_id>/<task_name>/`` (and the dashed alias
directory for dotted task names), which the vendored
TencentTaskSkillE2BEnvironment mounts into the sandbox read-only at
``/opt/openclaw-skills/<task_name>/`` -- the mechanism is the existing
skills-mode one, unchanged; a folder unit only changes WHAT rides it.
"""

import json
from pathlib import Path

import pytest

from expedistill.benchmarks.base import Task
from expedistill.benchmarks.tb import TBPlugin
from expedistill.benchmarks.tb.plugin import TB_SKILL_FOLDER_GUIDANCE
from expedistill.composites.skill_folder import materialize_skill_folder
from expedistill.core.models import Skill
from expedistill.llm.call import ModelHandle
from expedistill.methods.delivery.folder import skill_folder_manifest
from tests.unit.benchmarks.test_tb_plugin import (
    FakeRemoteLink,
    _completed_trial_files,
    _make_plugin,
)

ROOT = "/data2/niuyiming/expedistill"
TRIAL = f"{ROOT}/jobs/ed_train-fasttext_tok/train-fasttext__AbCdEf1"

HANDLE = ModelHandle(name="qwen3.6-flash-distill", role="executor_weak",
                     client=None)

_BANK = [
    Skill(skill_id="traj/tb:t1/strong/00/seg00/skill",
          content="[When to use] The service rejects connections.",
          subtask_id="st", source_subtasks=["st"], version=1,
          status="accepted"),
    Skill(skill_id="bank/merge/0001",
          content="[When to use] Check the log before restarting.",
          subtask_id="st", source_subtasks=["st"], version=3,
          status="accepted"),
]
_MANIFEST = skill_folder_manifest(_BANK)
_CATALOG = _MANIFEST["catalog_text"]


def _materialized(tmp_path):
    return materialize_skill_folder(_MANIFEST, _BANK,
                                    tmp_path / "skill_folder")


def _folder_task(tmp_path, task_name="train-fasttext"):
    return Task(task_id=task_name, instruction=task_name,
                meta={"task_name": task_name, "attempt_token": "tok",
                      "skill_folder": _MANIFEST,
                      "skill_folder_dir": str(_materialized(tmp_path))})


#: A trajectory whose agent cats the catalog and one skill file inside
#: the sandbox mount -- the raw material M4's read audit parses. The
#: step shape is Harbor's (benchmarks/tb/trajectory.py's frozen input).
_CAT_TRAJECTORY = {
    "schema_version": "harbor-trajectory-1",
    "session_id": "sess-folder",
    "agent": {"model_name": "qwen3.6-flash-distill"},
    "steps": [
        {"step_id": 1, "source": "user",
         "message": "TASK: train a fasttext model."},
        {"step_id": 2, "source": "agent",
         "tool_calls": [{"tool_call_id": "c1", "function_name": "bash_command",
                         "arguments": {"keystrokes":
                                       "cat /opt/openclaw-skills/"
                                       "train-fasttext/catalog.md\n"}}],
         "observation": {"results": [{"content": _CATALOG}]}},
        {"step_id": 3, "source": "agent",
         "tool_calls": [{"tool_call_id": "c2", "function_name": "bash_command",
                         "arguments": {"keystrokes":
                                       "cat /opt/openclaw-skills/"
                                       "train-fasttext/"
                                       "traj_tb_t1_strong_00_seg00_skill.md"
                                       "\n"}}],
         "observation": {"results": [{"content":
                                      _BANK[0].content}]}},
    ],
    "final_metrics": {"total_prompt_tokens": 100,
                      "total_completion_tokens": 20,
                      "total_cached_tokens": 0},
}


def _trial_files_with_cat():
    files = _completed_trial_files()
    files[f"{TRIAL}/agent/trajectory.json"] = json.dumps(_CAT_TRAJECTORY)
    return files


def test_folder_unit_ships_the_folder_and_the_guidance(tmp_path):
    link = FakeRemoteLink(
        polls=[f"RESULT {TRIAL}/result.json\n"],
        remote_files=_trial_files_with_cat())
    plugin = _make_plugin(link)
    workdir = tmp_path / "work"
    folder_dir = _materialized(tmp_path)

    outcome = plugin.run(_folder_task(tmp_path), HANDLE, _CATALOG, workdir)

    # The prompt-side injection is the GUIDANCE, never the catalog.
    skill_remote = f"{ROOT}/skills/ed_train-fasttext_tok/train-fasttext/SKILL.md"
    local_skill = workdir / "skills" / "train-fasttext" / "SKILL.md"
    assert local_skill.read_text(encoding="utf-8") == TB_SKILL_FOLDER_GUIDANCE
    assert TB_SKILL_FOLDER_GUIDANCE != _CATALOG
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
        for name in ([entry["filename"] for entry in _MANIFEST["files"]]
                     + [_MANIFEST["catalog_filename"]])}
    assert members == expected
    staged = f"{ROOT}/skills/ed_train-fasttext_tok/train-fasttext"
    for name, payload in expected.items():
        assert link.remote_files[f"{staged}/{name}"] == payload
    # The runner is pointed at the per-run skills root in skills mode.
    submit = next(cmd for cmd in link.commands if "nohup" in cmd)
    assert "TBENCH_EVAL_MODE=skills" in submit
    assert (f"TBENCH_SKILL_ROOT={ROOT}/skills/ed_train-fasttext_tok"
            in submit)
    # The outcome's meta records the folder facts and the folder prompt.
    assert outcome.meta["eval_mode"] == "skills"
    assert outcome.meta["skills_injected"] is True
    assert outcome.meta["prompt_name"] == "TB_TASK_PROMPT_FOLDER"
    assert outcome.meta["skill_folder"] == {
        "local_dir": str(folder_dir),
        "bank_fingerprint": _MANIFEST["bank_fingerprint"],
        "catalog_filename": _MANIFEST["catalog_filename"],
        "files": [entry["filename"] for entry in _MANIFEST["files"]],
    }
    # The trajectory keeps the agent's cat commands verbatim -- M4's
    # parsing input, unintercepted and unbudgeted by execution.
    actions = "\n".join(step["action"] for step in outcome.steps)
    assert "cat /opt/openclaw-skills/train-fasttext/catalog.md" in actions
    assert ("cat /opt/openclaw-skills/train-fasttext/"
            "traj_tb_t1_strong_00_seg00_skill.md") in actions


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
                json.dumps(_CAT_TRAJECTORY),
            f"{ROOT}/jobs/ed_install-windows-3.11_tok/"
            f"install-windows-3.11__AbCdEf1/verifier/test-stdout.txt":
                Path("tests/fixtures/tb/verifier_collected.txt").read_text(
                    encoding="utf-8"),
        })
    plugin = _make_plugin(link)
    task = _folder_task(tmp_path, task_name="install-windows-3.11")

    plugin.run(task, HANDLE, _CATALOG, tmp_path / "work")

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
    for name in ([entry["filename"] for entry in _MANIFEST["files"]]
                 + [_MANIFEST["catalog_filename"]]):
        payload = _materialized(tmp_path).joinpath(name).read_bytes()
        assert link.remote_files[
            f"{ROOT}/skills/ed_install-windows-3.11_tok/"
            f"install-windows-3.11/{name}"] == payload
        assert link.remote_files[
            f"{ROOT}/skills/ed_install-windows-3.11_tok/"
            f"install-windows-3-11/{name}"] == payload


def test_folder_unit_refuses_an_unmaterialized_directory(tmp_path):
    plugin = _make_plugin(FakeRemoteLink())
    task = Task(task_id="train-fasttext", instruction="train-fasttext",
                meta={"task_name": "train-fasttext",
                      "attempt_token": "tok",
                      "skill_folder": _MANIFEST})
    with pytest.raises(Exception, match="skill_folder_dir"):
        plugin.run(task, HANDLE, _CATALOG, tmp_path / "work")


def test_bare_control_unit_uploads_no_folder_and_no_guidance(tmp_path):
    # The control arm's bare unit: no manifest on the task, an empty
    # skills block, and therefore no upload at all -- the pre-folder
    # behaviour, byte for byte.
    link = FakeRemoteLink(
        polls=[f"RESULT {TRIAL}/result.json\n"],
        remote_files=_trial_files_with_cat())
    plugin = _make_plugin(link)
    task = Task(task_id="train-fasttext", instruction="train-fasttext",
                meta={"task_name": "train-fasttext", "attempt_token": "tok"})

    outcome = plugin.run(task, HANDLE, "", tmp_path / "work")

    assert outcome.meta["eval_mode"] == "bare"
    assert outcome.meta["skills_injected"] is False
    assert outcome.meta["prompt_name"] == "TB_TASK_PROMPT"
    assert "skill_folder" not in outcome.meta
    assert not any("skills/" in remote for _, remote in link.puts)


# ---------------------------------------------------------------------------
# Idempotent re-run: a resumed folder unit reuses its trial, re-uploads
# nothing (the stateful fake of tb_fake_remote.py).
# ---------------------------------------------------------------------------

def test_a_resumed_folder_unit_reuses_the_trial_without_reupload(
        tmp_path):
    from tests.unit.benchmarks.tb_fake_remote import JobsRemoteLink

    link = JobsRemoteLink()
    plugin = _make_plugin(link)
    task = _folder_task(tmp_path)

    first = plugin.run(task, HANDLE, _CATALOG, tmp_path / "work")
    puts_after_first = list(link.puts)
    assert any(remote.endswith(".tar")
               and remote.startswith(f"{ROOT}/skills_cache/")
               for _, remote in puts_after_first)

    second = plugin.run(task, HANDLE, _CATALOG, tmp_path / "work")

    # Same run id, same recorded launch conditions: the finished trial is
    # reused, nothing re-uploaded, and the outcome is the same verdict
    # with the resumed provenance.
    assert link.puts == puts_after_first
    assert second.correctness == first.correctness is True
    assert second.meta["resumed"] == "result"
    assert second.meta["skill_folder"] == first.meta["skill_folder"]


# ---------------------------------------------------------------------------
# Live smoke (never run by default: pyproject addopts is "-m 'not live'")
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_live_folder_remote_smoke(tmp_path):
    """Real 1-task folder-mode smoke for the remote link (AGENTS.md
    section 7): the same prerequisites as test_tb_plugin's
    test_live_remote_smoke, plus the folder riding the skills directory.

    Run explicitly with::

        .venv/bin/python -m pytest \\
            tests/unit/test_folder_m3_sandbox.py::test_live_folder_remote_smoke \\
            -m live -s
    """
    import os

    model = os.environ.get("EXPEDISTILL_TB_SMOKE_MODEL")
    if not model:
        pytest.skip("set EXPEDISTILL_TB_SMOKE_MODEL to run the live smoke")
    plugin = TBPlugin()  # real RemoteLink from the environment
    folder_dir = _materialized(tmp_path)
    task = Task(task_id="fix-git", instruction="fix-git",
                meta={"task_name": "fix-git", "attempt_token": "smokef1",
                      "skill_folder": _MANIFEST,
                      "skill_folder_dir": str(folder_dir)})
    handle = ModelHandle(name=model, role="smoke", client=None)
    outcome = plugin.run(task, handle, _CATALOG, tmp_path / "smoke")
    assert outcome.meta["run_id"].startswith("ed_fix-git_smokef1")
    assert outcome.meta["eval_mode"] == "skills"
    assert outcome.meta["skill_folder"]["local_dir"] == str(folder_dir)


def test_two_folder_units_share_one_cache_upload(tmp_path):
    """The transport fix's heart: the folder ships ONCE per folder
    content, no matter how many units ride it. The second unit finds
    the cache (test -d), stages with its own cp, and never puts a
    second tar -- the 2026-10-18 storm was 84 units x 141 per-file
    scp connections; this shape is one scp plus one cp ssh per unit."""
    second_trial = (f"{ROOT}/jobs/ed_crack-7z-hash_tok/"
                    f"crack-7z-hash__AbCdEf1")
    files = _trial_files_with_cat()
    for name, text in (
            ("result.json", Path("tests/fixtures/tb/result_success.json")
             .read_text(encoding="utf-8")),
            ("agent/trajectory.json", json.dumps(_CAT_TRAJECTORY)),
            ("verifier/test-stdout.txt",
             Path("tests/fixtures/tb/verifier_collected.txt")
             .read_text(encoding="utf-8"))):
        files[f"{second_trial}/{name}"] = text
    link = FakeRemoteLink(
        polls=[f"RESULT {TRIAL}/result.json\n",
               f"RESULT {second_trial}/result.json\n"],
        remote_files=files)
    plugin = _make_plugin(link)
    first = plugin.run(_folder_task(tmp_path), HANDLE, _CATALOG,
                       tmp_path / "work1")
    tars_after_first = [remote for _, remote in link.puts
                        if remote.startswith(f"{ROOT}/skills_cache/")
                        and remote.endswith(".tar")]
    assert len(tars_after_first) == 1

    second = plugin.run(_folder_task(tmp_path, task_name="crack-7z-hash"),
                        HANDLE, _CATALOG, tmp_path / "work2")

    # Exactly one tar ever; the second unit staged by cp alone.
    tars = [remote for _, remote in link.puts
            if remote.startswith(f"{ROOT}/skills_cache/")
            and remote.endswith(".tar")]
    assert tars == tars_after_first
    copies = [cmd for cmd in link.commands if "cp -r " in cmd]
    assert len(copies) == 2
    assert "crack-7z-hash" in copies[1]
    # Both task directories hold the folder's exact bytes.
    folder_dir = _materialized(tmp_path)
    for task in ("train-fasttext", "crack-7z-hash"):
        for name in ([entry["filename"] for entry in _MANIFEST["files"]]
                     + [_MANIFEST["catalog_filename"]]):
            expected = folder_dir.joinpath(name).read_bytes()
            assert link.remote_files[
                f"{ROOT}/skills/ed_{task}_tok/{task}/{name}"] == expected
    assert second.meta["skill_folder"] == first.meta["skill_folder"]


def test_the_upload_gate_admits_fresh_launches_only(tmp_path, monkeypatch):
    """The rolling admission: a fresh launch's upload+submit runs under
    the gate, a resumed unit (state=result, only polling) never touches
    it -- the long waits hold no slot, so admission is by upload
    completion alone."""
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
    from tests.unit.benchmarks.tb_fake_remote import JobsRemoteLink
    link = JobsRemoteLink()
    plugin = _make_plugin(link)
    task = _folder_task(tmp_path)

    first = plugin.run(task, HANDLE, _CATALOG, tmp_path / "work")
    assert events == ["enter", "exit"]
    # The finished trial is reused on the second call: no upload, no
    # gate traffic.
    second = plugin.run(task, HANDLE, _CATALOG, tmp_path / "work")
    assert events == ["enter", "exit"]
    assert second.meta["resumed"] == "result"
    assert first.meta["resumed"] is None


def test_the_gate_width_reads_the_environment(monkeypatch):
    from expedistill.benchmarks.tb.plugin import _env_int

    monkeypatch.setenv("EXPEDISTILL_TB_UPLOAD_GATE", "3")
    assert _env_int("EXPEDISTILL_TB_UPLOAD_GATE", 8) == 3
    monkeypatch.delenv("EXPEDISTILL_TB_UPLOAD_GATE")
    assert _env_int("EXPEDISTILL_TB_UPLOAD_GATE", 8) == 8
    monkeypatch.setenv("EXPEDISTILL_TB_UPLOAD_GATE", "zero")
    with pytest.raises(Exception, match="EXPEDISTILL_TB_UPLOAD_GATE"):
        _env_int("EXPEDISTILL_TB_UPLOAD_GATE", 8)
