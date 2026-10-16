"""The M4.1 instrument self-check, re-based on the landed phase world
(Wave 4): identity proofs + tamper proofs + the pinned disclosure.

The instrument (tests/golden/semantics.py) is only trustworthy if every
assertion can actually fail on the difference it claims to catch. This
suite proves exactly that:

* identity -- re-extracting the baseline from commit 69904f7 reproduces
  the committed bundle byte for byte, the regenerated golden matrix's
  phase-world layout canonicalises onto the canonical keys, and a fresh
  phase-world run lands on the pinned Wave 4 disclosure (the recorded
  difference set versus the archived baseline);
* disclosure -- the E1-E6 acceptance artifact (tests/fixtures/
  golden_acceptance/) is generated from the committed fixtures and
  pinned byte for byte: the red criteria are the disclosed differences
  (root-caused in the artifact itself), and any FURTHER drift -- a new
  difference, or a disclosed one regressing -- fails the suite;
* tamper -- eleven targeted corruptions of in-memory bundle copies (the
  committed one is never touched); each must turn its criterion red and
  point at the corrupted item, and leave the unrelated criteria green;
* bridge -- synthetic snapshot pairs pin the branches the golden matrix
  can never reach (P6): the E2 unborn mapping across layouts (aligned
  forms fully equal, misaligned forms red with a pointed diff);
* E5 -- the crash-variant list has the required shape and every variant,
  including the "revision persisted, re-answer not yet run" window (C-1),
  resumes equivalent on every combo under today's code, and the straight
  call count equals the layout-aware expectation (C-2).

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
    snapshot,
    write_config,
)
from tests.golden.semantics import (
    ACCEPTANCE_DIR,
    ACCEPTANCE_NAME,
    BANK,
    BASELINE_DIR,
    GENERATION_FAILURES,
    LEARN_OUTCOMES,
    LEARN_REVISIONS,
    LEARN_TASK_LIST,
    LIVE_ATTRIBUTIONS,
    LIVE_VERDICTS,
    REQUESTS,
    RETRIEVAL_AUDIT,
    SEQUENCES,
    TEST_SUMMARY,
    canonicalize,
    combo_disclosure,
    compare_combo,
    compare_views,
    crash_points,
    disclosure_report,
    expected_model_calls,
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
# The layout table (data -- pin the landed phase-world paths)
# ---------------------------------------------------------------------------

def test_v4_layout_pins_the_phase_world_paths():
    exam = v4_layout("exam")
    assert set(exam) == {
        "phases/p09_C/phase_summary.json",
        "phases/p08_L/skill_bank.jsonl",
        "phases/p03_A/generation_failures.jsonl",
        "phases/p05_A/generation_failures.jsonl",
        "phases/p07_A/generation_failures.jsonl",
        "phases/p02_L/task_list.jsonl", "phases/p02_L/revisions.jsonl",
        "phases/p02_L/outcomes.jsonl",
        "phases/p04_L/task_list.jsonl", "phases/p04_L/revisions.jsonl",
        "phases/p04_L/outcomes.jsonl",
        "phases/p06_L/task_list.jsonl", "phases/p06_L/revisions.jsonl",
        "phases/p06_L/outcomes.jsonl",
        "phases/p08_L/task_list.jsonl", "phases/p08_L/revisions.jsonl",
        "phases/p08_L/outcomes.jsonl",
    }
    # The bank is the LAST learn phase's artifact (R7); the C summary is
    # the execute phase's phase_summary.json; the generation failures
    # live in the A phases (the R8 ruling moved G3 there), keyed exam_id.
    assert exam["phases/p08_L/skill_bank.jsonl"] == (BANK, None, 7)
    assert exam["phases/p09_C/phase_summary.json"] == (TEST_SUMMARY, None, 0)
    assert exam["phases/p03_A/generation_failures.jsonl"] == (
        GENERATION_FAILURES, None, 2)
    assert exam["phases/p07_A/generation_failures.jsonl"] == (
        GENERATION_FAILURES, None, 6)
    live = v4_layout("live_run")
    assert set(live) == {
        "phases/p07_C/phase_summary.json",
        "phases/p06_L/skill_bank.jsonl",
        "phases/p02_L/task_list.jsonl", "phases/p02_L/revisions.jsonl",
        "phases/p02_L/outcomes.jsonl",
        "phases/p04_L/task_list.jsonl", "phases/p04_L/revisions.jsonl",
        "phases/p04_L/outcomes.jsonl",
        "phases/p06_L/task_list.jsonl", "phases/p06_L/revisions.jsonl",
        "phases/p06_L/outcomes.jsonl",
    }
    assert live["phases/p06_L/skill_bank.jsonl"] == (BANK, None, 5)
    assert live["phases/p04_L/revisions.jsonl"] == (LEARN_REVISIONS, None, 3)
    assert live["phases/p04_L/outcomes.jsonl"] == (LEARN_OUTCOMES, None, 3)
    assert live["phases/p02_L/task_list.jsonl"] == (LEARN_TASK_LIST, None, 1)
    # The run-level artifacts never moved (P4): no mapping may touch the
    # live verdicts, the retrieval audit or the request history.
    for table in (exam, live):
        for run_level in (LIVE_VERDICTS, RETRIEVAL_AUDIT, REQUESTS):
            assert run_level not in table
            assert run_level not in table.values()


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
def test_canonicalize_maps_the_fixture_onto_the_canonical_keys(combo):
    """The regenerated matrix IS the phase-world shape: canonicalising
    it must produce the canonical keys the views read -- the last learn
    phase's bank onto the canonical bank, the C phase's summary onto the
    canonical test summary, the learn ledgers onto their canonical names
    -- while the run-level artifacts pass through unmoved (P4)."""
    canonical = canonicalize(_golden_snapshot(combo),
                             combo["acceptance"], "v4")
    assert BANK in canonical
    assert TEST_SUMMARY in canonical
    # The learn ledgers: every L phase writes its task list and outcome
    # receipts; only a revise writes revision-plan rows (the exam combos
    # pass every skill on round one, so the golden matrix's exam
    # fixtures carry none).
    assert LEARN_TASK_LIST in canonical
    assert LEARN_OUTCOMES in canonical
    if combo["acceptance"] == "live_run":
        assert LEARN_REVISIONS in canonical
    raw = _golden_snapshot(combo)
    if LIVE_VERDICTS in raw:
        assert canonical[LIVE_VERDICTS] == raw[LIVE_VERDICTS]
    if RETRIEVAL_AUDIT in raw:
        assert canonical[RETRIEVAL_AUDIT] == raw[RETRIEVAL_AUDIT]


def test_canonicalize_leaves_v3_snapshots_untouched():
    snapshot_data = {"stage6_test/stage6_summary.json": {"x": 1}}
    assert canonicalize(snapshot_data, "live_run", "v3") is snapshot_data


def test_canonicalize_concatenates_generation_failure_shards():
    """The A phases' generation-failure shards concatenate onto the one
    canonical ledger in phase order -- a later shard must not silently
    drop an earlier one (the branch is unreachable on the golden matrix,
    whose ledgers are empty; this is its pin)."""
    shard_p03 = [{"exam_id": "traj/fakebench:t1/strong/00/seg00/exam",
                  "error_class": "ModelOutputError", "message": "a"}]
    shard_p07 = [{"exam_id": "traj/fakebench:t1/strong/00/seg01/exam",
                  "error_class": "ModelOutputError", "message": "b"},
                 {"exam_id": "traj/fakebench:t1/weak/00/seg00/exam",
                  "error_class": "ModelOutputError", "message": "c"}]
    snapshot_data = {  # insertion order deliberately not phase order
        "phases/p07_A/generation_failures.jsonl": shard_p07,
        "phases/p03_A/generation_failures.jsonl": shard_p03,
    }
    merged = canonicalize(snapshot_data, "exam", "v4")
    assert merged[GENERATION_FAILURES] == shard_p03 + shard_p07
    assert len(merged[GENERATION_FAILURES]) == 3


# ---------------------------------------------------------------------------
# The pinned Wave 4 disclosure (E1-E6 vs the archived baseline)
# ---------------------------------------------------------------------------

def _committed_disclosure() -> dict:
    return json.loads((ACCEPTANCE_DIR / ACCEPTANCE_NAME).read_text(
        encoding="utf-8"))


def test_wave4_disclosure_report_is_pinned():
    """The acceptance artifact, regenerated from the committed fixtures,
    equals the committed disclosure byte for byte. The red criteria are
    the disclosed differences (every one root-caused in the artifact);
    anything else -- a NEW difference, or a disclosed one regressing --
    fails here. This is the disclosure discipline: the differences are
    recorded and machine-pinned, never silently absorbed."""
    assert disclosure_report() == _committed_disclosure()


@pytest.mark.parametrize(
    "label", ("exam-retrieval-split", "live_run-whole_bank-full"))
def test_fresh_run_lands_on_the_pinned_disclosure(label, tmp_path):
    """The run-directory entry point: a fresh phase-world run's
    disclosure section equals the committed (fixture-derived) one, so
    the pinned artifact describes real runs, not just fixtures."""
    combo = _combo(label)
    config = write_config(tmp_path / "config.yaml", combo)
    with installed_plugin(plugin_for(combo)):
        run_straight(config, tmp_path / "run")
    report = compare_combo(combo, tmp_path / "run", BASELINE_DIR,
                           layout="v4")
    section = combo_disclosure(
        combo, canonicalize(snapshot(tmp_path / "run"),
                            combo["acceptance"], "v4"))
    assert section == _committed_disclosure()["combos"][label]
    assert any(not result["passed"] for result in report["criteria"].values())


def test_disclosure_records_every_red_criterion_with_a_full_diff():
    """Every red criterion carries its complete missing/extra
    enumeration -- a disclosure that only names the first offender would
    hide the size of the difference set."""
    report = _committed_disclosure()
    for label, section in report["combos"].items():
        red = sorted(name for name, result in section["criteria"].items()
                     if not result["passed"])
        assert sorted(section["full_diffs"]) == red, label
        for name in red:
            assert section["criteria"][name]["diff"], (label, name)


# ---------------------------------------------------------------------------
# Tamper proofs: every assertion must be able to fail, pointedly
# ---------------------------------------------------------------------------

def _tampered(combo: dict, mutate) -> dict:
    views = json.loads(json.dumps(_baseline_views(combo)))
    mutate(views)
    return views


def _baseline_views(combo: dict) -> dict:
    return json.loads(
        (BASELINE_DIR / f"{combo_label(combo)}.json").read_text(
            encoding="utf-8"))


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
                      _baseline_views(combo))
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert failed == ["E1"]
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
                      _baseline_views(combo))
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert failed == ["E1", "E2"]
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
                      _baseline_views(combo))
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert failed == ["E6"]
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
                      _baseline_views(combo))
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert failed == ["E6"]


def _live_combo_snapshot() -> dict:
    """A minimal live-combo snapshot in the v3 canonical key space: just
    enough shape for every view to project (the bank, the C summary, the
    request history and the live ledgers)."""
    return {
        BANK: [],
        TEST_SUMMARY: {"with_bank": {"tasks": 0}, "control": {"tasks": 0},
                       "delta": {}, "control_source": "fresh_bare_run",
                       "aux": None},
        REQUESTS: [],
        LIVE_VERDICTS: [],
        LIVE_ATTRIBUTIONS: [],
    }


def test_tamper_snapshot_verdict_deletion_couples_e6_and_e2():
    # Same corruption one level up, on the artifact snapshot both
    # criteria project: the final-round verdict row is gone, so E6 loses
    # its row AND E2's derived group view loses its outcome.
    combo = _combo("live_run-whole_bank-split")
    snapshot_data = _live_combo_snapshot()
    snapshot_data[LIVE_VERDICTS] = [
        {"schema": "live-verdict-v1", "task_id": "fakebench:t1",
         "round": 1, "rule": "strict_rate_gain",
         "baseline": {"valid": 2, "success": 0},
         "live": {"valid": 2, "success": 2}, "better": True,
         "injection_tokens": 10, "judged_with_shortfall": False,
         "aux": None}]
    fresh = semantic_views(snapshot_data, combo, "v3")
    golden = _baseline_views(combo)
    report = _compare(combo, golden, fresh)
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert "E2" in failed and "E6" in failed


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
    """One synthetic pair: the v3-shaped snapshot of ``combo`` with a
    generation_failures row for ``subtask_id`` (stage 3's pair save --
    no skill was stored) vs the phase-world snapshot of ``combo`` plus
    ``v4_extras`` store entries (the same event in the phase world's
    form: a skill born, then discarded with model_output_error). The
    phase world records the event in ONE form -- a store discard here,
    an A-phase failure row when nothing was ever born (pinned
    separately below) -- so the pair must not carry both. Returns
    (golden_views, fresh_views)."""
    v3 = _live_combo_snapshot()
    v3[GENERATION_FAILURES] = [{
        "subtask_id": subtask_id, "error_class": "ModelOutputError",
        "message": "rewrite budget exhausted"}]
    v4 = dict(_live_combo_snapshot())
    v4[LEARN_TASK_LIST] = []
    v4[LEARN_REVISIONS] = []
    v4[LEARN_OUTCOMES] = []
    v4.update(v4_extras)
    golden = semantic_views(v3, combo, "v3")
    fresh = semantic_views(canonicalize(v4, "exam", "v4"), combo, "v4")
    return golden, fresh


def test_e2_unborn_bridge_aligns_v3_failure_rows_with_v4_discards():
    """The plan's E2 mapping, end to end at snapshot level (P-A1/B-5):
    v3 records a G3-exhausted subtask as a generation_failures row
    (stage 3's pair save means no skill was stored); the phase world
    records the same event as a skills-store skill discarded with
    model_output_error (born in L, deleted after the A-phase exam
    failure; discards never enter the bank, so the store is their only
    durable home). The two forms must compare FULLY equal on the unborn
    multiset. The branch is unreachable in the golden matrix (P6: every
    fixture is all-accept, zero discard) -- this synthetic pair is its
    pin."""
    combo = _combo("exam-whole_bank-split")
    subtask_id = "traj/fakebench:t1/strong/00/seg07"  # not in the matrix
    skill_id = make_skill_id(subtask_id)
    golden, fresh = _bridge_views(
        combo, subtask_id,
        {_store_key(skill_id): _discarded_store_skill(skill_id,
                                                      subtask_id)})
    # The synthetic v4 store skill is a TERMINAL the bare v3 side lacks,
    # so E2's terminal_states legitimately differ -- the BRIDGE is the
    # unborn multiset, which must agree exactly.
    assert not _multiset_rows_differ(
        golden["E2"]["unborn"], fresh["E2"]["unborn"])


def test_generation_failure_exam_id_keys_bridge():
    """The phase world's other unborn form: the A-phase failure ledger
    keys ``exam_id`` (= subtask_id + '/exam', the R8 ruling) where v3
    keyed ``subtask_id``. A v3 row and the phase world's exam-id row
    project onto the SAME unborn entry, so the two snapshots compare
    fully equal under the v4 comparison (nothing was born on either
    side -- no store discard, no terminal difference)."""
    combo = _combo("exam-whole_bank-split")
    subtask_id = "traj/fakebench:t1/strong/00/seg07"
    v3 = _live_combo_snapshot()
    v3[GENERATION_FAILURES] = [{
        "subtask_id": subtask_id, "error_class": "ModelOutputError",
        "message": "rewrite budget exhausted"}]
    v4 = dict(_live_combo_snapshot())
    v4[LEARN_TASK_LIST] = []
    v4[LEARN_REVISIONS] = []
    v4[LEARN_OUTCOMES] = []
    v4[f"{phase_dir(SEQUENCES['exam'], 2)}/generation_failures.jsonl"] = [{
        "exam_id": subtask_id + "/exam",
        "error_class": "ModelOutputError",
        "message": "rewrite budget exhausted"}]
    golden = semantic_views(v3, combo, "v3")
    fresh = semantic_views(canonicalize(v4, "exam", "v4"), combo, "v4")
    failed = sorted(name for name, result in
                    compare_views(golden, fresh, combo,
                                  layout="v4")["criteria"].items()
                    if not result["passed"])
    assert failed == []


def test_generation_failure_row_keyed_neither_way_is_loud():
    # The unborn bridge reads both key spellings and refuses anything
    # else -- a row that keys neither subtask_id nor '<subtask>/exam'
    # must crash the view, not silently project an empty id.
    combo = _combo("exam-whole_bank-split")
    v3 = _live_combo_snapshot()
    v3[GENERATION_FAILURES] = [{
        "skill_id": "made/up", "error_class": "ModelOutputError"}]
    with pytest.raises(AssertionError, match="neither"):
        semantic_views(v3, combo, "v3")


def _multiset_rows_differ(want: list, got: list) -> bool:
    missing = list(want)
    for row in got:
        try:
            missing.remove(row)
        except ValueError:
            return True
    return bool(missing)


def test_tamper_v4_discard_key_misalignment_reds_e2():
    # The bridge's negative case: a phase-world discard naming a
    # DIFFERENT subtask's skill does not line up with the v3 row -- both
    # unborn multisets lose their counterpart and E2 must turn red with
    # the diff pointing at the skill_id. This also pins the id
    # derivation: a wrong make_skill_id spelling in the view reds the
    # same way.
    combo = _combo("exam-whole_bank-split")
    other_subtask = "traj/fakebench:t1/strong/00/seg08"
    other_skill = make_skill_id(other_subtask)
    golden, fresh = _bridge_views(
        combo, "traj/fakebench:t1/strong/00/seg07",
        {_store_key(other_skill): _discarded_store_skill(
            other_skill, other_subtask)})
    report = compare_views(golden, fresh, combo, layout="v4")
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert "E2" in failed
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
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert "E2" in failed
    diff = report["criteria"]["E2"]["diff"]
    assert "unborn" in diff and "missing" in diff


def test_tamper_call_request_text_reds_e4():
    combo = _combo("exam-retrieval-split")
    def mutate(views):
        views["E4"]["calls"][0]["request_text"] += " TAMPERED"
    report = _compare(combo, _tampered(combo, mutate),
                      _baseline_views(combo))
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert failed == ["E4"]
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
    report = _compare(combo, _baseline_views(combo),
                      _tampered(combo, mutate), layout="v4")
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert failed == ["E4"]
    assert "EXAM_GEN" in report["criteria"]["E4"]["diff"]


def test_tamper_moved_retrieval_audit_row_reds_e3():
    # "Moving" an audit row to another task changes its content: the
    # multiset loses the old row and gains a new one. (Pure reordering is
    # NOT a difference -- rows are compared as a canonical multiset.)
    combo = _combo("exam-retrieval-split")
    def mutate(views):
        views["E3"]["retrieval_audit"][0]["task_id"] = "t1"
    report = _compare(combo, _tampered(combo, mutate),
                      _baseline_views(combo))
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert failed == ["E3"]
    assert "retrieval_audit" in report["criteria"]["E3"]["diff"]


def test_tamper_summary_field_reds_e3():
    combo = _combo("exam-whole_bank-split")
    def mutate(views):
        views["E3"]["with_bank"]["pass_at_n"] = 0.5
    report = _compare(combo, _tampered(combo, mutate),
                      _baseline_views(combo))
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert failed == ["E3"]
    assert "with_bank.pass_at_n" in report["criteria"]["E3"]["diff"]


def test_tamper_control_source_alias_is_canonicalised():
    """R9's label change is the pre-registered alias: the stage world's
    'stage1_reuse' and the phase world's 'D_reuse' are the SAME control
    arm, and a label outside the alias table passes through untouched
    (the loud direction -- it becomes a difference). The alias runs at
    view-build time, so this is a snapshot-level pin."""
    combo = _combo("exam-whole_bank-full")
    for label in ("stage1_reuse", "D_reuse", "D", "p01_D"):
        snapshot_data = _live_combo_snapshot()
        snapshot_data[TEST_SUMMARY]["control_source"] = label
        views = semantic_views(snapshot_data, combo, "v3")
        assert views["E3"]["control_source"] == "D_weak_arm", label
    snapshot_data = _live_combo_snapshot()
    snapshot_data[TEST_SUMMARY]["control_source"] = "p07_reuse"
    views = semantic_views(snapshot_data, combo, "v3")
    assert views["E3"]["control_source"] == "p07_reuse"


def test_tamper_unborn_entry_reds_e2_only():
    # The never-born mapping (v3 "unborn" <-> phase-world discarded
    # rows): a bogus unborn entry must fail E2 and leave E1 alone -- the
    # bank projection never sees it.
    combo = _combo("exam-whole_bank-split")
    def mutate(views):
        views["E2"]["unborn"].append(
            {"skill_id": "traj/fakebench:t9/strong/00/seg00/skill",
             "reason": "model_output_error"})
    report = _compare(combo, _tampered(combo, mutate),
                      _baseline_views(combo))
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert failed == ["E2"]
    assert "unborn" in report["criteria"]["E2"]["diff"]


def test_tamper_group_outcome_reds_e2_only():
    combo = _combo("live_run-whole_bank-split")
    def mutate(views):
        views["E2"]["groups"][0]["outcome"] = "not_improved"
    report = _compare(combo, _tampered(combo, mutate),
                      _baseline_views(combo))
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert failed == ["E2"]
    assert "groups" in report["criteria"]["E2"]["diff"]


def test_tamper_e6_verdict_field_reds_e6_but_tokens_alone_do_not():
    # The v4 comparator's one disclosed projection: the bank-derived
    # injection_tokens field is blanked on both sides (the bank's own
    # difference is disclosed by E1), every other verdict field stays
    # compared in full. A tampered token value passes; a tampered
    # better flag fails.
    combo = _combo("live_run-whole_bank-split")
    def mutate_tokens(views):
        views["E6"]["verdicts"][0]["injection_tokens"] = 999999
    report = _compare(combo, _baseline_views(combo),
                      _tampered(combo, mutate_tokens), layout="v4")
    failed = sorted(name for name, result in report["criteria"].items()
                    if not result["passed"])
    assert "E6" not in failed

    def mutate_better(views):
        views["E6"]["verdicts"][0]["better"] = not \
            views["E6"]["verdicts"][0]["better"]
    report = _compare(combo, _baseline_views(combo),
                      _tampered(combo, mutate_better), layout="v4")
    assert "E6" in sorted(
        name for name, result in report["criteria"].items()
        if not result["passed"])


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
    """The public E5 entry, per combo, under the phase world: every
    crash-resume variant (three call-boundary fractions; live combos add
    the C-1 revision window) must land on the straight run's snapshot,
    and the straight run's model-call count must equal the LAYOUT-AWARE
    expectation (C-2): the regenerated golden matrix's own provenance
    count for a v4 run (the phase world's deliberate count changes are
    pinned by that provenance, itself pinned byte-for-byte by the golden
    matrix tests), the archived 69904f7 count minus the (examiner,
    EXAM_GEN) calls for a hypothetical v3 live run. This cross-check is
    the anti-drift gate -- any other count would move every crash
    point -- so a count change updates the provenance WITH its golden
    matrix, never deletes the check."""
    config = write_config(tmp_path / "config.yaml", combo)
    result = run_e5(config, combo, tmp_path / "work")
    assert result["model_calls"] == expected_model_calls(
        combo, result["layout"])
    expected = {variant["name"]: "equivalent"
                for variant in resume_variants(combo["acceptance"],
                                               result["model_calls"])}
    assert result["variants"] == expected


def test_expected_model_calls_is_layout_aware():
    """C-2's formula, pinned: the v4 expectation is the regenerated
    golden provenance count (drift there moves every crash point); the
    v3 expectation stays the archived-baseline formula (the 69904f7
    count, minus the (examiner, EXAM_GEN) calls for live combos --
    whitelist 1), so the whitelist's delta stays machine-checked against
    the archived truth."""
    v4_provenance = json.loads(
        (GOLDEN_DIR / "provenance.json").read_text(encoding="utf-8"))
    for combo in COMBOS:
        label = combo_label(combo)
        assert expected_model_calls(combo, "v4") \
            == v4_provenance["combos"][label]["model_calls"]
        archived = json.loads(
            (BASELINE_DIR / "provenance.json").read_text(
                encoding="utf-8"))["combos"][label]["model_calls"]
        if combo["acceptance"] == "live_run":
            baseline = json.loads(
                (BASELINE_DIR / f"{label}.json").read_text(
                    encoding="utf-8"))
            exam_gen = sum(1 for call in baseline["E4"]["calls"]
                           if call["role"] == "examiner"
                           and call["prompt"] == "EXAM_GEN")
            assert exam_gen > 0, label
            assert expected_model_calls(combo, "v3") == archived - exam_gen
        else:
            assert expected_model_calls(combo, "v3") == archived


def test_run_layout_reads_the_artifact_shape(tmp_path):
    assert run_layout(tmp_path) == "v3"
    (tmp_path / "phases").mkdir()
    assert run_layout(tmp_path) == "v4"


def test_revision_window_crash_lands_mid_reanswer(tmp_path):
    """The window crash's meaning, checked at the crash point itself:
    the revision IS in the skills store and NOTHING of the re-answer
    round has persisted (zero round-1 trajectories -- the K1 dispatch
    stops at the raise). Resuming must then land on the straight run's
    snapshot."""
    from tests.golden.harness import assert_resume_equivalent
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
