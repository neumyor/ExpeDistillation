"""R9 (W2-C): the phase loop in cli.run_experiment.

The loop is tested end to end over a fake benchmark (stage 0 runs for
real; the run's identity freezes) and FAKE phase drivers injected as
``expedistill.stages.phase_execute`` / ``phase_learn`` -- the modules
W2-A / W2-B own, imported lazily per dispatch, so a sys.modules
monkeypatch is exactly the seam. What is pinned here:

* the ledger: key stays ``"stage"`` (consumer compatibility), the
  freeze row's value is ``p00:freeze``, every sequence entry i's value
  is ``p{i+1:02d}:<name>``;
* the dispatch: one driver call per sequence entry, the PhaseInstance
  carries the right index/label/spec/task ids, and a driver answering
  for another phase is a loud error;
* ``--until-phase``: 0-based sequence index, freeze always runs, the
  bound is checked against the frozen sequence's length (after the
  config loads);
* the run settings checks (ported from the deleted pipeline tests),
  the handle wiring (one client per unique model, per-model
  extra_body, per-role output budgets), the lock, and the
  infra_shortfall_accepted ledger flag.
"""

import dataclasses
import json
import socket
import sys
import types
from pathlib import Path

import pytest

from expedistill import cli
from expedistill.benchmarks.base import (
    GENERIC_PACK, BenchmarkPlugin, Task, ViewRules)
from expedistill.core.config import (
    ExperimentConfig,
    PhaseResult,
    ROLE_DEFAULTS,
)
from expedistill.infra.reliability import ConfigError as InfraConfigError
from expedistill.infra.runlock import RunLockedError
from expedistill.infra.store import read_jsonl
from tests.helpers.fake_plugin import serve_plugin

_FAKE = "fakebench"
TASK_IDS = ("t1", "t2", "t3", "t4")

#: The fixture method: the baseline, two learns around a practice on the
#: val list, one final test -- enough to prove repeated names (learn
#: twice) get distinct pNN labels and each execute phase gets the frozen
#: list its ``tasks`` names.
SEQUENCE = ["baseline", "learn", "practice", "learn", "test"]
PHASES = {
    "baseline": {"kind": "execute", "role": "baseline", "tasks": "train",
          "delivery": {"name": "none"},
          "execution": {"name": "real",
                        "arms": ["executor_strong", "executor_weak"]}},
    "learn": {"kind": "learn",
          "review": {"name": "review", "K": 2},
          "author": {"name": "subtask_cards"}},
    "practice": {"kind": "execute", "role": "practice", "tasks": "val",
          "delivery": {"name": "folder"},
          "execution": {"name": "real_folder", "attempts": 3}},
    "test": {"kind": "execute", "role": "test", "tasks": "test",
          "delivery": {"name": "whole_bank"},
          "execution": {"name": "real"},
          "compare": {"against": "fresh_bare"}},
}


class LoopPlugin(BenchmarkPlugin):
    """Fake benchmark: serves the fixed task set; run() must never be
    called (no execute phase runs for real here -- the drivers are
    fakes)."""

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        return [Task(task_id=i, instruction=f"do {i}")
                for i in (tasks_spec or TASK_IDS)]

    def run(self, task, handle, skills_block, workdir):
        raise AssertionError("the fake drivers must intercept every phase")

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"


@pytest.fixture()
def plugin(monkeypatch, clean_registry):
    """Serve the fake plugin through the real import machinery."""
    serve_plugin(monkeypatch, LoopPlugin())


def _write_config(path: Path) -> Path:
    path.write_text(json.dumps({
        "benchmark": _FAKE,
        "models": {"executor_strong": "strong-model",
                   "executor_weak": "weak-model"},
        "n_attempts_strong": 1,
        "n_attempts_weak": 2,
        "schema_version": 9,
        "sequence": SEQUENCE,
        "phases": PHASES,
        "split": {"train": ["t1", "t2"], "val": ["t4"], "test": ["t3"]},
        "token_budgets": {"default_max_output_tokens": 65536},
    }), encoding="utf-8")
    return path


@pytest.fixture()
def config_path(tmp_path):
    return _write_config(tmp_path / "exp.yaml")


class _FakeClient:
    def __init__(self, model):
        self.model = model
        self.calls = []

    def complete(self, prompt, max_tokens=None, extra=None, **kwargs):
        self.calls.append({"model": self.model, "prompt": prompt,
                           "max_tokens": max_tokens, "extra": extra})
        return ""


class ClientLog:
    def __init__(self):
        self.clients = []

    def make_client(self, model_name):
        client = _FakeClient(model_name)
        self.clients.append(client)
        return client


@pytest.fixture()
def drivers(monkeypatch):
    """Fake phase drivers recording every call; both answer with the
    label they were asked about."""
    calls = []

    def install(kind):
        module = types.ModuleType(f"expedistill.stages.phase_{kind}")

        def run(ctx, phase):
            calls.append((kind, phase))
            return PhaseResult(label=phase.label,
                               summary={"kind": kind, "index": phase.index})

        module.run = run
        monkeypatch.setitem(sys.modules, f"expedistill.stages.phase_{kind}",
                             module)

    install("execute")
    install("learn")
    return calls


# ---------------------------------------------------------------------------
# The loop: dispatch, labels, ledger
# ---------------------------------------------------------------------------

def test_the_loop_dispatches_every_phase_with_the_right_instance(
        plugin, config_path, tmp_path, drivers):
    seen = []
    on_stage = lambda name, summary: seen.append(name)  # noqa: E731
    run_dir = tmp_path / "run"
    last = cli.run_experiment(
        config_path=config_path, run_dir=run_dir,
        make_client=ClientLog().make_client, on_stage=on_stage)

    assert [phase.label for _, phase in drivers] == [
        "p01:baseline", "p02:learn", "p03:practice", "p04:learn", "p05:test"]
    # Kinds dispatch by the phase definition, not the position.
    assert [kind for kind, _ in drivers] == [
        "execute", "learn", "execute", "learn", "execute"]
    # The instance carries the frozen identity of its position.
    d = drivers[0][1]
    assert (d.index, d.name, d.label) == (0, "baseline", "p01:baseline")
    assert d.spec.role == "baseline"
    assert d.task_ids == ("t1", "t2")
    b = drivers[2][1]
    assert (b.index, b.name, b.label) == (2, "practice", "p03:practice")
    assert b.spec.tasks == "val"
    assert b.task_ids == ("t4",)
    c = drivers[4][1]
    assert c.spec.tasks == "test"
    assert c.task_ids == ("t3",)
    # Learn phases run over no tasks.
    assert drivers[1][1].task_ids == ()
    # on_stage fired in the same order, and the last summary is the last
    # phase's.
    assert seen == ["p00:freeze", "p01:baseline", "p02:learn", "p03:practice", "p04:learn",
                   "p05:test"]
    assert last == {"kind": "execute", "index": 4}
    # The ledger KEY stays "stage" (consumer compatibility); the values
    # are the same labels.
    rows = read_jsonl(cli.ledger_path(run_dir))
    assert [row["stage"] for row in rows] == seen
    assert all(set(row) == {"stage", "summary"} for row in rows)


def test_a_driver_answering_for_another_phase_is_a_loud_error(
        plugin, config_path, tmp_path, monkeypatch):
    module = types.ModuleType("expedistill.stages.phase_execute")
    module.run = lambda ctx, phase: PhaseResult(
        label="p99:elsewhere", summary={})
    monkeypatch.setitem(sys.modules,
                        "expedistill.stages.phase_execute", module)
    learn = types.ModuleType("expedistill.stages.phase_learn")
    learn.run = lambda ctx, phase: PhaseResult(label=phase.label, summary={})
    monkeypatch.setitem(sys.modules, "expedistill.stages.phase_learn", learn)
    with pytest.raises(InfraConfigError, match="p99:elsewhere"):
        cli.run_experiment(config_path=config_path,
                           run_dir=tmp_path / "run",
                           make_client=ClientLog().make_client)


def test_infra_shortfall_accepted_flag_reaches_the_ledger_row_top_level(
        plugin, config_path, tmp_path, monkeypatch):
    # The same never-silent contract the old pipeline had: a phase that
    # accepted holes is flagged on the ledger line itself, not only in
    # the nested summary.
    drivers = []
    for kind in ("execute", "learn"):
        module = types.ModuleType(f"expedistill.stages.phase_{kind}")
        module.run = lambda ctx, phase, k=kind: PhaseResult(
            label=phase.label,
            summary={"kind": k,
                     "infra_shortfall_accepted": k == "execute"})
        monkeypatch.setitem(sys.modules, f"expedistill.stages.phase_{kind}",
                             module)
        drivers.append(module)
    run_dir = tmp_path / "run"
    cli.run_experiment(config_path=config_path, run_dir=run_dir,
                       make_client=ClientLog().make_client)
    rows = read_jsonl(cli.ledger_path(run_dir))
    flagged = [row for row in rows if row["stage"] == "p01:baseline"]
    assert flagged[0]["infra_shortfall_accepted"] is True
    assert all("infra_shortfall_accepted" not in row
               for row in rows if row["stage"] == "p02:learn")


# ---------------------------------------------------------------------------
# --until-phase
# ---------------------------------------------------------------------------

def test_until_phase_stops_after_the_named_entry(
        plugin, config_path, tmp_path, drivers):
    last = cli.run_experiment(
        config_path=config_path, run_dir=tmp_path / "run",
        make_client=ClientLog().make_client, until_phase=2)
    assert [phase.label for _, phase in drivers] == [
        "p01:baseline", "p02:learn", "p03:practice"]
    # Freeze always runs, so the return value is a real summary.
    assert last == {"kind": "execute", "index": 2}


@pytest.mark.parametrize("bad", [-2, 5, 99])
def test_until_phase_out_of_bounds_is_refused_after_the_config_loads(
        plugin, config_path, tmp_path, drivers, bad):
    # The bound is the frozen sequence's length (5 entries -> [0, 4];
    # -1 is legal since W3-B -- the freeze-only stop), so it can only
    # be checked once the config is loaded -- never a code constant
    # (the old --until-stage's [0, 6]).
    with pytest.raises(InfraConfigError, match="until_phase") as excinfo:
        cli.run_experiment(
            config_path=config_path, run_dir=tmp_path / "run",
            make_client=ClientLog().make_client, until_phase=bad)
    # Nothing ran: the refusal precedes the lock and the freeze.
    assert drivers == []
    assert not (tmp_path / "run" / "manifest.json").exists()
    # The message explains the freeze-only meaning of -1.
    assert "run the freeze step only" in str(excinfo.value)


def test_until_phase_minus_one_freezes_and_dispatches_no_phase(
        plugin, config_path, tmp_path, drivers):
    # The pre-run seeders need a manifest and a task table on disk before
    # any phase dispatches.
    run_dir = tmp_path / "run"
    summary = cli.run_experiment(
        config_path=config_path, run_dir=run_dir,
        make_client=ClientLog().make_client, until_phase=-1)
    assert drivers == []
    assert [row["stage"] for row in read_jsonl(cli.ledger_path(run_dir))] == [
        "p00:freeze"]
    # The return value is the real freeze summary: the seeder reads the
    # task table out of it.
    assert summary["train"] == ["t1", "t2"] and summary["test"] == ["t3"]
    assert (run_dir / "manifest.json").is_file()

    # The stop stays resumable: the next start replays the freeze row and
    # then runs the first phase.
    cli.run_experiment(config_path=config_path, run_dir=run_dir,
                       make_client=ClientLog().make_client, until_phase=0)
    assert [phase.label for _, phase in drivers] == ["p01:baseline"]
    assert [row["stage"] for row in read_jsonl(cli.ledger_path(run_dir))] == [
        "p00:freeze", "p00:freeze", "p01:baseline"]


# ---------------------------------------------------------------------------
# Run settings, handles, lock (ported from the deleted pipeline tests)
# ---------------------------------------------------------------------------

def test_locked_run_dir_raises_run_locked_error(
        plugin, config_path, tmp_path):
    import os
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.lock").write_text(json.dumps({
        "pid": os.getpid(),  # alive by definition: this test process
        "hostname": socket.gethostname(),
        "acquired_at": "2026-10-10T00:00:00+00:00",
    }), encoding="utf-8")
    with pytest.raises(RunLockedError):
        cli.run_experiment(config_path=config_path, run_dir=run_dir,
                           make_client=ClientLog().make_client)


def test_missing_role_is_config_error_naming_role_and_config(
        plugin, tmp_path):
    # An explicitly empty role survives config validation (the role table
    # is only default-filled, never pruned) and must be caught at handle
    # build time, before any client is built.
    config_path = tmp_path / "broken.yaml"
    config_path.write_text(
        "benchmark: fakebench\n"
        "schema_version: 9\n"
        "split: {train: [t1, t2, t4], test: [t3]}\n"
        "models:\n"
        "  executor_strong: strong-model\n"
        "  executor_weak: weak-model\n"
        "  judge:\n"
        "sequence: [D, L, C]\n"
        "phases:\n"
        "  D: {kind: execute, role: baseline, delivery: {name: none},"
        " execution: {name: real, arms: [executor_strong, executor_weak]}}\n"
        "  L: {kind: learn, review: {name: review, K: 2},"
        " author: {name: subtask_cards}}\n"
        "  C: {kind: execute, role: test, tasks: test,"
        " delivery: {name: whole_bank}, execution: {name: real},"
        " compare: {against: fresh_bare}}\n",
        encoding="utf-8")
    with pytest.raises(InfraConfigError) as excinfo:
        cli.run_experiment(
            config_path=config_path, run_dir=tmp_path / "run",
            make_client=ClientLog().make_client)
    assert "judge" in str(excinfo.value)
    assert str(config_path) in str(excinfo.value)


def test_invalid_config_is_config_error(plugin, tmp_path):
    # load_config's own ValueError (here: no executor_weak) is surfaced
    # as a ConfigError naming the config file, so the CLI prints it
    # cleanly.
    config_path = tmp_path / "no_weak.yaml"
    config_path.write_text(
        "benchmark: fakebench\n"
        "schema_version: 9\n"
        "split: {train: [t1, t2, t4], test: [t3]}\n"
        "models:\n"
        "  executor_strong: strong-model\n",
        encoding="utf-8")
    with pytest.raises(InfraConfigError) as excinfo:
        cli.run_experiment(
            config_path=config_path, run_dir=tmp_path / "run",
            make_client=ClientLog().make_client)
    assert str(config_path) in str(excinfo.value)


def test_one_client_per_unique_model(plugin, config_path, tmp_path, drivers):
    log = ClientLog()
    cli.run_experiment(config_path=config_path, run_dir=tmp_path / "run",
                       make_client=log.make_client)
    # Six roles, two distinct model names: two clients.
    assert len(log.clients) == 2
    assert {client.model for client in log.clients} == {
        "strong-model", "weak-model"}


def test_build_handles_wires_every_per_role_and_per_model_setting(
        plugin, monkeypatch, tmp_path):
    pack = dataclasses.replace(
        GENERIC_PACK, protocol="what the reader sees",
        overrides={"PLAYBOOK_UPDATE": "FAKE_PLAYBOOK"})
    monkeypatch.setattr(LoopPlugin, "prompt_pack", pack)
    config = ExperimentConfig(
        benchmark=_FAKE,
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model",
                "judge": "judge-model"},
        sequence=["baseline", "learn", "test"],
        phases={name: PHASES[name] for name in ("baseline", "learn", "test")},
        model_options={"strong-model": {"reasoning_effort": "max"},
                       "judge-model": {"top_p": 0.9}},
        token_budgets={
            "default_max_output_tokens": 65536,
            "roles": {
                "judge": {"max_output_tokens": 1024},
                "segmenter": {"max_output_tokens": 2048},
                "executor_weak": {"max_output_tokens": 65536,
                                  "max_input_tokens": 262144},
                "executor_strong": {"max_output_tokens": 65536,
                                    "max_input_tokens": 524288},
            },
        },
    )
    handles = cli._build_handles(
        config, tmp_path / "experiment.yaml", ClientLog().make_client,
        infra_retries=1, infra_backoff_seconds=0.0)
    assert set(handles) == set(ROLE_DEFAULTS)

    # Options follow the MODEL NAME: every role on the strong model carries
    # the strong model's extras, an explicitly overridden role its own.
    for role in ROLE_DEFAULTS:
        expected = {"strong-model": {"reasoning_effort": "max"},
                    "judge-model": {"top_p": 0.9},
                    "weak-model": {}}[config.models[role]]
        assert handles[role].extra_body == expected, role
    # Unlisted models get a fresh private dict, not a shared default.
    assert (handles["executor_weak"].extra_body
            is not handles["segmenter"].extra_body)

    # The prompt pack reaches every handle, each with its own alias dict.
    for handle in handles.values():
        assert handle.prompt_defaults == {
            **GENERIC_PACK.vocabulary.variables(),
            "protocol": "what the reader sees"}
        assert handle.prompt_aliases == {"PLAYBOOK_UPDATE": "FAKE_PLAYBOOK"}
    assert (handles["author"].prompt_aliases
            is not handles["judge"].prompt_aliases)

    # Per-role output budgets fall back to the default; the handle's
    # max_tokens is the only place the budget becomes live. Unlisted roles
    # keep max_input_tokens None.
    for role in ROLE_DEFAULTS:
        expected = {"judge": 1024, "segmenter": 2048}.get(role, 65536)
        assert handles[role].max_tokens == expected, role
    assert handles["executor_weak"].max_input_tokens == 262144
    assert handles["executor_strong"].max_input_tokens == 524288
    assert handles["judge"].max_input_tokens is None


def test_config_without_token_budgets_stops_before_the_freeze(
        plugin, tmp_path, drivers):
    # The token_budgets field is optional at load time, but the helper
    # fails closed; the loop surfaces that at handle-build time as an
    # infra ConfigError naming the config -- freeze era: no client
    # built, nothing frozen, no ledger line.
    raw = json.loads(_write_config(tmp_path / "tmp.yaml").read_text(
        encoding="utf-8"))
    del raw["token_budgets"]
    config_path = tmp_path / "no_budgets.yaml"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    run_dir = tmp_path / "run"
    with pytest.raises(InfraConfigError) as excinfo:
        cli.run_experiment(config_path=config_path, run_dir=run_dir,
                           make_client=ClientLog().make_client)
    assert "token_budgets" in str(excinfo.value)
    assert str(config_path) in str(excinfo.value)
    assert not cli.ledger_path(run_dir).exists()
    assert drivers == []


@pytest.mark.parametrize("kwargs", [
    {"workers": 0},
    {"workers": True},
    {"llm_infra_retries": -1},
    {"llm_infra_backoff_seconds": float("nan")},
    {"llm_timeout_sec": 0},
    {"k1_stagger": -0.5},
    {"allow_infra_shortfall": "yes"},
    {"rollout_workers": 0},
    {"rollout_workers": True},
])
def test_bad_run_settings_are_config_errors_before_anything_runs(kwargs):
    with pytest.raises(InfraConfigError, match="invalid run setting"):
        cli._check_run_settings(
            workers=kwargs.get("workers", 1),
            llm_infra_retries=kwargs.get("llm_infra_retries", 3),
            llm_infra_backoff_seconds=kwargs.get(
                "llm_infra_backoff_seconds", 5.0),
            llm_timeout_sec=kwargs.get("llm_timeout_sec", 300.0),
            k1_stagger=kwargs.get("k1_stagger", 0.0),
            allow_infra_shortfall=kwargs.get(
                "allow_infra_shortfall", False),
            rollout_workers=kwargs.get("rollout_workers"))


def test_a_stopped_run_replays_from_the_first_phase(
        plugin, config_path, tmp_path, drivers):
    # The resume model: until_phase is operational, so a stopped run
    # simply replays the sequence from the start and the drivers skip
    # finished work (their own unit-level resume).
    run_dir = tmp_path / "run"
    cli.run_experiment(config_path=config_path, run_dir=run_dir,
                       make_client=ClientLog().make_client, until_phase=1)
    assert len(drivers) == 2
    cli.run_experiment(config_path=config_path, run_dir=run_dir,
                       make_client=ClientLog().make_client)
    assert len(drivers) == 7  # 2 + the full replay of 5
    rows = read_jsonl(cli.ledger_path(run_dir))
    assert [row["stage"] for row in rows] == [
        "p00:freeze", "p01:baseline", "p02:learn",
        "p00:freeze", "p01:baseline", "p02:learn", "p03:practice", "p04:learn", "p05:test"]
