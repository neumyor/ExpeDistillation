"""A tiny finished run on disk, and scripted models, for the skill-use
analyses (``stages/judge_skills.py`` and ``stages/exam_replay.py``).

The run is built from the same writers the phase drivers use (answer sheets,
coverage, unit specs, bank artifacts, the object store), never by running a
pipeline, so a test controls every fact it asserts on: which cards were
delivered, which a trial opened and at which step, how each trial ended.

The scripted models answer by reading the prompt they receive (the card list,
the scene), so a test can make a model open exactly the cards it wants.
"""

import copy
import json
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
from expedistill.core.models import Exam, Skill, Step, Trajectory
from expedistill.core.trace import TraceRef
from expedistill.infra.store import Store, write_json, write_jsonl
from expedistill.inject import bank_reference
from expedistill.llm.fake import json_script
from expedistill.methods.delivery.folder_delivery import (
    skill_filename,
    skill_folder_manifest,
)
from tests.helpers.skills import make_skill

__all__ = [
    "BENCHMARK",
    "CARD_NAMES",
    "adherence_reply",
    "applicability_reply",
    "answerer_script",
    "card_filename",
    "card_id",
    "judge_script",
    "make_bank",
    "read_action",
    "skill_use_judge_script",
    "snapshot",
    "write_exam_source_run",
    "write_learn_phase",
    "write_test_phase",
]

BENCHMARK = "skillbench"
CARD_NAMES = ("alpha", "beta", "gamma")
TEST_LABEL = "p03:test"
SOURCE_LABEL = "p01:baseline"


def card_id(name: str) -> str:
    return f"traj/{BENCHMARK}:{name}/strong/00/seg00/skill"


def card_filename(name: str) -> str:
    return skill_filename(card_id(name))


def read_action(name: str) -> str:
    """The action that opens card ``name`` in a folder trial."""
    return f"cat {card_filename(name)}"


def card_content(name: str, n_steps: int = 2) -> str:
    steps = [f"{number}. do step {number} of {name}"
             for number in range(1, n_steps + 1)]
    return "\n".join([
        f"[When to use] When {name} matters.", "[Steps]", *steps,
        f"[Do not use when] {name} does not matter."])


def make_bank(names: Sequence[str] = CARD_NAMES, *, version: int = 1,
              status: str = "accepted") -> List[Skill]:
    return [make_skill(
        f"traj/{BENCHMARK}:{name}/strong/00/seg00",
        content=card_content(name), summary=f"Card {name} teaches {name}.",
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
                observation = card_content(name)
        steps.append(Step(action=action, observation=observation))
    return steps


def write_test_phase(
        run_dir: Path, bank: Sequence[Skill],
        trials: Sequence[Dict[str, Any]], *, label: str = TEST_LABEL,
        manifest_on_sheets: bool = False) -> Dict[str, Any]:
    """A finished test phase over ``bank``'s accepted cards.

    Each trial is ``{"task", "attempt", "actions", "correct"}`` (and an
    optional ``"arm"``, default ``with_bank``); ``actions`` is the trial's
    action list, an opening of a card being ``read_action(name)``. A
    ``control`` trial is written as a bare run. Like the real test phase the
    sheets do not carry the delivery manifest (``manifest_on_sheets=False``);
    the unit rows in ``units.jsonl`` do. Returns the manifest.
    """
    accepted = [skill for skill in bank if skill.status == "accepted"]
    manifest = skill_folder_manifest(accepted, "summary_then_when_to_use")
    reference = bank_reference(accepted)
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
                task_id, "weak", trial["attempt"]),
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
# An exam source run (the input of exam_replay)
# ---------------------------------------------------------------------------

_LEARN = {
    "kind": "learn",
    "review": {"name": "review", "decision": "task_level",
               "unread_in_won_group": "delete", "before_steps": 10,
               "after_steps": 10, "K": 4, "retry_limit": 2,
               "write_lessons": True},
    "author": {"name": "distill_diagnose"},
    "curate": {"name": "agentbrew", "merge_check": "none",
               "token_budget": 4096, "rounds": 3},
}


def _exam_phase(description_mode: str) -> Dict[str, Any]:
    return {"kind": "execute", "role": "exam",
            "delivery": {"name": "exam_folder",
                         "description_mode": description_mode},
            "execution": {"name": "exam", "bare_control": False,
                          "leak_audit": False, "max_skill_reads": 3}}


def _config() -> ExperimentConfig:
    return ExperimentConfig.from_dict({
        "benchmark": BENCHMARK, "mode": "split",
        "split": {"train": ["t1"], "test": ["t2"], "reserved": ["t3"]},
        "models": {"executor_strong": "big-model",
                   "executor_weak": "weak-7b"},
        "weak_profile": "a weak model",
        "n_attempts_strong": 1, "n_attempts_weak": 1,
        "schema_version": 7,
        "token_budgets": {"default_max_output_tokens": 512},
        "sequence": ["baseline", "learn", "exam_a", "learn", "exam_b",
                     "test"],
        "phases": copy.deepcopy({
            "baseline": {"kind": "execute", "role": "baseline",
                         "tasks": "train", "delivery": {"name": "none"},
                         "execution": {"name": "real",
                                       "arms": ["executor_strong",
                                                "executor_weak"]}},
            "learn": _LEARN,
            "exam_a": _exam_phase("summary"),
            "exam_b": _exam_phase("when_to_use"),
            "test": {"kind": "execute", "role": "test", "tasks": "test",
                     "delivery": {"name": "whole_bank"},
                     "execution": {"name": "real"},
                     "compare": {"against": "baseline"}}})})


def make_exam(name: str, version: int) -> Exam:
    return Exam(
        exam_id=ids.make_exam_id(card_id(name), version),
        skill_id=card_id(name), skill_version=version,
        scene_text=f"SCENE-{name}-v{version}: a situation about {name}.",
        expected_behavior=f"use {name}", reference_answer=f"do {name}",
        scene=TraceRef(phase_label=SOURCE_LABEL, sheet_id="sheet", step=0))


def write_exam_source_run(run_dir: Path) -> None:
    """A finished source run with two learn banks and four exams.

    ``p02:learn`` holds alpha v1, beta v1 and gamma v1 (the exam_a phase
    followed it, description mode ``summary``); ``p04:learn`` holds alpha v2
    and beta v1 (exam_b followed it, mode ``when_to_use``): gamma is gone and
    alpha was revised. Exams exist for alpha v1, alpha v2, beta v1, gamma v1.
    """
    run_dir.mkdir(parents=True)
    write_json(run_dir / "manifest.json", {
        "benchmark": BENCHMARK, "config": _config().to_dict()})
    store = Store(run_dir)
    for name, version in (("alpha", 1), ("alpha", 2), ("beta", 1),
                          ("gamma", 1)):
        store.save(make_exam(name, version), "exams")
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


def answerer_script(
        opens: Callable[[str, int], Sequence[str]],
        crash_after: Optional[int] = None) -> Callable[[str, str], str]:
    """An answering model for ``replay``: ``opens(name, version)`` lists the
    card names it opens for the scene of that skill version."""
    state = {"n": 0}

    def script(system: str, user: str) -> str:
        if crash_after is not None and state["n"] >= crash_after:
            raise RuntimeError("scripted crash")
        state["n"] += 1
        if "Choose which skills from the library catalog" in user:
            name, version = re.search(
                r"SCENE-(\w+)-v(\d+)", user).groups()
            return json_script({
                "skills": [card_id(opened)
                           for opened in opens(name, int(version))],
                "reason": "the scene matches"})
        return json_script({"plan": "do the thing"})

    return script


def judge_script(passes: Callable[[bool], bool] = lambda opened: opened
                 ) -> Callable[[str, str], str]:
    """The exam judge: ``passes(any_card_opened)`` decides the verdict."""
    def script(system: str, user: str) -> str:
        opened = re.search(
            r"SKILLS THE MODEL OPENED:\n(.*?)\n\nWHY IT OPENED THEM",
            user, re.S).group(1)
        verdict = "success" if passes("SKILL.md" in opened) else "failure"
        return json.dumps({"verdict": verdict, "reason": "r"})

    return script
