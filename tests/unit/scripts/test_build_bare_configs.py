"""The three ALFWorld bare arms, generated before the full matrix can be.

Every ALFWorld with-bank arm pins its student's frozen bare control file,
so those files must be produced by the bare runs first. This module tests
that first stage on its own; test_build_paper_configs.py covers the full
matrix and skips until the control files exist.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
CONFIG_DIR = _REPO / "configs" / "experiments"
_SPEC = importlib.util.spec_from_file_location(
    "build_paper_configs", _REPO / "scripts" / "run" / "build_paper_configs.py")
BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(BUILDER)

STEMS = ("e1_bare_q", "e1_bare_d", "e1_bare_x")
STUDENT_MODEL = {
    "q": BUILDER.DEFAULT_Q_MODEL,
    "d": BUILDER.DEFAULT_D_MODELS["alfworld"],
    "x": BUILDER.DEFAULT_X_MODEL,
}


@pytest.fixture(scope="module")
def generated():
    return BUILDER.generate(
        CONFIG_DIR, BUILDER.DEFAULT_D_MODELS, BUILDER.DEFAULT_Q_MODEL,
        stems=STEMS)


def test_the_three_bare_arms_are_alfworld_only(generated):
    assert sorted(generated) == sorted(
        f"alfworld_split_paper_v3_{stem}.yaml" for stem in STEMS)


@pytest.mark.parametrize("student", ["q", "d", "x"])
def test_a_bare_arm_is_one_bare_run_with_a_zero_execution_control(
        student, generated):
    config = generated[f"alfworld_split_paper_v3_e1_bare_{student}.yaml"].config
    assert config["sequence"] == ["test"]
    assert len(config["split"]["test"]) == 134
    assert (config["n_attempts_strong"], config["n_attempts_weak"]) == (3, 3)
    assert config["models"]["executor_weak"] == STUDENT_MODEL[student]
    test = config["phases"]["test"]
    assert test["delivery"] == {"name": "whole_bank"}
    assert test["execution"] == {"name": "real"}
    # The run IS the measurement; the pinned placeholder keeps the control
    # arm from repeating the same bare model.
    assert test["compare"]["against"] == "external_campaign"
    assert test["compare"]["external"] == BUILDER.POOLED_CONTROL


def test_the_checked_in_bare_configs_match(tmp_path):
    assert BUILDER.main(["--out-dir", str(tmp_path)] + [
        "--stems", ",".join(STEMS)]) == 0
    for student in "qdx":
        name = f"alfworld_split_paper_v3_e1_bare_{student}.yaml"
        assert (tmp_path / name).read_bytes() == \
            (CONFIG_DIR / name).read_bytes()
        yaml.safe_load((tmp_path / name).read_text())
