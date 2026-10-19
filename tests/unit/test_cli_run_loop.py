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

import importlib.abc
import importlib.machinery
import json
import socket
import sys
import types
from pathlib import Path

import pytest

from expedistill import cli
from expedistill.benchmarks.base import BenchmarkPlugin, Task, ViewRules
from expedistill.benchmarks.registry import _reset_registry, register_plugin
from expedistill.core.config import (
    ExperimentConfig,
    PhaseResult,
    ROLE_DEFAULTS,
    load_config,
)
from expedistill.infra.reliability import ConfigError as InfraConfigError
from expedistill.infra.runlock import RunLockedError
from expedistill.infra.store import read_jsonl

_FAKE = "fakebench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"
TASK_IDS = ("t1", "t2", "t3")

#: The fixture method: one baseline, two learns, one final test -- enough
#: to prove repeated names (L twice) get distinct pNN labels.
SEQUENCE = ["D", "L", "B", "L", "C"]
PHASES = {
    "D": {"kind": "execute", "role": "baseline", "tasks": "train",
          "delivery": {"name": "none"},
          "execution": {"name": "real",
                        "arms": ["executor_strong", "executor_weak"]}},
    "L": {"kind": "learn",
          "evidence": {"name": "segment", "K": 2},
          "author": {"name": "distill_diagnose"},
          "curate": {"name": "none", "token_budget": 4096}},
    "B": {"kind": "execute", "role": "live", "tasks": "train",
          "delivery": {"name": "group_by_task"},
          "execution": {"name": "real", "n_attempts": 1},
          "compare": {"against": "D"}},
    "C": {"kind": "execute", "role": "test", "tasks": "test",
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
def plugin(monkeypatch):
    """Serve the fake plugin through the real import machinery."""
    fullname = _MODULE

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            register_plugin(LoopPlugin())

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == fullname:
                return importlib.machinery.ModuleSpec(name, Loader())
            return None

    _reset_registry()
    sys.modules.pop(fullname, None)
    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, Finder()])
    yield
    sys.modules.pop(fullname, None)
    _reset_registry()


def _write_config(path: Path) -> Path:
    path.write_text(json.dumps({
        "benchmark": _FAKE,
        "mode": "split",
        "models": {"executor_strong": "strong-model",
                   "executor_weak": "weak-model"},
        "n_attempts_strong": 1,
        "n_attempts_weak": 2,
        "schema_version": 4,
        "sequence": SEQUENCE,
        "phases": PHASES,
        "split": {"train": ["t1", "t2"], "test": ["t3"]},
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
    last = cli.run_experiment(
        config_path=config_path, run_dir=tmp_path / "run",
        make_client=ClientLog().make_client, on_stage=on_stage)

    assert [phase.label for _, phase in drivers] == [
        "p01:D", "p02:L", "p03:B", "p04:L", "p05:C"]
    # Kinds dispatch by the phase definition, not the position.
    assert [kind for kind, _ in drivers] == [
        "execute", "learn", "execute", "learn", "execute"]
    # The instance carries the frozen identity of its position.
    d = drivers[0][1]
    assert (d.index, d.name, d.label) == (0, "D", "p01:D")
    assert d.spec.role == "baseline"
    assert d.task_ids == ("t1", "t2")
    b = drivers[2][1]
    assert (b.index, b.name, b.label) == (2, "B", "p03:B")
    assert b.task_ids == ("t1", "t2")
    c = drivers[4][1]
    assert c.spec.tasks == "test"
    assert c.task_ids == ("t3",)
    # Learn phases run over no tasks.
    assert drivers[1][1].task_ids == ()
    # on_stage fired in the same order, and the last summary is the last
    # phase's.
    assert seen == ["p00:freeze", "p01:D", "p02:L", "p03:B", "p04:L",
                   "p05:C"]
    assert last == {"kind": "execute", "index": 4}


def test_the_ledger_rows_key_stage_and_pnn_values(
        plugin, config_path, tmp_path, drivers):
    run_dir = tmp_path / "run"
    cli.run_experiment(config_path=config_path, run_dir=run_dir,
                       make_client=ClientLog().make_client)
    rows = read_jsonl(cli.ledger_path(run_dir))
    # The KEY stays "stage" (consumer compatibility, the implementation
    # contract section 6); the values are p00:freeze then pNN:<name>.
    assert [row["stage"] for row in rows] == [
        "p00:freeze", "p01:D", "p02:L", "p03:B", "p04:L", "p05:C"]
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
    flagged = [row for row in rows if row["stage"] == "p01:D"]
    assert flagged[0]["infra_shortfall_accepted"] is True
    assert all("infra_shortfall_accepted" not in row
               for row in rows if row["stage"] == "p02:L")


# ---------------------------------------------------------------------------
# --until-phase
# ---------------------------------------------------------------------------

def test_until_phase_stops_after_the_named_entry(
        plugin, config_path, tmp_path, drivers):
    last = cli.run_experiment(
        config_path=config_path, run_dir=tmp_path / "run",
        make_client=ClientLog().make_client, until_phase=2)
    assert [phase.label for _, phase in drivers] == [
        "p01:D", "p02:L", "p03:B"]
    # Freeze always runs, so the return value is a real summary.
    assert last == {"kind": "execute", "index": 2}


def test_until_phase_none_runs_the_whole_sequence(
        plugin, config_path, tmp_path, drivers):
    cli.run_experiment(config_path=config_path, run_dir=tmp_path / "run",
                       make_client=ClientLog().make_client)
    assert len(drivers) == 5


@pytest.mark.parametrize("bad", [-2, 5, 99])
def test_until_phase_out_of_bounds_is_refused_after_the_config_loads(
        plugin, config_path, tmp_path, drivers, bad):
    # The bound is the frozen sequence's length (5 entries -> [0, 4];
    # -1 is legal since W3-B -- the freeze-only stop), so it can only
    # be checked once the config is loaded -- never a code constant
    # (the old --until-stage's [0, 6]).
    with pytest.raises(InfraConfigError, match="until_phase"):
        cli.run_experiment(
            config_path=config_path, run_dir=tmp_path / "run",
            make_client=ClientLog().make_client, until_phase=bad)
    # Nothing ran: the refusal precedes the lock and the freeze.
    assert drivers == []
    assert not (tmp_path / "run" / "manifest.json").exists()


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
        "schema_version: 4\n"
        "mode: full\n"
        "models:\n"
        "  executor_strong: strong-model\n"
        "  executor_weak: weak-model\n"
        "  judge:\n"
        "sequence: [D, L, C]\n"
        "phases:\n"
        "  D: {kind: execute, role: baseline, delivery: {name: none},"
        " execution: {name: real, arms: [executor_strong, executor_weak]}}\n"
        "  L: {kind: learn, evidence: {name: segment, K: 2},"
        " author: {name: distill_diagnose},"
        " curate: {name: none, token_budget: 4096}}\n"
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
        "schema_version: 4\n"
        "mode: full\n"
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
    # Ten roles, two distinct model names: two clients.
    assert len(log.clients) == 2
    assert {client.model for client in log.clients} == {
        "strong-model", "weak-model"}


def test_build_handles_wires_extra_body_per_model(plugin, tmp_path):
    config = ExperimentConfig(
        benchmark=_FAKE, mode="full",
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model",
                "judge": "judge-model"},
        sequence=["D", "L", "C"],
        phases={name: PHASES[name] for name in ("D", "L", "C")},
        model_options={"strong-model": {"reasoning_effort": "max"},
                       "judge-model": {"top_p": 0.9}},
        token_budgets={"default_max_output_tokens": 65536},
    )
    log = ClientLog()
    handles = cli._build_handles(
        config, tmp_path / "experiment.yaml", log.make_client,
        infra_retries=1, infra_backoff_seconds=0.0)
    assert set(handles) == set(ROLE_DEFAULTS)
    # Options follow the MODEL NAME: every role on the strong model
    # carries the strong model's extras, an explicitly overridden role
    # its own.
    for role in ROLE_DEFAULTS:
        expected = {"strong-model": {"reasoning_effort": "max"},
                   "judge-model": {"top_p": 0.9},
                   "weak-model": {}}[config.models[role]]
        assert handles[role].extra_body == expected, role
    # Unlisted models get a fresh private dict, not a shared default.
    assert (handles["executor_weak"].extra_body
            is not handles["answerer"].extra_body)


def test_build_handles_wires_output_budget_per_role(plugin, tmp_path):
    # Per-role overrides fall back to default_max_output_tokens; the
    # handle's max_tokens is the only place the budget becomes live.
    config = ExperimentConfig(
        benchmark=_FAKE, mode="full",
        models={"executor_strong": "strong-model",
                "executor_weak": "weak-model"},
        sequence=["D", "L", "C"],
        phases={name: PHASES[name] for name in ("D", "L", "C")},
        token_budgets={
            "default_max_output_tokens": 65536,
            "roles": {"judge": {"max_output_tokens": 1024},
                      "segmenter": {"max_output_tokens": 2048}},
        },
    )
    handles = cli._build_handles(
        config, tmp_path / "experiment.yaml", ClientLog().make_client,
        infra_retries=0, infra_backoff_seconds=0.0)
    for role in ROLE_DEFAULTS:
        expected = {"judge": 1024, "segmenter": 2048}.get(role, 65536)
        assert handles[role].max_tokens == expected, role


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
                "allow_infra_shortfall", False))


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
        "p00:freeze", "p01:D", "p02:L",
        "p00:freeze", "p01:D", "p02:L", "p03:B", "p04:L", "p05:C"]


def test_the_frozen_sequence_survives_the_resume_check(
        plugin, config_path, tmp_path, drivers):
    # The manifest's config payload round-trips through from_dict on the
    # next start (B4 compares field by field): a run started under v4
    # resumes under v4 without a spurious mismatch.
    run_dir = tmp_path / "run"
    cli.run_experiment(config_path=config_path, run_dir=run_dir,
                       make_client=ClientLog().make_client, until_phase=0)
    cli.run_experiment(config_path=config_path, run_dir=run_dir,
                       make_client=ClientLog().make_client)
    manifest = json.loads((run_dir / "manifest.json").read_text(
        encoding="utf-8"))
    assert manifest["config"]["sequence"] == SEQUENCE
    # The loaded config equals a fresh load of the same file.
    assert load_config(str(config_path)).sequence == SEQUENCE
