"""The M4.1 semantic-equivalence instrument (implementation plan section 8).

The byte-for-byte golden matrix cannot survive M4.3 (config schema v4,
phase-driven stages, ``phases/pNN_<name>/`` layout) -- this module is the
pre-registered "behaviour unchanged" instrument that can. It projects a
run's artifacts onto per-criterion semantic views (E1-E6), compares them
against the committed baseline bundle extracted from commit 69904f7 (the
last v1-object golden matrix, the pre-change truth M4.3 must reproduce),
and carries the E5 crash-resume variant machinery. The views and the
normalisation were fixed BEFORE any v4 artifact existed; the tamper
self-check (test_semantic_instrument.py) proves every assertion actually
catches the difference it claims to catch.

What each criterion reads (the extraction inventory -- the baseline bundle
contains exactly these fields, nothing else; paths are the canonical v3
names, see v4_layout for the v4 side):

E1  ``skill_bank.jsonl`` -- per row: skill_id, content, version (stored as
    final_version), status, discard_reason, group, merged_from,
    rewrite_history (each entry whole).

E2  ``skill_bank.jsonl`` (terminals: skill_id, status, discard_reason);
    ``stage3_generation/generation_failures.jsonl`` + the skills store
    (presence only, for the G2-vs-G3 split of the plan's "unborn <->
    discarded(model_output_error)" mapping); the live groups, derived from
    the final-round verdicts; ``stage4_acceptance/leak_verdicts.jsonl``
    rows; the rewrite / revision / generation failure-ledger rows.

E3  ``stage6_test/stage6_summary.json`` -- with_bank, control (stored as
    bare), delta, aux, control_source (through the alias table) -- and
    ``stage6_test/retrieval_audit.jsonl`` rows.

E4  ``llm_requests/dumps`` (the harness's normalised multiset) -- per
    call: (role, prompt_name, request_text).

E6  ``stage4_acceptance/live_run/verdicts.jsonl`` and ``.../attributions.
    jsonl`` -- full rows, ``round`` canonicalised to the B-phase ordinal.

E5  the harness config/script + the baseline provenance call counts (see
    resume_variants / run_e5).

Normalisation (plan section 8, fixed here -- nothing else may differ):

1. the four volatile keys + run_dir paths: already applied by
   ``harness.snapshot`` (the baseline bundle inherits it);
2. layout mapping: per-phase ledgers/summaries only. Object stores, the
   bank, the shared leak-verdicts ledger and the llm request history are
   run-level and never move (P4), so they carry no mapping;
3. object new-field removal: every view projects its fields explicitly,
   so v2/v4 object additions (source_subtasks, exam skill_id,
   schema_version) fall away -- a fresh v3 run under v2 objects already
   exercises this (the standing identity test).

Explicitly NOT compared (plan section 8, T12): phase-summary bucketing
and key names outside E3's named fields, intermediate persistence points,
call order, the pipeline_log label substitution, workdir trees, the
manifest. Audit rows are compared as a canonical multiset: reordering
rows is not a difference, any content change is.

The v4 layout table is DATA derived from the two upgrade_v3 phase
sequences (plan section 4). If M4.3's real layout disagrees, edit
SEQUENCES / v4_layout -- the assertion functions hard-code no paths.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from expedistill.benchmarks.base import BenchmarkPlugin
from expedistill.core.ids import make_skill_id
from expedistill.infra.parallel import ParallelError
from expedistill.infra.store import KIND_DIRS
from expedistill.stages.pipeline import run_pipeline

from tests.golden.harness import (
    G11_REVISION_MARKER,
    SimulatedCrash,
    _ClientLog,
    assert_resume_equivalent,
    combo_label,
    installed_plugin,
    plugin_for,
    run_crash_and_resume,
    run_straight,
    snapshot,
)

#: Where the committed baseline bundle lives (extract_baseline.py writes
#: it; the tamper tests copy it to a tmp dir before editing).
BASELINE_DIR = (Path(__file__).resolve().parents[1] / "fixtures"
                / "golden_semantic_baseline")

# ---------------------------------------------------------------------------
# The layout table (data)
# ---------------------------------------------------------------------------

#: The two phase sequences the upgrade_v3 mapping derives (plan section 4):
#: exam (retry_limit 2) -> [D, L] + [A, L] x 3 + [C]; live_run ->
#: [D, L, B, L, B, L, C]. p00 is freeze; sequence entry i is phase
#: p{i+1:02d}_<local-name>.
SEQUENCES: Dict[str, Tuple[str, ...]] = {
    "exam": ("D", "L", "A", "L", "A", "L", "A", "L", "C"),
    "live_run": ("D", "L", "B", "L", "B", "L", "C"),
}


def phase_dir(sequence: Sequence[str], index: int) -> str:
    """The phase directory of sequence entry ``index`` (0-based)."""
    return f"phases/p{index + 1:02d}_{sequence[index]}"


#: Canonical (v3-shaped) snapshot keys the views read. The baseline bundle
#: is stored in this key space; the v4 side is mapped onto it.
BANK = "skill_bank.jsonl"
GENERATION_FAILURES = "stage3_generation/generation_failures.jsonl"
LEAK_VERDICTS = "stage4_acceptance/leak_verdicts.jsonl"
REWRITE_FAILURES = "stage4_acceptance/rewrite_failures.jsonl"
LIVE_DIR = "stage4_acceptance/live_run"
LIVE_VERDICTS = f"{LIVE_DIR}/verdicts.jsonl"
LIVE_ATTRIBUTIONS = f"{LIVE_DIR}/attributions.jsonl"
LIVE_REVISION_FAILURES = f"{LIVE_DIR}/revision_failures.jsonl"
TEST_SUMMARY = "stage6_test/stage6_summary.json"
RETRIEVAL_AUDIT = "stage6_test/retrieval_audit.jsonl"
REQUESTS = "llm_requests/dumps"

#: The skills store key prefix (run-level, P4) for the E2 G2-vs-G3 split.
_SKILLS_PREFIX = KIND_DIRS["skills"] + "/"

#: Values the C summary's ``control_source`` may take, canonicalised: a
#: v3 stage reference and its v4 phase equivalents name the same control
#: arm (full mode's C reuses D's weak arm). M4.3 adds its real token here
#: if it differs from the predicted ones.
CONTROL_SOURCE_ALIASES = {
    "fresh_bare_run": "fresh_bare_run",
    "stage1_reuse": "D_weak_arm",
    "D": "D_weak_arm",
    "phase_D": "D_weak_arm",
    "p01_D": "D_weak_arm",
}


def v4_layout(acceptance: str) -> Dict[str, Tuple[str, Optional[str], int]]:
    """The v4 -> canonical mapping table, as data.

    Key: a v4 snapshot path. Value: (canonical path, the row field that
    carries the phase-local round number, that round's canonical value).
    Ledger shards concatenate in table order (the sequences enumerate in
    phase order, so shard order IS round order); ``None`` marks a plain
    file move. Everything else in a v4 snapshot passes through unchanged.
    """
    sequence = SEQUENCES[acceptance]
    table: Dict[str, Tuple[str, Optional[str], int]] = {}
    c = sequence.index("C")
    table[f"{phase_dir(sequence, c)}/summary.json"] = (TEST_SUMMARY, None, 0)
    table[f"{phase_dir(sequence, c)}/retrieval_audit.jsonl"] = (
        RETRIEVAL_AUDIT, None, 0)
    b_seen = 0
    a_seen = 0
    for i, local in enumerate(sequence):
        directory = phase_dir(sequence, i)
        if local == "B":
            table[f"{directory}/verdicts.jsonl"] = (LIVE_VERDICTS,
                                                   "round", b_seen)
            b_seen += 1
        elif local == "L":
            # The L after an execution phase owns that round's bookkeeping
            # (plan section 3): attributions/revision failures follow a B,
            # G6 rewrite failures follow an A; generation failures may
            # land in any L (G2 runs wherever the baseline report is read).
            if i > 0 and sequence[i - 1] == "B":
                table[f"{directory}/attributions.jsonl"] = (
                    LIVE_ATTRIBUTIONS, "round", b_seen - 1)
                table[f"{directory}/revision_failures.jsonl"] = (
                    LIVE_REVISION_FAILURES, "round", b_seen - 1)
            if i > 0 and sequence[i - 1] == "A":
                table[f"{directory}/rewrite_failures.jsonl"] = (
                    REWRITE_FAILURES, "retry_round", a_seen - 1)
            table[f"{directory}/generation_failures.jsonl"] = (
                GENERATION_FAILURES, None, 0)
        elif local == "A":
            a_seen += 1
    return table


def canonicalize(snapshot_data: Dict[str, Any], acceptance: str,
                 layout: str = "v3") -> Dict[str, Any]:
    """Map a raw snapshot onto the canonical v3 key space.

    ``layout="v3"`` is the identity (the baseline's own form). For
    ``"v4"`` the per-phase shards are merged onto their canonical paths
    with their round fields rewritten to the canonical phase ordinals
    (v3's round numbering -- the B ordinal for live ledgers, the exam
    round for rewrite failures). Unmapped keys pass through: object
    stores and run-level files do not move (P4), and the not-compared
    files simply stay out of the views.
    """
    if layout != "v4":
        return snapshot_data
    table = v4_layout(acceptance)
    out: Dict[str, Any] = {}
    shards: Dict[str, List[Tuple[int, List[Any]]]] = {}
    for key in sorted(snapshot_data):
        value = snapshot_data[key]
        rule = table.get(key)
        if rule is None:
            out[key] = value
            continue
        canonical, round_field, ordinal = rule
        if round_field is None:
            out[canonical] = value
            continue
        rows = [dict(row) for row in value]
        for row in rows:
            row[round_field] = ordinal
        shards.setdefault(canonical, []).append((ordinal, rows))
    for canonical, parts in shards.items():
        parts.sort(key=lambda part: part[0])
        merged: List[Any] = []
        for _, rows in parts:
            merged.extend(rows)
        out[canonical] = merged
    return out


# ---------------------------------------------------------------------------
# The per-criterion views (pure projections -- the pre-registration)
# ---------------------------------------------------------------------------


def _canon(value: Any) -> str:
    """The canonical JSON of a view row (its sort/equality key)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _sorted(rows: Sequence[Any]) -> List[Any]:
    return sorted(rows, key=_canon)


def _e1(snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
    return {"bank_rows": _sorted([
        {"skill_id": row["skill_id"], "content": row["content"],
         "final_version": row["version"], "status": row["status"],
         "discard_reason": row["discard_reason"], "group": row["group"],
         "merged_from": row["merged_from"],
         "rewrite_history": row["rewrite_history"]}
        for row in snapshot_data[BANK]])}


def _skill_in_store(snapshot_data: Dict[str, Any], skill_id: str) -> bool:
    key = (_SKILLS_PREFIX + urllib.parse.quote(skill_id, safe="")
           + ".json")
    return key in snapshot_data


def _e2(snapshot_data: Dict[str, Any], acceptance: str) -> Dict[str, Any]:
    terminals = [{"skill_id": row["skill_id"], "status": row["status"],
                  "discard_reason": row["discard_reason"]}
                 for row in snapshot_data[BANK]]
    unborn: List[Dict[str, Any]] = []
    # A terminally failed stage-3 unit is one row in generation_failures.
    # The plan's E2 mapping: with its skill written (G3 failed after G2
    # succeeded) it is a v4 bank discard; without one (G2 failed) it is a
    # v4 never-born ledger row. The skills store is run-level (P4), so the
    # presence probe works identically on both layouts.
    for row in snapshot_data.get(GENERATION_FAILURES, []):
        skill_id = make_skill_id(row["subtask_id"])
        if _skill_in_store(snapshot_data, skill_id):
            terminals.append({"skill_id": skill_id, "status": "discarded",
                              "discard_reason": "model_output_error"})
        else:
            unborn.append({"skill_id": skill_id,
                           "reason": "model_output_error"})
    finals: Dict[str, Dict[str, Any]] = {}
    for row in snapshot_data.get(LIVE_VERDICTS, []):
        prior = finals.get(row["task_id"])
        if prior is None or row["round"] > prior["round"]:
            finals[row["task_id"]] = row
    groups = [{"task_id": task_id, "final_round": row["round"],
               "outcome": row.get("verdict")
               or ("improved" if row["better"] else "not_improved")}
              for task_id, row in sorted(finals.items())]
    return {
        "terminal_states": _sorted(terminals),
        "unborn": _sorted(unborn),
        "groups": _sorted(groups),
        "leak_verdicts": _sorted(
            [{"skill_id": row["skill_id"],
              "skill_version": row["skill_version"],
              "leaked": row["leaked"], "reason": row["reason"]}
             for row in snapshot_data.get(LEAK_VERDICTS, [])]),
        "generation_failures": _sorted(
            snapshot_data.get(GENERATION_FAILURES, [])),
        "rewrite_failures": _sorted(snapshot_data.get(REWRITE_FAILURES, [])),
        "revision_failures": _sorted(
            snapshot_data.get(LIVE_REVISION_FAILURES, [])),
    }


def _e3(snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
    summary = snapshot_data[TEST_SUMMARY]
    return {
        "with_bank": summary["with_bank"],
        "bare": summary["control"],
        "delta": summary["delta"],
        "aux": summary.get("aux"),
        "control_source": CONTROL_SOURCE_ALIASES.get(
            summary["control_source"], summary["control_source"]),
        "retrieval_audit": _sorted(snapshot_data.get(RETRIEVAL_AUDIT, [])),
    }


def _e4(snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
    return {"calls": _sorted([
        {"role": dump["role"], "prompt": dump["prompt_name"],
         "request_text": dump["request_text"]}
        for dump in snapshot_data[REQUESTS]])}


def _e6(snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "verdicts": _sorted(snapshot_data.get(LIVE_VERDICTS, [])),
        "attributions": _sorted(snapshot_data.get(LIVE_ATTRIBUTIONS, [])),
    }


def semantic_views(snapshot_data: Dict[str, Any],
                   combo: Dict[str, str]) -> Dict[str, Any]:
    """The E1-E6 view dict of a CANONICALISED snapshot (one combo)."""
    return {
        "label": combo_label(combo),
        "acceptance": combo["acceptance"],
        "E1": _e1(snapshot_data),
        "E2": _e2(snapshot_data, combo["acceptance"]),
        "E3": _e3(snapshot_data),
        "E4": _e4(snapshot_data),
        "E6": _e6(snapshot_data),
    }


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _preview(value: Any, limit: int = 80) -> str:
    text = _canon(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _first_difference(golden: Any, fresh: Any, path: str) -> str:
    """A readable, first-pointing description of where two values split."""
    if isinstance(golden, dict) and isinstance(fresh, dict):
        for key in sorted(set(golden) | set(fresh)):
            if key not in golden:
                return f"{path}.{key}: only in fresh ({_preview(fresh[key])})"
            if key not in fresh:
                return f"{path}.{key}: only in golden"
            if golden[key] != fresh[key]:
                return _first_difference(golden[key], fresh[key],
                                         f"{path}.{key}")
    if isinstance(golden, list) and isinstance(fresh, list):
        if len(golden) != len(fresh):
            return (f"{path}: {len(golden)} row(s) in baseline vs "
                    f"{len(fresh)} in fresh")
        for index, (want, got) in enumerate(zip(golden, fresh)):
            if want != got:
                return _first_difference(want, got, f"{path}[{index}]")
        return f"{path}: row order differs"
    return f"{path}: baseline={_preview(golden)} fresh={_preview(fresh)}"


def _multiset_diff(label: str, golden: Sequence[Any],
                   fresh: Sequence[Any]) -> Optional[str]:
    """Missing/extra rows of two multisets, first offender named.

    A modified row surfaces as one missing plus one extra; when the
    counts pair up, the diff walks the first pair field by field so the
    message points at the changed field inside the changed row.
    """
    missing = list(golden)
    extra: List[Any] = []
    for row in fresh:
        try:
            missing.remove(row)
        except ValueError:
            extra.append(row)
    if not missing and not extra:
        return None
    parts = [f"{len(missing)} row(s) missing, {len(extra)} extra"]
    if len(missing) == 1 and len(extra) == 1:
        parts.append(f"changed row ({_row_of_interest(missing[0])}): "
                     + _first_difference(missing[0], extra[0], label))
    else:
        if missing:
            parts.append(f"first missing: {_preview(missing[0], 160)}")
        if extra:
            parts.append(f"first extra: {_preview(extra[0], 160)}")
    return f"{label}: " + "; ".join(parts)


def _diff(golden: Any, fresh: Any, label: str) -> Optional[str]:
    if golden == fresh:
        return None
    return _first_difference(golden, fresh, label)


def _row_of_interest(row: Dict[str, Any]) -> str:
    """The row's identity for diff messages (skill, task, call or role)."""
    for key in ("skill_id", "task_id", "subtask_id", "prompt", "role"):
        if key in row:
            return f"{key}={row[key]!r}"
    return _preview(row, 120)


def _compare_e1(golden: Dict[str, Any], fresh: Dict[str, Any],
                combo: Dict[str, str]) -> Optional[str]:
    return _multiset_diff("E1.bank_rows", golden["bank_rows"],
                          fresh["bank_rows"])


def _compare_e2(golden: Dict[str, Any], fresh: Dict[str, Any],
                combo: Dict[str, str]) -> Optional[str]:
    parts = []
    for key, label in (("terminal_states", "E2.terminal_states"),
                       ("unborn", "E2.unborn"),
                       ("groups", "E2.groups"),
                       ("leak_verdicts", "E2.leak_verdicts"),
                       ("generation_failures", "E2.generation_failures"),
                       ("rewrite_failures", "E2.rewrite_failures"),
                       ("revision_failures", "E2.revision_failures")):
        if not golden[key] and not fresh[key]:
            continue  # an empty pinned branch on a combo that cannot reach it
        parts.append(_multiset_diff(label, golden[key], fresh[key]))
    diffs = [part for part in parts if part]
    return "; ".join(diffs) if diffs else None


def _compare_e3(golden: Dict[str, Any], fresh: Dict[str, Any],
                combo: Dict[str, str]) -> Optional[str]:
    parts = []
    for key in ("with_bank", "bare", "delta", "aux", "control_source"):
        label = f"E3.{key}"
        if golden[key] != fresh[key]:
            parts.append(_diff(golden[key], fresh[key], label))
    if golden["retrieval_audit"] != fresh["retrieval_audit"]:
        parts.append(_multiset_diff("E3.retrieval_audit",
                                    golden["retrieval_audit"],
                                    fresh["retrieval_audit"]))
    diffs = [part for part in parts if part]
    return "; ".join(diffs) if diffs else None


def _exam_gen(call: Dict[str, Any]) -> bool:
    return call["role"] == "examiner" and call["prompt"] == "EXAM_GEN"


def _compare_e4(golden: Dict[str, Any], fresh: Dict[str, Any],
                combo: Dict[str, str], layout: str) -> Optional[str]:
    if combo["acceptance"] == "live_run" and layout == "v4":
        # Live whitelist (1), machine-checked, a v3-baseline <-> v4-fresh
        # relation: the v4 call multiset must be EXACTLY the v3 multiset
        # with every (examiner, EXAM_GEN) call removed -- any other
        # difference fails. A leftover exam-gen call shows up as an extra
        # row and the diff names role and prompt. A v3 fresh side (the
        # identity self-check) compares plainly, whitelist not applied.
        expected = [call for call in golden["calls"] if not _exam_gen(call)]
        return _multiset_diff("E4.calls (live whitelist: fresh must equal "
                              "the baseline without examiner/EXAM_GEN "
                              "calls)", expected, fresh["calls"])
    return _multiset_diff("E4.calls", golden["calls"], fresh["calls"])


def _compare_e6(golden: Dict[str, Any], fresh: Dict[str, Any],
                combo: Dict[str, str]) -> Optional[str]:
    if not golden["verdicts"] and not golden["attributions"]:
        return None if (not fresh["verdicts"] and not fresh["attributions"]) \
            else "E6: live ledgers present in fresh but pinned empty"
    parts = [
        _multiset_diff("E6.verdicts", golden["verdicts"], fresh["verdicts"]),
        _multiset_diff("E6.attributions", golden["attributions"],
                       fresh["attributions"]),
    ]
    diffs = [part for part in parts if part]
    return "; ".join(diffs) if diffs else None


_COMPARATORS = {
    "E1": lambda g, f, c, layout: _compare_e1(g["E1"], f["E1"], c),
    "E2": lambda g, f, c, layout: _compare_e2(g["E2"], f["E2"], c),
    "E3": lambda g, f, c, layout: _compare_e3(g["E3"], f["E3"], c),
    "E4": lambda g, f, c, layout: _compare_e4(g["E4"], f["E4"], c, layout),
    "E6": lambda g, f, c, layout: _compare_e6(g["E6"], f["E6"], c),
}

CRITERIA: Tuple[str, ...] = ("E1", "E2", "E3", "E4", "E6")


def compare_views(golden: Dict[str, Any], fresh: Dict[str, Any],
                  combo: Dict[str, str], *,
                  layout: str = "v4") -> Dict[str, Any]:
    """The per-criterion pass/fail report of two view dicts.

    ``layout`` is the FRESH side's layout: "v4" applies the live
    whitelist to E4 (the M4.3 acceptance default); "v3" compares
    plainly (the identity self-check). Returns ``{"criteria": {name:
    {"passed": bool, "diff": str|None}}, "passed": bool}`` -- plain
    JSON-serialisable data, so an M4.3 acceptance run can drop it
    straight into its evidence.
    """
    criteria = {}
    for name in CRITERIA:
        diff = _COMPARATORS[name](golden, fresh, combo, layout)
        criteria[name] = {"passed": diff is None, "diff": diff}
    return {"criteria": criteria,
            "passed": all(result["passed"]
                          for result in criteria.values())}


def assert_semantically_equal(report: Dict[str, Any]) -> None:
    """Loud failure naming every red criterion (the M4.3 gate)."""
    failed = [f"{name}: {result['diff']}"
              for name, result in sorted(report["criteria"].items())
              if not result["passed"]]
    if failed:
        raise AssertionError(
            "semantic drift vs the 69904f7 baseline: "
            + " | ".join(failed))


def load_baseline(baseline_root: Path, combo: Dict[str, str]) -> Dict[str, Any]:
    """The committed baseline bundle's view dict for ``combo``."""
    path = Path(baseline_root) / f"{combo_label(combo)}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def compare_combo(combo: Dict[str, str], v4_root: Path,
                  baseline_root: Path = BASELINE_DIR,
                  *, layout: str = "v4") -> Dict[str, Any]:
    """The comparison entry point: one combo's fresh run vs the baseline.

    ``v4_root`` is the run directory M4.3 just produced; ``layout`` says
    which side of the layout table its snapshot is on ("v4" for the real
    acceptance run; the identity self-check passes "v3"). Returns the
    compare_views report.
    """
    fresh_snapshot = snapshot(Path(v4_root))
    fresh = semantic_views(
        canonicalize(fresh_snapshot, combo["acceptance"], layout), combo)
    golden = load_baseline(baseline_root, combo)
    return compare_views(golden, fresh, combo, layout=layout)


# ---------------------------------------------------------------------------
# E5: the parameterised crash-resume variants
# ---------------------------------------------------------------------------

#: Crash points as fractions of the straight run's model call count (the
#: generate.py slots) -- the variants the golden matrix already runs, kept
#: so M4.3's acceptance compares the same three boundaries per combo.
CRASH_FRACTIONS: Tuple[float, ...] = (0.3, 0.6, 0.95)


def crash_points(model_calls: int) -> List[int]:
    return [max(1, min(model_calls - 1, round(model_calls * fraction)))
            for fraction in CRASH_FRACTIONS]


def resume_variants(acceptance: str, model_calls: int) -> List[Dict[str, Any]]:
    """The E5 variant list for one combo (plan section 8 + reviewer C-1).

    Every combo crashes before three fraction-derived model calls. Live
    combos additionally crash in the "revision persisted, re-answer not
    yet run" window: that window exists only where a G11 revision
    happens, which in the golden matrix is exactly the live combos (the
    exam combos pass every skill on the first round, so no rewrite ever
    occurs -- the exam-side window is pinned exhaustively by the
    composite-level crash-at-every-boundary suite,
    tests/composites/test_acceptance_resume.py).
    """
    variants = [{"name": f"crash_before_call_{point}", "kind": "before_call",
                 "n": point} for point in crash_points(model_calls)]
    if acceptance == "live_run":
        variants.append({"name": "revision_window",
                         "kind": "revision_window"})
    return variants


class RevisionWindowCrash(BenchmarkPlugin):
    """Delegates to the wrapped plugin, but raises ``SimulatedCrash``
    before the first ``run()`` whose skills block carries the G11
    revision marker.

    The block is a pure derivation from the skills store
    (``live_acceptance.survivors_and_block``), so marker-in-block means
    the revision IS persisted -- and the task execution it belongs to is
    the pending re-answer. The K1 dispatch stops at the first raise, so
    nothing of the re-answer round persists (verified by the standing
    self-check: zero round-1 trajectories at the crash point). And it is
    layout-agnostic by construction: it works unchanged when the rounds
    become B phases.
    """

    def __init__(self, inner: BenchmarkPlugin):
        self._inner = inner
        self.fired = False

    @property
    def name(self) -> str:
        return self._inner.name

    def load_tasks(self, tasks_spec):
        return self._inner.load_tasks(tasks_spec)

    def run(self, task, handle, skills_block, workdir):
        if not self.fired and G11_REVISION_MARKER in skills_block:
            self.fired = True
            raise SimulatedCrash(
                "simulated crash in the revision-persisted window "
                f"(task {task.task_id}, block carries the G11 marker)")
        return self._inner.run(task, handle, skills_block, workdir)

    @property
    def view_rules(self):
        return self._inner.view_rules

    @property
    def task_prompt_name(self) -> str:
        return self._inner.task_prompt_name


def _is_simulated_crash(exc: BaseException) -> bool:
    """True when ``exc`` is the harness's SimulatedCrash, however deep a
    pool wrapped it. The stage drivers unwrap their run_units ParallelError
    back into the unit's own exception; the live round dispatch does not,
    so the window runner accepts both surfaces."""
    while exc is not None:
        if isinstance(exc, SimulatedCrash):
            return True
        exc = exc.__cause__
    return False


def _crash_in_revision_window(config_path: Path, combo: Dict[str, str],
                               run_dir: Path) -> None:
    """Crash with the revision persisted and the re-answer pending (C-1),
    then prove the window was real before returning: the crash must have
    fired, and the skills store must already carry the revised content.
    (``skill_bank.jsonl`` is a stage-5 export; at this point the
    revision's only durable home is the store, and the round-1 block was
    rendered from exactly those files -- which is why marker-in-block
    proves persistence.)

    Owns its plugin install (installed_plugin refuses nested same-name
    registrations), so callers must NOT wrap this in another one.
    """
    wrapper = RevisionWindowCrash(plugin_for(combo))
    with installed_plugin(wrapper):
        try:
            run_pipeline(config_path=Path(config_path), run_dir=Path(run_dir),
                         make_client=_ClientLog().make_client)
            raise AssertionError("the revision-window crash never fired")
        except (SimulatedCrash, ParallelError) as exc:
            if not _is_simulated_crash(exc):
                raise
    skills_dir = Path(run_dir) / KIND_DIRS["skills"]
    persisted = [path.name for path in sorted(skills_dir.glob("*.json"))
                 if G11_REVISION_MARKER
                 in path.read_text(encoding="utf-8")]
    if not persisted:
        raise AssertionError(
            "the revision-window crash fired before the revision reached "
            "the skills store (no G11 marker in any stored skill) -- not "
            "the C-1 window; fix the crash predicate, do not weaken the "
            "check")


def run_e5(config_path: Path, combo: Dict[str, str],
           workdir: Path) -> Dict[str, Any]:
    """E5 for one combo: the straight reference, then every crash-resume
    variant, each resumed with the same command and checked against the
    straight snapshot with the harness's ``assert_resume_equivalent``
    (result state identical; request history may only repeat calls whose
    result was never persisted).

    Installs the combo's plugin per segment itself; the caller only
    provides the config and a scratch directory. Returns
    ``{"model_calls": n, "variants": {name: "equivalent"}}``. Raises on
    any inequivalence -- the loud-abort rule from generate.py: the
    instrument must never land on a resume bug quietly. M4.3's acceptance
    runs this per combo and asserts every variant equivalent.
    """
    workdir = Path(workdir)
    straight = workdir / "straight"
    with installed_plugin(plugin_for(combo)):
        calls = run_straight(Path(config_path), straight)
    golden = snapshot(straight)
    results = {}
    for variant in resume_variants(combo["acceptance"], calls):
        run_dir = workdir / variant["name"]
        if variant["kind"] == "before_call":
            with installed_plugin(plugin_for(combo)):
                run_crash_and_resume(Path(config_path), run_dir, variant["n"])
        else:
            _crash_in_revision_window(Path(config_path), combo, run_dir)
            with installed_plugin(plugin_for(combo)):
                run_straight(Path(config_path), run_dir)
        assert_resume_equivalent(golden, snapshot(run_dir))
        results[variant["name"]] = "equivalent"
    return {"model_calls": calls, "variants": results}
