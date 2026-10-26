"""P02's cluster-by-trigger authoring driver (new path).

``distill_diagnose`` writes one skill per segment inside one task. This
driver writes one skill per **problem cluster** over the add-authorized
segments of ALL tasks:

1. classify each segment into one of five fixed trigger-timing families;
2. cluster one family's segments by the problem they handle;
3. write one three-section skill per cluster.

The model calls live here. Disk layout, resume and parallel scheduling
belong to ``stages/learn_add_cluster`` so this module stays a pure-ish
driver (it only calls the model and constructs objects).

Skill identity follows the existing stage-3 rule: the cluster's
deterministically smallest subtask id is the anchor, so
``skill_id == make_skill_id(subtask_id)`` still passes
``ids.is_stage3_candidate`` and the live/exam paths see the skill. The
full membership travels in ``Skill.source_subtasks``.
"""

import hashlib
from typing import Any, Dict, List, Mapping, Sequence

from expedistill.blocks.author import (
    SKILL_SUMMARY_FIELD,
    render_skill_content,
    validate_skill_sections,
)
from expedistill.core.ids import make_skill_id
from expedistill.core.models import Skill
from expedistill.infra.parallel import run_parallel
from expedistill.infra.reliability import InfrastructureError, ModelOutputError
from expedistill.llm.call import call_model
from expedistill.methods.author import ClusterAuthoringParams
from expedistill.prompts.profile import profile_section

__all__ = [
    "TRIGGER_FAMILIES",
    "TRIGGER_PHRASES",
    "build_card",
    "classify_trigger",
    "cluster_family",
    "cluster_id_for",
    "write_cluster_skill",
]

#: The five families, in the prompt's exact vocabulary. The values are
#: the canonical family keys; nothing outside this table is accepted.
TRIGGER_FAMILIES: Mapping[str, str] = {
    "at_task_start":
        "the reader has just received the task and is reading its "
        "description, inputs and initial files",
    "after_first_environment_check":
        "the reader has run its first ls / which / import / network probe "
        "and knows what is installed and reachable",
    "when_command_fails":
        "a command errored, hung, or the same action keeps repeating "
        "without progress",
    "before_submitting":
        "the work looks done and the reader is about to run the final "
        "verifier and submit",
    "at_any_step":
        "a basic rule that applies at every step and fits none of the "
        "four moments above",
}

#: The opening sentence each family's skill must use.
TRIGGER_PHRASES: Mapping[str, str] = {
    "at_task_start": "At task start,",
    "after_first_environment_check":
        "After the first environment check,",
    "when_command_fails": "When a command fails, hangs, or repeats,",
    "before_submitting": "Before submitting,",
    "at_any_step": "At any step,",
}

_ELIDED = "\n[... steps elided to fit the cluster card budget ...]"


def _experience_subtask(experience: Mapping[str, Any]) -> Any:
    subtask = experience.get("subtask")
    if subtask is None:
        raise ModelOutputError(
            "a source experience carries no subtask; the add path must "
            "hand over the stored Subtask objects")
    return subtask


def build_card(experience: Mapping[str, Any], max_chars: int) -> str:
    """One subtask's compact card for a clustering/writing prompt.

    The card carries the identity and context the model needs to judge
    similarity; the steps view is clipped to ``max_chars`` (head and tail
    kept, the middle marked) so a family call stays inside the request
    envelope.
    """
    subtask = _experience_subtask(experience)
    steps = str(experience.get("steps_view") or "")
    if len(steps) > max_chars:
        head = max(1, int(max_chars * 0.6))
        tail = max(0, max_chars - head - len(_ELIDED))
        steps = steps[:head] + _ELIDED + (steps[-tail:] if tail else "")
    lines = [
        f"SUBTASK_ID: {subtask.subtask_id}",
        f"TASK: {subtask.task_id}",
        f"SOURCE: {subtask.source}",
        f"SUBTASK_SUCCESS: {subtask.success}",
        f"SEGMENT_INDEX: {subtask.segment_index}",
        f"SEGMENT_POSITION: {experience.get('segment_position') or ''}",
        f"MODE: {experience.get('mode') or ''}",
        f"LABEL: {subtask.label}",
        f"DESCRIPTION: {subtask.description}",
    ]
    context = (getattr(subtask, "context_summary", "") or "").strip()
    if context:
        lines.append(f"CONTEXT_BEFORE_SEGMENT: {context}")
    lines.append("STEPS:")
    lines.append(steps)
    return "\n".join(lines)


def _validate_family(family: Any) -> str:
    if not isinstance(family, str) or family not in TRIGGER_FAMILIES:
        raise ModelOutputError(
            f"CLUSTER_TRIGGER_FAMILY returned an unknown family {family!r}; "
            f"expected one of {sorted(TRIGGER_FAMILIES)}")
    return family


def classify_trigger(experience: Mapping[str, Any], *, handle: Any,
                     max_chars: int, usage: Any = None,
                     run_dir: Any = None) -> Dict[str, str]:
    """One segment's trigger family (one model call, no persistence)."""
    subtask = _experience_subtask(experience)
    parsed = call_model(
        handle, "CLUSTER_TRIGGER_FAMILY",
        {
            "label": subtask.label,
            "description": subtask.description,
            "source": subtask.source,
            "success": str(subtask.success),
            "segment_position": str(
                experience.get("segment_position") or ""),
            "steps_view": build_card(experience, max_chars).split(
                "STEPS:\n", 1)[-1],
        },
        output_json=True, usage=usage, run_dir=run_dir)
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            "CLUSTER_TRIGGER_FAMILY: expected a JSON object, got "
            f"{type(parsed).__name__}")
    family = _validate_family(parsed.get("family"))
    reason = parsed.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ModelOutputError(
            "CLUSTER_TRIGGER_FAMILY: reason must be a non-empty string")
    return {"family": family, "reason": reason.strip()}


def cluster_id_for(family: str, subtask_ids: Sequence[str]) -> str:
    """Deterministic cluster id: the family plus the sorted member ids.

    Model output never chooses an id, so the same membership always names
    the same cluster on resume.
    """
    payload = family + "\0" + "\0".join(sorted(subtask_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _validate_clusters(parsed: Any, expected: Sequence[str],
                       min_size: int) -> List[Dict[str, Any]]:
    if not isinstance(parsed, dict) or not isinstance(
            parsed.get("clusters"), list):
        raise ModelOutputError(
            "CLUSTER_SUBTASKS: expected a JSON object with a clusters list")
    expected_set = set(expected)
    seen = set()
    out: List[Dict[str, Any]] = []
    for index, cluster in enumerate(parsed["clusters"]):
        if not isinstance(cluster, dict):
            raise ModelOutputError(
                f"CLUSTER_SUBTASKS: clusters[{index}] is not an object")
        theme = cluster.get("theme")
        reason = cluster.get("reason")
        ids = cluster.get("subtask_ids")
        if not isinstance(theme, str) or not theme.strip():
            raise ModelOutputError(
                f"CLUSTER_SUBTASKS: clusters[{index}].theme is empty")
        if not isinstance(reason, str) or not reason.strip():
            raise ModelOutputError(
                f"CLUSTER_SUBTASKS: clusters[{index}].reason is empty")
        if (not isinstance(ids, list) or not ids
                or not all(isinstance(item, str) for item in ids)):
            raise ModelOutputError(
                f"CLUSTER_SUBTASKS: clusters[{index}].subtask_ids must be "
                "a non-empty list of strings")
        if len(ids) < min_size:
            raise ModelOutputError(
                f"CLUSTER_SUBTASKS: clusters[{index}] has {len(ids)} "
                f"member(s), below min_cluster_size={min_size}")
        for subtask_id in ids:
            if subtask_id not in expected_set:
                raise ModelOutputError(
                    f"CLUSTER_SUBTASKS invented or misplaced subtask id "
                    f"{subtask_id!r} (family {index})")
            if subtask_id in seen:
                raise ModelOutputError(
                    f"CLUSTER_SUBTASKS repeated subtask id {subtask_id!r}")
            seen.add(subtask_id)
        out.append({
            "theme": theme.strip(),
            "reason": reason.strip(),
            "subtask_ids": list(ids),
        })
    missing = expected_set - seen
    if missing:
        raise ModelOutputError(
            "CLUSTER_SUBTASKS left subtask(s) unassigned: "
            f"{sorted(missing)[:10]}")
    return out


def _fallback_clusters(family: str, experiences: Sequence[Mapping[str, Any]]
                       ) -> List[Dict[str, Any]]:
    """One cluster per subtask: the loud-but-progressing fallback."""
    out = []
    for experience in sorted(
            experiences,
            key=lambda item: _experience_subtask(item).subtask_id):
        subtask = _experience_subtask(experience)
        out.append({
            "theme": f"{family}: {subtask.label}".strip(),
            "reason": "fallback: the clustering call did not produce a "
                      "complete partition",
            "subtask_ids": [subtask.subtask_id],
            "fallback": True,
        })
    return out


def _cluster_chunk(family: str, experiences: Sequence[Mapping[str, Any]],
                   *, handle: Any, params: ClusterAuthoringParams,
                   usage: Any = None, run_dir: Any = None
                   ) -> List[Dict[str, Any]]:
    expected = [_experience_subtask(item).subtask_id for item in experiences]
    cards = "\n\n".join(
        build_card(item, params.max_chars_per_card) for item in experiences)
    variables = {
        "family": family,
        "family_definition": TRIGGER_FAMILIES[family],
        "cards": cards,
    }
    last_error: Any = None
    for _ in range(max(1, params.cluster_retry_limit)):
        try:
            parsed = call_model(
                handle, "CLUSTER_SUBTASKS", variables,
                output_json=True, usage=usage, run_dir=run_dir)
            clusters = _validate_clusters(
                parsed, expected, params.min_cluster_size)
            return clusters
        except ModelOutputError as exc:
            last_error = exc
    if last_error is not None:
        return _fallback_clusters(family, experiences)
    raise ModelOutputError("CLUSTER_SUBTASKS produced no result")


def cluster_family(family: str, experiences: Sequence[Mapping[str, Any]],
                   *, handle: Any, params: ClusterAuthoringParams,
                   usage: Any = None, run_dir: Any = None,
                   workers: int = 1) -> List[Dict[str, Any]]:
    """Cluster one family, in deterministic subtask-id chunks.

    Chunks are independent model calls, so they run in parallel (bounded
    by ``workers``) and their results are flattened in input order. v1
    has no cross-chunk merge: chunking keeps a large family inside the
    request envelope, and the stage records that the family was chunked.
    """
    ordered = sorted(
        experiences,
        key=lambda item: _experience_subtask(item).subtask_id)
    chunks = [ordered[index:index + max(1, params.max_cards_per_prompt)]
              for index in range(0, len(ordered),
                                 max(1, params.max_cards_per_prompt))]
    try:
        chunk_results = run_parallel(
            lambda chunk: _cluster_chunk(
                family, chunk, handle=handle, params=params,
                usage=usage, run_dir=run_dir),
            chunks, max(1, min(int(workers), len(chunks))), thread=True)
    except Exception as exc:
        raise InfrastructureError(
            f"cluster chunks for family {family!r} failed: "
            f"{type(exc).__name__}: {exc}") from exc
    out: List[Dict[str, Any]] = []
    for clusters in chunk_results:
        for cluster in clusters:
            ids = sorted(cluster["subtask_ids"])
            out.append({
                **cluster,
                "family": family,
                "subtask_ids": ids,
                "cluster_id": cluster_id_for(family, ids),
            })
    return sorted(out, key=lambda item: item["cluster_id"])


def write_cluster_skill(
        cluster: Mapping[str, Any],
        experiences_by_id: Mapping[str, Mapping[str, Any]], *,
        weak_profile: str, handle: Any,
        params: ClusterAuthoringParams,
        usage: Any = None, run_dir: Any = None) -> Skill:
    """Write ONE skill for one cluster (one model call, no persistence)."""
    family = cluster.get("family")
    if family not in TRIGGER_FAMILIES:
        raise ModelOutputError(
            f"cluster carries unknown family {family!r}")
    ids = list(cluster.get("subtask_ids") or ())
    if not ids:
        raise ModelOutputError("cluster carries no subtask_ids")
    members = []
    for subtask_id in ids:
        experience = experiences_by_id.get(subtask_id)
        if experience is None:
            raise ModelOutputError(
                f"cluster names unknown subtask {subtask_id!r}")
        members.append(experience)
    cards = "\n\n".join(
        build_card(item, params.max_chars_per_card) for item in members)
    parsed = call_model(
        handle, "CLUSTER_SKILL",
        {
            "family": family,
            "family_trigger": TRIGGER_PHRASES[family],
            "theme": cluster["theme"],
            "reason": cluster["reason"],
            "cards": cards,
            "profile_section": profile_section(
                "THE MODEL THAT WILL READ THE SKILL (weak model profile):",
                weak_profile),
        },
        output_json=True, usage=usage, run_dir=run_dir)
    if not isinstance(parsed, dict):
        raise ModelOutputError(
            "CLUSTER_SKILL: expected a JSON object, got "
            f"{type(parsed).__name__}")
    validate_skill_sections("distill", parsed)
    phrase = TRIGGER_PHRASES[family]
    if not str(parsed["when_to_use"]).lstrip().startswith(phrase):
        raise ModelOutputError(
            f"CLUSTER_SKILL: when_to_use must start with the family "
            f"phrase {phrase!r}, got {parsed['when_to_use']!r}")
    summary = parsed[SKILL_SUMMARY_FIELD]
    anchor = sorted(ids)[0]
    return Skill(
        skill_id=make_skill_id(anchor),
        content=render_skill_content(parsed),
        subtask_id=anchor,
        source_subtasks=sorted(ids),
        summary=summary.strip(),
        version=1,
        status="pending",
        rewrite_history=[],
    )
