"""A tiny finished run on disk, and a scripted judge, for the skill-use
analysis (``stages/judge_skills.py``) and the analysis CLI.

The run is built from the same writers the phase drivers use (answer sheets,
coverage, unit specs, bank artifacts, the object store), never by running a
pipeline, so a test controls every fact it asserts on: which cards were
delivered, which a trial opened and at which step, how each trial ended.

The scripted judge answers by reading the prompt it receives (the card
list), so a test decides exactly which cards apply.
"""

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from expedistill.composites import answer_sheets, phase_report
from expedistill.composites.phase_bank import (
    BANK_NAME,
    SUMMARY_NAME as BANK_SUMMARY_NAME,
    phase_bank_dir,
)
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core import ids
from expedistill.core.answer_sheet import UnitSpec
from expedistill.core.config import ExperimentConfig
from expedistill.core.contracts import ExecutionUnitRow
from expedistill.core.models import Skill, Step, Trajectory
from expedistill.infra.store import write_json, write_jsonl
from expedistill.inject import bank_reference
from expedistill.inject.skill_text import render_card
from expedistill.llm.fake import json_script
from expedistill.methods.delivery.folder_delivery import (
    skill_filename,
    skill_folder_manifest,
)
from tests.helpers.configs import reference_method
from tests.helpers.skills import make_skill

__all__ = [
    "BENCHMARK",
    "CARD_NAMES",
    "adherence_reply",
    "applicability_reply",
    "card_filename",
    "card_id",
    "make_bank",
    "read_action",
    "skill_use_judge_script",
    "snapshot",
    "write_learn_phase",
    "write_source_run",
    "write_test_phase",
]

BENCHMARK = "skillbench"
CARD_NAMES = ("alpha", "beta", "gamma")
TEST_LABEL = "p03:test"


def _type_id(name: str) -> str:
    """Card ``name``'s subtask type: its position in CARD_NAMES."""
    return ids.make_type_id(CARD_NAMES.index(name) + 1)


def card_id(name: str) -> str:
    return ids.make_skill_id(_type_id(name))


def card_filename(name: str) -> str:
    return skill_filename(card_id(name))


def read_action(name: str) -> str:
    """The action that opens card ``name`` in a folder trial."""
    return f"cat {card_filename(name)}"


def make_bank(names: Sequence[str] = CARD_NAMES, *, version: int = 1,
              status: str = "active") -> List[Skill]:
    return [make_skill(
        _type_id(name),
        f"When {name} matters.",
        steps=[f"do step {number} of {name}" for number in (1, 2)],
        not_applicable_when=f"{name} does not matter.",
        summary=f"Card {name} teaches {name}.",
        status=status, version=version) for name in names]


def write_learn_phase(run_dir: Path, label: str, bank: Sequence[Skill]) -> None:
    """A learn phase's bank artifact, the way the phase drivers leave it."""
    directory = phase_bank_dir(run_dir, label)
    rows = [skill.to_dict() for skill in bank]
    write_jsonl(directory / BANK_NAME, rows)
    write_json(directory / BANK_SUMMARY_NAME, {
        "members": [row["skill_id"] for row in rows], "size": len(rows),
        "bank_reference": bank_reference(rows)})
    write_json(directory / "phase.json", {"label": label, "role": "learn"})


def _steps(actions: Sequence[str]) -> List[Step]:
    steps = []
    for index, action in enumerate(actions):
        observation = f"result {index}"
        for name in CARD_NAMES:
            if action == read_action(name):
                observation = render_card(make_bank([name])[0])
        steps.append(Step(action=action, observation=observation))
    return steps


def write_test_phase(
        run_dir: Path, bank: Sequence[Skill],
        trials: Sequence[Dict[str, Any]], *, label: str = TEST_LABEL,
        manifest_on_sheets: bool = False) -> Dict[str, Any]:
    """A finished test phase over ``bank``'s active cards.

    Each trial is ``{"task", "attempt", "actions", "correct"}`` (and an
    optional ``"arm"``, default ``with_bank``); ``actions`` is the trial's
    action list, an opening of a card being ``read_action(name)``. A
    ``control`` trial is written as a bare run. Like the real test phase the
    sheets do not carry the delivery manifest (``manifest_on_sheets=False``);
    the unit rows in ``units.jsonl`` do. Returns the manifest.
    """
    active = [skill for skill in bank if skill.status == "active"]
    manifest = skill_folder_manifest(active, "summary_then_when_to_use")
    reference = bank_reference(active)
    directory = phase_bank_dir(run_dir, label)

    executions, specs, unit_rows = [], {}, {}
    for trial in trials:
        arm = trial.get("arm", "with_bank")
        injected = reference if arm == "with_bank" else None
        task_id = ids.make_task_id(BENCHMARK, trial["task"])
        unit_id = make_unit_id(
            phase_label=label, benchmark=BENCHMARK, task_ref=task_id,
            role="weak", skills_injected=injected)
        trajectory = Trajectory(
            trajectory_id=ids.make_trajectory_id(
                label, task_id, "weak", trial["attempt"]),
            task_id=task_id, benchmark=BENCHMARK, model_role="weak",
            model_name="weak-7b", attempt_index=trial["attempt"],
            skills_injected=injected, steps=_steps(trial["actions"]),
            correctness=trial["correct"])
        executions.append(answer_sheets.execution_from_trajectory(
            trajectory, unit_id=unit_id, phase_label=label, arm=arm,
            folder=manifest if manifest_on_sheets and arm == "with_bank"
            else None))
        specs[unit_id] = UnitSpec(
            unit_id=unit_id, phase_label=label, task_ref=task_id,
            role="weak", arm=arm, expected_attempts=1,
            injected_skills=injected)
        if arm == "with_bank":
            unit_rows[unit_id] = ExecutionUnitRow(
                unit_id=unit_id, task_ref=task_id, skills_injected=injected,
                skills_block=manifest["catalog_text"],
                aux={"skill_folder": manifest})

    sheets = answer_sheets.assemble_answer_sheets(
        executions, [], phase_label=label)
    answer_sheets.grade_answer_sheets(sheets, "scorer")
    answer_sheets.write_answer_sheets(
        sheets, directory / phase_report.ANSWER_SHEETS_DIR,
        render_markdown=False)
    answer_sheets.write_coverage(
        [sheet.coverage for sheet in sheets],
        directory / phase_report.COVERAGE_NAME)
    phase_report.write_unit_specs(list(specs.values()), directory)
    write_jsonl(directory / "units.jsonl",
                [asdict(row) for row in unit_rows.values()])
    write_json(directory / "phase.json", {"label": label, "role": "test"})
    return manifest


# ---------------------------------------------------------------------------
# A finished source run (the analysis CLI's input)
# ---------------------------------------------------------------------------

def _config() -> ExperimentConfig:
    return ExperimentConfig.from_dict({
        "benchmark": BENCHMARK,
        "split": {"train": ["t1"], "test": ["t2"], "reserved": ["t3"]},
        "models": {"executor_strong": "big-model",
                   "executor_weak": "weak-7b"},
        "n_attempts_strong": 1, "n_attempts_weak": 1,
        "schema_version": 9,
        "token_budgets": {"default_max_output_tokens": 512},
        **reference_method()})


def write_source_run(run_dir: Path) -> None:
    """A finished source run's manifest (the reference method frozen over
    this benchmark) and two learn banks: ``p02:learn`` holds alpha, beta
    and gamma v1; ``p04:learn`` holds alpha v2 and beta v1."""
    run_dir.mkdir(parents=True)
    write_json(run_dir / "manifest.json", {
        "benchmark": BENCHMARK, "config": _config().to_dict()})
    write_learn_phase(run_dir, "p02:learn", make_bank())
    later = make_bank(("alpha", "beta"))
    later[0].version = 2
    write_learn_phase(run_dir, "p04:learn", later)


def snapshot(directory: Path) -> Dict[str, Optional[bytes]]:
    """Every path under ``directory`` with its bytes (None for a directory):
    equal snapshots mean the tree was not touched."""
    return {str(path.relative_to(directory)):
            (path.read_bytes() if path.is_file() else None)
            for path in sorted(directory.rglob("*"))}


# ---------------------------------------------------------------------------
# Scripted models
# ---------------------------------------------------------------------------

def applicability_reply(listing_ids: Sequence[str],
                        applicable: Sequence[str] = ()) -> str:
    """An applicability reply naming ``applicable`` cards (by id) applicable."""
    return json_script({"skills": [
        {"skill_id": skill_id, "applicable": skill_id in applicable,
         "first_step": 0 if skill_id in applicable else None,
         "reason": "because"} for skill_id in listing_ids]})


def adherence_reply(statuses: Sequence[str]) -> str:
    return json_script({"steps": [
        {"number": number, "status": status, "evidence": f"quote {number}"}
        for number, status in enumerate(statuses, 1)]})


def skill_use_judge_script(
        applicable: Callable[[str, List[str]], Sequence[str]],
        statuses: Sequence[str] = ("followed", "skipped"),
        crash_after: Optional[int] = None,
        calls: Optional[List[str]] = None) -> Callable[[str, str], str]:
    """A judge for ``judge_phase``: ``applicable(user, listed_ids)`` names the
    applicable card ids of the trial in the prompt; adherence replies cycle
    through ``statuses``. After ``crash_after`` calls it raises RuntimeError
    (a crash no stage retries); ``calls`` records ``"applicability"`` /
    ``"adherence"`` per call."""
    state = {"n": 0}

    def script(system: str, user: str) -> str:
        if crash_after is not None and state["n"] >= crash_after:
            raise RuntimeError("scripted crash")
        state["n"] += 1
        if "CARD LIST, exactly as the agent saw it" in user:
            if calls is not None:
                calls.append("applicability")
            listed = re.findall(r"^- skill_id: (.*)$", user, re.M)
            return applicability_reply(listed, applicable(user, listed))
        if calls is not None:
            calls.append("adherence")
        count = int(re.search(r"has (\d+) numbered", user).group(1))
        return adherence_reply(
            [statuses[index % len(statuses)] for index in range(count)])

    return script
