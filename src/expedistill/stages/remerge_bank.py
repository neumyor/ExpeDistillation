"""Re-run the recorded merge step of a finished run's last learn phase.

Used when the MERGE prompt or the reader profile changes after a run
finished (2026-11-03): repeating the learning pipeline would redo hours of
authoring and review that the change does not touch. This tool takes the
run's last learn phase bank and its curation journal, re-merges exactly the
groups the journal recorded (same sources, same mode) with the current
prompt and the given reader profile, and writes the result as the accepted
bank of a fresh TEST-ONLY run.

Every skill that is not a merge product rides along byte-identical: same
id, content, summary, status and version. A merge product keeps its id and
version too; only ``content`` and ``summary`` are replaced, and
``REMERGED.md`` records the old and new sizes. The new run's test phase
therefore sees the same bank as the source run except for the re-merged
cards -- which is what isolates the merge change.

The source run must be finished and idle (no lock). The target directory
must be fresh: the test phase reads the bank from
``<run>/skill_bank.jsonl`` when no learn phase exists (the same fallback
``import-bank`` relies on), and the first ``run`` invocation freezes the
manifest afterwards.

Merges are applied in journal order. A later round may merge an earlier
round's product; the earlier product is replaced first, so the later merge
reads the new text exactly as a real curation round would have.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping

from expedistill.blocks.author import has_skill_sections
from expedistill.blocks.curate import merge_skills
from expedistill.composites.phase_bank import (
    BANK_NAME,
    load_phase_bank,
    phase_bank_dir,
)
from expedistill.core.config import ExperimentConfig, LearnPhaseSpec, phase_label
from expedistill.core.models import SKILL_MARKERS, Skill
from expedistill.infra.manifest import MANIFEST_NAME
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import LOCK_NAME
from expedistill.infra.store import Store, read_json, write_jsonl
from expedistill.inject import bank_reference

__all__ = ["REMERGED_NAME", "remerge_bank"]

REMERGED_NAME = "REMERGED.md"
_MERGE_LOG_NAME = "curation_log.json"


def _source_config(source_run: Path) -> ExperimentConfig:
    manifest = source_run / MANIFEST_NAME
    if not manifest.is_file():
        raise ConfigError(
            f"{source_run} has no {MANIFEST_NAME}; only a run frozen by "
            "stage 0 can be re-merged")
    return ExperimentConfig.from_dict(read_json(manifest)["config"])


def _last_learn_label(config: ExperimentConfig) -> str:
    learn = [(index, name) for index, name in enumerate(config.sequence)
             if isinstance(config.phases[name], LearnPhaseSpec)]
    if not learn:
        raise ConfigError(
            "the source run has no learn phase; there is no merge step to "
            "re-run")
    index, name = learn[-1]
    return phase_label(index, name)


def _kept_merges(source_run: Path, label: str) -> List[Dict[str, Any]]:
    log_path = phase_bank_dir(source_run, label) / _MERGE_LOG_NAME
    if not log_path.is_file():
        raise ConfigError(
            f"the source run's last learn phase {label!r} has no "
            f"{_MERGE_LOG_NAME}: it recorded no curation step")
    records = read_json(log_path).get("merge_records") or []
    kept = [record for record in records
            if record.get("outcome") == "kept" and record.get("skill_id")]
    if not kept:
        raise ConfigError(
            f"the source run's last learn phase {label!r} recorded no kept "
            "merge; there is nothing to re-run")
    return kept


def _load_sources(store: Store, ids: List[str],
                  replaced: Mapping[str, Skill]) -> List[Skill]:
    """The recorded merge inputs; a product of an earlier round resolves to
    its already-re-merged version."""
    return [replaced[sid] if sid in replaced
            else store.load("skills", sid, Skill) for sid in ids]


def remerge_bank(
    *,
    source_run: Path,
    run_dir: Path,
    weak_profile: str,
    curator_handle: Any,
    usage: Any = None,
) -> Dict[str, Any]:
    """Re-merge the source run's recorded merges into a test-only bank.

    Returns a JSON-safe summary (the source phase label, merge counts, the
    old/new member sizes and the new bank reference). Raises ConfigError on
    a missing journal/bank, a source skill that is not in the store, or a
    target directory that already holds a run.
    """
    source_run, run_dir = Path(source_run), Path(run_dir)
    if (source_run / LOCK_NAME).exists():
        raise ConfigError(
            f"{source_run} holds {LOCK_NAME}: it is running, or a driver "
            "died without releasing it; a finished run can be re-merged")
    if run_dir.exists() and any(run_dir.iterdir()):
        raise ConfigError(
            f"{run_dir} is not empty; re-merge into a fresh run directory "
            "(the first `run` invocation freezes its manifest)")

    source = _source_config(source_run)
    label = _last_learn_label(source)
    rows = load_phase_bank(source_run, label)
    by_id = {row["skill_id"]: row for row in rows}
    records = _kept_merges(source_run, label)
    store = Store(source_run)

    replaced: Dict[str, Skill] = {}
    changes = []
    for record in records:
        product = record["skill_id"]
        if product not in by_id:
            raise ConfigError(
                f"merge product {product!r} of {label!r} is not in the phase "
                "bank; the run and its journal disagree -- a human must "
                "look at this run")
        sources = _load_sources(store, list(record.get("sources") or []),
                                replaced)
        old = by_id[product]
        content, summary = merge_skills(
            sources, curator_handle, mode=record["mode"],
            weak_profile=weak_profile, usage=usage, run_dir=run_dir)
        if any(has_skill_sections(skill.content) for skill in sources) \
                and not has_skill_sections(content):
            raise ConfigError(
                "the re-merged skill lost the three bracket sections "
                f"({' / '.join(SKILL_MARKERS)}): {product}")
        row = dict(old)
        row["content"] = content
        row["summary"] = summary
        by_id[product] = row
        replaced[product] = Skill.from_dict(row)
        changes.append({
            "skill_id": product,
            "mode": record["mode"],
            "sources": len(sources),
            "old_chars": len(old.get("content") or ""),
            "new_chars": len(content),
            "old_summary_chars": len(old.get("summary") or ""),
            "new_summary_chars": len(summary),
        })

    new_rows = [by_id[row["skill_id"]] for row in rows]
    run_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(run_dir / BANK_NAME, new_rows)
    target_store = Store(run_dir)
    for row in new_rows:
        target_store.save(Skill.from_dict(row), "skills")

    reference = bank_reference(new_rows)
    total_old = sum(change["old_chars"] for change in changes)
    total_new = sum(change["new_chars"] for change in changes)
    _write_remerged(run_dir, source_run, label, weak_profile, changes,
                    reference)
    return {
        "source_run": str(source_run),
        "source_phase": label,
        "merges": len(changes),
        "bank_size": len(new_rows),
        "bank_reference": reference,
        "merged_chars_old": total_old,
        "merged_chars_new": total_new,
        "changed": changes,
    }


def _write_remerged(run_dir: Path, source_run: Path, label: str,
                    weak_profile: str, changes: List[Dict[str, Any]],
                    reference: str) -> None:
    lines = [
        "# REMERGED — the source run's merge step re-run under the current "
        "MERGE prompt",
        "",
        f"- Source run: `{source_run}`",
        f"- Source phase: `{label}` (its `curation_log.json` names the "
        "groups and modes)",
        f"- New bank reference: `{reference}`",
        f"- Reader profile supplied to every merge: "
        f"{len(weak_profile)} characters",
        "",
        "Every non-merge skill in the bank is byte-identical to the source "
        "run's phase bank (same id, content, summary, status and version). "
        "Only the merge products below were regenerated; their ids and "
        "versions are unchanged.",
        "",
        "| skill | mode | sources | old chars | new chars |",
        "|---|---|---|---|---|",
    ]
    for change in changes:
        lines.append(
            f"| `{change['skill_id']}` | {change['mode']} | "
            f"{change['sources']} | {change['old_chars']} | "
            f"{change['new_chars']} |")
    lines.append("")
    (run_dir / REMERGED_NAME).write_text("\n".join(lines), encoding="utf-8")
