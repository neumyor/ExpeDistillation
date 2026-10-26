"""P02's cluster-by-trigger landing path (new author method only).

The existing P02 path is task-scoped: each add row segments its task,
writes one skill per selected segment and receipts it immediately. This
module keeps the SAME decision input (the baseline-gap add rows) and
the SAME evidence collectors, but replaces only the writing landing:

1. prepare every add-authorized task (segment + collect) and drop
   subtasks already covered by a terminal cluster receipt or by an
   existing single-subtask skill;
2. classify every remaining segment into one of five trigger families;
3. cluster each family by the problem the segments handle;
4. write one skill per cluster (anchor = the smallest subtask id).

The existing ``distill_diagnose`` path is untouched: ``learn_add`` calls
this module only when the configured author method is
``cluster_by_trigger``.

Artifacts live beside the phase's normal ledgers and are deliberately
JSONL/JSON (Markdown is only a human view):

``trigger_families.jsonl``
    one row per subtask: family + the model's one-line reason;
``clusters.jsonl``
    one row per cluster: family, deterministic cluster_id, theme,
    member subtask ids, reason;
``cluster_skills.jsonl``
    one row per written skill: cluster_id -> skill_id / anchor / sources;
``cluster_outcomes.jsonl``
    one row per cluster: added / skipped_existing / failed / infra_failed;
``cluster_summary.json``
    counts for the phase summary and human inspection.

Resume is per subtask / family / cluster. A stored classification or
cluster is read back, never re-asked; a cluster whose anchor skill
already exists is skipped with a receipt.
"""

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from expedistill.benchmarks.registry import get_plugin
from expedistill.core.ids import make_skill_id
from expedistill.core.models import Skill
from expedistill.infra.parallel import run_parallel
from expedistill.infra.reliability import (
    CodeBugError,
    InfrastructureError,
    ModelOutputError,
    UnitFailureRecorder,
)
from expedistill.infra.store import (
    Store,
    append_jsonl,
    read_jsonl,
    write_json,
    write_jsonl,
)
from expedistill.methods import resolve_params
from expedistill.methods.author import ClusterAuthoringParams
from expedistill.methods.author import cluster_by_trigger as author
from expedistill.methods.evidence import add_path, segmentation
from expedistill.stages import learn_ledger as ledger

__all__ = [
    "CLUSTERS_NAME",
    "CLUSTER_OUTCOMES_NAME",
    "CLUSTER_SKILLS_NAME",
    "CLUSTER_SUMMARY_NAME",
    "TRIGGER_FAMILIES_NAME",
    "execute_add_rows",
]

TRIGGER_FAMILIES_NAME = "trigger_families.jsonl"
CLUSTERS_NAME = "clusters.jsonl"
CLUSTER_SKILLS_NAME = "cluster_skills.jsonl"
CLUSTER_OUTCOMES_NAME = "cluster_outcomes.jsonl"
CLUSTER_SUMMARY_NAME = "cluster_summary.json"

_TRIGGER_SCHEMA = "cluster-trigger-v1"
_CLUSTER_SCHEMA = "cluster-v1"
_CLUSTER_SKILL_SCHEMA = "cluster-skill-v1"
_CLUSTER_OUTCOME_SCHEMA = "cluster-outcome-v1"


def _has_outcome(phase: Any, row: Any, phase_dir: Path) -> bool:
    return bool(ledger.rows_of(phase_dir / ledger.OUTCOMES_NAME,
                               phase.label, row.target_id))


def _prepare_task(ctx: Any, phase: Any, row: Any, phase_dir: Path,
                  params: Any, view_rules: Any) -> List[dict]:
    """Segment one add task and return its selected source experiences.

    This mirrors ``learn_add._land_add``'s preparation half on purpose:
    the existing path stays byte-for-byte untouched, while this new path
    uses the same K2 and D6 collectors. A no-material task gets the same
    task-level receipt the old path writes; the "already has a skill"
    filter runs later because only the cluster receipts know that all
    members of a multi-subtask cluster are done.
    """
    store: Store = ctx.store
    segmentation.segment_add_material(
        store=store,
        task_id=row.target_id,
        params=params,
        trajectory_select=ctx.config.rules["trajectory_select"],
        view_rules=view_rules,
        handle=ctx.handle("segmenter"),
        usage=ctx.usage,
        run_dir=ctx.run_dir)
    try:
        package = add_path.collect_add_evidence(
            task_row=row, store=store,
            rule_name=ctx.config.rules["segment_select"],
            write_lessons=params.write_lessons,
            view_rules=view_rules)
    except add_path.NoAddMaterial:
        if not _has_outcome(phase, row, phase_dir):
            ledger.append_outcome(phase, row, None, "skipped_no_material",
                                  None, phase_dir)
        return []
    return list(package.source_experiences)


def _trigger_rows(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    return {row["subtask_id"]: row for row in read_jsonl(path)
            if isinstance(row, dict) and row.get("subtask_id")}


def _clusters_by_family(path: Path) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    if not path.exists():
        return out
    for row in read_jsonl(path):
        if isinstance(row, dict) and row.get("family"):
            out.setdefault(row["family"], []).append(row)
    return out


def _outcome_rows(path: Path) -> List[dict]:
    if not path.exists():
        return []
    return [row for row in read_jsonl(path)
            if isinstance(row, dict) and row.get("cluster_id")]


def _completed_subtasks(clusters: Sequence[Mapping[str, Any]],
                        outcomes: Sequence[Mapping[str, Any]]) -> Set[str]:
    """Subtasks whose cluster already has a terminal receipt.

    ``added`` / ``skipped_existing`` are terminal; ``failed`` and
    ``infra_failed`` stay retryable. Skill existence alone is NOT a
    completion signal: a crash between ``store.save`` and the receipt
    must land in the retry path, where ``_write_cluster`` sees the skill
    and writes the missing ``skipped_existing`` receipt.
    """
    terminal = {
        row["cluster_id"] for row in outcomes
        if row.get("outcome") in ("added", "skipped_existing")}
    done: Set[str] = set()
    for cluster in clusters:
        if cluster.get("cluster_id") in terminal:
            done.update(cluster.get("subtask_ids") or ())
    return done


def _classify_pending(ctx: Any, pending: Sequence[Mapping[str, Any]],
                      params: ClusterAuthoringParams,
                      handle: Any) -> List[dict]:
    """Classify unclassified experiences in parallel; failure -> fallback."""
    def one(experience: Mapping[str, Any]) -> dict:
        subtask = experience["subtask"]
        last_error: Optional[BaseException] = None
        for _ in range(max(1, params.classify_retry_limit)):
            try:
                result = author.classify_trigger(
                    experience, handle=handle,
                    max_chars=params.max_chars_per_card,
                    usage=ctx.usage, run_dir=ctx.run_dir)
                return {
                    "subtask_id": subtask.subtask_id,
                    "family": result["family"],
                    "reason": result["reason"],
                }
            except ModelOutputError as exc:
                last_error = exc
        # A classification failure is not a reason to lose the segment:
        # at_any_step is the documented catch-all family.
        return {
            "subtask_id": subtask.subtask_id,
            "family": "at_any_step",
            "reason": "fallback: classification failed: "
                      f"{type(last_error).__name__}",
            "fallback": True,
        }

    try:
        return run_parallel(
            one, list(pending),
            max(1, min(int(ctx.run_settings.workers), len(pending))),
            thread=True)
    except Exception as exc:  # the per-item wrapper should prevent this
        raise InfrastructureError(
            "cluster trigger classification pool failed: "
            f"{type(exc).__name__}: {exc}") from exc


def _write_cluster(ctx: Any, phase: Any, cluster: Mapping[str, Any],
                   experiences_by_id: Mapping[str, Mapping[str, Any]],
                   params: ClusterAuthoringParams, handle: Any,
                   weak_profile: str) -> Tuple[dict, Any, Optional[Any]]:
    """Write one cluster's skill; return (outcome row, skill, error)."""
    store: Store = ctx.store
    ids = list(cluster["subtask_ids"])
    if not ids:
        return ({
            "schema": _CLUSTER_OUTCOME_SCHEMA,
            "phase_label": phase.label,
            "cluster_id": cluster["cluster_id"],
            "family": cluster["family"],
            "skill_id": None,
            "outcome": "failed",
            "error": "empty_cluster",
        }, None, None)
    anchor = sorted(ids)[0]
    skill_id = make_skill_id(anchor)
    if store.exists("skills", skill_id):
        existing = store.load("skills", skill_id, Skill)
        return ({
            "schema": _CLUSTER_OUTCOME_SCHEMA,
            "phase_label": phase.label,
            "cluster_id": cluster["cluster_id"],
            "family": cluster["family"],
            "skill_id": skill_id,
            "outcome": "skipped_existing",
        }, existing, None)
    last_error: Optional[BaseException] = None
    for _ in range(max(1, params.skill_retry_limit)):
        try:
            skill = author.write_cluster_skill(
                cluster, experiences_by_id,
                weak_profile=weak_profile, handle=handle, params=params,
                usage=ctx.usage, run_dir=ctx.run_dir)
            store.save(skill, "skills")
            return ({
                "schema": _CLUSTER_OUTCOME_SCHEMA,
                "phase_label": phase.label,
                "cluster_id": cluster["cluster_id"],
                "family": cluster["family"],
                "skill_id": skill.skill_id,
                "outcome": "added",
            }, skill, None)
        except ModelOutputError as exc:
            last_error = exc
        except InfrastructureError as exc:
            return ({
                "schema": _CLUSTER_OUTCOME_SCHEMA,
                "phase_label": phase.label,
                "cluster_id": cluster["cluster_id"],
                "family": cluster["family"],
                "skill_id": skill_id,
                "outcome": "infra_failed",
            }, None, exc)
    return ({
        "schema": _CLUSTER_OUTCOME_SCHEMA,
        "phase_label": phase.label,
        "cluster_id": cluster["cluster_id"],
        "family": cluster["family"],
        "skill_id": skill_id,
        "outcome": "failed",
        "error": type(last_error).__name__ if last_error else "unknown",
    }, None, None)


def _write_clusters(ctx: Any, phase: Any, clusters: Sequence[Mapping[str, Any]],
                    experiences_by_id: Mapping[str, Mapping[str, Any]],
                    params: ClusterAuthoringParams, handle: Any,
                    weak_profile: str) -> Tuple[List[dict], List[dict], Set[str]]:
    """Write all clusters in parallel, then append receipts in input order."""
    def one(cluster: Mapping[str, Any]):
        return _write_cluster(
            ctx, phase, cluster, experiences_by_id, params, handle,
            weak_profile)

    try:
        results = run_parallel(
            one, list(clusters),
            max(1, min(int(ctx.run_settings.workers), len(clusters))),
            thread=True)
    except Exception as exc:
        raise InfrastructureError(
            "cluster skill pool failed: "
            f"{type(exc).__name__}: {exc}") from exc

    outcomes: List[dict] = []
    skills: List[dict] = []
    failed_tasks: Set[str] = set()
    for cluster, (outcome, skill, error) in zip(clusters, results):
        outcomes.append(outcome)
        if skill is not None:
            skills.append({
                "schema": _CLUSTER_SKILL_SCHEMA,
                "phase_label": phase.label,
                "cluster_id": cluster["cluster_id"],
                "family": cluster["family"],
                "skill_id": skill.skill_id,
                "subtask_id": skill.subtask_id,
                "source_subtasks": list(skill.source_subtasks),
                "version": skill.version,
            })
        if error is not None:
            failed_tasks.update(
                str(experience["subtask"].task_id)
                for experience in (
                    experiences_by_id[subtask_id]
                    for subtask_id in cluster["subtask_ids"]))
    return outcomes, skills, failed_tasks


def _persist(path: Path, rows: Sequence[dict]) -> None:
    write_jsonl(path, rows)


def _write_cluster_views(phase_dir: Path,
                         clusters: Sequence[Mapping[str, Any]]) -> None:
    """Render the human-readable Markdown view; JSON stays canonical."""
    by_family: Dict[str, List[Mapping[str, Any]]] = {}
    for cluster in clusters:
        by_family.setdefault(cluster["family"], []).append(cluster)
    directory = phase_dir / "clusters"
    directory.mkdir(parents=True, exist_ok=True)
    for family, rows in sorted(by_family.items()):
        lines = [f"# {family}", ""]
        for cluster in sorted(rows, key=lambda item: item["cluster_id"]):
            lines.extend([
                f"## {cluster.get('theme') or '(untitled)'}",
                "",
                f"- cluster_id: `{cluster['cluster_id']}`",
                "- subtasks: " + ", ".join(
                    f"`{subtask_id}`"
                    for subtask_id in cluster.get("subtask_ids") or ()),
                f"- reason: {cluster.get('reason') or ''}",
                "",
            ])
        (directory / f"{family}.md").write_text(
            "\n".join(lines), encoding="utf-8")


def execute_add_rows(ctx: Any, phase: Any, rows: Sequence[Any],
                     phase_dir: Path, *,
                     author_params: Optional[ClusterAuthoringParams] = None
                     ) -> Set[str]:
    """Run the cluster-by-trigger P02 landing; return infra-failed task ids."""
    phase_dir = Path(phase_dir)
    params = author_params or resolve_params("author", phase.spec.author)
    if not isinstance(params, ClusterAuthoringParams):
        raise TypeError(
            "cluster_by_trigger received the wrong params class: "
            f"{type(params).__name__}")
    evidence_params = resolve_params("evidence", phase.spec.evidence)
    view_rules = get_plugin(ctx.config.benchmark).view_rules
    recorder = UnitFailureRecorder(ctx.run_dir)

    def prepare(row: Any):
        try:
            experiences = _prepare_task(
                ctx, phase, row, phase_dir, evidence_params, view_rules)
            return row.target_id, experiences, None
        except InfrastructureError as exc:
            recorder.record(row.target_id, phase.label, exc, exc)
            return row.target_id, [], exc
        except ModelOutputError as exc:
            recorder.record(row.target_id, phase.label, exc, exc)
            return row.target_id, [], None

    prepared = run_parallel(
        prepare, list(rows),
        max(1, min(int(ctx.run_settings.workers), len(rows))),
        thread=True)
    failed_tasks: Set[str] = {
        target_id for target_id, _experiences, failure in prepared
        if failure is not None}
    experiences = [experience for _target, items, _failure in prepared
                   for experience in items]

    clusters_path = phase_dir / CLUSTERS_NAME
    existing_clusters = _clusters_by_family(clusters_path)
    existing_cluster_rows = [cluster for rows_ in existing_clusters.values()
                             for cluster in rows_]
    outcomes_path = phase_dir / CLUSTER_OUTCOMES_NAME
    existing_outcomes = _outcome_rows(outcomes_path)
    completed = _completed_subtasks(
        existing_cluster_rows, existing_outcomes)
    cluster_members = {
        subtask_id for cluster in existing_cluster_rows
        for subtask_id in cluster.get("subtask_ids") or ()}
    experiences = [
        experience for experience in experiences
        if experience["subtask"].subtask_id not in completed
        and (experience["subtask"].subtask_id in cluster_members
             or not ctx.store.exists(
                 "skills",
                 make_skill_id(experience["subtask"].subtask_id)))]

    eligible_ids = {experience["subtask"].subtask_id
                    for experience in experiences}
    row_by_target = {row.target_id: row for row in rows}
    for target_id, items, failure in prepared:
        if failure is not None or not items:
            continue
        if any(experience["subtask"].subtask_id in eligible_ids
               for experience in items):
            continue
        row = row_by_target.get(target_id)
        if row is not None and not _has_outcome(phase, row, phase_dir):
            ledger.append_outcome(phase, row, None, "skipped_existing",
                                  None, phase_dir)

    if not experiences:
        terminal_counts: Dict[str, int] = {}
        for outcome in existing_outcomes:
            name = outcome.get("outcome")
            if isinstance(name, str):
                terminal_counts[name] = terminal_counts.get(name, 0) + 1
        existing_skill_rows = (
            read_jsonl(phase_dir / CLUSTER_SKILLS_NAME)
            if (phase_dir / CLUSTER_SKILLS_NAME).exists() else [])
        write_json(phase_dir / CLUSTER_SUMMARY_NAME, {
            "author_method": "cluster_by_trigger",
            "subtasks": 0, "families": {}, "clusters": len(
                existing_cluster_rows),
            "skills": len(existing_skill_rows),
            "outcomes": terminal_counts,
            "failed_tasks": sorted(failed_tasks),
        }, indent=2)
        return failed_tasks

    # 1. Trigger families: resume from the existing rows; classify only
    # the new subtask ids.
    trigger_path = phase_dir / TRIGGER_FAMILIES_NAME
    known = _trigger_rows(trigger_path)
    pending = [experience for experience in experiences
               if experience["subtask"].subtask_id not in known]
    if pending:
        classified = _classify_pending(
            ctx, pending, params, ctx.handle("author"))
        for row in classified:
            if row["subtask_id"] in known:
                continue
            row.update({
                "schema": _TRIGGER_SCHEMA,
                "phase_label": phase.label,
            })
            append_jsonl(trigger_path, row)
            known[row["subtask_id"]] = row

    by_family: Dict[str, List[dict]] = {}
    for experience in experiences:
        subtask_id = experience["subtask"].subtask_id
        family = known[subtask_id]["family"]
        if family not in author.TRIGGER_FAMILIES:
            raise CodeBugError(
                f"stored trigger family {family!r} for {subtask_id!r} is "
                "not one of the five frozen families")
        by_family.setdefault(family, []).append(experience)

    # 2. Clusters: reuse a complete existing partition for a family;
    # otherwise ask the model for that family. The five families are
    # independent, so their (possibly large) model calls run in parallel.
    # Families with no eligible subtask keep their existing rows.
    workers = max(1, int(ctx.run_settings.workers))
    family_workers = max(1, workers // max(1, len(by_family)))

    def cluster_one(item: Tuple[str, List[dict]]):
        family, family_experiences = item
        expected = {experience["subtask"].subtask_id
                    for experience in family_experiences}
        previous = existing_clusters.get(family, [])
        covered = {subtask_id for cluster in previous
                   for subtask_id in cluster.get("subtask_ids", [])}
        if previous and covered == expected:
            return family, previous, None
        try:
            return family, author.cluster_family(
                family, family_experiences,
                handle=ctx.handle("author"), params=params,
                usage=ctx.usage, run_dir=ctx.run_dir,
                workers=family_workers), None
        except InfrastructureError as exc:
            return family, [], exc

    families = sorted(by_family.items())
    try:
        family_results = run_parallel(
            cluster_one, families,
            max(1, min(workers, len(families))),
            thread=True)
    except Exception as exc:
        raise InfrastructureError(
            "cluster family pool failed: "
            f"{type(exc).__name__}: {exc}") from exc
    computed: Dict[str, List[dict]] = {}
    for family, clusters, failure in family_results:
        if failure is not None:
            failed_tasks.update(
                str(experience["subtask"].task_id)
                for experience in by_family[family])
            continue
        computed[family] = clusters
    preserved = [cluster for family, rows_ in existing_clusters.items()
                 if family not in computed for cluster in rows_]
    all_clusters = preserved + [
        cluster for family in sorted(computed) for cluster in computed[family]]
    all_clusters.sort(key=lambda cluster: cluster["cluster_id"])
    _persist(clusters_path, all_clusters)
    _write_cluster_views(phase_dir, all_clusters)

    experiences_by_id = {
        experience["subtask"].subtask_id: experience
        for experience in experiences}
    terminal_ids = {
        outcome["cluster_id"] for outcome in existing_outcomes
        if outcome.get("outcome") in ("added", "skipped_existing")}
    pending_clusters = [
        cluster for cluster in all_clusters
        if cluster["cluster_id"] not in terminal_ids
        and set(cluster.get("subtask_ids") or ()) <= eligible_ids]
    outcomes, skills, cluster_failed = _write_clusters(
        ctx, phase, pending_clusters, experiences_by_id, params,
        ctx.handle("author"), ctx.config.effective_weak_profile())
    failed_tasks |= cluster_failed
    for outcome in outcomes:
        append_jsonl(outcomes_path, outcome)
    for row in skills:
        append_jsonl(phase_dir / CLUSTER_SKILLS_NAME, row)

    all_outcomes = existing_outcomes + outcomes
    counts: Dict[str, int] = {}
    for outcome in all_outcomes:
        name = outcome.get("outcome")
        if isinstance(name, str):
            counts[name] = counts.get(name, 0) + 1
    existing_skill_rows = (
        read_jsonl(phase_dir / CLUSTER_SKILLS_NAME)
        if (phase_dir / CLUSTER_SKILLS_NAME).exists() else [])
    write_json(phase_dir / CLUSTER_SUMMARY_NAME, {
        "author_method": "cluster_by_trigger",
        "subtasks": len(experiences),
        "families": {family: len(items)
                     for family, items in sorted(by_family.items())},
        "clusters": len(all_clusters),
        "skills": len(existing_skill_rows),
        "outcomes": counts,
        "failed_tasks": sorted(failed_tasks),
    }, indent=2)
    return failed_tasks
