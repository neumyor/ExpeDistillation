"""An imported bank through the real entry points: the stage-0 freeze and a
test-only ``expedistill run`` over a fake benchmark, for every delivery.

``cli.run_experiment`` is the entry the ``run`` subcommand calls. Nothing is
faked below it except the benchmark plugin (records what it is handed) and
the model clients (never asked for text). The checks recompute what the
delivery should hand over from the imported skills and compare, so a change
to a delivery's rendering does not need a test edit.
"""

import importlib
import importlib.abc
import importlib.machinery
import json
import sys
import types
from pathlib import Path

import pytest
import yaml

# Importing the TB plugin registers TB_TASK_PROMPT_FOLDER, the folder prompt
# the fake plugin below declares (the freeze checks that it is registered).
import expedistill.benchmarks.tb.plugin  # noqa: F401
from expedistill import cli
from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.benchmarks.registry import _reset_registry, register_plugin
from expedistill.core.models import Skill
from expedistill.infra.manifest import MANIFEST_NAME
from expedistill.infra.store import Store, read_json, read_jsonl
from expedistill.inject import bank_reference, format_skills_block
from expedistill.llm.fake import FakeClient
from expedistill.methods.delivery.folder_delivery import (
    CATALOG_FILENAME,
    skill_filename,
)
from expedistill.stages.import_bank import BANK_ORIGIN_NAME, import_external_bank

_FAKE = "importbench"
_MODULE = f"expedistill.benchmarks.{_FAKE}"
TEST_TASKS = ("t2", "t3")

NOTES = [
    {"id": "stove", "text": "Check the stove before the pan.",
     "title": "Stove first"},
    {"id": "quotes", "text": "Prefer exact quotes from the passage."},
    {"id": "units", "text": "Keep the units of every number you report."},
]

SKILL_MD = ("---\nname: {name}\ndescription: {description}\n---\n\n"
            "# {name}\n\n{text}\n")


class RecordingPlugin(BenchmarkPlugin):
    """Records every unit it is asked to run: the task (with its folder
    meta) and the skills block."""

    def __init__(self):
        self.calls = []

    @property
    def name(self):
        return _FAKE

    def load_tasks(self, tasks_spec):
        names = ["t1", *TEST_TASKS] if tasks_spec is None else list(tasks_spec)
        return [Task(task_id=name, instruction=f"do {name}")
                for name in names]

    def run(self, task, handle, skills_block, workdir):
        self.calls.append({"task": task, "skills_block": skills_block})
        handle.client.complete(handle.name, "system", task.instruction)
        return RunOutcome(
            steps=[{"action": f"exec {task.task_id}", "observation": "ok"}],
            correctness=True)

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return "FAKE_TASK"

    @property
    def skill_folder_prompt_name(self):
        return "TB_TASK_PROMPT_FOLDER"


@pytest.fixture()
def plugin(monkeypatch):
    """The recording plugin, served through the real import machinery."""
    instance = RecordingPlugin()

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            register_plugin(instance)

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == _MODULE:
                return importlib.machinery.ModuleSpec(name, Loader())
            return None

    _reset_registry()
    sys.modules.pop(_MODULE, None)
    monkeypatch.setattr(sys, "meta_path", [*sys.meta_path, Finder()])
    yield instance
    sys.modules.pop(_MODULE, None)
    _reset_registry()


def _delivery(name):
    if name == "retrieval":
        # min_score 0 and top_k / max_categories at the bank size: the
        # retrieval reaches every accepted skill for every task.
        return ({"name": "retrieval", "top_k": len(NOTES),
                 "max_categories": len(NOTES), "min_score": 0.0},
                {"name": "real"})
    if name == "folder":
        return {"name": "folder"}, {"name": "real_folder"}
    return {"name": "whole_bank"}, {"name": "real"}


def _write_config(path: Path, delivery_name: str) -> Path:
    delivery, execution = _delivery(delivery_name)
    path.write_text(json.dumps({
        "benchmark": _FAKE,
        "mode": "split",
        "models": {"executor_strong": "strong-model",
                   "executor_weak": "weak-model"},
        "n_attempts_strong": 1,
        "n_attempts_weak": 1,
        "schema_version": 7,
        "sequence": ["test"],
        "phases": {"test": {"kind": "execute", "role": "test",
                            "tasks": "test", "delivery": delivery,
                            "execution": execution,
                            "compare": {"against": "fresh_bare"}}},
        "split": {"train": ["t1"], "test": list(TEST_TASKS)},
        "token_budgets": {"default_max_output_tokens": 4096},
    }), encoding="utf-8")
    return path


def _make_source(tmp_path: Path, source_format: str):
    """One tiny source in the given format, and the (content, summary)
    pairs the three notes must come out as."""
    if source_format == "notes_jsonl":
        source = tmp_path / "notes.jsonl"
        source.write_text(
            "\n".join(json.dumps(row) for row in NOTES) + "\n",
            encoding="utf-8")
    else:
        source = tmp_path / "skills"
        for row in NOTES:
            directory = source / row["id"]
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text(
                SKILL_MD.format(name=row["id"],
                                description=f"About {row['id']}.",
                                text=row["text"]), encoding="utf-8")
    return source


def _run(config_path: Path, run_dir: Path):
    return cli.run_experiment(
        config_path=config_path, run_dir=run_dir,
        make_client=lambda model_name: FakeClient(""))


def _front_matter_and_body(text: str):
    _, front, body = text.split("---\n", 2)
    return yaml.safe_load(front), body.strip()


@pytest.mark.parametrize("source_format", ["notes_jsonl", "skill_folder"])
@pytest.mark.parametrize("delivery_name",
                         ["folder", "whole_bank", "retrieval"])
def test_the_test_phase_delivers_exactly_the_imported_skills(
        plugin, tmp_path, source_format, delivery_name):
    run_dir = tmp_path / "run"
    result = import_external_bank(
        _make_source(tmp_path, source_format), source_format, run_dir,
        origin="baseline")
    config_path = _write_config(tmp_path / "exp.yaml", delivery_name)

    summary = _run(config_path, run_dir)

    # The importer wrote the bank; the first run froze the manifest.
    assert (run_dir / MANIFEST_NAME).exists()
    assert (run_dir / BANK_ORIGIN_NAME).exists()
    assert summary["complete"] is True

    # The bank the phase read: every imported skill, all accepted.
    bank = [Skill.from_dict(row) for row in Store(run_dir).bank()]
    assert [skill.skill_id for skill in bank] == result["skill_ids"]
    assert {skill.status for skill in bank} == {"accepted"}
    assert summary["bank_reference"] == bank_reference(bank)

    with_bank = [call for call in plugin.calls if call["skills_block"]]
    control = [call for call in plugin.calls if not call["skills_block"]]
    assert len(with_bank) == len(TEST_TASKS) == len(control)

    if delivery_name == "whole_bank":
        for call in with_bank:
            assert call["skills_block"] == format_skills_block(bank)
    elif delivery_name == "retrieval":
        audit = read_jsonl(run_dir / "stage6_test" / "retrieval_audit.jsonl")
        assert [row["task_id"] for row in audit] == list(TEST_TASKS)
        for row in audit:
            assert sorted(entry["skill_id"] for entry in row["scores"]) == \
                sorted(result["skill_ids"])
            assert sorted(item["skill_id"] for item in row["selected"]) == \
                sorted(result["skill_ids"])
        for call in with_bank:
            for skill in bank:
                assert " ".join(skill.content.split()) in \
                    call["skills_block"]
    else:
        manifest = with_bank[0]["task"].meta["skill_folder"]
        assert [entry["skill_id"] for entry in manifest["files"]] == \
            result["skill_ids"]
        folder_dir = Path(with_bank[0]["task"].meta["skill_folder_dir"])
        assert folder_dir == run_dir / "phases" / "p01_test" / "skill_folder"
        assert sorted(path.relative_to(folder_dir).as_posix()
                      for path in folder_dir.rglob("*")
                      if path.is_file()) == sorted(
            [CATALOG_FILENAME]
            + [skill_filename(skill.skill_id) for skill in bank])
        for skill in bank:
            meta, body = _front_matter_and_body(
                (folder_dir / skill_filename(skill.skill_id)).read_text(
                    encoding="utf-8"))
            # The agent sees the source's own summary as the description
            # and opens the source's own text.
            assert meta["description"] == skill.summary
            assert body == skill.content


def test_the_bank_reference_of_the_summary_is_that_of_the_imported_skills(
        plugin, tmp_path):
    run_dir = tmp_path / "run"
    import_external_bank(_make_source(tmp_path, "notes_jsonl"),
                         "notes_jsonl", run_dir, origin="expel")

    summary = _run(_write_config(tmp_path / "exp.yaml", "whole_bank"),
                   run_dir)

    rows = read_jsonl(run_dir / "skill_bank.jsonl")
    assert summary["bank_reference"] == bank_reference(rows)
    phase_summary = read_json(run_dir / "phases" / "p01_test"
                              / "phase_summary.json")
    assert phase_summary["bank_reference"] == summary["bank_reference"]
