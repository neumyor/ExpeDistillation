"""B4 manifest freeze: write once, then refuse any change, naming the keys."""

import pytest

from expedistill.infra.manifest import (
    FrozenProtocolChanged, freeze, load, load_manifest, load_or_none,
)

MAPPING = {
    "benchmark": "tb",
    "mode": "split",
    "seed": 7,
    "retry_limit": 3,
    "role_table": {"strong": "model-a", "weak": "model-b"},
    "prompt_digests": {"segment": "d1", "exam": "d2"},
}


def test_freeze_writes_manifest_when_absent(tmp_path):
    freeze(tmp_path, MAPPING)
    assert load(tmp_path) == MAPPING


def test_freeze_is_idempotent_when_identical(tmp_path):
    freeze(tmp_path, MAPPING)
    # Deep-equal, differently ordered: still fine.
    freeze(tmp_path, {k: MAPPING[k] for k in reversed(list(MAPPING))})


def test_freeze_refuses_changed_value_and_names_keys(tmp_path):
    freeze(tmp_path, MAPPING)
    changed = dict(MAPPING, retry_limit=5)
    with pytest.raises(FrozenProtocolChanged) as excinfo:
        freeze(tmp_path, changed)
    assert "retry_limit" in str(excinfo.value)


def test_freeze_names_nested_changed_keys(tmp_path):
    freeze(tmp_path, MAPPING)
    changed = dict(MAPPING, prompt_digests=dict(MAPPING["prompt_digests"],
                                                segment="d9"))
    with pytest.raises(FrozenProtocolChanged) as excinfo:
        freeze(tmp_path, changed)
    assert "prompt_digests.segment" in str(excinfo.value)


def test_freeze_names_added_and_removed_keys(tmp_path):
    freeze(tmp_path, MAPPING)
    with pytest.raises(FrozenProtocolChanged) as added:
        freeze(tmp_path, dict(MAPPING, new_key=1))
    assert "new_key" in str(added.value)
    trimmed = {k: v for k, v in MAPPING.items() if k != "seed"}
    with pytest.raises(FrozenProtocolChanged) as removed:
        freeze(tmp_path, trimmed)
    assert "seed" in str(removed.value)


def test_frozen_protocol_changed_is_config_error():
    from expedistill.infra.reliability import ConfigError
    assert issubclass(FrozenProtocolChanged, ConfigError)


def test_load_helpers(tmp_path):
    assert load_or_none(tmp_path) is None
    with pytest.raises(FileNotFoundError):
        load(tmp_path)
    freeze(tmp_path, MAPPING)
    assert load_or_none(tmp_path) == MAPPING
    assert load_manifest(tmp_path) == MAPPING
    assert load_manifest is load
