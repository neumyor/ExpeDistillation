"""The evolving texts on disk: one version per learn phase, read back only
from the immediately preceding learn phase."""

import pytest

from expedistill.composites import evolving_text
from expedistill.core.config import ExecutePhaseSpec, LearnPhaseSpec
from expedistill.core.models import EvolvingText
from expedistill.infra.reliability import CodeBugError, ConfigError
from tests.helpers.plugins import ensure_plugin

NAMES = ["baseline", "learn", "practice", "learn2", "test"]
SPECS = {
    "baseline": ExecutePhaseSpec(role="baseline"),
    "learn": LearnPhaseSpec(),
    "practice": ExecutePhaseSpec(role="practice",
                                 delivery={"name": "folder"},
                                 execution={"name": "real_folder"}),
    "learn2": LearnPhaseSpec(),
    "test": ExecutePhaseSpec(role="test", tasks="test"),
}


def test_a_text_round_trips(tmp_path):
    item = EvolvingText(kind="playbook", text="rules", version=2,
                        phase_label="p04:learn2", source_sheet_ids=["s"])
    evolving_text.write_text(tmp_path, item)
    assert evolving_text.read_text(tmp_path, "p04:learn2", "playbook") \
        == item
    assert evolving_text.read_text(tmp_path, "p04:learn2", "profile") \
        is None


def test_the_reader_takes_the_immediately_preceding_learn_phase(tmp_path):
    for label, version in (("p02:learn", 1), ("p04:learn2", 2)):
        evolving_text.write_text(tmp_path, EvolvingText(
            kind="profile", text=label, version=version,
            phase_label=label))
    assert evolving_text.previous_learn_label(NAMES, SPECS, 4) == \
        "p04:learn2"
    assert evolving_text.load_previous(
        tmp_path, NAMES, SPECS, 4, "profile").text == "p04:learn2"
    assert evolving_text.load_previous(
        tmp_path, NAMES, SPECS, 2, "profile").version == 1


def test_before_the_first_learn_phase_there_is_no_text(tmp_path):
    assert evolving_text.load_previous(
        tmp_path, NAMES, SPECS, 1, "profile") is None


def test_a_missing_text_of_the_preceding_learn_phase_is_an_error(tmp_path):
    with pytest.raises(CodeBugError, match="no playbook text"):
        evolving_text.load_previous(tmp_path, NAMES, SPECS, 4, "playbook")


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(CodeBugError, match="unknown evolving text kind"):
        evolving_text.text_path(tmp_path, "p02:learn", "notes")


@pytest.mark.parametrize("delivery", ["none", "whole_bank"])
def test_tb_shows_a_playbook_only_through_the_folder(delivery):
    ensure_plugin("tb")
    folder = ExecutePhaseSpec(role="practice", playbook="evolved",
                              delivery={"name": "folder"},
                              execution={"name": "real_folder"})
    evolving_text.check_playbook_capability(folder, "tb")
    plain = ExecutePhaseSpec(role="test", tasks="test", playbook="evolved",
                             delivery={"name": delivery},
                             execution={"name": "real"})
    with pytest.raises(ConfigError, match="cannot show a playbook"):
        evolving_text.check_playbook_capability(plain, "tb")
