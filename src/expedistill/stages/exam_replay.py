"""Re-answer a finished run's exams with another model (layer 7).

The exam phase asks a weak model to find and use a skill in a scene cut from
a real trajectory. This stage asks the same question of a different
answering model, over the SAME skill bank and the SAME stored exams, so the
models' trigger behaviour can be compared on identical material: does the
model open the skill the exam was written for (recall), how many other
cards does it open (misfire), and does its plan pass.

What is replayed. The bank is a learn phase's artifact (``bank_phase_label``,
default the last one). An exam is replayed when its (skill id, version) is a
member of that bank -- an exam of an older version of a skill the bank has
since revised describes text the bank no longer holds, so it is counted in
``skipped`` and not answered. The card list is built exactly as the exam
phase builds it: ``exam_folder.resolve`` over the whole artifact (pending
members included) with the description mode of the source config's exam
phase that ran against this bank. ``max_skill_reads`` is a parameter: pass
the value the source exam phase used when the answer should be comparable
with it.

Read-only on the source run: its manifest, bank artifact and exam objects
are read, and every new object (the exams and skills copied in, the
attempts) goes to a fresh ``Store(out_dir / "store")``. One row per exam goes
to ``<out_dir>/replay.jsonl`` as soon as the exam is done, so a resume
re-answers only the exams without a row. A reply that broke the output
contract is terminal for its exam (``status: model_output_error``, counted
in ``failed`` and left out of the rates), as in the exam phase; an
infrastructure error leaves the exam without a row and makes the call raise
InfrastructureError at the end, so a resume retries it.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from expedistill.composites.acceptance_skill_status import find_judged_attempt
from expedistill.composites.exam_once import exam_once
from expedistill.composites.phase_bank import load_phase_bank
from expedistill.composites.skill_use import skill_files_read
from expedistill.composites.trigger_metrics import replay_metrics
from expedistill.core.config import PHASE_ROLE_EXAM, ExperimentConfig
from expedistill.core.models import Exam, Skill
from expedistill.infra.manifest import MANIFEST_NAME
from expedistill.infra.parallel import run_units
from expedistill.infra.reliability import (
    ConfigError,
    InfrastructureError,
    ModelOutputError,
)
from expedistill.infra.store import (
    KIND_DIRS,
    Store,
    append_jsonl,
    read_json,
    read_jsonl,
    write_json,
)
from expedistill.llm.call import ModelHandle
from expedistill.methods import registry as method_registry
from expedistill.methods.delivery import exam_folder
from expedistill.stages.analysis_common import (
    bank_phase_labels,
    phase_number,
    prepare_out_dir,
)

__all__ = ["REPLAY_NAME", "SUMMARY_NAME", "replay"]

REPLAY_NAME = "replay.jsonl"
SUMMARY_NAME = "replay_metrics.json"

_STATUS_OK = "ok"
_STATUS_FORMAT = "model_output_error"


def replay(
    source_run_dir: Union[str, Path],
    out_dir: Union[str, Path],
    answerer_handle: ModelHandle,
    judge_handle: ModelHandle,
    *,
    bank_phase_label: Optional[str] = None,
    max_skill_reads: int = 3,
    workers: int = 1,
    usage: Any = None,
) -> Dict[str, Any]:
    """Re-answer the source run's exams with ``answerer_handle``.

    Returns the run's settings, the exam counts (``selected``, ``skipped``,
    ``answered``, ``failed``) and ``metrics``
    (``composites.trigger_metrics.replay_metrics`` over the answered rows);
    the same dict is written to ``<out_dir>/replay_metrics.json``.
    """
    source = Path(source_run_dir)
    config = _source_config(source)
    label = bank_phase_label or _last_bank_label(source)
    bank = [Skill.from_dict(row) for row in load_phase_bank(source, label)]
    description_mode = _exam_description_mode(config, label)
    selected, skipped = _select_exams(_stored_exams(source), bank)
    if not selected:
        raise ConfigError(
            f"no stored exam of {source} matches a member of the "
            f"{label!r} bank (skipped: {skipped})")

    out = prepare_out_dir(source, out_dir, {
        "source_run": str(source.resolve()), "bank_phase_label": label,
        "description_mode": description_mode,
        "max_skill_reads": max_skill_reads,
        "answerer_model": answerer_handle.name,
        "judge_model": judge_handle.name})
    store = Store(out / "store")
    for skill in bank:
        if not store.exists("skills", skill.skill_id):
            store.save(skill, "skills")
    for exam in selected:
        if not store.exists("exams", exam.exam_id):
            store.save(exam, "exams")

    rows = exam_folder.resolve(
        bank, [exam.exam_id for exam in selected],
        exam_folder.ExamFolderParams(description_mode=description_mode))
    folder = rows[0].aux["skill_folder"]

    replay_path = out / REPLAY_NAME
    done = {row["exam_id"] for row in read_jsonl(replay_path)}
    infra_failed: List[str] = []

    def answer(exam: Exam) -> None:
        try:
            attempt = find_judged_attempt(
                store, exam.exam_id, "acceptance", exam.skill_id,
                exam.skill_version, 0)
            if attempt is None:
                attempt = exam_once(
                    store=store, exam=exam,
                    answerer_handle=answerer_handle,
                    judge_handle=judge_handle, skill_folder=folder,
                    purpose="acceptance", skill_id=exam.skill_id,
                    skill_version=exam.skill_version,
                    max_skill_reads=max_skill_reads,
                    usage=usage, run_dir=out)
        except InfrastructureError:
            infra_failed.append(exam.exam_id)
            return
        except ModelOutputError as exc:
            append_jsonl(replay_path, {
                "exam_id": exam.exam_id, "skill_id": exam.skill_id,
                "status": _STATUS_FORMAT,
                "error": " ".join(str(exc).split())[:500]})
            return
        opened = skill_files_read(attempt, folder)
        append_jsonl(replay_path, {
            "exam_id": exam.exam_id,
            "skill_id": exam.skill_id,
            "status": _STATUS_OK,
            "opened": opened,
            "target_opened": exam.skill_id in opened,
            "other_opened": len(
                [skill_id for skill_id in opened
                 if skill_id != exam.skill_id]),
            "verdict": "success" if attempt.judge_verdict else "failure",
        })

    run_units(answer, [exam for exam in selected
                       if exam.exam_id not in done], workers)

    wanted = {exam.exam_id for exam in selected}
    stored = sorted((row for row in read_jsonl(replay_path)
                     if row["exam_id"] in wanted),
                    key=lambda row: row["exam_id"])
    answered = [row for row in stored if row["status"] == _STATUS_OK]
    summary = {
        "source_run": str(source),
        "bank_phase_label": label,
        "description_mode": description_mode,
        "max_skill_reads": max_skill_reads,
        "answerer_model": answerer_handle.name,
        "judge_model": judge_handle.name,
        "bank_skills": len(bank),
        "selected": len(selected),
        "skipped": skipped,
        "answered": len(answered),
        "failed": len(stored) - len(answered),
        "metrics": replay_metrics(answered),
    }
    write_json(out / SUMMARY_NAME, summary, indent=2)
    if infra_failed:
        raise InfrastructureError(
            f"{len(infra_failed)} exam(s) are pending on infrastructure "
            "errors; resume to answer them")
    return summary


def _source_config(source: Path) -> ExperimentConfig:
    path = source / MANIFEST_NAME
    if not path.is_file():
        raise ConfigError(
            f"{source} has no {MANIFEST_NAME}; only a run frozen by "
            "stage 0 can be replayed")
    try:
        return ExperimentConfig.from_dict(read_json(path)["config"])
    except (KeyError, ValueError) as exc:
        raise ConfigError(
            f"the config frozen in {path} does not load: {exc}") from exc


def _last_bank_label(source: Path) -> str:
    labels = bank_phase_labels(source)
    if not labels:
        raise ConfigError(f"no phase of {source} left a skill bank artifact")
    return labels[-1]


def _exam_description_mode(config: ExperimentConfig, bank_label: str) -> str:
    """The description mode of the exam phase that ran against the bank:
    the first exam phase after the bank's phase, else the last exam phase
    of the sequence (a bank no exam phase followed)."""
    bank_index = phase_number(bank_label) - 1
    exam_specs = [
        (index, config.phases[name])
        for index, name in enumerate(config.sequence)
        if getattr(config.phases[name], "role", None) == PHASE_ROLE_EXAM]
    if not exam_specs:
        raise ConfigError(
            "the source run has no exam phase, so it has no exam card "
            "list to rebuild")
    after = [spec for index, spec in exam_specs if index > bank_index]
    spec = after[0] if after else exam_specs[-1][1]
    return method_registry.resolve_params(
        "delivery", spec.delivery).description_mode


def _stored_exams(source: Path) -> List[Exam]:
    """Every exam object of the source run, read without opening a Store
    (a Store creates directories)."""
    directory = source / KIND_DIRS["exams"]
    if not directory.is_dir():
        raise ConfigError(f"{source} has no exam objects ({directory})")
    try:
        return [Exam.from_dict(read_json(path))
                for path in sorted(directory.glob("*.json"))]
    except ValueError as exc:
        raise ConfigError(f"an exam of {source} does not load: {exc}") from exc


def _select_exams(exams: List[Exam], bank: List[Skill]):
    """The exams of the bank's own skill versions, and why the rest are out."""
    version_of = {skill.skill_id: skill.version for skill in bank}
    selected: List[Exam] = []
    skipped = {"skill_not_in_bank": 0, "version_not_in_bank": 0}
    for exam in sorted(exams, key=lambda item: item.exam_id):
        if exam.skill_id not in version_of:
            skipped["skill_not_in_bank"] += 1
        elif version_of[exam.skill_id] != exam.skill_version:
            skipped["version_not_in_bank"] += 1
        else:
            selected.append(exam)
    return selected, skipped
