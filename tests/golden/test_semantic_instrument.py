"""M4.1 instrument self-check: identity proofs + tamper proofs (plan §8).

The instrument (tests/golden/semantics.py) is only trustworthy if every
assertion can actually fail on the difference it claims to catch. This
suite proves exactly that, four ways:

* identity -- re-extracting the baseline from commit 69904f7 reproduces
  the committed bundle byte for byte, the CURRENT golden matrix (v2
  objects, v3 layout) maps onto it unchanged, a fresh v3 pipeline run
  matches it end to end, and a synthetic v4-layout snapshot
  (the predicted M4.3 shape) canonicalises back onto it -- so both sides
  of the layout table are exercised on real data;
* tamper -- eleven targeted corruptions of in-memory bundle copies (the
  committed one is never touched); each must turn its criterion red and
  point at the corrupted item, and leave the unrelated criteria green;
* bridge -- synthetic snapshot pairs pin the branches the golden matrix
  can never reach (P6): the E2 unborn mapping across layouts (aligned
  forms fully equal, misaligned forms red with a pointed diff) and the
  multi-shard generation-failures concatenation;
* E5 -- the crash-variant list has the required shape and every variant,
  including the "revision persisted, re-answer not yet run" window (C-1),
  resumes equivalent on a live combo under today's code, and the
  straight call count equals the layout-aware expectation (C-2).

Baseline bundle: tests/fixtures/golden_semantic_baseline/ (~399 KiB; 8
combo view files + provenance.json), extracted from commit 69904f7's
golden matrix with:
    .venv/bin/python tests/golden/extract_baseline.py
"""

import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

from expedistill.core.ids import make_skill_id
from tests.golden.harness import (
    COMBOS,
    combo_label,
    installed_plugin,
    plugin_for,
    run_straight,
    write_config,
)
from tests.golden.semantics import (
    BASELINE_DIR,
    GENERATION_FAILURES,
    LIVE_ATTRIBUTIONS,
    LIVE_REVISION_FAILURES,
    LIVE_VERDICTS,
    REQUESTS,
    RETRIEVAL_AUDIT,
    REWRITE_FAILURES,
    SEQUENCES,
    TEST_SUMMARY,
    assert_semantically_equal,
    canonicalize,
    compare_combo,
    compare_views,
    crash_points,
    expected_model_calls,
    load_baseline,
    phase_dir,
    resume_variants,
    run_e5,
    run_layout,
    semantic_views,
    v4_layout,
)

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _combo(label: str) -> dict:
    return next(c for c in COMBOS if combo_label(c) == label)


def _golden_snapshot(combo: dict) -> dict:
    label = combo_label(combo)
    return json.loads((GOLDEN_DIR / f"{label}.json").read_text(
        encoding="utf-8"))


def _expected_red(report: dict, *red: str) -> None:
    """Exactly ``red`` criteria fail; everything else passes."""
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert failed == sorted(red), (failed, report["criteria"])
    for name in red:
        assert report["criteria"][name]["diff"], name


# ---------------------------------------------------------------------------
# The layout table (data -- pin its derived shape)
# ---------------------------------------------------------------------------

def test_v4_layout_pins_the_predicted_phase_paths():
    exam = v4_layout("exam")
    assert set(exam) == {
        "phases/p09_C/summary.json", "phases/p09_C/retrieval_audit.jsonl",
        "phases/p02_L/generation_failures.jsonl",
        "phases/p04_L/generation_failures.jsonl",
        "phases/p04_L/rewrite_failures.jsonl",
        "phases/p06_L/generation_failures.jsonl",
        "phases/p06_L/rewrite_failures.jsonl",
        "phases/p08_L/generation_failures.jsonl",
        "phases/p08_L/rewrite_failures.jsonl",
    }
    assert exam["phases/p04_L/rewrite_failures.jsonl"] == (
        REWRITE_FAILURES, "retry_round", 0)
    assert exam["phases/p06_L/rewrite_failures.jsonl"] == (
        REWRITE_FAILURES, "retry_round", 1)
    # No round field to rewrite: the ordinal is the phase index that
    # orders the shard concatenation (see the dual-shard test below).
    assert exam["phases/p02_L/generation_failures.jsonl"] == (
        GENERATION_FAILURES, None, 1)
    assert exam["phases/p08_L/generation_failures.jsonl"] == (
        GENERATION_FAILURES, None, 7)
    live = v4_layout("live_run")
    assert set(live) == {
        "phases/p07_C/summary.json", "phases/p07_C/retrieval_audit.jsonl",
        "phases/p02_L/generation_failures.jsonl",
        "phases/p03_B/verdicts.jsonl", "phases/p04_L/attributions.jsonl",
        "phases/p04_L/revision_failures.jsonl",
        "phases/p04_L/generation_failures.jsonl",
        "phases/p05_B/verdicts.jsonl", "phases/p06_L/attributions.jsonl",
        "phases/p06_L/revision_failures.jsonl",
        "phases/p06_L/generation_failures.jsonl",
    }
    assert live["phases/p03_B/verdicts.jsonl"] == (LIVE_VERDICTS, "round", 0)
    assert live["phases/p05_B/verdicts.jsonl"] == (LIVE_VERDICTS, "round", 1)
    assert live["phases/p04_L/attributions.jsonl"] == (
        LIVE_ATTRIBUTIONS, "round", 0)


def test_phase_sequences_match_the_upgrade_v3_table():
    assert SEQUENCES["exam"] == ("D", "L", "A", "L", "A", "L", "A", "L", "C")
    assert SEQUENCES["live_run"] == ("D", "L", "B", "L", "B", "L", "C")
    assert phase_dir(SEQUENCES["exam"], 0) == "phases/p01_D"
    assert phase_dir(SEQUENCES["live_run"], 6) == "phases/p07_C"


# ---------------------------------------------------------------------------
# Identity proofs
# ---------------------------------------------------------------------------

def test_extraction_reproduces_the_committed_bundle(tmp_path):
    result = subprocess.run(
        [sys.executable, "tests/golden/extract_baseline.py",
         "--out", str(tmp_path)],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    # The bundle also carries a human README; the extractor writes the
    # machine-readable outputs only.
    committed = sorted(p.name for p in BASELINE_DIR.iterdir()
                       if p.suffix == ".json")
    extracted = sorted(p.name for p in tmp_path.iterdir())
    assert extracted == committed
    for name in committed:
        assert (tmp_path / name).read_bytes() == (BASELINE_DIR / name).read_bytes(), name


@pytest.mark.parametrize("combo", COMBOS, ids=[combo_label(c) for c in COMBOS])
def test_current_matrix_semantically_matches_the_baseline(combo):
    """The standing no-drift proof: M1-M3 changed object fields, not
    behaviour -- the current golden matrix (v2 objects, v3 layout) maps
    onto the 69904f7 baseline under the plain (v3-side) comparison."""
    fresh = semantic_views(_golden_snapshot(combo), combo)
    golden = load_baseline(BASELINE_DIR, combo)
    assert_semantically_equal(
        compare_views(golden, fresh, combo, layout="v3"))


@pytest.mark.parametrize(
    "label", ("exam-whole_bank-split", "live_run-retrieval-split"))
def test_fresh_v3_run_matches_the_baseline_end_to_end(label, tmp_path):
    """The run-directory entry point: a fresh pipeline under today's code,
    snapshotted and canonicalised as v3, equals the committed baseline."""
    combo = _combo(label)
    config = write_config(tmp_path / "config.yaml", combo)
    with installed_plugin(plugin_for(combo)):
        run_straight(config, tmp_path / "run")
    report = compare_combo(combo, tmp_path / "run", BASELINE_DIR,
                           layout="v3")
    assert_semantically_equal(report)


def _synthetic_v4(snapshot_data: dict, combo: dict) -> dict:
    """Rename a v3 snapshot onto the PREDICTED v4 layout (test-side).

    Executable documentation of what M4.3 is expected to write: the C
    summary and retrieval audit under the C phase directory, the live
    ledgers sharded per phase with their round fields rewritten to
    phase-absolute values (proving the canonicaliser overrides them from
    the shard's position), rewrite/revision failures in the L phase that
    follows their execution phase, generation failures in the first L --
    and, for live combos, the (examiner, EXAM_GEN) calls gone (the v4
    live sequence has no A phase; whitelist 1). Object stores, the bank
    and the request history stay put (P4).
    """
    acceptance = combo["acceptance"]
    sequence = SEQUENCES[acceptance]
    out = {key: value for key, value in snapshot_data.items()
           if key not in (TEST_SUMMARY, RETRIEVAL_AUDIT, LIVE_VERDICTS,
                          LIVE_ATTRIBUTIONS, REWRITE_FAILURES,
                          GENERATION_FAILURES, LIVE_REVISION_FAILURES)}
    c = sequence.index("C")
    if TEST_SUMMARY in snapshot_data:
        out[f"{phase_dir(sequence, c)}/summary.json"] = snapshot_data[TEST_SUMMARY]
    if RETRIEVAL_AUDIT in snapshot_data:
        out[f"{phase_dir(sequence, c)}/retrieval_audit.jsonl"] = \
            snapshot_data[RETRIEVAL_AUDIT]
    b_at = [i for i, local in enumerate(sequence) if local == "B"]
    a_at = [i for i, local in enumerate(sequence) if local == "A"]
    for round_, i in enumerate(b_at):
        rows = [dict(row) for row in snapshot_data.get(LIVE_VERDICTS, [])
                if row["round"] == round_]
        if rows:
            for row in rows:
                row["round"] = i  # v4 would write the phase-absolute value
            out[f"{phase_dir(sequence, i)}/verdicts.jsonl"] = rows
        rows = [dict(row) for row in snapshot_data.get(LIVE_ATTRIBUTIONS, [])
                if row["round"] == round_]
        rows += [dict(row) for row in
                 snapshot_data.get(LIVE_REVISION_FAILURES, [])
                 if row["round"] == round_]
        if rows:
            for row in rows:
                row["round"] = i
            out[f"{phase_dir(sequence, i + 1)}/attributions.jsonl"] = rows
    for round_, i in enumerate(a_at):
        rows = [dict(row) for row in snapshot_data.get(REWRITE_FAILURES, [])
                if row["retry_round"] == round_]
        if rows:
            for row in rows:
                row["retry_round"] = i
            out[f"{phase_dir(sequence, i + 1)}/rewrite_failures.jsonl"] = rows
    if snapshot_data.get(GENERATION_FAILURES):
        out[f"{phase_dir(sequence, 1)}/generation_failures.jsonl"] = \
            snapshot_data[GENERATION_FAILURES]
    if acceptance == "live_run":
        out[REQUESTS] = [dump for dump in out[REQUESTS]
                         if not (dump["role"] == "examiner"
                                 and dump["prompt_name"] == "EXAM_GEN")]
    return out


@pytest.mark.parametrize("combo", COMBOS, ids=[combo_label(c) for c in COMBOS])
def test_synthetic_v4_layout_canonicalises_to_the_baseline(combo):
    """The v4 side of the layout table, exercised end to end on real
    data: the predicted M4.3 artifact shape maps back onto the committed
    baseline, live whitelist included."""
    fresh = semantic_views(
        canonicalize(_synthetic_v4(_golden_snapshot(combo), combo),
                     combo["acceptance"], "v4"), combo)
    golden = load_baseline(BASELINE_DIR, combo)
    assert_semantically_equal(compare_views(golden, fresh, combo))


def test_canonicalize_leaves_v3_snapshots_untouched():
    combo = _combo("live_run-retrieval-split")
    snapshot_data = _golden_snapshot(combo)
    assert canonicalize(snapshot_data, "live_run", "v3") is snapshot_data


def test_canonicalize_concatenates_generation_failure_shards():
    """C-1/B-6: generation_failures is registered from EVERY L phase, so
    a v4 run may write several shards onto the one canonical ledger.
    They must concatenate in phase order -- the last shard must not
    silently drop the earlier ones (the branch is unreachable on the
    golden matrix, whose ledgers are empty; this is its pin)."""
    shard_p02 = [{"subtask_id": "traj/fakebench:t1/strong/00/seg00",
                  "error_class": "ModelOutputError", "message": "a"}]
    shard_p06 = [{"subtask_id": "traj/fakebench:t1/strong/00/seg01",
                  "error_class": "ModelOutputError", "message": "b"},
                 {"subtask_id": "traj/fakebench:t1/weak/00/seg00",
                  "error_class": "ModelOutputError", "message": "c"}]
    snapshot_data = {  # insertion order deliberately not phase order
        "phases/p06_L/generation_failures.jsonl": shard_p06,
        "phases/p02_L/generation_failures.jsonl": shard_p02,
    }
    merged = canonicalize(snapshot_data, "exam", "v4")
    assert merged[GENERATION_FAILURES] == shard_p02 + shard_p06
    assert len(merged[GENERATION_FAILURES]) == 3


# ---------------------------------------------------------------------------
# Tamper proofs: every assertion must be able to fail, pointedly
# ---------------------------------------------------------------------------

def _tampered(combo: dict, mutate) -> dict:
    views = json.loads(json.dumps(load_baseline(BASELINE_DIR, combo)))
    mutate(views)
    return views


def _compare(combo: dict, golden: dict, fresh: dict, layout: str = "v3"):
    """Bundle-vs-bundle comparison: both sides are baseline-shaped (v3),
    so the plain comparison is the right one -- EXCEPT the whitelist
    test, which explicitly compares a v4-shaped fresh side."""
    return compare_views(golden, fresh, combo, layout=layout)


def test_tamper_e1_bank_row_content():
    combo = _combo("exam-whole_bank-split")
    def mutate(views):
        views["E1"]["bank_rows"][0]["content"] += " TAMPERED"
    report = _compare(combo, _tampered(combo, mutate),
                      load_baseline(BASELINE_DIR, combo))
    _expected_red(report, "E1")
    diff = report["criteria"]["E1"]["diff"]
    assert "skill_id" in diff and "content" in diff


def test_tamper_discard_reason_reds_e1_and_e2():
    combo = _combo("exam-whole_bank-split")
    def mutate(views):
        row = views["E1"]["bank_rows"][0]
        row["discard_reason"] = "model_output_error"
        terminal = next(t for t in views["E2"]["terminal_states"]
                        if t["skill_id"] == row["skill_id"])
        terminal["discard_reason"] = "model_output_error"
    report = _compare(combo, _tampered(combo, mutate),
                      load_baseline(BASELINE_DIR, combo))
    _expected_red(report, "E1", "E2")
    assert "discard_reason" in report["criteria"]["E2"]["diff"]


def test_tamper_deleted_verdict_row_reds_e6_only():
    # A round-0 row is not the group final, so E2's derived group view
    # keeps passing -- exactly the separation the two criteria claim.
    combo = _combo("live_run-whole_bank-split")
    def mutate(views):
        views["E6"]["verdicts"].pop(
            next(i for i, row in enumerate(views["E6"]["verdicts"])
                 if row["round"] == 0))
    report = _compare(combo, _tampered(combo, mutate),
                      load_baseline(BASELINE_DIR, combo))
    _expected_red(report, "E6")
    assert "verdicts" in report["criteria"]["E6"]["diff"]


def test_tamper_deleted_final_verdict_row_reds_e6():
    # Deleting the final-round row changes the group's outcome -- but in
    # the committed bundle E2 and E6 are independent sections, so only
    # E6 fails here. The derived coupling (a lost final verdict changing
    # the E2 group view too, because both project the same ledger) is
    # proven below on a tampered SNAPSHOT.
    combo = _combo("live_run-whole_bank-split")
    def mutate(views):
        views["E6"]["verdicts"].pop(
            next(i for i, row in enumerate(views["E6"]["verdicts"])
                 if row["round"] == 1))
    report = _compare(combo, _tampered(combo, mutate),
                      load_baseline(BASELINE_DIR, combo))
    _expected_red(report, "E6")


def test_tamper_snapshot_verdict_deletion_couples_e6_and_e2():
    # Same corruption one level up, on the artifact snapshot both
    # criteria project: the final-round verdict row is gone, so E6 loses
    # its row AND E2's derived group view loses its outcome.
    combo = _combo("live_run-whole_bank-split")
    snapshot_data = _golden_snapshot(combo)
    snapshot_data["stage4_acceptance/live_run/verdicts.jsonl"] = [
        row for row in snapshot_data[
            "stage4_acceptance/live_run/verdicts.jsonl"]
        if row["round"] != 1]
    fresh = semantic_views(snapshot_data, combo)
    golden = load_baseline(BASELINE_DIR, combo)
    report = _compare(combo, golden, fresh)
    _expected_red(report, "E2", "E6")


def _store_key(skill_id: str) -> str:
    """The skills-store snapshot key, built the way the store writes it
    (infra.store.path_for: the percent-encoded id) -- derived here
    independently of the instrument's own prefix, so a spelling bug in
    its probe turns the bridge tests red."""
    return ("stage3_generation/skills/"
            + urllib.parse.quote(skill_id, safe="") + ".json")


def _discarded_store_skill(skill_id: str, subtask_id: str) -> dict:
    return {"skill_id": skill_id, "content": "inspect the state first.",
            "subtask_id": subtask_id, "source_subtasks": [subtask_id],
            "version": 1, "status": "discarded",
            "discard_reason": "model_output_error", "group": None,
            "merged_from": [], "rewrite_history": [], "schema_version": 2}


def _bridge_views(combo: dict, subtask_id: str,
                  v4_extras: dict) -> tuple:
    """One synthetic pair: the v3 snapshot of ``combo`` with a
    generation_failures row for ``subtask_id`` (stage 3's pair save --
    no skill was stored) vs the synthetic v4 snapshot of ``combo`` plus
    ``v4_extras`` store entries. Returns (golden_views, fresh_views)."""
    v3 = _golden_snapshot(combo)
    v3[GENERATION_FAILURES] = [{
        "subtask_id": subtask_id, "error_class": "ModelOutputError",
        "message": "rewrite budget exhausted"}]
    v4 = _synthetic_v4(_golden_snapshot(combo), combo)
    v4.update(v4_extras)
    golden = semantic_views(v3, combo)
    fresh = semantic_views(canonicalize(v4, combo["acceptance"], "v4"),
                           combo)
    return golden, fresh


def test_e2_unborn_bridge_aligns_v3_failure_rows_with_v4_discards():
    """The plan's E2 mapping, end to end at snapshot level (P-A1/B-5):
    v3 records a G3-exhausted subtask as a generation_failures row
    (stage 3's pair save means no skill was stored); v4 records the same
    event as a skills-store skill discarded with model_output_error
    (born in L, deleted after the A-phase exam failure; discards never
    enter the bank, so the store is their only durable home). The two
    forms must compare FULLY equal. The branch is unreachable in the
    golden matrix (P6: every fixture is all-accept, zero discard) --
    this synthetic pair is its pin."""
    combo = _combo("exam-whole_bank-split")
    subtask_id = "traj/fakebench:t1/strong/00/seg07"  # not in the matrix
    skill_id = make_skill_id(subtask_id)
    golden, fresh = _bridge_views(
        combo, subtask_id,
        {_store_key(skill_id): _discarded_store_skill(skill_id,
                                                      subtask_id)})
    assert_semantically_equal(
        compare_views(golden, fresh, combo, layout="v4"))


def test_tamper_v4_discard_key_misalignment_reds_e2():
    # The bridge's negative case: a v4-side discard naming a DIFFERENT
    # subtask's skill does not line up with the v3 row -- both unborn
    # multisets lose their counterpart and E2 must turn red with the
    # diff pointing at the skill_id. This also pins the id derivation:
    # a wrong make_skill_id spelling in the view reds the same way.
    combo = _combo("exam-whole_bank-split")
    other_subtask = "traj/fakebench:t1/strong/00/seg08"
    other_skill = make_skill_id(other_subtask)
    golden, fresh = _bridge_views(
        combo, "traj/fakebench:t1/strong/00/seg07",
        {_store_key(other_skill): _discarded_store_skill(
            other_skill, other_subtask)})
    report = compare_views(golden, fresh, combo, layout="v4")
    _expected_red(report, "E2")
    diff = report["criteria"]["E2"]["diff"]
    assert "unborn" in diff and "skill_id" in diff


def test_tamper_v4_discard_wrong_reason_reds_e2():
    # The other negative case: a discard with a different reason is a
    # different event class (retry_exhausted is the rewrite budget,
    # pinned by the M4 acceptance scenario tests) and must NOT bridge
    # into unborn -- the v3 row loses its counterpart.
    combo = _combo("exam-whole_bank-split")
    subtask_id = "traj/fakebench:t1/strong/00/seg07"
    skill_id = make_skill_id(subtask_id)
    wrong = _discarded_store_skill(skill_id, subtask_id)
    wrong["discard_reason"] = "retry_exhausted"
    golden, fresh = _bridge_views(
        combo, subtask_id, {_store_key(skill_id): wrong})
    report = compare_views(golden, fresh, combo, layout="v4")
    _expected_red(report, "E2")
    diff = report["criteria"]["E2"]["diff"]
    assert "unborn" in diff and "missing" in diff


def test_tamper_call_request_text_reds_e4():
    combo = _combo("exam-retrieval-split")
    def mutate(views):
        views["E4"]["calls"][0]["request_text"] += " TAMPERED"
    report = _compare(combo, _tampered(combo, mutate),
                      load_baseline(BASELINE_DIR, combo))
    _expected_red(report, "E4")
    diff = report["criteria"]["E4"]["diff"]
    assert "request_text" in diff and "prompt" in diff


def test_tamper_extra_exam_gen_call_in_live_fresh_fails_whitelist():
    # Whitelist 1: the v4 (fresh) side may not carry ANY examiner/EXAM_GEN
    # call -- adding one must fail the live E4 with the call named. The
    # fresh side is compared as v4-shaped, hence layout="v4".
    combo = _combo("live_run-whole_bank-split")
    def mutate(views):
        views["E4"]["calls"].append(
            {"role": "examiner", "prompt": "EXAM_GEN",
             "request_text": "Write one exam question that tests whether "
                             "a model has the ability exercised by the "
                             "subtask below."})
    report = _compare(combo, load_baseline(BASELINE_DIR, combo),
                      _tampered(combo, mutate), layout="v4")
    _expected_red(report, "E4")
    assert "EXAM_GEN" in report["criteria"]["E4"]["diff"]


def test_tamper_moved_retrieval_audit_row_reds_e3():
    # "Moving" an audit row to another task changes its content: the
    # multiset loses the old row and gains a new one. (Pure reordering is
    # NOT a difference -- rows are compared as a canonical multiset.)
    combo = _combo("exam-retrieval-split")
    def mutate(views):
        views["E3"]["retrieval_audit"][0]["task_id"] = "t1"
    report = _compare(combo, _tampered(combo, mutate),
                      load_baseline(BASELINE_DIR, combo))
    _expected_red(report, "E3")
    assert "retrieval_audit" in report["criteria"]["E3"]["diff"]


def test_tamper_summary_field_reds_e3():
    combo = _combo("exam-whole_bank-split")
    def mutate(views):
        views["E3"]["with_bank"]["pass_at_n"] = 0.5
    report = _compare(combo, _tampered(combo, mutate),
                      load_baseline(BASELINE_DIR, combo))
    _expected_red(report, "E3")
    assert "with_bank.pass_at_n" in report["criteria"]["E3"]["diff"]


def test_tamper_unborn_entry_reds_e2_only():
    # The never-born mapping (v3 "unborn" <-> v4 discarded rows): a
    # bogus unborn entry must fail E2 and leave E1 alone -- the bank
    # projection never sees it.
    combo = _combo("exam-whole_bank-split")
    def mutate(views):
        views["E2"]["unborn"].append(
            {"skill_id": "traj/fakebench:t9/strong/00/seg00/skill",
             "reason": "model_output_error"})
    report = _compare(combo, _tampered(combo, mutate),
                      load_baseline(BASELINE_DIR, combo))
    _expected_red(report, "E2")
    assert "unborn" in report["criteria"]["E2"]["diff"]


def test_tamper_group_outcome_reds_e2_only():
    combo = _combo("live_run-whole_bank-split")
    def mutate(views):
        views["E2"]["groups"][0]["outcome"] = "not_improved"
    report = _compare(combo, _tampered(combo, mutate),
                      load_baseline(BASELINE_DIR, combo))
    _expected_red(report, "E2")
    assert "groups" in report["criteria"]["E2"]["diff"]


# ---------------------------------------------------------------------------
# E5: the crash-resume variant machinery
# ---------------------------------------------------------------------------

def test_resume_variants_shape():
    exam = resume_variants("exam", 18)
    assert [v["kind"] for v in exam] == ["before_call"] * 3
    assert [v["n"] for v in exam] == crash_points(18)
    live = resume_variants("live_run", 15)
    assert [v["kind"] for v in live] == (["before_call"] * 3
                                         + ["revision_window"])
    assert [v["n"] for v in live if v["kind"] == "before_call"] \
        == crash_points(15)


def test_baseline_provenance_matches_the_variant_formula():
    provenance = json.loads(
        (BASELINE_DIR / "provenance.json").read_text(encoding="utf-8"))
    for label, row in provenance["combos"].items():
        assert row["crash_points"] == crash_points(row["model_calls"]), label


@pytest.mark.parametrize("combo", COMBOS, ids=[combo_label(c) for c in COMBOS])
def test_e5_all_variants_equivalent(combo, tmp_path):
    """The public E5 entry, per combo, under today's code: every
    crash-resume variant (three call-boundary fractions; live combos add
    the C-1 revision window) must land on the straight run's snapshot,
    and the straight run's model-call count must equal the LAYOUT-AWARE
    expectation (C-2): the 69904f7 provenance count for a v3 run, that
    count minus the (examiner, EXAM_GEN) calls for a v4 live run
    (whitelist 1). This cross-check is the anti-drift gate -- any other
    count would move every crash point -- so M4.3 updates the
    expectation, never deletes the check."""
    config = write_config(tmp_path / "config.yaml", combo)
    result = run_e5(config, combo, tmp_path / "work")
    assert result["model_calls"] == expected_model_calls(
        combo, result["layout"])
    expected = {variant["name"]: "equivalent"
                for variant in resume_variants(combo["acceptance"],
                                               result["model_calls"])}
    assert result["variants"] == expected


def test_expected_model_calls_is_layout_aware():
    """C-2's formula, pinned ahead of M4.3: the v3 provenance count for
    a v3 run (and for exam combos, whose v4 sequence keeps the A
    phases); for a v4 live run exactly the (examiner, EXAM_GEN) calls
    fewer -- the whitelist-1 delta, derived from the baseline bundle
    rather than hardcoded."""
    for combo in COMBOS:
        v3 = expected_model_calls(combo, "v3")
        if combo["acceptance"] == "live_run":
            baseline = load_baseline(BASELINE_DIR, combo)
            exam_gen = sum(1 for call in baseline["E4"]["calls"]
                           if call["role"] == "examiner"
                           and call["prompt"] == "EXAM_GEN")
            assert exam_gen > 0, combo_label(combo)
            assert expected_model_calls(combo, "v4") == v3 - exam_gen
        else:
            # Exam combos keep their A phases under v4: no subtraction.
            assert expected_model_calls(combo, "v4") == v3


def test_run_layout_reads_the_artifact_shape(tmp_path):
    assert run_layout(tmp_path) == "v3"
    (tmp_path / "phases").mkdir()
    assert run_layout(tmp_path) == "v4"


def test_revision_window_crash_lands_mid_reanswer(tmp_path):
    """The window crash's meaning, checked at the crash point itself
    (today's v3 layout): the revision IS in the skills store and NOTHING
    of the re-answer round has persisted (zero round-1 trajectories --
    the K1 dispatch stops at the raise). Resuming must then land on the
    straight run's snapshot."""
    from tests.golden.harness import assert_resume_equivalent, snapshot
    from tests.golden.semantics import _crash_in_revision_window

    combo = _combo("live_run-whole_bank-split")
    config = write_config(tmp_path / "config.yaml", combo)
    with installed_plugin(plugin_for(combo)):
        run_straight(config, tmp_path / "straight")
        golden = snapshot(tmp_path / "straight")
    _crash_in_revision_window(config, combo, tmp_path / "run")
    skills = (tmp_path / "run" / "stage3_generation" / "skills")
    revised = [path.name for path in skills.glob("*.json")
               if "REVISED-BY-G11" in path.read_text(encoding="utf-8")]
    assert revised
    round01 = (tmp_path / "run" / "stage4_acceptance" / "live_run"
               / "round01" / "stage1_baseline" / "trajectories")
    assert list(round01.glob("*.json")) == []
    with installed_plugin(plugin_for(combo)):
        run_straight(config, tmp_path / "run")
    assert_resume_equivalent(golden, snapshot(tmp_path / "run"))
