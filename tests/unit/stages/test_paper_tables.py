"""Paper tables: reading finished runs, comparing arms, rendering.

The runs are built by hand (tests/helpers/paper_runs.py) through the real
answer-sheet writers, with ``phase_summary.json`` computed the way the test
phase computes it.  The table renderer is checked against one literal
markdown text (the protocol surface); the data is chosen so the bootstrap
intervals are fixed by the task differences, not by the seed.
"""

import json
from pathlib import Path

import pytest

from expedistill.stages import paper_tables as pt
from tests.helpers.paper_runs import write_test_run

S, F, INVALID, MISSING, HOLE = "success", "failure", "invalid", "missing", "hole"


def _all(verdict, tasks=("t1", "t2", "t3", "t4"), n=3):
    return {task: [verdict] * n for task in tasks}


def _main_runs(root):
    """Two arms over four tasks with three trials each; both controls are
    bare-model zeros.  Ours: 2/3, 2/3, 0, 0.  Base: 1, 1, 0, 0."""
    ours = write_test_run(
        root, name="ours",
        with_bank={"t1": [S, S, F], "t2": [S, S, F],
                   "t3": [F, F, F], "t4": [F, F, F]},
        control=_all(F))
    base = write_test_run(
        root, name="base",
        with_bank={"t1": [S, S, S], "t2": [S, S, S],
                   "t3": [F, F, F], "t4": [F, F, F]},
        control=_all(F))
    return ours, base


def _spec(ours, base, *, order=("Ours", "Base")):
    runs = {"Ours": str(ours), "Base": str(base)}
    return {"main": {"alfworld": {label: runs[label] for label in order}},
            "references": {"main": "Ours"}}


# ---------------------------------------------------------------------------
# load_arm
# ---------------------------------------------------------------------------

def test_load_arm_numbers_equal_the_phase_summary(tmp_path):
    run = write_test_run(
        tmp_path,
        with_bank={"t1": [S, S, F], "t2": [F, F, F],
                   "t3": [S, INVALID, MISSING],
                   "t4": [INVALID, MISSING, HOLE]},
        control={"t1": [F, F, S], "t2": [F, F, F], "t3": [S, S, S],
                 "t4": [F, F, F]})

    arm = pt.load_arm(run)

    # t1 2/3, t2 0, t3 1 (one scored trial), t4 has no scored trial
    assert arm["with_bank"] == {
        "alfworld:t1": [1.0, 1.0, 0.0], "alfworld:t2": [0.0, 0.0, 0.0],
        "alfworld:t3": [1.0], "alfworld:t4": []}
    assert arm["control"]["alfworld:t1"] == [0.0, 0.0, 1.0]
    summary = json.loads(
        (run / "phases" / "p03_test" / "phase_summary.json").read_text())
    from expedistill.composites import paired_stats
    for key in ("with_bank", "control"):
        own = paired_stats.arm_metrics(arm[key])
        for metric in ("avg_at_n", "avg_at_n_conservative", "pass_at_n"):
            assert own[metric] == pytest.approx(summary[key][metric])
    # hand-computed
    assert summary["with_bank"]["avg_at_n"] == pytest.approx(5 / 9)
    assert summary["with_bank"]["avg_at_n_conservative"] == pytest.approx(5 / 12)
    assert summary["with_bank"]["pass_at_n"] == pytest.approx(2 / 4)
    assert summary["control"]["avg_at_n"] == pytest.approx(1 / 3)
    assert summary["control"]["pass_at_n"] == pytest.approx(2 / 4)


def test_unscored_trials_are_listed_and_never_scored_zero(tmp_path):
    run = write_test_run(
        tmp_path,
        with_bank={"t1": [S, INVALID, MISSING, HOLE]},
        control={"t1": [F, F, F, F]})

    arm = pt.load_arm(run)

    assert arm["with_bank"]["alfworld:t1"] == [1.0]
    assert arm["meta"]["unscored"]["with_bank"] == [
        {"task": "alfworld:t1", "attempt": 1, "reason": "invalid"},
        {"task": "alfworld:t1", "attempt": 2, "reason": "missing"},
        {"task": "alfworld:t1", "attempt": 3, "reason": "accepted_hole"}]
    assert arm["meta"]["unscored"]["control"] == []
    assert arm["meta"]["n_trials"] == {"with_bank": 1, "control": 4}
    assert arm["meta"]["expected_attempts"] == {"with_bank": 4, "control": 4}


def test_load_arm_reports_the_run_identity(tmp_path):
    run = write_test_run(tmp_path, benchmark="searchqa", model="m-1",
                         with_bank=_all(S, ("t1",)), control=_all(F, ("t1",)))

    meta = pt.load_arm(run)["meta"]

    assert (meta["benchmark"], meta["phase_label"], meta["delivery"]) \
        == ("searchqa", "p03:test", "whole_bank")
    assert meta["role"] == "weak"
    assert meta["model"] == "weak-model"
    assert meta["control_source"] == "fresh_bare"
    assert meta["n_tasks"] == 1 and meta["complete"] is True


def test_an_external_campaign_control_is_read_like_any_control(tmp_path):
    run = write_test_run(
        tmp_path, external_control=True,
        with_bank={"t1": [S, S, F], "t2": [F, F, F]},
        control={"t1": [S, F, F], "t2": [F, F, S]})

    arm = pt.load_arm(run)

    assert arm["control"] == {"alfworld:t1": [1.0, 0.0, 0.0],
                              "alfworld:t2": [0.0, 0.0, 1.0]}
    assert arm["meta"]["control_source"] == "external_campaign"


def test_a_summary_that_disagrees_with_the_trials_is_refused(tmp_path):
    run = write_test_run(tmp_path, with_bank=_all(S, ("t1",)),
                         control=_all(F, ("t1",)))
    path = run / "phases" / "p03_test" / "phase_summary.json"
    summary = json.loads(path.read_text())
    summary["with_bank"]["avg_at_n"] = 0.5
    path.write_text(json.dumps(summary))

    with pytest.raises(ValueError, match="with_bank arm's avg_at_n"):
        pt.load_arm(run)


def test_several_test_phases_need_a_label(tmp_path):
    run = write_test_run(
        tmp_path, sequence=("baseline", "test", "learn", "final"),
        test_phases=("test", "final"), data_phase="final",
        with_bank=_all(S, ("t1",)), control=_all(F, ("t1",)))

    with pytest.raises(ValueError, match="2 test phases"):
        pt.load_arm(run)
    assert pt.load_arm(run, "p04:final")["meta"]["phase_label"] == "p04:final"
    with pytest.raises(ValueError, match="not a test phase"):
        pt.load_arm(run, "p03:learn")
    # the other test phase has no artifacts: it has not finished
    with pytest.raises(ValueError, match="has not finished"):
        pt.load_arm(run, "p02:test")


def test_a_directory_that_is_not_a_run_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not a run directory"):
        pt.load_arm(tmp_path)


def test_load_arm_never_writes_into_the_run(tmp_path):
    run = write_test_run(tmp_path, with_bank=_all(S, ("t1",)),
                         control=_all(F, ("t1",)))
    before = {path: path.stat().st_mtime_ns
              for path in run.rglob("*") if path.is_file()}

    pt.load_arm(run)

    after = {path: path.stat().st_mtime_ns
             for path in run.rglob("*") if path.is_file()}
    assert before == after


# ---------------------------------------------------------------------------
# compare_arms
# ---------------------------------------------------------------------------

def test_compare_arms_against_own_control_and_the_reference(tmp_path):
    ours, base = _main_runs(tmp_path)
    arms = {"Ours": pt.load_arm(ours), "Base": pt.load_arm(base)}

    result = pt.compare_arms(arms, reference="Ours", n_boot=2000, seed=0,
                             n_resample=100)

    assert result["reference"] == "Ours"
    ours_row, base_row = result["arms"]["Ours"], result["arms"]["Base"]
    assert ours_row["vs_reference"] is None
    assert ours_row["vs_control"]["avg_at_n"]["delta"] == pytest.approx(1 / 3)
    assert base_row["vs_control"]["avg_at_n"]["delta"] == pytest.approx(0.5)
    # Base minus Ours per task: +1/3, +1/3, 0, 0
    reference = base_row["vs_reference"]["avg_at_n"]
    assert reference["delta"] == pytest.approx(1 / 6)
    assert (reference["wins"], reference["losses"], reference["ties"]) \
        == (2, 0, 2)
    assert base_row["vs_reference"]["pass_at_n"]["delta"] == pytest.approx(0.0)
    assert ours_row["noise_floor"]["sd"] == 0.0


def test_compare_arms_without_a_reference_and_with_unsplittable_control(tmp_path):
    run = write_test_run(tmp_path, with_bank=_all(S, ("t1", "t2"), 1),
                         control=_all(F, ("t1", "t2"), 1))

    result = pt.compare_arms({"Only": pt.load_arm(run)})

    assert result["reference"] is None
    assert result["arms"]["Only"]["vs_reference"] is None
    # one trial per task: the control cannot be split into two halves
    assert result["arms"]["Only"]["noise_floor"] is None


def test_compare_arms_rejects_an_unknown_reference(tmp_path):
    run = write_test_run(tmp_path, with_bank=_all(S, ("t1",)),
                         control=_all(F, ("t1",)))

    with pytest.raises(ValueError, match="reference arm 'Nope'"):
        pt.compare_arms({"Only": pt.load_arm(run)}, reference="Nope")


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------

_FOOTER_AVG = (
    "Metrics: avg@N: mean over tasks of the per-task mean of scored trials "
    "(conditional: a task with no scored trial is left out of that "
    "comparison). Delta: paired difference of per-task scores against the "
    "control of the same run and against Ours; [95% CI]: percentile "
    "bootstrap over tasks (2000 resamples, seed 0). W/L/T: tasks where the "
    "arm scores higher / lower / exactly equal. Control noise SD: SD of the "
    "difference between two random halves of the control's own trials (200 "
    "resamples); an effect inside it cannot be told from rerun noise.")

_EXPECTED_MARKDOWN = f"""\
## main

### main / alfworld

| Arm | avg@N (delta vs control [95% CI]) | W/L/T vs control | control avg@N | control noise SD | delta vs Ours [95% CI] | W/L/T vs Ours |
|---|---|---|---|---|---|---|
| Ours | 0.333 (+0.333 [+0.000, +0.667]) | 2/0/2 | 0.000 | 0.000 | - | - |
| Base | 0.500 (+0.500 [+0.000, +1.000]) | 2/0/2 | 0.000 | 0.000 | +0.167 [+0.000, +0.333] | 2/0/2 |

{_FOOTER_AVG}
"""


def test_render_markdown_matches_the_literal_table(tmp_path):
    ours, base = _main_runs(tmp_path)
    spec = _spec(ours, base)

    results = pt.build_results(spec, n_boot=2000, seed=0, n_resample=200)

    assert pt.render_markdown(spec, results) == _EXPECTED_MARKDOWN


def test_pass_at_n_table_shows_the_mcnemar_column(tmp_path):
    ours, base = _main_runs(tmp_path)
    spec = _spec(ours, base)
    results = pt.build_results(spec, n_boot=2000, seed=0, n_resample=200)

    text = pt.render_markdown(spec, results, metric="pass_at_n")

    assert "McNemar p vs control" in text
    # Ours passes t1, t2 and the control passes nothing: 2 discordant, p = 2/4
    assert "| Ours | 0.500 (+0.500 [+0.000, +1.000]) | 2/0/2 | 0.000 | 0.500 |" \
        in text
    assert "McNemar p: exact two-sided test" in text


def test_rows_and_tables_follow_the_spec_not_the_results(tmp_path):
    ours, base = _main_runs(tmp_path)
    other = write_test_run(tmp_path, name="other", benchmark="searchqa",
                           with_bank=_all(F), control=_all(S))
    spec = {"zeta": {"searchqa": {"Worst": str(other)}},
            "main": {"alfworld": {"Base": str(base), "Ours": str(ours)}},
            "references": {"main": "Ours"}}

    results = pt.build_results(spec, n_boot=500, seed=0, n_resample=50)
    text = pt.render_markdown(spec, results)

    assert text.index("## zeta") < text.index("## main")
    assert text.index("| Base |") < text.index("| Ours |")
    # the arm that loses to its control is listed where the spec put it,
    # and nothing is bolded or ranked
    assert "**" not in text
    assert "| Worst | 0.000 (-1.000" in text


def test_notes_name_unscored_trials_and_dropped_tasks(tmp_path):
    run = write_test_run(
        tmp_path, name="holes",
        with_bank={"t1": [S, S, S], "t2": [INVALID, MISSING, HOLE]},
        control={"t1": [F, F, F], "t2": [F, F, F]})
    spec = {"main": {"alfworld": {"Holey": str(run)}}}

    results = pt.build_results(spec, n_boot=200, seed=0, n_resample=50)
    text = pt.render_markdown(spec, results)

    assert "Unscored trials (left out, never scored 0): Holey 3 with-bank, " \
        "0 control." in text
    assert "Tasks with no scored trial in one of the two compared arms are " \
        "left out of that delta: Holey 1 vs control." in text
    # the arm's own conditional average ignores t2, the conservative one
    # keeps it as 0 (so the metric is visible in the JSON)
    arm = results["experiments"]["main"]["benchmarks"]["alfworld"]["arms"][
        "Holey"]
    assert arm["summary"]["with_bank"]["avg_at_n"] == 1.0
    assert arm["summary"]["with_bank"]["avg_at_n_conservative"] == 0.5
    assert arm["vs_control"]["avg_at_n"]["n_tasks"] == 1
    assert arm["vs_control"]["avg_at_n_conservative"]["n_tasks"] == 2


def test_render_markdown_rejects_an_unknown_metric(tmp_path):
    ours, base = _main_runs(tmp_path)
    spec = _spec(ours, base)
    results = pt.build_results(spec, n_boot=100, seed=0, n_resample=50)

    with pytest.raises(ValueError, match="metric must be one of"):
        pt.render_markdown(spec, results, metric="best_of_three")


# ---------------------------------------------------------------------------
# The spec
# ---------------------------------------------------------------------------

def test_a_run_under_the_wrong_benchmark_is_refused(tmp_path):
    ours, _ = _main_runs(tmp_path)
    spec = {"main": {"searchqa": {"Ours": str(ours)}}}

    with pytest.raises(ValueError, match="'alfworld' run"):
        pt.build_results(spec, n_boot=100)


@pytest.mark.parametrize("spec, message", [
    ([], "non-empty JSON object"),
    ({}, "non-empty JSON object"),
    ({"references": {}}, "names no experiment"),
    ({"main": []}, "benchmark -> arms"),
    ({"main": {"alfworld": {}}}, "arm label -> run dir"),
    ({"main": {"alfworld": {"A": ""}}}, "non-empty string"),
    ({"main": {"alfworld": {"A": "x"}}, "references": {"main": "B"}},
     "not an arm"),
    ({"main": {"alfworld": {"A": "x"}}, "references": {"other": "A"}},
     "which the spec does not have"),
    ({"main": {"alfworld": {"A": "x"}, "searchqa": {"B": "y"}},
      "references": {"main": "A"}}, "every benchmark"),
    ({"main": {"alfworld": {"A": "x"}}, "references": []},
     "must map experiment"),
])
def test_a_malformed_spec_is_refused(spec, message):
    with pytest.raises(ValueError, match=message):
        pt.build_results(spec)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def test_results_json_carries_every_metric_and_the_per_task_scores(tmp_path):
    ours, base = _main_runs(tmp_path)
    spec = _spec(ours, base)
    results = pt.build_results(spec, n_boot=500, seed=3, n_resample=50)
    out = tmp_path / "analysis" / "tables.json"

    pt.write_results_json(results, out, run_dirs=[ours, base])

    loaded = json.loads(out.read_text())
    assert loaded["settings"] == {"n_boot": 500, "seed": 3, "level": 0.95,
                                  "n_resample": 50}
    base_row = loaded["experiments"]["main"]["benchmarks"]["alfworld"][
        "arms"]["Base"]
    assert set(base_row["vs_control"]) == set(pt.METRICS)
    assert set(base_row["vs_reference"]) == set(pt.METRICS)
    assert base_row["scores"]["with_bank"]["alfworld:t1"] == [1.0, 1.0, 1.0]
    assert base_row["meta"]["benchmark"] == "alfworld"
    assert loaded["experiments"]["main"]["reference"] == "Ours"


def test_the_same_settings_reproduce_the_same_json(tmp_path):
    ours, base = _main_runs(tmp_path)
    spec = _spec(ours, base)

    first = pt.build_results(spec, n_boot=300, seed=5, n_resample=50)
    again = pt.build_results(spec, n_boot=300, seed=5, n_resample=50)

    assert first == again


@pytest.mark.parametrize("relative", ["tables.json", "phases/tables.json",
                                      "."])
def test_output_inside_a_run_directory_is_refused(tmp_path, relative):
    ours, base = _main_runs(tmp_path)
    spec = _spec(ours, base)
    results = pt.build_results(spec, n_boot=100, seed=0, n_resample=50)
    target = ours / relative
    snapshot = sorted(path.name for path in ours.rglob("*"))

    with pytest.raises(ValueError, match="inside the run directory"):
        pt.write_results_json(results, target, run_dirs=[ours, base])
    with pytest.raises(ValueError, match="inside the run directory"):
        pt.write_markdown("x", target, run_dirs=[ours, base])
    assert sorted(path.name for path in ours.rglob("*")) == snapshot


def test_run_refuses_an_output_path_inside_a_run_before_reading_anything(
        tmp_path):
    spec_path = tmp_path / "spec.json"
    # the run directories do not exist: the output check must come first
    fake = tmp_path / "not_yet_a_run"
    spec_path.write_text(json.dumps(
        {"main": {"alfworld": {"A": str(fake)}}}))

    with pytest.raises(ValueError, match="inside the run directory"):
        pt.run(spec_path, out_json=fake / "out.json")
    assert not fake.exists()


def test_run_writes_both_files_and_returns_the_markdown(tmp_path):
    ours, base = _main_runs(tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_spec(ours, base)))
    out_md = tmp_path / "out" / "tables.md"
    out_json = tmp_path / "out" / "tables.json"

    text = pt.run(spec_path, out_md=out_md, out_json=out_json, n_boot=2000,
                  seed=0)

    assert out_md.read_text() == text
    assert text.startswith("## main\n")
    assert json.loads(out_json.read_text())["settings"]["n_boot"] == 2000
    assert not list(Path(out_md.parent).glob("*.tmp"))


def test_an_arm_with_another_executor_model_is_flagged_against_the_reference(
        tmp_path):
    ours, _ = _main_runs(tmp_path)
    other = write_test_run(
        tmp_path, name="other_model", model="other", with_bank=_all(S),
        control=_all(F))
    manifest_path = other / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["config"]["models"]["executor_weak"] = "other-weak-model"
    manifest_path.write_text(json.dumps(manifest))
    spec = {"teacher_student": {"alfworld": {
        "D->D": str(ours), "Q->D": str(other)}},
        "references": {"teacher_student": "D->D"}}

    results = pt.build_results(spec, n_boot=100, seed=0, n_resample=50)
    text = pt.render_markdown(spec, results)

    assert "These arms ran another executor model than D->D (weak-model), " \
        "so their delta vs D->D compares different models: " \
        "Q->D (other-weak-model)." in text


def test_the_confidence_level_in_the_headers_follows_the_settings(tmp_path):
    ours, base = _main_runs(tmp_path)
    spec = _spec(ours, base)

    results = pt.build_results(spec, n_boot=100, seed=0, level=0.9,
                               n_resample=50)
    text = pt.render_markdown(spec, results)

    assert "[90% CI]" in text and "[95% CI]" not in text
