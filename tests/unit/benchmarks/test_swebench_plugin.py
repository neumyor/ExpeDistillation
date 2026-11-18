"""Tests for benchmarks/swebench: the plugin against a fake bridge script.

The fake (``tests/fixtures/swebench/fake_bridge.py``) is installed as
``<root>/scripts/run_unit.py`` with ``<root>/.venv/bin/python`` pointing at
this interpreter, and copies a recorded unit directory where the real bridge
would run one. No sandbox, no model, no network.
"""

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from expedistill.benchmarks.base import Task
from expedistill.benchmarks.skill_folder import SkillFolder
from expedistill.benchmarks.swebench.plugin import (
    ROOT_ENV,
    SWEBenchPlugin,
    grader_report,
)
from expedistill.benchmarks.swebench.protocol import render_block
from expedistill.benchmarks.swebench.trajectory import (
    initial_prompt,
    steps_from_messages,
)
from expedistill.blocks.execute import run_one
from expedistill.composites.skill_use import skill_read_events
from expedistill.infra.reliability import (
    CodeBugError,
    ConfigError,
    InfrastructureError,
)
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.call import ModelHandle
from expedistill.methods.delivery.folder_delivery import skill_folder_manifest
from expedistill.methods.delivery.units import make_row, with_playbook
from expedistill.prompts import get as get_prompt
from tests.helpers.plugins import ensure_plugin
from tests.helpers.skill_folder import write_skill_folder

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "swebench"
RESOLVED = "pytest-dev__pytest-5809"
APPLY_FAILED = "django__django-16429"
PLAYBOOK = "Reproduce the issue before editing."


def _messages(case):
    return json.loads((FIXTURES / case / "traj.json").read_text(
        encoding="utf-8"))["messages"]


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    """A fake swebench-eval checkout; ``calls()`` reads the bridge's log."""
    root = tmp_path / "swebench-eval"
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (root / "scripts").mkdir()
    shutil.copy(FIXTURES / "fake_bridge.py", root / "scripts" / "run_unit.py")
    log = tmp_path / "bridge_calls.jsonl"
    monkeypatch.setenv(ROOT_ENV, str(root))
    monkeypatch.setenv("FAKE_BRIDGE_FIXTURES", str(FIXTURES))
    monkeypatch.setenv("FAKE_BRIDGE_LOG", str(log))
    monkeypatch.delenv("FAKE_BRIDGE_MODE", raising=False)
    monkeypatch.delenv("FAKE_BRIDGE_CASE", raising=False)

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in
                log.read_text(encoding="utf-8").splitlines()]
    return SimpleNamespace(root=root, calls=calls)


@pytest.fixture
def handle(tmp_path):
    return ModelHandle(name="student-model", role="executor_weak",
                       client=None, max_input_tokens=131072,
                       usage=UsageRecorder(tmp_path / "usage.jsonl"))


def _usage_rows(tmp_path):
    return [json.loads(line) for line in
            (tmp_path / "usage.jsonl").read_text(
                encoding="utf-8").splitlines()]


def _task(instance_id=RESOLVED, **meta):
    return Task(task_id=instance_id, instruction="issue",
                meta=dict({"attempt_token": "a00"}, **meta))


def _argument(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


def _folder_meta(tmp_path, names=("alpha", "beta"), playbook=PLAYBOOK):
    folder_dir = tmp_path / "skill_folder"
    if names:
        manifest, _, locations = write_skill_folder(folder_dir, names)
    else:
        folder_dir.mkdir()
        manifest, locations = skill_folder_manifest([]), []
    row = make_row("swebench:x", None, "", {"skill_folder": manifest})
    manifest = with_playbook([row], playbook)[0].aux["skill_folder"]
    return ({"skill_folder": manifest, "skill_folder_dir": str(folder_dir)},
            locations)


# ---------------------------------------------------------------------------
# Registration and configuration
# ---------------------------------------------------------------------------

def test_the_package_registers_the_plugin_without_reading_the_environment(
        monkeypatch):
    monkeypatch.delenv(ROOT_ENV, raising=False)
    plugin = ensure_plugin("swebench")
    assert isinstance(plugin, SWEBenchPlugin)
    assert plugin.task_prompt_name == "SWEBENCH_INSTANCE_TEMPLATE"
    assert plugin.skill_folder_prompt_name == "SWEBENCH_SKILL_READ"
    assert plugin.skill_folder_mandatory_prompt_name \
        == "SWEBENCH_SKILL_READ_MANDATORY"
    assert plugin.verdict_rule == "scorer"
    assert plugin.playbook_in_skills_block is True


def test_a_missing_root_is_a_config_error(monkeypatch, tmp_path, handle):
    monkeypatch.delenv(ROOT_ENV, raising=False)
    with pytest.raises(ConfigError, match=ROOT_ENV):
        SWEBenchPlugin().load_tasks([RESOLVED])
    with pytest.raises(ConfigError, match=ROOT_ENV):
        SWEBenchPlugin().run(_task(), handle, "", tmp_path / "work")


def test_a_root_without_the_bridge_is_a_config_error(monkeypatch, tmp_path):
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "nowhere"))
    with pytest.raises(ConfigError, match="cannot start"):
        SWEBenchPlugin().load_tasks([RESOLVED])


def test_a_handle_without_an_input_ceiling_is_a_config_error(
        bridge, tmp_path):
    handle = ModelHandle(name="student-model", role="executor_weak",
                         client=None)
    with pytest.raises(ConfigError, match="max_input_tokens"):
        SWEBenchPlugin().run(_task(), handle, "", tmp_path / "work")
    assert bridge.calls() == []


# ---------------------------------------------------------------------------
# load_tasks
# ---------------------------------------------------------------------------

def test_load_tasks_describes_the_ids_through_the_bridge(bridge):
    tasks = SWEBenchPlugin().load_tasks([RESOLVED, APPLY_FAILED])
    assert [task.task_id for task in tasks] == [RESOLVED, APPLY_FAILED]
    assert tasks[0].instruction == f"Problem statement of {RESOLVED}."
    assert tasks[1].meta == {"repo": "django"}
    (call,) = bridge.calls()
    assert call["argv"] == ["describe", "--instance", APPLY_FAILED,
                            "--instance", RESOLVED]


@pytest.mark.parametrize("spec", [None, "tasks.txt", []])
def test_load_tasks_needs_an_explicit_id_list(bridge, spec):
    with pytest.raises(ConfigError):
        SWEBenchPlugin().load_tasks(spec)
    assert bridge.calls() == []


def test_load_tasks_refuses_an_unsafe_id(bridge):
    with pytest.raises(ConfigError, match="instance id"):
        SWEBenchPlugin().load_tasks(["../escape"])


def test_a_refused_describe_is_a_config_error(bridge):
    with pytest.raises(ConfigError, match="not in split_v1.json"):
        SWEBenchPlugin().load_tasks([RESOLVED, "unknown__repo-1"])


# ---------------------------------------------------------------------------
# run: outcomes
# ---------------------------------------------------------------------------

def test_a_resolved_bare_unit(bridge, handle, tmp_path):
    outcome = SWEBenchPlugin().run(_task(), handle, "", tmp_path / "work")
    assert outcome.correctness is True
    messages = _messages("resolved")
    assert outcome.steps == steps_from_messages(messages)
    assert len(outcome.steps) == 14
    assert outcome.steps[0] == {
        "action": 'find /testbed -name "pastebin.py" -type f 2>/dev/null',
        "observation": "/testbed/src/_pytest/pastebin.py\n"}
    meta = outcome.meta
    assert meta["initial_prompt"] == messages[1]["content"]
    assert "grader_report" not in meta
    unit_dir = tmp_path / "work" / "a00" / "out" / RESOLVED / "rep0"
    assert meta["bridge_unit_dir"] == str(unit_dir)
    assert {key: meta[key] for key in (
        "instance_id", "exit_status", "score_status", "n_steps",
        "prompt_tokens", "completion_tokens", "agent_wall_sec", "model",
        "max_input_tokens", "prompt_name")} == {
        "instance_id": RESOLVED, "exit_status": "Submitted",
        "score_status": "resolved", "n_steps": 14,
        "prompt_tokens": 67061, "completion_tokens": 2145,
        "agent_wall_sec": 46.1, "model": "student-model",
        "max_input_tokens": 131072,
        "prompt_name": "SWEBENCH_INSTANCE_TEMPLATE"}
    (call,) = bridge.calls()
    argv = call["argv"]
    assert argv[:2] == ["run", "--instance"] and argv[2] == RESOLVED
    assert _argument(argv, "--out") == str(tmp_path / "work" / "a00" / "out")
    assert _argument(argv, "--model") == "student-model"
    assert _argument(argv, "--max-input-tokens") == "131072"
    # A bare unit sends nothing: its prompt is the harness's bare prompt.
    assert "--skill-block-file" not in argv and "--skill-dir" not in argv
    assert call["out_existed"] is False
    (row,) = _usage_rows(tmp_path)
    assert (row["status"], row["in_tokens"], row["out_tokens"],
            row["role"], row["prompt"], row["model"]) == (
        "ok", 67061, 2145, "executor_weak", "SWEBENCH_INSTANCE_TEMPLATE",
        "student-model")


def test_an_unapplied_patch_carries_its_grader_report(
        bridge, handle, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_BRIDGE_CASE", str(FIXTURES / "apply_failed"))
    outcome = SWEBenchPlugin().run(_task(APPLY_FAILED), handle, "",
                                   tmp_path / "work")
    assert outcome.correctness is False
    assert len(outcome.steps) == 41
    assert outcome.meta["score_status"] == "apply_failed"
    # No tests ran: the status and the apply error are the whole report.
    assert outcome.meta["grader_report"] == (
        "Patch status: the patch did not apply.\n"
        "Apply error: >>>>> Patch Apply Failed: patch: **** Only garbage "
        "was found in the patch input.")


def _graded_failure(tmp_path):
    """The resolved fixture, edited into a unit whose tests ran and
    failed."""
    case = tmp_path / "graded_failure"
    shutil.copytree(FIXTURES / "resolved", case)
    result = json.loads((case / "score" / "result.json").read_text())
    failing = "testing/test_pastebin.py::TestPaste::test_create_new_paste"
    result.update(resolved=False, status="unresolved")
    result["tests"]["FAIL_TO_PASS"] = {"success": [], "failure": [failing]}
    (case / "score" / "result.json").write_text(json.dumps(result))
    unit = json.loads((case / "unit.json").read_text())
    unit.update(resolved=False, score_status="unresolved")
    (case / "unit.json").write_text(json.dumps(unit))
    return case, failing


def test_a_graded_failure_names_the_failing_tests_and_the_log_tail(
        bridge, handle, tmp_path, monkeypatch):
    case, failing = _graded_failure(tmp_path)
    monkeypatch.setenv("FAKE_BRIDGE_CASE", str(case))
    outcome = SWEBenchPlugin().run(_task(), handle, "", tmp_path / "work")
    assert outcome.correctness is False
    report = outcome.meta["grader_report"]
    log_tail = (case / "score" / "test_output.txt").read_text().splitlines()
    assert report == (
        "Patch status: the patch applied and the tests ran.\n\n"
        f"Failing FAIL_TO_PASS tests:\n- {failing}\n\n"
        "Last lines of the test log:\n" + "\n".join(log_tail[-40:]))
    # The agent's own patch is not part of the scoring account.
    assert (case / "patch.diff").read_text() not in report


def test_the_grader_report_is_bounded_and_keeps_the_log_end():
    result = {"status": "unresolved", "tests": {
        "FAIL_TO_PASS": {"failure": [f"tests/t.py::test_{i}"
                                     for i in range(25)]},
        "PASS_TO_PASS": {"failure": ["tests/t.py::test_kept"]}}}
    log = "\n".join(f"line {i} " + "x" * 200 for i in range(100)) \
        + "\nFAILED (failures=26)"
    report = grader_report(result, log)
    assert len(report) <= 3000
    assert "- ... and 5 more" in report
    assert "tests/t.py::test_19" in report
    assert "tests/t.py::test_20" not in report
    assert "Failing PASS_TO_PASS tests:\n- tests/t.py::test_kept" in report
    assert report.endswith("FAILED (failures=26)")
    assert "line 59 " not in report  # older than the last 40 lines


def test_the_grader_report_drops_terminal_colors():
    log = "\x1b[32mPASSED\x1b[0m tests/t.py::\x1b[1mtest_a\x1b[0m"
    report = grader_report({"status": "unresolved"}, log)
    assert report.endswith("Last lines of the test log:\nPASSED tests/t.py::test_a")


@pytest.mark.parametrize("status, line", [
    ("empty_patch", "Patch status: empty patch."),
    ("eval_timeout", "Patch status: the evaluation timed out."),
    (None, "Patch status: no grading result."),
])
def test_the_grader_report_states_the_patch_status(status, line):
    result = {} if status is None else {"status": status}
    assert grader_report(result, None) == line


def test_an_infrastructure_failure_raises_for_a_retry(
        bridge, handle, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_BRIDGE_MODE", "infra")
    with pytest.raises(InfrastructureError, match="sandbox lost"):
        SWEBenchPlugin().run(_task(), handle, "", tmp_path / "work")
    (row,) = _usage_rows(tmp_path)
    assert row["status"] == "infra"


def test_a_refused_unit_is_a_config_error(
        bridge, handle, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_BRIDGE_MODE", "config")
    with pytest.raises(ConfigError, match="unknown model 'student-model'"):
        SWEBenchPlugin().run(_task(), handle, "", tmp_path / "work")


def test_a_bridge_crash_is_a_code_bug_with_the_stderr_tail(
        bridge, handle, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_BRIDGE_MODE", "crash")
    with pytest.raises(CodeBugError, match="(?s)exit code 1.*RuntimeError: boom"):
        SWEBenchPlugin().run(_task(), handle, "", tmp_path / "work")
    stderr = tmp_path / "work" / "a00" / "bridge_stderr.txt"
    assert "RuntimeError: boom" in stderr.read_text()


def test_a_bridge_killed_by_a_signal_is_retried_as_infrastructure(
        bridge, handle, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_BRIDGE_MODE", "killed")
    with pytest.raises(InfrastructureError, match="killed by signal 9"):
        SWEBenchPlugin().run(_task(), handle, "", tmp_path / "work")


def test_every_call_gets_a_fresh_out_directory(bridge, handle, tmp_path):
    plugin = SWEBenchPlugin()
    plugin.run(_task(), handle, "", tmp_path / "work")
    # A resume after a crash reuses the attempt token.
    plugin.run(_task(), handle, "", tmp_path / "work")
    no_token = Task(task_id=RESOLVED, instruction="issue")
    plugin.run(no_token, handle, "", tmp_path / "work")
    plugin.run(no_token, handle, "", tmp_path / "work")
    outs = [_argument(call["argv"], "--out") for call in bridge.calls()]
    assert outs == [str(tmp_path / "work" / name / "out") for name in
                    ("a00", "a00-1", "attempt", "attempt-1")]
    assert not any(call["out_existed"] for call in bridge.calls())


# ---------------------------------------------------------------------------
# run: what each kind of unit sends
# ---------------------------------------------------------------------------

def test_a_folder_unit_sends_its_block_and_its_folder(
        bridge, handle, tmp_path):
    meta, locations = _folder_meta(tmp_path)
    task = _task(**meta)
    outcome = SWEBenchPlugin().run(task, handle, "## Skill folder catalog\n",
                                   tmp_path / "work")
    (call,) = bridge.calls()
    assert call["block"] == render_block(folder=SkillFolder.from_task(task))
    assert _argument(call["argv"], "--skill-dir") \
        == meta["skill_folder_dir"]
    assert sorted(call["skill_files"]) == sorted(
        ["catalog.md", *locations])
    assert outcome.meta["prompt_name"] == "SWEBENCH_SKILL_READ"
    block_file = Path(_argument(call["argv"], "--skill-block-file"))
    assert block_file.parent == tmp_path / "work" / "a00"


def test_a_mandatory_folder_unit_sends_the_mandatory_card_rule(
        bridge, handle, tmp_path):
    meta, _ = _folder_meta(tmp_path)
    meta["skill_folder"] = dict(meta["skill_folder"], read_rule="mandatory")
    task = _task(**meta)
    outcome = SWEBenchPlugin().run(task, handle, "", tmp_path / "work")
    (call,) = bridge.calls()
    assert call["block"] == render_block(folder=SkillFolder.from_task(task),
                                         mandatory=True)
    assert get_prompt("SWEBENCH_SKILL_READ_MANDATORY") in call["block"]
    assert get_prompt("SWEBENCH_SKILL_READ") not in call["block"]
    assert outcome.meta["prompt_name"] == "SWEBENCH_SKILL_READ_MANDATORY"


def test_an_empty_folder_still_sends_the_playbook_and_the_rules(
        bridge, handle, tmp_path):
    meta, _ = _folder_meta(tmp_path, names=())
    SWEBenchPlugin().run(_task(**meta), handle, "", tmp_path / "work")
    (call,) = bridge.calls()
    assert call["block"] == (f"Global playbook:\n{PLAYBOOK}\n\n"
                             + get_prompt("SWEBENCH_DISCIPLINE"))
    assert call["skill_files"] == []


def test_a_whole_bank_unit_sends_its_skills_block(bridge, handle, tmp_path):
    skills = "## Skill rules\n- Reproduce the bug first.\n"
    outcome = SWEBenchPlugin().run(_task(), handle, skills,
                                   tmp_path / "work")
    (call,) = bridge.calls()
    assert call["block"] == ("## Skill rules\n- Reproduce the bug first."
                             "\n\n" + get_prompt("SWEBENCH_DISCIPLINE"))
    assert "--skill-dir" not in call["argv"]
    assert outcome.meta["prompt_name"] == "SWEBENCH_INSTANCE_TEMPLATE"


def test_a_card_read_is_detected_against_the_folder_manifest(
        bridge, handle, tmp_path, monkeypatch):
    meta, locations = _folder_meta(tmp_path)
    case = tmp_path / "with_read"
    shutil.copytree(FIXTURES / "resolved", case)
    traj = json.loads((case / "traj.json").read_text())
    command = f"cat /skills/{locations[1]}"
    traj["messages"][2:2] = [
        {"role": "assistant", "content": "Let me read a card.",
         "extra": {"actions": [{"command": command}]}},
        {"role": "tool", "content": "<returncode>0</returncode>",
         "extra": {"raw_output": "the card", "returncode": 0}},
    ]
    (case / "traj.json").write_text(json.dumps(traj))
    monkeypatch.setenv("FAKE_BRIDGE_CASE", str(case))
    trajectory = run_one(
        phase_label="p03:practice", benchmark="swebench", task=_task(**meta),
        model_role="weak", model_name="student-model", attempt_index=0,
        skills_injected="bank:x", skills_block="", handle=handle,
        workdir=tmp_path / "work")
    events = skill_read_events(trajectory, meta["skill_folder"])
    assert [(event["filename"], event["step_index"], event["command"])
            for event in events] == [(locations[1], 0, command)]


# ---------------------------------------------------------------------------
# Trajectory conversion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case, count", [("resolved", 14),
                                         ("apply_failed", 41)])
def test_one_step_per_assistant_message(case, count):
    messages = _messages(case)
    steps = steps_from_messages(messages)
    assert len(steps) == count
    assert all(set(step) == {"action", "observation"}
               and isinstance(step["action"], str)
               and isinstance(step["observation"], str) for step in steps)
    assert initial_prompt(messages) == messages[1]["content"]
    # The last step is the submit command; the exit message (the
    # submission) is what it got back.
    assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in steps[-1]["action"]
    assert steps[-1]["observation"].endswith(messages[-1]["content"])


def test_commands_fall_back_to_tool_calls_and_format_errors_are_observed():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "two commands", "tool_calls": [
            {"function": {"name": "bash",
                          "arguments": json.dumps({"command": "ls"})}},
            {"function": {"name": "bash",
                          "arguments": json.dumps({"command": "pwd"})}}]},
        {"role": "tool", "content": "<output>a</output>",
         "extra": {"raw_output": "a\n"}},
        {"role": "tool", "content": "<output>/testbed</output>"},
        {"role": "assistant", "content": [{"type": "text",
                                           "text": "no tool call"}]},
        {"role": "user", "content": "Tool call error: use the bash tool."},
    ]
    assert steps_from_messages(messages) == [
        {"action": "ls\npwd", "observation": "a\n\n<output>/testbed</output>"},
        {"action": "", "observation": "Tool call error: use the bash tool."},
    ]
