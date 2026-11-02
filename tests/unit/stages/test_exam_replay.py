"""stages/exam_replay.py over a tiny finished source run.

The source run (tests/helpers/skill_use_runs.py) holds two learn banks and
four exams: alpha v1, beta v1 and gamma v1 belong to the p02 bank, alpha v2
and beta v1 to the p04 bank. The answering and judge models are scripted;
the answerer opens the cards a test names.
"""

import json

import pytest

from expedistill.core.ids import make_exam_id
from expedistill.core.models import Attempt
from expedistill.infra.reliability import ConfigError, InfrastructureError
from expedistill.infra.store import Store, read_json, read_jsonl
from expedistill.llm.fake import make_fake_handle
from expedistill.stages import exam_replay
from expedistill.stages.analysis_common import PINNED_NAME
from tests.helpers.skill_use_runs import (
    answerer_script,
    card_id,
    judge_script,
    snapshot,
    write_exam_source_run,
)

ALPHA, BETA, GAMMA = (card_id(name) for name in ("alpha", "beta", "gamma"))


def _opens_target_only(name, version):
    return [name]


@pytest.fixture
def source(tmp_path):
    run = tmp_path / "source"
    write_exam_source_run(run)
    return run


def _replay(source, out_dir, opens=_opens_target_only, *, answerer_name="D",
            crash_after=None, **kwargs):
    answerer = make_fake_handle(
        "answerer", answerer_script(opens, crash_after=crash_after),
        name=answerer_name)
    judge = make_fake_handle("judge", judge_script())
    summary = exam_replay.replay(source, out_dir, answerer, judge, **kwargs)
    return summary, answerer, judge


def _rows(out_dir):
    return read_jsonl(out_dir / exam_replay.REPLAY_NAME)


def test_the_default_bank_is_the_last_one_and_only_its_exams_are_replayed(
        source, tmp_path):
    summary, _, _ = _replay(source, tmp_path / "out")

    # p04 holds alpha v2 and beta v1: alpha v1 is an older version, gamma
    # is no longer in the bank.
    assert summary["bank_phase_label"] == "p04:learn"
    assert summary["selected"] == 2 and summary["answered"] == 2
    assert summary["skipped"] == {
        "skill_not_in_bank": 1, "version_not_in_bank": 1}
    assert [(row["skill_id"], row["status"])
            for row in _rows(tmp_path / "out")] == [
        (ALPHA, "ok"), (BETA, "ok")]


def test_a_named_bank_phase_selects_that_banks_exams(source, tmp_path):
    summary, _, _ = _replay(source, tmp_path / "out",
                            bank_phase_label="p02:learn")

    # alpha v2 is a version the p02 bank does not hold.
    assert summary["selected"] == 3
    assert summary["skipped"] == {
        "skill_not_in_bank": 0, "version_not_in_bank": 1}
    assert {row["skill_id"] for row in _rows(tmp_path / "out")} == {
        ALPHA, BETA, GAMMA}


def test_rows_record_what_the_answerer_opened(source, tmp_path):
    def opens(name, version):
        return {"alpha": ["alpha", "beta"], "beta": []}[name]

    summary, _, _ = _replay(source, tmp_path / "out", opens,
                            bank_phase_label="p04:learn")

    alpha, beta = _rows(tmp_path / "out")
    assert alpha["opened"] == [ALPHA, BETA]
    assert alpha["target_opened"] is True and alpha["other_opened"] == 1
    assert alpha["verdict"] == "success"
    assert beta["opened"] == [] and beta["target_opened"] is False
    assert beta["other_opened"] == 0 and beta["verdict"] == "failure"
    metrics = summary["metrics"]
    assert metrics["recall"]["value"] == 0.5
    assert metrics["misfire"]["value"] == 0.5
    assert metrics["plan_pass_rate"]["value"] == 0.5
    assert metrics["pass_given_target_opened"]["value"] == 1.0
    assert metrics["pass_given_target_not_opened"]["value"] == 0.0
    assert read_json(tmp_path / "out" / exam_replay.SUMMARY_NAME) == summary


def test_the_card_list_is_built_the_way_the_exam_phase_builds_it(
        source, tmp_path):
    # The p04 bank was followed by exam_b (description mode when_to_use),
    # the p02 bank by exam_a (mode summary).
    _, answerer, _ = _replay(source, tmp_path / "late")
    _, early_answerer, _ = _replay(source, tmp_path / "early",
                                   bank_phase_label="p02:learn")

    late = answerer.client.calls[0]["user"]
    early = early_answerer.client.calls[0]["user"]
    assert f"{ALPHA} | " in late and "When alpha matters." in late
    assert "Card alpha teaches alpha." not in late
    assert "Card alpha teaches alpha." in early
    assert "When alpha matters." not in early
    # At most max_skill_reads (the parameter) is what the model is told.
    assert "at most 3 skill(s)" in late
    _, narrow, _ = _replay(source, tmp_path / "narrow", max_skill_reads=1)
    assert "at most 1 skill(s)" in narrow.client.calls[0]["user"]


def test_a_card_outside_the_catalog_is_a_terminal_format_error(
        source, tmp_path):
    def opens(name, version):
        return ["alpha", "unknown-card"] if name == "alpha" else [name]

    summary, _, _ = _replay(source, tmp_path / "out", opens)

    assert summary["answered"] == 1 and summary["failed"] == 1
    failed = [row for row in _rows(tmp_path / "out")
              if row["status"] == "model_output_error"]
    assert [row["skill_id"] for row in failed] == [ALPHA]
    assert "unknown skill" in failed[0]["error"]
    assert summary["metrics"]["n"] == 1


def test_attempts_land_in_a_fresh_store_not_the_source(source, tmp_path):
    before = snapshot(source)

    _replay(source, tmp_path / "out")

    assert snapshot(source) == before
    store = Store(tmp_path / "out" / "store")
    assert store.ids("exams") == sorted([
        make_exam_id(ALPHA, 2), make_exam_id(BETA, 1)])
    assert len(store.ids("attempts")) == 2
    attempt = store.load("attempts", store.ids("attempts")[0], Attempt)
    assert attempt.purpose == "acceptance" and attempt.judge_verdict is True


def test_the_output_may_not_be_inside_the_source_run(source):
    with pytest.raises(ConfigError, match="inside the run directory"):
        _replay(source, source / "replay")

    assert not (source / "replay").exists()


def test_a_resume_after_a_crash_matches_a_straight_run(source, tmp_path):
    straight, _, _ = _replay(source, tmp_path / "straight",
                             bank_phase_label="p02:learn")

    # Two model calls per exam (select, plan); the judge never fails. The
    # fourth call is the second exam's plan: it crashes after the first
    # exam is complete and the second has only chosen its skills.
    with pytest.raises(RuntimeError, match="scripted crash"):
        _replay(source, tmp_path / "out", bank_phase_label="p02:learn",
                crash_after=3)
    assert len(_rows(tmp_path / "out")) == 1

    resumed, answerer, _ = _replay(source, tmp_path / "out",
                                   bank_phase_label="p02:learn")

    # The resume answers only the two exams without a row (select + plan).
    assert len(answerer.client.calls) == 4
    assert resumed == straight
    assert _rows(tmp_path / "out") == _rows(tmp_path / "straight")


def test_a_finished_replay_asks_the_models_nothing(source, tmp_path):
    _replay(source, tmp_path / "out")

    again, answerer, judge = _replay(source, tmp_path / "out")

    assert answerer.client.calls == [] and judge.client.calls == []
    assert again["answered"] == 2


def test_an_interrupted_judgment_reuses_the_stored_answer(source, tmp_path):
    # The first judge call dies after the answer was stored; the resume
    # judges the stored answer without asking the answerer again.
    answerer = make_fake_handle(
        "answerer", answerer_script(_opens_target_only), name="D")

    def flaky_judge(system, user):
        raise RuntimeError("judge crash")

    with pytest.raises(RuntimeError, match="judge crash"):
        exam_replay.replay(source, tmp_path / "out", answerer,
                           make_fake_handle("judge", flaky_judge))
    assert len(answerer.client.calls) == 2  # select + plan of one exam

    summary, resumed, _ = _replay(source, tmp_path / "out")

    assert summary["answered"] == 2
    # Only the second exam is answered again; the first reuses its answer.
    assert len(resumed.client.calls) == 2


def test_an_infrastructure_error_leaves_the_exam_to_a_resume(
        source, tmp_path):
    good = answerer_script(_opens_target_only)
    state = {"down": True}

    def script(system, user):
        if state["down"] and "SCENE-beta" in user:
            raise InfrastructureError("endpoint down")
        return good(system, user)

    answerer = make_fake_handle("answerer", script)
    judge = make_fake_handle("judge", judge_script())
    with pytest.raises(InfrastructureError, match="resume"):
        exam_replay.replay(source, tmp_path / "out", answerer, judge)
    assert len(_rows(tmp_path / "out")) == 1

    state["down"] = False
    summary = exam_replay.replay(source, tmp_path / "out", answerer, judge)

    assert summary["answered"] == 2


def test_a_resume_with_another_answering_model_is_refused(source, tmp_path):
    _replay(source, tmp_path / "out", answerer_name="D")

    with pytest.raises(ConfigError, match="fresh output directory"):
        _replay(source, tmp_path / "out", answerer_name="Q")
    assert json.loads(
        (tmp_path / "out" / PINNED_NAME).read_text())["answerer_model"] == "D"


def test_two_answerers_replay_the_same_exams_into_separate_outputs(
        source, tmp_path):
    def never(name, version):
        return []

    d, _, _ = _replay(source, tmp_path / "d", answerer_name="D")
    q, _, _ = _replay(source, tmp_path / "q", never, answerer_name="Q")

    assert d["selected"] == q["selected"]
    assert d["metrics"]["recall"]["value"] == 1.0
    assert q["metrics"]["recall"]["value"] == 0.0
    assert [row["exam_id"] for row in _rows(tmp_path / "d")] == [
        row["exam_id"] for row in _rows(tmp_path / "q")]


def test_a_bank_with_no_stored_exam_is_refused(source, tmp_path):
    for path in (source / "stage3_generation" / "exams").glob("*.json"):
        path.unlink()

    with pytest.raises(ConfigError, match="no stored exam"):
        _replay(source, tmp_path / "out")


def test_a_run_without_an_exam_phase_is_refused(source, tmp_path):
    manifest = read_json(source / "manifest.json")
    config = manifest["config"]
    config["sequence"] = [name for name in config["sequence"]
                          if not name.startswith("exam")]
    config["phases"] = {name: spec for name, spec in config["phases"].items()
                        if not name.startswith("exam")}
    (source / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ConfigError, match="no exam phase"):
        _replay(source, tmp_path / "out")
