"""D11 lexical two-step skill retrieval (layer 4, pure functions).

Stage 6 protocol v2 replaces the whole-bank skill injection with a
deterministic, model-free retrieval over the frozen bank (README block
table, D11). Two steps, both pure functions of their inputs -- no model,
no IO, dependencies on core and the standard library only:

1. ``derive_categories`` merges the G9 fine-grained groups into coarse
   categories (lexical agglomerative clustering, average-link cosine).
2. ``select_skills`` routes a task to at most ``max_categories``
   categories, then ranks the routed pool against the task text and keeps
   the top-``top_k`` skills (TF-IDF cosine).

Both steps score in the same lexical space: tokens are lowercase
``[a-z0-9_]+`` runs and IDF is computed from the retrieval documents of
the given bank. A skill's retrieval document is its full text with one
weighted section kind: the body of the applicability section -- the one
opened by ``[When to use]`` -- counts x3, and every other text counts x1, the marker
headers included (they are structure, not payload). A skill without
markers is its full text at x1 (the whole current TB bank).

The scorer functions are private on purpose: the frozen contract is the
two public functions' signatures and return shapes (decision document
retrieval_delivery_tb_split_20261013, contract 4).
"""

import math
import re
from collections import Counter
from collections.abc import Mapping as _Mapping
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from expedistill.core.models import SKILL_MARKERS

__all__ = ["CATEGORY_MERGE_THRESHOLD", "derive_categories", "select_skills"]

#: Tokens of the lexical space: lowercase runs of [a-z0-9_]+.
_TOKEN_RE = re.compile(r"[a-z0-9_]+")

#: One alternation over every skill-content section marker; a match opens
#: a weighted section. Sorted so the pattern text is stable across runs.
_MARKER_RE = re.compile("|".join(
    re.escape(marker) for marker in sorted(SKILL_MARKERS)))

#: The applicability section named by the D11 spec (contract 4): the
#: section opened by ``[When to use]`` -- the "when does this apply"
#: text, which is what task matching needs. Only this section's BODY is
#: weighted; the other sections, the marker headers and any text outside
#: sections stay x1 (a v2 skill has no text outside its sections, so
#: weighting every section would be a uniform scale change and the
#: mechanism would be provably inert).
_APPLICABILITY_MARKERS = (SKILL_MARKERS[0],)

#: Tokens of an applicability section body count this many times.
_MARKED_SECTION_WEIGHT = 3

#: Agglomerative merge threshold for ``derive_categories`` (decision 10):
#: pinned by the two-round review at 0.25; a per-bank constant, never a
#: config knob. Measured with this implementation on the production
#: 42-skill / 29-group TB bank: 0.15 -> 1 category, 0.20 -> 2, 0.25 -> 4,
#: 0.30 -> 6 -- the giant-class effect dominates the whole range.
CATEGORY_MERGE_THRESHOLD = 0.25


# ---------------------------------------------------------------------------
# Inputs: skill records
# ---------------------------------------------------------------------------

def _field(record: Any, name: str, default: Any = None) -> Any:
    """Read one field from a Skill object or a mapping (contract: both
    shapes are accepted everywhere in this module)."""
    if isinstance(record, _Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _skill_id(record: Any) -> str:
    value = _field(record, "skill_id")
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"skill record needs a non-empty string skill_id, got {value!r}")
    return value


def _content(record: Any) -> str:
    value = _field(record, "content")
    if not isinstance(value, str):
        name = _field(record, "skill_id")
        raise ValueError(f"skill {name!r} needs string content, got {value!r}")
    return value


def _group_of(record: Any) -> Optional[str]:
    """The G9 group name, or None for an ungrouped skill (group None or
    empty -- G9 itself never emits an empty name, lenient input here)."""
    value = _field(record, "group")
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"skill {_field(record, 'skill_id')!r} has a non-string group, "
            f"got {value!r}")
    return value


def _retrieval_counts(content: str) -> Counter:
    """Token counts of a skill's retrieval document.

    Sections are delimited by the skill-content markers; a section's body
    runs from its opening marker to the next marker or the end of the
    text. The body of the applicability section (``[When to use]``) counts ``_MARKED_SECTION_WEIGHT`` times; every other
    text -- the marker headers themselves, the other sections' bodies,
    and any text before the first marker -- counts once. A skill without
    markers is its full text counting once, which is the same rule
    spelled out ("无标记 skill 全文同权 x1").
    """
    counts: Counter = Counter()
    matches = list(_MARKER_RE.finditer(content))
    if not matches:
        for token in _TOKEN_RE.findall(content.lower()):
            counts[token] += 1
        return counts
    for token in _TOKEN_RE.findall(content[:matches[0].start()].lower()):
        counts[token] += 1
    for index, match in enumerate(matches):
        # The header is structural text of the full document, never payload.
        for token in _TOKEN_RE.findall(match.group(0).lower()):
            counts[token] += 1
        body_start = match.end()
        body_end = (matches[index + 1].start() if index + 1 < len(matches)
                    else len(content))
        weight = (_MARKED_SECTION_WEIGHT
                  if match.group(0) in _APPLICABILITY_MARKERS else 1)
        for token in _TOKEN_RE.findall(content[body_start:body_end].lower()):
            counts[token] += weight
    return counts


# ---------------------------------------------------------------------------
# The lexical space: TF-IDF vectors and cosine
# ---------------------------------------------------------------------------

def _idf(document_counts: Sequence[Counter]) -> Dict[str, float]:
    """Smoothed inverse document frequency over the bank's retrieval
    documents: ``log((1 + N) / (1 + df)) + 1``. The +1 smoothing keeps a
    token present in every document from vanishing (a shared technical
    vocabulary is the normal case here) and keeps every weight positive."""
    total = len(document_counts)
    document_frequency: Counter = Counter()
    for counts in document_counts:
        for token in counts:
            document_frequency[token] += 1
    return {
        token: math.log((1.0 + total) / (1.0 + df)) + 1.0
        for token, df in document_frequency.items()
    }


def _vectorize(counts: Counter, idf: Mapping[str, float]) -> Dict[str, float]:
    """TF-IDF vector with keys inserted in sorted order, so every later
    iteration over a vector is a canonical order. Tokens outside the IDF
    table (out of bank vocabulary) are dropped."""
    return {
        token: counts[token] * idf[token]
        for token in sorted(counts)
        if token in idf
    }


def _cosine(vector_a: Mapping[str, float],
            vector_b: Mapping[str, float]) -> float:
    if not vector_a or not vector_b:
        return 0.0
    dot = 0.0
    for token, weight in vector_a.items():
        other = vector_b.get(token)
        if other is not None:
            dot += weight * other
    if dot == 0.0:
        return 0.0
    norm_a = math.sqrt(sum(w * w for w in vector_a.values()))
    norm_b = math.sqrt(sum(w * w for w in vector_b.values()))
    return dot / (norm_a * norm_b)


def _average_link_clusters(
        profiles: Dict[str, Dict[str, float]],
        threshold: float) -> Dict[str, List[str]]:
    """Average-linkage agglomeration over the group profiles.

    Nodes are the G9 groups; the similarity of two clusters is the mean of
    the pairwise profile cosines of their member groups. While the best
    pair sits at or above ``threshold``, merge it; the best pair is
    ``(similarity desc, cluster-name pair lexicographic asc)`` so equal
    similarities resolve deterministically ("并列按组名字典序"). A
    cluster is named by its lexicographically smallest member group.
    """
    names = sorted(profiles)
    pair_similarity: Dict[Tuple[str, str], float] = {}
    for i, name_a in enumerate(names):
        for name_b in names[i + 1:]:
            pair_similarity[(name_a, name_b)] = _cosine(
                profiles[name_a], profiles[name_b])
    clusters: Dict[str, List[str]] = {name: [name] for name in names}
    while len(clusters) > 1:
        best_key: Optional[Tuple[float, str, str]] = None
        best_pair: Optional[Tuple[str, str]] = None
        reps = sorted(clusters)
        for i, rep_a in enumerate(reps):
            members_a = clusters[rep_a]
            for rep_b in reps[i + 1:]:
                members_b = clusters[rep_b]
                total = 0.0
                for group_a in members_a:
                    for group_b in members_b:
                        total += pair_similarity[
                            (group_a, group_b) if group_a < group_b
                            else (group_b, group_a)]
                similarity = total / (len(members_a) * len(members_b))
                if similarity < threshold:
                    continue
                order_key = (-similarity, rep_a, rep_b)
                if best_key is None or order_key < best_key:
                    best_key = order_key
                    best_pair = (rep_a, rep_b)
        if best_pair is None:
            break
        rep_a, rep_b = best_pair
        clusters[rep_a] = sorted(clusters[rep_a] + clusters[rep_b])
        del clusters[rep_b]
    return clusters


# ---------------------------------------------------------------------------
# D11 step 1: derive_categories
# ---------------------------------------------------------------------------

def derive_categories(skills: Sequence[Any]) -> Dict[str, str]:
    """Merge the G9 fine-grained groups into coarse categories (D11 step 1).

    Deterministic lexical agglomeration; a pure function of the bank, no
    model and no IO. Returns ``{group_name: category_name}``.

    Inputs and outputs:

    * ``skills`` -- the frozen bank: ``Skill`` objects or mappings with
      ``skill_id`` / ``content`` keys (``group`` optional). Input order
      never matters: everything is sorted internally, so the same bank
      yields the same mapping bit for bit.
    * Keys are the G9 group names plus, for every ungrouped skill, its
      skill id (an ungrouped skill is its own "group" for mapping
      purposes). The value is the category name: the lexicographically
      smallest group name of the skill's cluster. Ungrouped skills map to
      themselves and never merge with anything (contract 15; the 42-skill
      TB bank happens to contain none, the rule is pinned anyway).

    Semantics (decision document, contract 4):

    * Group profile = the concatenation of its members' retrieval
      documents (module docstring: full text, applicability section bodies
      x3, everything else x1).
    * Similarity = TF-IDF cosine over those profiles, in the module's
      shared lexical space (tokens lowercase ``[a-z0-9_]+``; IDF from the
      retrieval documents of the whole bank).
    * Clustering = average-linkage agglomeration merging while the best
      pair's mean pairwise cosine is >= ``CATEGORY_MERGE_THRESHOLD``
      (0.25); ties resolve by the cluster-name pair in lexicographic
      order.

    Expectation management, disclosed on purpose (decisions 10 and 8):

    * Giant-class effect, measured on the production 42-skill / 29-group
      TB bank: the shared technical vocabulary collapses the 29 groups
      into 4 categories at the pinned threshold, one of which covers 26
      groups, so the two-step routing is NEAR-INERT in this run -- a
      recorded expectation, not a defect. The routing's discriminative
      value is expected in the v2 structured bank, not here.
    * The retrieval score distribution was measured BEFORE the protocol
      freeze (second-round review probe: 18 of 48 test+validation tasks
      had zero lexical overlap with the 42-skill bank, top-1 score 0;
      median top-1 0.07, max 0.44). The min_score calibration protocol
      was designed knowing that number, and the official implementation
      re-runs the probe on these functions.
    """
    member_ids: Dict[str, List[str]] = {}
    ungrouped: List[str] = []
    counts: Dict[str, Counter] = {}
    for record in skills:
        skill_id = _skill_id(record)
        counts[skill_id] = _retrieval_counts(_content(record))
        group = _group_of(record)
        if group is None:
            ungrouped.append(skill_id)
        else:
            member_ids.setdefault(group, []).append(skill_id)

    merged: Dict[str, str] = {}
    if member_ids:
        idf = _idf([counts[skill_id] for skill_id in sorted(counts)])
        profiles: Dict[str, Dict[str, float]] = {}
        for group in sorted(member_ids):
            group_counts: Counter = Counter()
            for skill_id in sorted(member_ids[group]):
                group_counts.update(counts[skill_id])
            profiles[group] = _vectorize(group_counts, idf)
        clusters = _average_link_clusters(profiles, CATEGORY_MERGE_THRESHOLD)
        for representative, group_names in clusters.items():
            for group in group_names:
                merged[group] = representative
    for skill_id in ungrouped:
        merged[skill_id] = skill_id
    return {key: merged[key] for key in sorted(merged)}


# ---------------------------------------------------------------------------
# D11 step 2: select_skills
# ---------------------------------------------------------------------------

def _validate_retrieval_params(top_k: Any, max_categories: Any,
                               min_score: Any) -> None:
    """Mirror of the config-side ``skill_delivery`` contract (contract 2):
    the block validates its own inputs so direct callers (tests, the
    decision-8 calibration probe) cannot pass nonsense silently."""
    if (not isinstance(top_k, int) or isinstance(top_k, bool)
            or top_k < 1):
        raise ValueError(f"top_k must be an int >= 1, got {top_k!r}")
    if (not isinstance(max_categories, int) or isinstance(max_categories, bool)
            or max_categories < 1):
        raise ValueError(f"max_categories must be an int >= 1, "
                         f"got {max_categories!r}")
    if (isinstance(min_score, bool)
            or not isinstance(min_score, (int, float))
            or not 0.0 <= min_score < 1.0):
        raise ValueError(f"min_score must be a number in [0, 1), "
                         f"got {min_score!r}")


def select_skills(
        task_text: str,
        skills: Sequence[Any],
        categories: Mapping[str, str],
        *,
        top_k: int,
        max_categories: int,
        min_score: float,
) -> Dict[str, Any]:
    """Select the skills to inject for one task (D11 step 2).

    Two deterministic lexical steps over the given bank; no model, no IO.
    ``task_text`` is the task instruction verbatim (for TB, the task name
    -- decision 2: this run's retrieval is literal task-name matching, the
    x3 section weighting never activates because the bank is all v1
    prose, and conclusions do not transfer to ALFWorld).

    1. Routing: every skill resolves to one category through
       ``categories`` (the ``derive_categories`` output), keyed by its
       group name, or by its skill id when it has no group. Keys missing
       from ``categories`` fall back to the identity mapping (each group
       its own category), so ``{}`` is a legal input. The task text is
       scored against each category profile (concatenation of the member
       retrieval documents) and the top ``max_categories`` categories by
       (score desc, name asc) are routed.
    2. Pool ranking: the skills of the routed categories are scored
       against the task text (same TF-IDF cosine; task tokens outside the
       bank vocabulary are dropped) and ranked by (score desc,
       skill_id asc).

    Returns a dict with exactly four keys:

    * ``selected`` -- the top ``top_k`` skill records of the ranking (the
      original input objects, best first; fewer when the pool is smaller),
      or ``[]`` when the threshold gate failed;
    * ``routed`` -- the routed category names, best first;
    * ``scores`` -- the full pool ranking, ``[{"skill_id", "score"}, ...]``
      by (score desc, skill_id asc), kept even when the gate fails (the
      caller writes it to the retrieval audit);
    * ``threshold_passed`` -- whether the top-1 score reached min_score.

    The ``min_score`` gate applies to the TOP-1 ONLY: ranks 2..k may sit
    below the threshold and are still selected. Deliberate design choice
    (contract 11): one confident hit carries its neighbours into the
    injection -- the top-k list is rendered as one block -- while a bank
    that cannot produce one confident hit injects nothing. When the top-1
    misses the threshold, ``selected`` is empty and ``threshold_passed``
    is False, which the caller turns into an empty injection ("",
    equivalent to the bare run). An empty bank behaves the same way (no
    top-1 exists to pass the gate).

    IDF scales with the bank: ``min_score`` is a PER-BANK parameter, to
    be calibrated per bank by the decision-8 protocol. The TB calibration
    value does not transfer to ALFWorld (decision 9).
    """
    _validate_retrieval_params(top_k, max_categories, min_score)
    if not isinstance(task_text, str):
        raise ValueError(f"task_text must be a string, got {task_text!r}")
    if not isinstance(categories, _Mapping):
        raise TypeError("categories must be a mapping of group name (or "
                        f"skill id) to category name, got {categories!r}")

    if not skills:
        return {"selected": [], "routed": [], "scores": [],
                "threshold_passed": False}

    entries: List[Tuple[str, Any]] = []
    counts: Dict[str, Counter] = {}
    category_of: Dict[str, str] = {}
    for record in skills:
        skill_id = _skill_id(record)
        counts[skill_id] = _retrieval_counts(_content(record))
        entries.append((skill_id, record))
    entries.sort(key=lambda entry: entry[0])
    for skill_id, record in entries:
        group = _group_of(record)
        key = group if group is not None else skill_id
        category_of[skill_id] = categories.get(key, key)

    idf = _idf([counts[skill_id] for skill_id, _ in entries])
    task_vector = _vectorize(
        Counter(_TOKEN_RE.findall(task_text.lower())), idf)

    category_members: Dict[str, List[str]] = {}
    for skill_id, _ in entries:
        category_members.setdefault(category_of[skill_id], []).append(skill_id)
    category_vectors: Dict[str, Dict[str, float]] = {}
    for name in sorted(category_members):
        category_counts: Counter = Counter()
        for skill_id in category_members[name]:
            category_counts.update(counts[skill_id])
        category_vectors[name] = _vectorize(category_counts, idf)

    routed = [
        name
        for _, name in sorted(
            ((_cosine(task_vector, category_vectors[name]), name)
             for name in sorted(category_members)),
            key=lambda item: (-item[0], item[1]))
    ][:max_categories]
    routed_set = set(routed)

    skill_vectors = {
        skill_id: _vectorize(counts[skill_id], idf) for skill_id, _ in entries}
    ranked = sorted(
        ((skill_id, record, _cosine(task_vector, skill_vectors[skill_id]))
         for skill_id, record in entries
         if category_of[skill_id] in routed_set),
        key=lambda item: (-item[2], item[0]))
    scores = [{"skill_id": skill_id, "score": score}
              for skill_id, _, score in ranked]
    threshold_passed = bool(ranked) and ranked[0][2] >= min_score
    selected = [record for _, record, _ in ranked[:top_k]] \
        if threshold_passed else []
    return {"selected": selected, "routed": routed, "scores": scores,
            "threshold_passed": threshold_passed}
