"""The test phase driver's folder and executor wiring over fake plugins.

Folder delivery (materialize once per phase, attach to the with-bank units
only, refuse an incapable plugin), the per-unit read sets in the phase
summary, the executor choice (weak by default, strong on request) and a
true mid-run resume of the folder phase. Fixtures are
tests/helpers/final_test_folder.py's (no network); the plugin-specific
upload behaviour is tests/unit/benchmarks/test_tb_folder_sandbox.py's and a
plugin that really executes a folder unit is test_local_folder_final_test.py's.
"""

import dataclasses
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from expedistill.core import ids
from expedistill.core.config import (
    CompareSpec,
    ExecutePhaseSpec,
    ExternalControlSpec,
    PhaseInstance,
)
from expedistill.infra.reliability import ConfigError
from expedistill.infra.store import Store, object_filename
from expedistill.inject.skill_text import render_card
from expedistill.stages import phase_execute
from expedistill.stages.phase_execute import resolve
from expedistill.stages.phase_execute.phase_disk import (
    SUMMARY_NAME,
    arm_dir,
    execute_phase_dir,
)
from tests.helpers.fake_plugin import serve_plugin
from tests.helpers.final_test_folder import (
    FAKE,
    TASK_IDS,
    CattingPlugin,
    FolderPlugin,
    folder_c_phase,
    folder_config,
    folder_ctx,
    folder_ctx_at,
    skill_card,
    trajectory_id,
    write_bank,
)

pytestmark = pytest.mark.usefixtures("clean_registry")

# ---------------------------------------------------------------------------
# Folder delivery: materialize once, attach to the with-bank units
# ---------------------------------------------------------------------------

def test_c_phase_materializes_once_and_attaches_to_with_bank_units(
        monkeypatch, tmp_path):
    plugin = serve_plugin(monkeypatch, FolderPlugin(capable=True))
    ctx = folder_ctx(tmp_path)
    skills = [skill_card(1), skill_card(2)]
    write_bank(ctx, skills)
    phase = folder_c_phase()

    result = phase_execute.run(ctx, phase).summary

    assert result["role"] == "test"
    assert len(plugin.calls) == 2 * len(TASK_IDS)  # both arms, weak x1
    with_bank = [call for call in plugin.calls
                 if call["skills_block"]]
    control = [call for call in plugin.calls
               if not call["skills_block"]]
    assert len(with_bank) == len(TASK_IDS)
    assert len(control) == len(TASK_IDS)
    # Every with-bank unit carries the manifest and the materialized
    # directory; the control units carry neither.
    manifests = {id(call["task"].meta.get("skill_folder"))
                 for call in with_bank}
    assert len(manifests) == 1  # one shared read-only manifest
    manifest = with_bank[0]["task"].meta["skill_folder"]
    folder_dir = Path(with_bank[0]["task"].meta["skill_folder_dir"])
    assert folder_dir == Path(ctx.run_dir) / "phases" / "p03_test" / "skill_folder"
    for call in control:
        assert call["task"].meta.get("skill_folder") is None
        assert call["task"].meta.get("skill_folder_dir") is None
    # The folder on disk: the catalog plus one <skill>/SKILL.md per
    # active skill (frontmatter plus the skill body).
    assert sorted(path.relative_to(folder_dir).as_posix()
                  for path in folder_dir.rglob("*") if path.is_file()) == \
        sorted([manifest["catalog_filename"]]
               + [entry["filename"] for entry in manifest["files"]])
    assert (folder_dir / manifest["catalog_filename"]).read_text(
        encoding="utf-8") == manifest["catalog_text"]
    for skill in skills:
        filename = manifest["files"][skills.index(skill)]["filename"]
        text = (folder_dir / filename).read_text(encoding="utf-8")
        assert text.split("\n---\n", 1)[1].lstrip("\n") == render_card(skill)
    # The skills block handed to the plugin is the catalog (the delivery's
    # frozen row shape); the capable plugin is the one that must not
    # inject it -- the prompt test holds that side.
    for call in with_bank:
        assert call["skills_block"] == manifest["catalog_text"]


def test_a_resumed_c_phase_materializes_idempotently(monkeypatch, tmp_path):
    serve_plugin(monkeypatch, FolderPlugin(capable=True))
    ctx = folder_ctx(tmp_path)
    write_bank(ctx, [skill_card(1)])
    phase = folder_c_phase()
    phase_execute.run(ctx, phase)
    folder_dir = Path(ctx.run_dir) / "phases" / "p03_test" / "skill_folder"
    before = {path.relative_to(folder_dir).as_posix():
              (path.read_bytes(), path.stat().st_mtime_ns)
              for path in folder_dir.rglob("*") if path.is_file()}

    phase_execute.run(ctx, phase)

    after = {path.relative_to(folder_dir).as_posix():
             (path.read_bytes(), path.stat().st_mtime_ns)
             for path in folder_dir.rglob("*") if path.is_file()}
    # Same bytes AND untouched files: unchanged content is not rewritten
    # (M2's idempotency contract, held through the phase driver).
    assert before == after


def test_an_empty_bank_keeps_the_folder_presentation(monkeypatch, tmp_path):
    # An empty bank (here: its only card discarded) still runs the
    # with-bank units as folder units: the manifest with no files and the
    # (empty) materialized directory attach as with a bank, so the plugin
    # keeps its folder prompt; only the catalog is empty. The control arm
    # stays bare, and every read set is reported empty, not skipped.
    plugin = serve_plugin(monkeypatch, CattingPlugin(capable=True))
    ctx = folder_ctx(tmp_path)
    write_bank(ctx, [skill_card(1, status="discarded")])
    phase = folder_c_phase()

    result = phase_execute.run(ctx, phase).summary

    assert result["complete"] is True
    assert result["skill_files_read"] == {
        trajectory_id(task_id): [] for task_id in TASK_IDS}
    folder_dir = Path(ctx.run_dir) / "phases" / "p03_test" / "skill_folder"
    with_bank = [call for call in plugin.calls
                 if call["task"].meta.get("skill_folder") is not None]
    assert len(with_bank) == len(TASK_IDS)
    for call in with_bank:
        assert call["task"].meta["skill_folder"]["files"] == []
        assert call["task"].meta["skill_folder_dir"] == str(folder_dir)
    assert all(call["skills_block"] == "" for call in plugin.calls)
    assert folder_dir.is_dir() and not any(folder_dir.iterdir())


@pytest.mark.parametrize("status", ["active", "discarded"])
def test_a_folder_c_phase_on_an_incapable_plugin_stops_loudly(
        monkeypatch, tmp_path, status):
    # The phase driver refuses a folder phase on an incapable plugin
    # BEFORE dispatch, bank or no bank (the per-unit E1 guard is the
    # second layer).
    plugin = serve_plugin(monkeypatch, FolderPlugin(capable=False))
    ctx = folder_ctx(tmp_path)
    write_bank(ctx, [skill_card(1, status=status)])
    phase = folder_c_phase()

    with pytest.raises(ConfigError, match="does not support skill"):
        phase_execute.run(ctx, phase)
    assert plugin.calls == []  # refused before any unit ran


# ---------------------------------------------------------------------------
# The phase summary carries the per-unit read sets
# ---------------------------------------------------------------------------

def test_folder_c_phase_reports_the_read_sets_in_its_summary(
        monkeypatch, tmp_path):
    plugin = serve_plugin(monkeypatch, CattingPlugin(capable=True))
    ctx = folder_ctx(tmp_path)
    write_bank(ctx, [skill_card(1), skill_card(2)])
    phase = folder_c_phase()

    result = phase_execute.run(ctx, phase).summary

    # The with-bank arm's units, keyed by trajectory id: t1 cat the
    # first manifest file, t2 both (manifest order), t3 nothing.
    assert result["skill_files_read"] == {
        trajectory_id("t1"): [skill_card(1).skill_id],
        trajectory_id("t2"): [skill_card(1).skill_id, skill_card(2).skill_id],
        trajectory_id("t3"): [],
    }
    # Only the with-bank units carry a manifest (the control arm's
    # calls never do -- the materialize-once test's pin), so the read
    # sets are exactly the parse of the with-bank units' cats.
    with_bank = [call for call in plugin.calls
                 if call["task"].meta.get("skill_folder") is not None]
    assert sorted(call["task"].task_id for call in with_bank) \
        == ["t1", "t2", "t3"]


def _plain_c_phase():
    """A test phase on the OLD execution pair (whole_bank + real): the
    standard test combo, no folder anywhere."""
    return PhaseInstance(
        index=2, name="test", label="p03:test",
        spec=ExecutePhaseSpec(
            role="test", tasks="test",
            delivery={"name": "whole_bank"},
            execution={"name": "real"},
            compare=CompareSpec(against="fresh_bare"),
            acceptance=None, kind="execute"),
        task_ids=TASK_IDS)


def test_the_old_execution_summary_carries_no_read_sets(
        monkeypatch, tmp_path):
    plugin = serve_plugin(monkeypatch, CattingPlugin(capable=False))
    ctx = folder_ctx(tmp_path)
    write_bank(ctx, [skill_card(1)])
    phase = _plain_c_phase()

    result = phase_execute.run(ctx, phase).summary

    assert "skill_files_read" not in result
    # Every unit ran with the old plain injection: no manifest anywhere.
    assert all(call["task"].meta.get("skill_folder") is None
               for call in plugin.calls)


# ---------------------------------------------------------------------------
# A true mid-run resume of the folder phase
# ---------------------------------------------------------------------------

def test_a_folder_c_phase_resumed_mid_run_lands_on_the_one_shot_summary(
        monkeypatch, tmp_path):
    plugin = serve_plugin(monkeypatch, CattingPlugin(capable=True))
    phase = folder_c_phase()
    skills = [skill_card(1), skill_card(2)]

    # The one-shot run: every unit executes once, the summary lands.
    ctx_once = folder_ctx_at(tmp_path / "one_shot")
    write_bank(ctx_once, skills)
    phase_execute.run(ctx_once, phase)
    summary_once = (execute_phase_dir(ctx_once, phase) / SUMMARY_NAME).read_bytes()

    # The interrupted run: the crash happened after ONE with-bank unit
    # and ONE control unit stored (1 of 3 in each arm). Copy exactly
    # those stored trajectories into a fresh run's arm stores -- the
    # bytes the killed process itself had written.
    plugin.calls.clear()
    ctx_half = folder_ctx_at(tmp_path / "half")
    write_bank(ctx_half, skills)
    stored_id = trajectory_id("t1")
    for arm in ("with_bank", "control"):
        source = Store(arm_dir(ctx_once, phase, arm) / "store")
        target = Store(arm_dir(ctx_half, phase, arm) / "store")
        shutil.copy(source.path_for("trajectories", stored_id),
                    target.path_for("trajectories", stored_id))

    phase_execute.run(ctx_half, phase)

    # The resume REUSED the stored units: only t2 and t3 reached the
    # plugin in each arm (the one-shot run answered six calls, this
    # one four -- the stored units never re-ran).
    with_bank = [call for call in plugin.calls
                 if call["task"].meta.get("skill_folder") is not None]
    control = [call for call in plugin.calls
               if call["task"].meta.get("skill_folder") is None]
    assert sorted(call["task"].task_id for call in with_bank) == \
        ["t2", "t3"]
    assert sorted(call["task"].task_id for call in control) == \
        ["t2", "t3"]
    # And the whole summary -- read sets included -- is byte-identical
    # to the one-shot run's (the fresh units are deterministic, and the
    # pre-stored t1 trajectories are the one-shot run's own).
    summary_half = (execute_phase_dir(ctx_half, phase)
                    / SUMMARY_NAME).read_bytes()
    assert summary_half == summary_once


# ---------------------------------------------------------------------------
# The executor: weak by default, strong on request
# ---------------------------------------------------------------------------

class RoleRecordingPlugin(FolderPlugin):
    """A capable folder plugin that records which model role each call
    was dispatched for."""

    def __init__(self):
        super().__init__(capable=True)
        self.roles = []

    def run(self, task, handle, skills_block, workdir):
        self.roles.append(handle.role)
        return super().run(task, handle, skills_block, workdir)


def _phase(*, executor=None, compare):
    execution = {"name": "real_folder"}
    if executor is not None:
        execution["executor"] = executor
    return PhaseInstance(
        index=2, name="test", label="p03:test",
        spec=ExecutePhaseSpec(
            role="test", tasks="test", delivery={"name": "folder"},
            execution=execution, compare=compare, acceptance=None,
            kind="execute"),
        task_ids=TASK_IDS)


def _deep_strong_ctx(tmp_path):
    """The folder-test context with distinct depths: strong 2, weak 1."""
    return dataclasses.replace(
        folder_ctx(tmp_path),
        config=dataclasses.replace(folder_config(), n_attempts_strong=2))


def _trajectory_files(run_dir, arm):
    directory = (run_dir / "phases" / "p03_test" / arm / "store"
                 / "stage1_baseline" / "trajectories")
    return sorted(path.name for path in directory.glob("*.json"))


def _plain_real_spec():
    return ExecutePhaseSpec(
        role="test", tasks="test", delivery={"name": "whole_bank"},
        execution={"name": "real"}, compare=None, acceptance=None,
        kind="execute")


@pytest.mark.parametrize("spec, expected", [
    pytest.param(_phase(compare=None).spec, "weak", id="folder_default"),
    pytest.param(_plain_real_spec(), "weak", id="plain_real"),
    pytest.param(_phase(executor="executor_strong", compare=None).spec,
                 "strong", id="strong_named"),
    pytest.param(_phase(executor="executor_middle", compare=None).spec,
                 None, id="unknown_refused"),
])
def test_the_executor_role_follows_the_execution_params(spec, expected):
    if expected is None:
        with pytest.raises(ConfigError, match="executor must be one of"):
            resolve.execution_params(spec)
    else:
        assert resolve.executor_role(resolve.execution_params(spec)) \
            == expected


def test_a_strong_test_phase_dispatches_the_strong_handle_at_its_depth(
        monkeypatch, tmp_path):
    plugin = serve_plugin(monkeypatch, RoleRecordingPlugin())
    ctx = _deep_strong_ctx(tmp_path)
    write_bank(ctx, [skill_card(1)])

    result = phase_execute.run(
        ctx, _phase(executor="executor_strong",
                    compare=CompareSpec(against="fresh_bare"))).summary

    # Both arms (with-bank and the fresh bare control) run the strong
    # handle, twice per task: strong depth 2, not the weak depth 1.
    assert plugin.roles == ["executor_strong"] * (2 * len(TASK_IDS) * 2)
    assert result["complete"] is True
    expected = sorted(
        object_filename(ids.make_trajectory_id(
            "p03:test", ids.make_task_id(FAKE, task), "strong", attempt))
        for task in TASK_IDS for attempt in range(2))
    for arm in ("with_bank", "control"):
        assert _trajectory_files(ctx.run_dir, arm) == expected


def test_the_folder_test_phase_runs_its_attempts(monkeypatch, tmp_path):
    # ``attempts`` overrides the executor's global depth in both arms.
    plugin = serve_plugin(monkeypatch, RoleRecordingPlugin())
    ctx = _deep_strong_ctx(tmp_path)
    write_bank(ctx, [skill_card(1)])
    phase = _phase(compare=CompareSpec(against="fresh_bare"))
    phase = dataclasses.replace(phase, spec=dataclasses.replace(
        phase.spec, execution={"name": "real_folder", "attempts": 3}))

    result = phase_execute.run(ctx, phase).summary

    assert plugin.roles == ["executor_weak"] * (2 * len(TASK_IDS) * 3)
    assert result["complete"] is True


def test_a_strong_test_phase_reads_the_pinned_campaign_column(
        monkeypatch, tmp_path):
    plugin = serve_plugin(monkeypatch, RoleRecordingPlugin())
    ctx = _deep_strong_ctx(tmp_path)
    write_bank(ctx, [skill_card(1)])
    campaign = tmp_path / "campaign.json"
    campaign.write_text(json.dumps({"tasks": {
        f"dpsk:{task}": {"n": 3, "rewards": [1.0, 0.0, 1.0]}
        for task in TASK_IDS}}), encoding="utf-8")
    digest = hashlib.sha256(campaign.read_bytes()).hexdigest()
    compare = CompareSpec(
        against="external_campaign",
        external=ExternalControlSpec(
            path=str(campaign), sha256=digest, model_key="dpsk"))

    result = phase_execute.run(
        ctx, _phase(executor="executor_strong", compare=compare)).summary

    # Only the with-bank arm executes; the control is the archived column.
    assert plugin.roles == ["executor_strong"] * (len(TASK_IDS) * 2)
    # The control is aligned to the strong depth (2): the first two of the
    # archived rewards [1, 0, 1].
    assert result["control"]["avg_at_n"] == pytest.approx(0.5)
    sheets_dir = ctx.run_dir / "phases" / "p03_test" / "answer_sheets"
    external = [json.loads(path.read_text(encoding="utf-8"))
                for path in sheets_dir.glob("*.json")]
    external = [sheet["execution"] for sheet in external
                if sheet["execution"]["source"] == "external"]
    assert len(external) == len(TASK_IDS) * 2
    assert {record["role"] for record in external} == {"strong"}
    assert {record["model_role"] for record in external} == {"strong"}
