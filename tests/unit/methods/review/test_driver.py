"""The review driver: baseline add items and evidence-free keep/delete."""

from types import SimpleNamespace

import pytest

import expedistill.benchmarks.tb  # noqa: F401  (registers the plugin)
from expedistill.composites.evidence_package import EvidencePackage
from expedistill.composites.unit_ids import make_unit_id
from expedistill.core.answer_sheet import PhaseReport, UnitSummary
from expedistill.core.contracts import TaskListRow
from expedistill.infra.store import Store
from expedistill.methods.review import ReviewParams
from expedistill.methods.review import driver
from expedistill.methods.review.contracts import ReviewContext
from tests.helpers.plugins import ensure_plugin

@pytest.fixture(autouse=True)
def _tb_plugin_registered():
    ensure_plugin("tb")


TASK = "tb:t1"


def _unit(phase_label="p01:baseline", role="weak",
          injected=None, valid=2, success=0):
    return UnitSummary(
        unit_id=make_unit_id(
            phase_label=phase_label, benchmark="tb", task_ref=TASK,
            role=role, skills_injected=injected),
        phase_label=phase_label, task_ref=TASK, role=role, arm=None,
        expected_attempts=valid, injected_skills=injected,
        valid_count=valid, success_count=success, status="normal", cause="")


def _context(phase_kind="baseline"):
    return ReviewContext(
        phase_kind=phase_kind, phase_label="p02:learn", checks_left=2,
        round=0, delivery="none", compare_rule="strict_rate_gain",
        n_attempts_weak=2, benchmark="tb", weak_profile="",
        task_select_rule="any_weak_failed",
        trajectory_select="all",
        segment_select="strong_success_weak_failed")


def test_baseline_review_emits_an_add_item_with_evidence(tmp_path, monkeypatch):
    current = PhaseReport(
        phase_kind="baseline", learning_usable=True, sheets=[],
        units=[_unit()])
    store = Store(tmp_path / "run")
    calls = []

    def segment(**kwargs):
        calls.append("segment")

    def collect(**kwargs):
        calls.append("collect")
        return EvidencePackage(
            target=TASK, facts=[],
            source_experiences=[{"subtask": SimpleNamespace(
                subtask_id="traj/tb:t1/strong/00/seg00")}])

    monkeypatch.setattr(driver.segmentation, "segment_add_material", segment)
    monkeypatch.setattr(driver.add_evidence, "collect_add_evidence", collect)

    result = driver.review(
        current=current, control=None, bank=[], context=_context(),
        params=ReviewParams(), store=store,
        handles={"segmenter": object(), "author": object()},
        run_dir=tmp_path / "run")

    assert calls == ["segment", "collect"]
    assert len(result.items) == 1
    assert result.items[0].row.action == "add"
    assert result.items[0].row.target_id == TASK
    assert isinstance(result.items[0].evidence, EvidencePackage)


def test_keep_and_delete_need_no_evidence_or_model(tmp_path):
    rows = [
        TaskListRow(target_kind="skill", target_id="s1", action="keep",
                    reason="won", basis_unit="u1"),
        TaskListRow(target_kind="skill", target_id="s2", action="delete",
                    reason="never_read", basis_unit="u2"),
    ]
    current = PhaseReport(
        phase_kind="live", learning_usable=True, sheets=[], units=[])
    store = Store(tmp_path / "run")

    result = driver.diagnose(
        rows=rows, current=current, context=_context("live"),
        params=ReviewParams(), store=store,
        handles={"author": object()}, usage=None,
        run_dir=tmp_path / "run")

    assert [item.row.action for item in result.items] == ["keep", "delete"]
    assert all(item.evidence is None for item in result.items)


def test_skip_evidence_leaves_already_settled_rows_untouched(tmp_path,
                                                            monkeypatch):
    rows = [
        TaskListRow(target_kind="skill", target_id="s1", action="revise",
                    reason="not_won", basis_unit="u1"),
    ]
    current = PhaseReport(
        phase_kind="live", learning_usable=True, sheets=[], units=[])
    store = Store(tmp_path / "run")
    called = []
    monkeypatch.setattr(
        driver.attribution, "collect_skill_evidence",
        lambda **kwargs: called.append(True) or {})

    result = driver.diagnose(
        rows=rows, current=current, context=_context("live"),
        params=ReviewParams(), store=store,
        handles={"author": object()}, usage=None,
        run_dir=tmp_path / "run", skip_evidence={"s1"})

    assert called == []
    assert result.items[0].evidence is None
