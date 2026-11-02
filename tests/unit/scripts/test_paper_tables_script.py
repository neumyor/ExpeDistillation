"""scripts/analysis/paper_tables.py: argument handling and exit codes.

The script is a thin wrapper over ``expedistill.stages.paper_tables``
(tested in tests/unit/stages/test_paper_tables.py); these tests pin what
the wrapper itself owns: the options, stdout versus file output, and a
non-zero exit with a message on stderr for a bad spec or output path.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from tests.helpers.paper_runs import write_test_run

_SPEC = importlib.util.spec_from_file_location(
    "paper_tables_script",
    Path(__file__).resolve().parents[3] / "scripts" / "analysis"
    / "paper_tables.py")
SCRIPT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(SCRIPT)

S, F = "success", "failure"


@pytest.fixture
def spec_file(tmp_path):
    run = write_test_run(
        tmp_path, name="ours",
        with_bank={"t1": [S, S, F], "t2": [F, F, F]},
        control={"t1": [F, F, F], "t2": [F, F, F]})
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"main": {"alfworld": {"Ours": str(run)}}}))
    return path, run


def test_defaults():
    args = SCRIPT.build_parser().parse_args(["--spec", "s.json"])

    assert (args.spec, args.out_md, args.out_json) == ("s.json", None, None)
    assert (args.n_boot, args.seed, args.metric) == (10000, 0, "avg_at_n")


def test_spec_is_required(capsys):
    with pytest.raises(SystemExit) as raised:
        SCRIPT.build_parser().parse_args([])

    assert raised.value.code == 2
    assert "--spec" in capsys.readouterr().err


def test_an_unknown_metric_is_refused_by_the_parser(capsys):
    with pytest.raises(SystemExit):
        SCRIPT.build_parser().parse_args(
            ["--spec", "s.json", "--metric", "best"])

    assert "--metric" in capsys.readouterr().err


def test_without_out_md_the_markdown_goes_to_stdout(spec_file, capsys):
    path, _ = spec_file

    code = SCRIPT.main(["--spec", str(path), "--n-boot", "200"])

    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("## main\n")
    assert "| Ours |" in out


def test_files_are_written_and_stdout_stays_empty(spec_file, tmp_path, capsys):
    path, _ = spec_file
    out_md, out_json = tmp_path / "t.md", tmp_path / "t.json"

    code = SCRIPT.main(["--spec", str(path), "--out-md", str(out_md),
                        "--out-json", str(out_json), "--n-boot", "200",
                        "--seed", "4", "--metric", "pass_at_n"])

    assert code == 0
    assert capsys.readouterr().out == ""
    assert "pass@N" in out_md.read_text()
    settings = json.loads(out_json.read_text())["settings"]
    assert (settings["n_boot"], settings["seed"]) == (200, 4)


def test_an_output_inside_a_run_exits_2_and_writes_nothing(
        spec_file, capsys):
    path, run = spec_file
    target = run / "tables.json"

    code = SCRIPT.main(["--spec", str(path), "--out-json", str(target)])

    assert code == 2
    assert "inside the run directory" in capsys.readouterr().err
    assert not target.exists()


def test_a_missing_spec_or_a_bad_run_exits_2(tmp_path, capsys):
    assert SCRIPT.main(["--spec", str(tmp_path / "nope.json")]) == 2

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(
        {"main": {"alfworld": {"A": str(tmp_path / "no_run")}}}))
    assert SCRIPT.main(["--spec", str(bad)]) == 2
    assert "paper_tables:" in capsys.readouterr().err
