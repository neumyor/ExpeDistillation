"""The delivery-level global-playbook switch (2026-11-04 P0).

The phase resolver reads ``playbook_file``, verifies the config's frozen
sha256, and hands the text to the delivery params; a missing file or a
post-freeze edit is a ConfigError, never a silent prompt change.
"""

import hashlib

import pytest

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
