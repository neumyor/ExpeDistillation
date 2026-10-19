"""Unit tests for scripts/run/convert_v3_configs.py (W3-C).

The pure v3 -> v4 mapping has its own suite
(tests/unit/core/test_upgrade_v3_config.py); these tests pin the
CONVERTER's own behaviour: which files a directory scan picks up, the
v2 -> v3 -> v4 chain for an explicitly passed older file, the refusal
of a v2 file whose v3 record already exists, the never-overwrite rule,
and -- the acceptance row -- that every schema-3 config in the
repository converts into a file that loads through the real v4 loader.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

from expedistill.core.config import load_config

_REPO = Path(__file__).resolve().parents[3]
_EXPERIMENTS = _REPO / "configs" / "experiments"

_SPEC = importlib.util.spec_from_file_location(
    "convert_v3_configs", _REPO / "scripts" / "run" / "convert_v3_configs.py")
CONVERTER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CONVERTER)

#: The seven frozen v3 experiment records (the default scan's whole
#: input set; the same list tests/unit/core/test_upgrade_v3_config.py
#: pins).
V3_CONFIGS = (
    "alfworld_skillopt_split_v2_v3.yaml",
    "alfworld_skillopt_split_v3.yaml",
    "searchqa_skillopt_split_v3.yaml",
    "tb21_full_v2_v3.yaml",
    "tb21_split_live_run_v3.yaml",
    "tb21_split_reuse_v3.yaml",
    "tb21_split_v2_v3.yaml",
)

#: A minimal schema-v2 config (every key the v2 -> v3 mapping moves),
#: for the chain tests.
_MINIMAL_V2 = {
    "benchmark": "searchqa",
    "mode": "full",
    "models": {"executor_strong": "strong-model",
               "executor_weak": "weak-model"},
    "n_attempts_strong": 1,
    "n_attempts_weak": 1,
    "K": 5,
    "segment_retry_limit": 2,
    "retry_limit": 1,
    "bank_token_budget": 4096,
    "curation_rounds": 3,
    "token_budgets": {"default_max_output_tokens": 5120},
    "schema_version": 2,
}


def _write(path: Path, payload) -> Path:
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=True, allow_unicode=True)
    return path


def test_detect_version_reads_the_field_and_the_shape():
    assert CONVERTER.detect_version({"schema_version": 3}) == 3
    assert CONVERTER.detect_version({"schema_version": 4}) == 4
    assert CONVERTER.detect_version({"methods": {}}) == 3
    assert CONVERTER.detect_version({}) == 2


def test_every_repository_v3_config_converts_and_loads(tmp_path):
    for name in V3_CONFIGS:
        status = CONVERTER.convert_file(_EXPERIMENTS / name,
                                        out_dir=tmp_path)
        assert status.startswith(f"{name} -> "), status
        out = tmp_path / f"{Path(name).stem}_v4.yaml"
        # The real loader on the written file: the YAML round-trip and
        # the whole v4 load-time checklist.
        config = load_config(str(out))
        assert config.phases["D"].role == "baseline"
        assert config.sequence[0] == "D"
        assert config.sequence[-1] == "C"


@pytest.mark.parametrize("retry_limit,expected", [
    (0, ["D", "L", "A", "L", "C"]),
    (2, ["D", "L", "A", "L", "A", "L", "A", "L", "C"]),
])
def test_v2_exam_input_chains_through_both_upgrades(tmp_path, retry_limit,
                                                     expected):
    # v2's exam protocol read the top-level retry_limit (upgrade_v2_
    # config's frozen mapping); the sequence carries retry_limit + 1
    # exam phases.
    raw = dict(_MINIMAL_V2)
    raw["retry_limit"] = retry_limit
    path = _write(tmp_path / "chain_v2.yaml", raw)
    status = CONVERTER.convert_file(path, out_dir=tmp_path)
    assert status.startswith("chain_v2.yaml -> "), status
    config = load_config(str(tmp_path / "chain_v2_v4.yaml"))
    assert config.sequence == expected
    # The chained output carries the v4-only global at its default, so
    # the converted identity never depends on the default.
    assert config.invalid_redispatch_limit == 1


def test_v2_live_run_input_chains_through_both_upgrades(tmp_path):
    raw = dict(_MINIMAL_V2)
    raw["n_attempts_weak"] = 2  # the live depth may not exceed the control's
    raw["acceptance"] = {"mode": "live_run", "n_attempts": 2,
                         "improvement": "strict_rate_gain"}
    path = _write(tmp_path / "chain_v2.yaml", raw)
    status = CONVERTER.convert_file(path, out_dir=tmp_path)
    assert status.startswith("chain_v2.yaml -> "), status
    config = load_config(str(tmp_path / "chain_v2_v4.yaml"))
    assert config.sequence == ["D", "L", "B", "L", "B", "L", "C"]
    live = config.phases["B"]
    assert live.role == "live"
    assert live.execution["n_attempts"] == 2
    assert live.compare.against == "D"
    assert live.compare.rule == "strict_rate_gain"


def test_v2_file_with_an_existing_v3_record_is_refused(tmp_path):
    path = _write(tmp_path / "old.yaml", _MINIMAL_V2)
    (tmp_path / "old_v3.yaml").write_text("schema_version: 3\n",
                                          encoding="utf-8")
    status = CONVERTER.convert_file(path, out_dir=tmp_path)
    assert status.startswith("old.yaml: REFUSED")
    assert "old_v3.yaml" in status
    assert not (tmp_path / "old_v4.yaml").exists()


def test_v4_input_is_skipped(tmp_path):
    path = _write(tmp_path / "done_v4.yaml", {"schema_version": 4})
    assert CONVERTER.convert_file(path, out_dir=tmp_path) == \
        "done_v4.yaml: already schema v4, skipped"


def test_existing_output_is_never_overwritten(tmp_path):
    path = _write(tmp_path / "old.yaml", _MINIMAL_V2)
    out = tmp_path / "old_v4.yaml"
    out.write_text("sentinel\n", encoding="utf-8")
    status = CONVERTER.convert_file(path, out_dir=tmp_path)
    assert status == "old_v4.yaml: exists, left untouched"
    assert out.read_text(encoding="utf-8") == "sentinel\n"


def test_invalid_v3_input_is_refused_per_file(tmp_path):
    raw = {"schema_version": 3, "benchmark": "searchqa", "mode": "full",
           "methods": {"acceptance": {"name": "no-such-method"}}}
    path = _write(tmp_path / "broken.yaml", raw)
    status = CONVERTER.convert_file(path, out_dir=tmp_path)
    assert status.startswith("broken.yaml: REFUSED")
    assert not (tmp_path / "broken_v4.yaml").exists()


def test_directory_scan_picks_exactly_the_schema_3_files(tmp_path,
                                                          monkeypatch,
                                                          capsys):
    for name in V3_CONFIGS:
        (tmp_path / name).write_text(
            (_EXPERIMENTS / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    _write(tmp_path / "a_v2.yaml", _MINIMAL_V2)
    _write(tmp_path / "b_v4.yaml", {"schema_version": 4})
    CONVERTER.main(["--dir", str(tmp_path)])
    out = capsys.readouterr().out
    converted = {line.split(" ->")[0]
                 for line in out.splitlines() if " -> " in line}
    assert converted == set(V3_CONFIGS)
    assert "a_v2.yaml" not in out
    assert "converted 7 config(s)" in out


def test_dir_without_a_value_prints_usage_and_exits_2(capsys):
    code = CONVERTER.main(["--dir"])
    assert code == 2
    out = capsys.readouterr().out
    assert "usage" in out
    assert "--dir needs a value" in out


def test_an_unknown_flag_is_rejected_instead_of_treated_as_a_path(
        tmp_path, capsys):
    # A mistyped flag used to be opened as a file (FileNotFoundError
    # traceback); it now names the flag and the only real one.
    code = CONVERTER.main(["--dry"])
    assert code == 2
    out = capsys.readouterr().out
    assert "unknown flag '--dry'" in out
    assert "--dir" in out


def test_an_unreadable_input_file_is_refused_per_file(tmp_path):
    missing = tmp_path / "no_such_config.yaml"
    status = CONVERTER.convert_file(missing)
    assert status.startswith("no_such_config.yaml: REFUSED (cannot read:")


def test_the_write_is_atomic_no_temp_left_behind(tmp_path):
    # The output lands through a temp file + rename (the O4 ruling): a
    # successful conversion leaves exactly one file, no *.tmp residue
    # for the never-overwrite rule to trip over.
    path = _write(tmp_path / "old.yaml", _MINIMAL_V2)
    status = CONVERTER.convert_file(path, out_dir=tmp_path)
    assert status.startswith("old.yaml -> "), status
    assert sorted(p.name for p in tmp_path.glob("*")) == [
        "old.yaml", "old_v4.yaml"]
    load_config(str(tmp_path / "old_v4.yaml"))
