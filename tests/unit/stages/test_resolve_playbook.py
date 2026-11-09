"""The delivery-level global-playbook switch (2026-11-04 P0).

The phase resolver reads ``playbook_file``, verifies the config's frozen
sha256, and hands the text to the delivery params; a missing file or a
post-freeze edit is a ConfigError, never a silent prompt change.
"""

import hashlib

import pytest

from expedistill.core.config import ConfigError as CoreConfigError
from expedistill.core.config import ExecutePhaseSpec, PHASE_ROLE_TEST
from expedistill.infra.reliability import ConfigError
from expedistill.stages.phase_execute.resolve import _resolve_delivery


def _spec(path, sha):
    return ExecutePhaseSpec(
        role=PHASE_ROLE_TEST,
        delivery={"name": "folder",
                  "description_mode": "summary_then_when_to_use",
                  "playbook_file": str(path),
                  "playbook_sha256": sha})


def test_playbook_file_is_read_and_passed_inline(tmp_path):
    path = tmp_path / "playbook.md"
    text = "GLOBAL PLAYBOOK\n"
    path.write_text(text, encoding="utf-8")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    _driver, params = _resolve_delivery(_spec(path, sha))
    assert params.playbook == text


def test_a_changed_playbook_is_refused(tmp_path):
    path = tmp_path / "playbook.md"
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(ConfigError, match="sha256"):
        _resolve_delivery(_spec(path, "0" * 64))


def test_a_missing_playbook_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="cannot be read"):
        _resolve_delivery(_spec(tmp_path / "missing.md", ""))


# -- the learned source (2026-11-05) -----------------------------------------

from types import SimpleNamespace

from expedistill.composites import playbook_store


def _learned_spec():
    return ExecutePhaseSpec(
        role=PHASE_ROLE_TEST,
        delivery={"name": "folder", "playbook_source": "learned"})


def test_a_learned_playbook_is_read_from_the_earlier_learn_phase(tmp_path):
    playbook_store.write_learned_playbook(
        tmp_path, "p02:learn_add", "LEARNED TEXT", facts_chars=10)
    ctx = SimpleNamespace(run_dir=tmp_path)
    phase = SimpleNamespace(index=6)
    _driver, params = _resolve_delivery(_learned_spec(), ctx, phase)
    assert params.playbook == "LEARNED TEXT"


def test_a_learned_source_without_a_playbook_is_refused(tmp_path):
    ctx = SimpleNamespace(run_dir=tmp_path)
    with pytest.raises(ConfigError, match="none exists"):
        _resolve_delivery(_learned_spec(), ctx, SimpleNamespace(index=6))


def test_a_phase_before_the_playbook_does_not_see_it(tmp_path):
    playbook_store.write_learned_playbook(
        tmp_path, "p02:learn_add", "LEARNED TEXT", facts_chars=10)
    ctx = SimpleNamespace(run_dir=tmp_path)
    with pytest.raises(ConfigError, match="none exists"):
        _resolve_delivery(_learned_spec(), ctx, SimpleNamespace(index=1))


def test_a_learned_source_needs_the_run_context():
    with pytest.raises(ConfigError, match="run context"):
        _resolve_delivery(_learned_spec())


def test_the_newest_playbook_wins(tmp_path):
    playbook_store.write_learned_playbook(
        tmp_path, "p02:learn_add", "OLD", facts_chars=1)
    playbook_store.write_learned_playbook(
        tmp_path, "p04:learn", "NEW", facts_chars=1)
    assert playbook_store.load_learned_playbook(tmp_path, 6) == "NEW"
    assert playbook_store.load_learned_playbook(tmp_path, 3) == "OLD"


def test_the_two_sources_cannot_be_combined():
    spec = ExecutePhaseSpec(
        role=PHASE_ROLE_TEST,
        delivery={"name": "folder", "playbook_source": "learned",
                  "playbook_file": "x.md"})
    with pytest.raises(ConfigError, match="one playbook source"):
        _resolve_delivery(spec, SimpleNamespace(run_dir="."),
                          SimpleNamespace(index=1))


def test_an_unknown_source_is_refused():
    spec = ExecutePhaseSpec(
        role=PHASE_ROLE_TEST,
        delivery={"name": "folder", "playbook_source": "guessed"})
    with pytest.raises(CoreConfigError, match="playbook_source"):
        _resolve_delivery(spec)


def test_the_exam_catalog_takes_the_learned_source_too(tmp_path):
    playbook_store.write_learned_playbook(
        tmp_path, "p02:learn_add", "LEARNED TEXT", facts_chars=10)
    spec = ExecutePhaseSpec(
        role="exam", delivery={"name": "exam_folder",
                               "playbook_source": "learned"})
    _driver, params = _resolve_delivery(
        spec, SimpleNamespace(run_dir=tmp_path), SimpleNamespace(index=2))
    assert params.playbook == "LEARNED TEXT"
