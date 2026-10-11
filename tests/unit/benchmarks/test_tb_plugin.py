"""Tests for benchmarks/tb: RemoteLink env validation, the TBPlugin end to end
against a FakeRemoteLink (scripted poll sequence + recorded fixtures),
load_tasks, verdict classification, and the task prompt contract.

No network, no ssh: every remote interaction goes through the fake. The one
``live``-marked smoke documents (and, when run explicitly with ``-m live``,
performs) the real manual smoke command.
"""

import importlib
import json
import string
from pathlib import Path

import pytest

from expedistill.benchmarks.base import Task
from expedistill.benchmarks.registry import _reset_registry, get_plugin
from expedistill.benchmarks.tb import TBPlugin
from expedistill.benchmarks.tb.plugin import classify_result
from expedistill.benchmarks.tb.remote import ENV_HOST, ENV_KEY, ENV_ROOT, RemoteLink
from expedistill.infra.reliability import ConfigError, InfrastructureError
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.call import ModelHandle
from expedistill.prompts.registry import PROMPTS, get as get_prompt
from tests.unit.benchmarks.tb_fake_remote import (
    JobsRemoteLink,
    SimulatedCrash,
    trial_files,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tb"

ROOT = "/data2/niuyiming/expedistill"
TRIAL = f"{ROOT}/jobs/ed_train-fasttext_tok/train-fasttext__AbCdEf1"

HANDLE = ModelHandle(name="qwen3.6-flash-distill", role="executor_weak",
                     client=None)
TASK = Task(task_id="train-fasttext", instruction="train-fasttext",
            meta={"task_name": "train-fasttext", "attempt_token": "tok"})


def _fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeRemoteLink:
    """Test double for RemoteLink: scripted poll outcomes plus a remote file
    store. Command routing mirrors the plugin's call patterns."""

    def __init__(self, polls=None, remote_files=None, home="/home/node29",
                 probe="ABSENT\n"):
        self.host = "user@node29"
        self.key_path = "/tmp/fake-key"
        self.root = ROOT
        self.home = home
        self.polls = list(polls or [])
        self.remote_files = dict(remote_files or {})
        self.commands = []
        self.puts = []
        self.pid = 4242
        self.probe = probe

    def run(self, cmd, timeout=None):
        self.commands.append(cmd)
        if "$HOME" in cmd:
            return self.home, "", 0
        if "nohup" in cmd:  # the submit call
            return f"{self.pid}\n", "", 0
        if "ABSENT" in cmd:  # the pre-submit resume probe
            return self.probe, "", 0
        if "result.json" in cmd:  # a poll check
            if self.polls:
                return self.polls.pop(0), "", 0
            return "PENDING\n", "", 0
        if "tail -c" in cmd:
            return self.remote_files.get("__log__", ""), "", 0
        return "", "", 0  # mkdir, partial-trajectory lookup, ...

    def get(self, remote, local, timeout=None):
        try:
            content = self.remote_files[str(remote)]
        except KeyError:
            raise InfrastructureError(
                f"scp failed (rc=1) get {remote} -> {local}")
        Path(local).write_text(content, encoding="utf-8")

    def put(self, local, remote, timeout=None):
        self.puts.append((local, str(remote)))
        self.remote_files[str(remote)] = Path(local).read_text(encoding="utf-8")


def _completed_trial_files(result="result_success.json",
                           verifier_log="verifier_collected.txt"):
    return {
        f"{TRIAL}/result.json": _fixture_text(result),
        f"{TRIAL}/agent/trajectory.json": _fixture_text("trajectory.json"),
        f"{TRIAL}/verifier/test-stdout.txt": _fixture_text(verifier_log),
    }


def _make_plugin(link, usage_recorder=None):
    return TBPlugin(link=link, usage_recorder=usage_recorder,
                    poll_seconds=0.0, deadline_seconds=10.0)


# ---------------------------------------------------------------------------
# RemoteLink configuration
# ---------------------------------------------------------------------------

def test_remote_link_reads_env_and_reports_missing_vars(monkeypatch):
    monkeypatch.setenv(ENV_HOST, "user@node29")
    monkeypatch.setenv(ENV_KEY, "/keys/id_tb")
    monkeypatch.setenv(ENV_ROOT, "/data2/niuyiming/expedistill")
    link = RemoteLink()
    assert link.host == "user@node29"
    assert link.root == "/data2/niuyiming/expedistill"


def test_remote_link_missing_env_is_config_error(monkeypatch):
    for name in (ENV_HOST, ENV_KEY, ENV_ROOT):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError, match=r"\.env\.example"):
        RemoteLink()
    monkeypatch.setenv(ENV_HOST, "user@node29")
    with pytest.raises(ConfigError, match=ENV_KEY):
        RemoteLink()
    monkeypatch.setenv(ENV_KEY, "/keys/id_tb")
    with pytest.raises(ConfigError, match=ENV_ROOT):
        RemoteLink()
    # A relative root is rejected too: it would land in the login dir.
    monkeypatch.setenv(ENV_ROOT, "expedistill")
    with pytest.raises(ConfigError, match="absolute"):
        RemoteLink()


# ---------------------------------------------------------------------------
# load_tasks
# ---------------------------------------------------------------------------

def test_load_tasks_from_list():
    tasks = TBPlugin().load_tasks(["fix-git", "train-fasttext"])
    assert [t.task_id for t in tasks] == ["fix-git", "train-fasttext"]
    # The instruction is the raw task name: the real instruction lives in the
    # remote task directory and is read by the remote run.
    assert tasks[0].instruction == "fix-git"
    assert tasks[0].meta["task_name"] == "fix-git"


def test_load_tasks_from_file(tmp_path):
    task_file = tmp_path / "tasks.txt"
    task_file.write_text(
        "# TB2.1 subset\nfix-git\n\ntrain-fasttext  # inline comment\n",
        encoding="utf-8")
    tasks = TBPlugin().load_tasks(str(task_file))
    assert [t.task_id for t in tasks] == ["fix-git", "train-fasttext"]


def test_load_tasks_none_or_empty_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match="explicit task list"):
        TBPlugin().load_tasks(None)
    with pytest.raises(ConfigError, match="empty"):
        TBPlugin().load_tasks([])
    with pytest.raises(ConfigError, match="cannot read"):
        TBPlugin().load_tasks(str(tmp_path / "missing.txt"))
    with pytest.raises(ConfigError, match="invalid TB task name"):
        TBPlugin().load_tasks(["fix-git; rm -rf /"])


# ---------------------------------------------------------------------------
# Verdict classification (pure)
# ---------------------------------------------------------------------------

def _classify(result_name, verifier_log_name):
    result = json.loads(_fixture_text(result_name))
    log = _fixture_text(verifier_log_name) if verifier_log_name else None
    return classify_result(result, log)


def test_classify_success_and_failure_with_pytest_evidence():
    status, reward, info = _classify("result_success.json",
                                     "verifier_collected.txt")
    assert (status, reward) == ("success", 1.0)
    status, reward, _ = _classify("result_failure.json",
                                  "verifier_collected.txt")
    assert (status, reward) == ("failure", 0.0)


def test_classify_pseudo_zero_is_infra_not_model_failure():
    # Reward present but pytest never collected anything: the campaign's
    # "fake zero" (broken verifier download), never the model's failure.
    status, _, info = _classify("result_failure.json",
                                "verifier_not_collected.txt")
    assert status == "infra_error"
    assert info["verdict_reason"] == "pseudo_zero"
    # Missing verifier log entirely: same verdict.
    status, _, info = _classify("result_failure.json", None)
    assert status == "infra_error"


def test_classify_exceptions_split_model_vs_infra():
    status, _, info = _classify("result_infra.json", None)
    assert status == "infra_error"
    assert info["exception_type"] == "AgentSetupTimeoutError"
    # The model's own timeout is a genuine failure (scored 0), per the
    # campaign's audit rule.
    status, _, info = _classify("result_model_fail.json", None)
    assert status == "failure"
    assert info["exception_type"] == "AgentTimeoutError"
    # No reward and no exception at all: infra by default.
    status, _, info = classify_result(
        {"verifier_result": None, "exception_info": None}, None)
    assert status == "infra_error"
    assert info["verdict_reason"] == "no_reward_no_exception"


# ---------------------------------------------------------------------------
# run(): end to end against the fake link
# ---------------------------------------------------------------------------

def test_run_success_end_to_end(tmp_path):
    link = FakeRemoteLink(
        polls=["PENDING\n", f"RESULT {TRIAL}/result.json\n"],
        remote_files=_completed_trial_files())
    recorder = UsageRecorder(tmp_path / "usage.jsonl")
    plugin = _make_plugin(link, recorder)
    outcome = plugin.run(TASK, HANDLE, "", tmp_path / "work")

    assert outcome.status == "success"
    assert len(outcome.steps) == 2
    meta = outcome.meta
    assert meta["run_id"] == "ed_train-fasttext_tok"
    assert meta["eval_mode"] == "bare"
    assert meta["skills_injected"] is False
    assert meta["reward"] == 1.0
    assert meta["skipped_steps"] == 2
    assert meta["remote"]["result"] == f"{TRIAL}/result.json"
    assert meta["timing"]["duration_s"] >= 0
    # One submit command with the runner, detached, echoing the pid.
    submit = [cmd for cmd in link.commands if "nohup" in cmd]
    assert len(submit) == 1
    assert "RUN_ID=ed_train-fasttext_tok" in submit[0]
    assert "TBENCH_EVAL_MODE=bare" in submit[0]
    assert "TBENCH_N_ATTEMPTS=1" in submit[0]
    assert "MODEL_NAME=qwen3.6-flash-distill" in submit[0]
    assert "echo $!" in submit[0]
    assert "TBENCH_SKILL_ROOT" not in submit[0]
    # Harbor's token totals were accounted from the trial's trajectory.
    totals = recorder.totals()
    assert totals["overall"]["request_count"] == 1
    assert totals["overall"]["in"] == 6333
    assert totals["overall"]["out"] == 1224


def test_run_with_skills_uploads_skill_md(tmp_path):
    link = FakeRemoteLink(
        polls=[f"RESULT {TRIAL}/result.json\n"],
        remote_files=_completed_trial_files())
    plugin = _make_plugin(link)
    skills_block = "## Skill rules\n- check pip mirrors before installing"
    outcome = plugin.run(TASK, HANDLE, skills_block, tmp_path / "work")

    assert outcome.status == "success"
    assert outcome.meta["eval_mode"] == "skills"
    assert outcome.meta["skills_injected"] is True
    assert outcome.meta["remote_skill_file"] == (
        f"{ROOT}/skills/ed_train-fasttext_tok/train-fasttext/SKILL.md")
    # The uploaded SKILL.md is the D9 block verbatim...
    local_skill = tmp_path / "work" / "skills" / "train-fasttext" / "SKILL.md"
    assert local_skill.read_text(encoding="utf-8") == skills_block
    assert link.puts[0] == (str(local_skill),
                            f"{ROOT}/skills/ed_train-fasttext_tok/"
                            f"train-fasttext/SKILL.md")
    # Then the remote submit meta (uploaded last, before the launch).
    assert [remote for _, remote in link.puts[1:]] == [
        f"{ROOT}/jobs/ed_train-fasttext_tok.ed_meta.json"]
    # ...and the runner was pointed at the per-run skills root.
    submit = next(cmd for cmd in link.commands if "nohup" in cmd)
    assert "TBENCH_EVAL_MODE=skills" in submit
    assert (f"TBENCH_SKILL_ROOT={ROOT}/skills/ed_train-fasttext_tok"
            in submit)


def test_run_dead_runner_is_infra_error_with_partial_evidence(tmp_path):
    link = FakeRemoteLink(
        polls=["PENDING\n", "DEAD\n"],
        remote_files={"__log__": _fixture_text("runner_log.txt")})
    plugin = _make_plugin(link)
    outcome = plugin.run(TASK, HANDLE, "", tmp_path / "work")

    assert outcome.status == "infra_error"
    assert "exited without producing result.json" in outcome.meta["error"]
    assert "run_id" in outcome.meta
    assert "ConfigValidationError" in outcome.meta["runner_log_tail"]
    # A failure ledger row would be K1's business; usage here is null, not 0.
    recorder = UsageRecorder(tmp_path / "usage.jsonl")
    plugin = _make_plugin(FakeRemoteLink(
        polls=["DEAD\n"], remote_files={}), recorder)
    outcome = plugin.run(TASK, HANDLE, "", tmp_path / "work2")
    assert outcome.status == "infra_error"
    rows = recorder.totals()
    assert rows["overall"]["request_count"] == 1
    assert rows["overall"]["in"] == 0  # null contributes nothing
    lines = (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["in_tokens"] is None


def test_run_deadline_is_infra_error(tmp_path):
    link = FakeRemoteLink(polls=["PENDING\n"])
    plugin = TBPlugin(link=link, poll_seconds=0.0, deadline_seconds=0.0)
    outcome = plugin.run(TASK, HANDLE, "", tmp_path / "work")
    assert outcome.status == "infra_error"
    assert "deadline" in outcome.meta["error"]


def test_run_infra_exception_result_is_infra_error(tmp_path):
    link = FakeRemoteLink(
        polls=[f"RESULT {TRIAL}/result.json\n"],
        remote_files={
            f"{TRIAL}/result.json": _fixture_text("result_infra.json"),
            f"{TRIAL}/agent/trajectory.json": _fixture_text("trajectory.json"),
        })
    outcome = _make_plugin(link).run(TASK, HANDLE, "", tmp_path / "work")
    assert outcome.status == "infra_error"
    assert "AgentSetupTimeoutError" in outcome.meta["error"]
    assert outcome.meta["exception_type"] == "AgentSetupTimeoutError"
    # The stepwise view is still rebuilt and kept.
    assert len(outcome.steps) == 2


def test_run_model_timeout_result_is_failure(tmp_path):
    link = FakeRemoteLink(
        polls=[f"RESULT {TRIAL}/result.json\n"],
        remote_files={
            f"{TRIAL}/result.json": _fixture_text("result_model_fail.json"),
            f"{TRIAL}/agent/trajectory.json": _fixture_text("trajectory.json"),
        })
    outcome = _make_plugin(link).run(TASK, HANDLE, "", tmp_path / "work")
    assert outcome.status == "failure"
    assert outcome.meta["exception_type"] == "AgentTimeoutError"


def test_run_pseudo_zero_is_infra_error_end_to_end(tmp_path):
    link = FakeRemoteLink(
        polls=[f"RESULT {TRIAL}/result.json\n"],
        remote_files=_completed_trial_files(result="result_failure.json",
                                            verifier_log="verifier_not_collected.txt"))
    outcome = _make_plugin(link).run(TASK, HANDLE, "", tmp_path / "work")
    assert outcome.status == "infra_error"
    assert outcome.meta["verdict_reason"] == "pseudo_zero"


def test_run_transport_failure_is_infra_error(tmp_path):
    class BrokenLink(FakeRemoteLink):
        def run(self, cmd, timeout=None):
            raise InfrastructureError("ssh to user@node29 timed out")

    outcome = _make_plugin(BrokenLink()).run(TASK, HANDLE, "", tmp_path / "work")
    assert outcome.status == "infra_error"
    assert "timed out" in outcome.meta["error"]


def test_run_rejects_invalid_task_name(tmp_path):
    link = FakeRemoteLink()
    bad = Task(task_id="x; rm", instruction="x", meta={"task_name": "x; rm"})
    with pytest.raises(ConfigError, match="invalid TB task name"):
        _make_plugin(link).run(bad, HANDLE, "", tmp_path / "work")


def test_run_rejects_bad_attempt_token(tmp_path):
    link = FakeRemoteLink()
    bad = Task(task_id="fix-git", instruction="fix-git",
               meta={"task_name": "fix-git", "attempt_token": "a b/c"})
    with pytest.raises(ConfigError, match="attempt_token"):
        _make_plugin(link).run(bad, HANDLE, "", tmp_path / "work")


# ---------------------------------------------------------------------------
# Run namespace and remote resume
# ---------------------------------------------------------------------------

def _task(token="a00", namespace=None, name="train-fasttext"):
    meta = {"task_name": name, "attempt_token": token}
    if namespace is not None:
        meta["run_namespace"] = namespace
    return Task(task_id=name, instruction=name, meta=meta)


def test_run_id_carries_the_namespace(tmp_path):
    link = JobsRemoteLink()
    outcome = _make_plugin(link).run(_task(namespace="0123abcdef"), HANDLE,
                                     "", tmp_path / "work")
    assert outcome.status == "success"
    assert outcome.meta["run_id"] == "ed_train-fasttext_0123abcdef_a00"
    assert "RUN_ID=ed_train-fasttext_0123abcdef_a00" in link.submits()[0]


def test_run_rejects_bad_namespace(tmp_path):
    with pytest.raises(ConfigError, match="run_namespace"):
        _make_plugin(JobsRemoteLink()).run(
            _task(namespace="a b/../c"), HANDLE, "", tmp_path / "work")


def test_weak_arm_never_reads_the_strong_arms_result(tmp_path):
    # Stage 1 shape: the strong arm already finished this task/attempt (a
    # success); the weak arm runs the same task with the same attempt token
    # but its own namespace. Its run must be a fresh trial with its own
    # verdict, never the strong arm's result.json.
    strong_id = "ed_train-fasttext_5757575757_a00"
    link = JobsRemoteLink(
        files=trial_files(strong_id, "train-fasttext"),
        on_submit=lambda run_id, task, model: trial_files(
            run_id, task, result="result_failure.json", trial="WeAk001"))
    weak = _make_plugin(link).run(_task(namespace="eaeaeaeaea"), HANDLE, "",
                                  tmp_path / "weak")
    assert weak.meta["run_id"] == "ed_train-fasttext_eaeaeaeaea_a00"
    assert weak.meta["run_id"] != strong_id
    assert len(link.submits()) == 1
    assert weak.status == "failure"
    assert weak.meta["remote"]["result"].startswith(
        f"{ROOT}/jobs/ed_train-fasttext_eaeaeaeaea_a00/")
    assert not any(strong_id in cmd for cmd in link.commands)


NS_TASK_RUN_ID = "ed_train-fasttext_0123abcdef_a00"


def _crashed_launch(link, plugin, workdir, skills_block=""):
    """Launch the unit, then let the local process "die" mid-poll; the
    remote runner keeps going (detached), exactly as after a real crash."""
    link.crash_polls = 1
    with pytest.raises(SimulatedCrash):
        plugin.run(_task(namespace="0123abcdef"), HANDLE, skills_block,
                   workdir)
    assert link.crash_polls == 0
    return link.launches[-1]["pid"]


def _running_link(**kwargs):
    return JobsRemoteLink(on_submit=lambda run_id, task, model: None,
                          **kwargs)


def test_crashed_unit_reuses_its_finished_result_without_submit(tmp_path):
    link = _running_link()
    plugin = _make_plugin(link)
    workdir = tmp_path / "work"
    skills = "## Skill rules\n- one"
    _crashed_launch(link, plugin, workdir, skills)
    puts_before = list(link.puts)
    link.finish(NS_TASK_RUN_ID)

    recorder = UsageRecorder(tmp_path / "usage.jsonl")
    plugin = _make_plugin(link, recorder)
    outcome = plugin.run(_task(namespace="0123abcdef"), HANDLE, skills,
                         workdir)
    assert outcome.status == "success"
    assert outcome.meta["resumed"] == "result"
    assert len(link.submits()) == 1
    # Nothing re-uploaded; the provenance is still recorded.
    assert link.puts == puts_before
    assert outcome.meta["eval_mode"] == "skills"
    assert outcome.meta["remote_skill_root"] == (
        f"{ROOT}/skills/{NS_TASK_RUN_ID}")
    # The trial's real duration comes from result.json (40 minutes), not
    # from how long the fetch took.
    timing = outcome.meta["timing"]
    assert timing["source"] == "result.json"
    assert timing["duration_s"] == 2400.0
    assert recorder.totals()["overall"]["request_count"] == 1


def test_reused_result_without_timing_fields_marks_duration_unknown(tmp_path):
    link = _running_link()
    plugin = _make_plugin(link)
    workdir = tmp_path / "work"
    _crashed_launch(link, plugin, workdir)
    link.finish(NS_TASK_RUN_ID)
    result_path = next(path for path in link.files
                       if path.endswith("/result.json"))
    data = json.loads(link.files[result_path])
    data.pop("started_at")
    data.pop("finished_at")
    link.files[result_path] = json.dumps(data)
    outcome = plugin.run(_task(namespace="0123abcdef"), HANDLE, "", workdir)
    assert outcome.meta["resumed"] == "result"
    assert outcome.meta["timing"]["source"] == "unknown"
    assert outcome.meta["timing"]["duration_s"] is None


def test_remote_result_without_local_submit_record_is_never_reused(tmp_path):
    # Somebody's result.json sits under this run id, but this unit has no
    # record of ever submitting it: no evidence it belongs to this request.
    files = trial_files(NS_TASK_RUN_ID, "train-fasttext")
    link = JobsRemoteLink(files=files)
    recorder = UsageRecorder(tmp_path / "usage.jsonl")
    outcome = _make_plugin(link, recorder).run(
        _task(namespace="0123abcdef"), HANDLE, "", tmp_path / "work")
    assert outcome.status == "infra_error"
    assert outcome.meta["resumed"] == "stale"
    assert "no local submit record" in outcome.meta["error"]
    assert link.submits() == []
    assert outcome.meta.get("reward") is None
    # No rollout happened, so no usage row either.
    assert recorder.totals()["overall"]["request_count"] == 0


@pytest.mark.parametrize("tamper", ["env", "skills", "remote_meta"])
def test_submit_conditions_mismatch_is_stale(tmp_path, monkeypatch, tamper):
    monkeypatch.setenv("TBENCH_PYPI_MIRROR", "http://mirror-a/simple")
    link = _running_link()
    plugin = _make_plugin(link)
    workdir = tmp_path / "work"
    _crashed_launch(link, plugin, workdir, "- skill A")
    link.finish(NS_TASK_RUN_ID)
    skills = "- skill A"
    if tamper == "env":
        monkeypatch.setenv("TBENCH_PYPI_MIRROR", "http://mirror-b/simple")
    elif tamper == "skills":
        skills = "- skill B"
    else:
        meta_path = f"{ROOT}/jobs/{NS_TASK_RUN_ID}.ed_meta.json"
        meta = json.loads(link.files[meta_path])
        meta["env_sha256"] = "0" * 64
        link.files[meta_path] = json.dumps(meta)
    outcome = plugin.run(_task(namespace="0123abcdef"), HANDLE, skills,
                         workdir)
    assert outcome.status == "infra_error"
    assert outcome.meta["resumed"] == "stale"
    assert "refusing to" in outcome.meta["error"]
    assert len(link.submits()) == 1


def test_submit_writes_local_record_and_remote_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("TBENCH_PYPI_MIRROR", "http://mirror-a/simple")
    link = _running_link()
    workdir = tmp_path / "work"
    pid = _crashed_launch(link, _make_plugin(link), workdir, "- skill A")
    record = json.loads((workdir / "tb_remote" / f"{NS_TASK_RUN_ID}.json")
                        .read_text(encoding="utf-8"))
    remote = json.loads(link.files[f"{ROOT}/jobs/{NS_TASK_RUN_ID}.ed_meta.json"])
    assert record["run_id"] == remote["run_id"] == NS_TASK_RUN_ID
    assert record["pid"] == pid
    assert record["env_sha256"] == remote["env_sha256"]
    assert record["skill_sha256"] == remote["skill_sha256"] is not None
    # The meta went up before the runner was launched.
    meta_put = next(i for i, (_, remote_path) in enumerate(link.puts)
                    if remote_path.endswith(".ed_meta.json"))
    assert meta_put == len(link.puts) - 1
    assert link.commands.index(link.submits()[0]) > 0


def test_existing_run_dir_with_dead_pid_is_infra_error_without_submit(
        tmp_path):
    link = _running_link()
    plugin = _make_plugin(link)
    workdir = tmp_path / "work"
    pid = _crashed_launch(link, plugin, workdir)
    link.alive.discard(pid)  # the runner died without a result
    recorder = UsageRecorder(tmp_path / "usage.jsonl")
    outcome = _make_plugin(link, recorder).run(
        _task(namespace="0123abcdef"), HANDLE, "", workdir)
    assert outcome.status == "infra_error"
    assert outcome.meta["resumed"] == "stale"
    assert f"recorded pid {pid} is not alive" in outcome.meta["error"]
    assert len(link.submits()) == 1
    assert any(f"/proc/{pid}/environ" in cmd for cmd in link.commands
               if "ABSENT" in cmd)
    assert recorder.totals()["overall"]["request_count"] == 0


def test_existing_run_dir_without_pid_file_is_infra_error_without_submit(
        tmp_path):
    # A launch whose pid never reached the local record (crash between the
    # remote launch and the local write): the runner log exists, so the run
    # id is taken; resubmitting could start a second runner into it.
    link = JobsRemoteLink(files={f"{ROOT}/logs/{NS_TASK_RUN_ID}.log": ""})
    outcome = _make_plugin(link).run(_task(namespace="0123abcdef"), HANDLE,
                                     "", tmp_path / "work")
    assert outcome.status == "infra_error"
    assert outcome.meta["resumed"] == "stale"
    assert link.submits() == []


def test_existing_run_dir_with_alive_pid_polls_without_submit(tmp_path):
    link = _running_link()
    plugin = _make_plugin(link)
    workdir = tmp_path / "work"
    pid = _crashed_launch(link, plugin, workdir)
    polls = {"n": 0}
    original_check = link._check

    def check_then_finish(cmd):
        # The live runner finishes after the second poll of the resume.
        if "ABSENT" not in cmd:
            polls["n"] += 1
            if polls["n"] == 2:
                link.finish(NS_TASK_RUN_ID)
        return original_check(cmd)

    link._check = check_then_finish
    outcome = plugin.run(_task(namespace="0123abcdef"), HANDLE, "", workdir)
    assert outcome.status == "success"
    assert outcome.meta["resumed"] == "alive"
    assert outcome.meta["remote_pid"] == pid
    assert outcome.meta["timing"]["source"] == "result.json"
    assert len(link.submits()) == 1
    assert polls["n"] == 2


def test_deadline_kills_only_the_recorded_pid(tmp_path):
    link = _running_link(alive={1234})  # somebody else's live process
    plugin = TBPlugin(link=link, poll_seconds=0.0, deadline_seconds=0.0)
    recorder = UsageRecorder(tmp_path / "usage.jsonl")
    plugin.usage_recorder = recorder
    outcome = plugin.run(_task(namespace="0123abcdef"), HANDLE, "",
                         tmp_path / "work")
    assert outcome.status == "infra_error"
    assert "deadline" in outcome.meta["error"]
    pid = link.launches[0]["pid"]
    assert link.kills == [pid]
    assert pid not in link.alive and 1234 in link.alive
    assert outcome.meta["deadline_kill"] == {
        "pid": pid, "verified": True, "signalled": True, "rc": 0}
    # The launched rollout did happen: one (null) usage row.
    assert recorder.totals()["overall"]["request_count"] == 1


def test_reused_pid_of_a_foreign_process_is_not_rejoined(tmp_path):
    # The runner died and an unrelated process got its pid: alive, but its
    # environment carries no RUN_ID of ours -> stale, never rejoined.
    link = _running_link()
    plugin = _make_plugin(link)
    workdir = tmp_path / "work"
    pid = _crashed_launch(link, plugin, workdir)
    link.owner.pop(pid)  # same pid, somebody else's process now
    outcome = plugin.run(_task(namespace="0123abcdef"), HANDLE, "", workdir)
    assert outcome.status == "infra_error"
    assert outcome.meta["resumed"] == "stale"
    assert pid in link.alive
    assert len(link.submits()) == 1


def test_deadline_never_kills_a_process_that_reused_the_pid(tmp_path):
    link = _running_link()
    original_check = link._check

    def runner_dies_and_pid_is_reused(cmd):
        answer = original_check(cmd)
        if "ABSENT" not in cmd:
            # After the (last) poll: the runner exits and a foreign process
            # takes over its pid before the deadline kill.
            link.owner.pop(link.launches[0]["pid"], None)
        return answer

    link._check = runner_dies_and_pid_is_reused
    plugin = TBPlugin(link=link, poll_seconds=0.0, deadline_seconds=0.0)
    outcome = plugin.run(_task(namespace="0123abcdef"), HANDLE, "",
                         tmp_path / "work")
    pid = link.launches[0]["pid"]
    assert outcome.status == "infra_error"
    assert link.kills == []
    assert pid in link.alive
    assert outcome.meta["deadline_kill"] == {
        "pid": pid, "verified": False, "signalled": False}


def test_identity_check_command_runs_in_real_bash(tmp_path):
    # The probe's identity check, executed by a real local bash (no ssh)
    # against a live process that is NOT our runner: this test process.
    # Read-only: nothing is signalled.
    import os
    import subprocess

    class LocalBash:
        host = "localhost"

        def run(self, cmd, timeout=None):
            proc = subprocess.run(["bash", "-c", cmd], capture_output=True,
                                  text=True, timeout=30)
            return proc.stdout, proc.stderr, proc.returncode

    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "ed_x_a00.log").write_text("", encoding="utf-8")
    state, _, _ = TBPlugin()._probe(LocalBash(), str(tmp_path), "ed_x_a00",
                                    "x", os.getpid())
    # Alive, but its environment has no RUN_ID=ed_x_a00 (and on macOS there
    # is no /proc at all): unverifiable means not ours.
    assert state == "stale"


# ---------------------------------------------------------------------------
# Prompt contract and package registration
# ---------------------------------------------------------------------------

def test_task_prompt_registered_with_exactly_three_variables():
    template = get_prompt("TB_TASK_PROMPT")
    fields = {fname for _, fname, _, _ in string.Formatter().parse(template)
              if fname}
    assert fields == {"instruction", "skills_block", "history"}
    # Renders both ways: bare (empty block) and with skills.
    bare = template.format(instruction="do the thing", skills_block="",
                           history="")
    assert "do the thing" in bare
    with_skills = template.format(instruction="do the thing",
                                  skills_block="## Skill rules\n- be careful",
                                  history="")
    assert "## Skill rules" in with_skills


def test_import_registers_plugin_and_prompt():
    # Reload the package against a clean registry to prove the import side
    # effects are exactly: plugin registration + prompt registration.
    _reset_registry()
    PROMPTS.pop("TB_TASK_PROMPT", None)
    importlib.reload(importlib.import_module(
        "expedistill.benchmarks.tb.plugin"))
    importlib.reload(importlib.import_module("expedistill.benchmarks.tb"))
    assert get_plugin("tb").name == "tb"
    assert "TB_TASK_PROMPT" in PROMPTS
    # The registered instance resolves the standard interface names.
    plugin = get_plugin("tb")
    assert plugin.task_prompt_name == "TB_TASK_PROMPT"
    assert plugin.view_rules.max_obs_chars == 8000


# ---------------------------------------------------------------------------
# Live smoke (never run by default: pyproject addopts is "-m 'not live'")
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_live_remote_smoke(tmp_path):
    """Real 1-task execution smoke for the remote link (AGENTS.md section 7).

    Run explicitly with::

        .venv/bin/python -m pytest \\
            tests/unit/benchmarks/test_tb_plugin.py::test_live_remote_smoke \\
            -m live -s

    Prerequisites (all in .env / the shell): EXPEDISTILL_TB_HOST / _KEY /
    _ROOT, plus a remote ``env.sh`` at the root loading the Harbor venv and
    the E2B/OpenAI credentials, and the network-fix knobs from the tb21
    campaign (TBENCH_PYPI_MIRROR, TBENCH_VPC_DNS,
    TBENCH_APT_SNAPSHOT_BULLSEYE). The equivalent manual command is the one
    this plugin submits:

        ssh $EXPEDISTILL_TB_HOST 'cd $EXPEDISTILL_TB_ROOT \\
          && [ -f env.sh ] && . ./env.sh \\
          && RUN_ID=ed_smoke_manual TBENCH_RUN_MODE=selected \\
             TBENCH_TASK_NAMES=fix-git TBENCH_EVAL_MODE=bare \\
             TBENCH_N_ATTEMPTS=1 TBENCH_N_CONCURRENT=1 \\
             TBENCH_MAX_TRIAL_RETRIES=0 MODEL_NAME=<model> \\
             nohup setsid bash \\
             /data2/niuyiming/scripts/run_tencent_tb21_smoke_niuyiming.sh \\
             > logs/ed_smoke_manual.log 2>&1 & echo $!'

    and the audit afterwards checks logs/ed_smoke_manual.log plus the trial's
    verifier output for "collected N items" before its reward is trusted.
    """
    import os

    model = os.environ.get("EXPEDISTILL_TB_SMOKE_MODEL")
    if not model:
        pytest.skip("set EXPEDISTILL_TB_SMOKE_MODEL to run the live smoke")
    plugin = TBPlugin()  # real RemoteLink from the environment
    task = Task(task_id="fix-git", instruction="fix-git",
                meta={"task_name": "fix-git", "attempt_token": "smoke1"})
    handle = ModelHandle(name=model, role="smoke", client=None)
    outcome = plugin.run(task, handle, "", tmp_path / "smoke")
    # The smoke asserts only that the link works end to end; a model failure
    # (reward 0) is still a successful smoke of the plumbing.
    assert outcome.status in ("success", "failure", "infra_error")
    assert outcome.meta["run_id"].startswith("ed_fix-git_smoke1")
