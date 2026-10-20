"""Unit tests for the G7/G8/G9 curation blocks (llm.fake only, no disk IO).

Wiring is asserted exactly: the user message sent to the client must equal
the named prompt template rendered with the expected variables, so a listing
that leaks into the wrong field or a mode that never reaches the MERGE
prompt fails loudly. Validation is loud by contract (AGENTS.md block rules):
unknown ids, malformed groups, missing mappings and bad modes raise; an
empty input short-circuits without any model call.
"""

import pytest

from expedistill.blocks.curate import assign_groups, find_similar, merge_skills
from expedistill.core.models import Skill
from expedistill.infra.reliability import ConfigError, ModelOutputError
from expedistill.llm import json_script, make_fake_handle
from expedistill.prompts.registry import get as get_prompt


class RecordingUsage:
    """Test double for the B7 UsageRecorder (same record signature)."""

    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


def make_skill(skill_id, content):
    return Skill(skill_id=skill_id, content=content,
                 subtask_id=f"traj/tb:t/{skill_id}",
                 source_subtasks=[f"traj/tb:t/{skill_id}"])


SKILLS = [
    make_skill("s0", "Check the log file first."),
    # Multi-line content: the listing must collapse it onto one line.
    make_skill("s1", "Look at the log file\nbefore anything else."),
    make_skill("s2", "Restart the service."),
    make_skill("s3", "Never restart the service."),
]

LISTING = (
    "- s0: Check the log file first.\n"
    "- s1: Look at the log file before anything else.\n"
    "- s2: Restart the service.\n"
    "- s3: Never restart the service."
)


# ---------------------------------------------------------------------------
# G7 find_similar
# ---------------------------------------------------------------------------

def test_find_similar_wiring_and_result(tmp_path):
    handle = make_fake_handle(script=json_script({
        "similar_groups": [["s0", "s1"]],
        "conflicts": [["s2", "s3"]],
        "low_value": ["s2", "s2"],
    }))
    usage = RecordingUsage()

    groups, conflicts, low_value = find_similar(
        SKILLS, handle, usage=usage, run_dir=tmp_path)

    assert len(handle.client.calls) == 1
    expected = get_prompt("SIMILARITY").format(skills_listing=LISTING)
    assert handle.client.calls[0]["user"] == expected
    assert groups == [["s0", "s1"]]
    assert conflicts == [["s2", "s3"]]
    assert low_value == ["s2"]  # a repeated id collapses

    # usage and run_dir thread through to call_model.
    assert len(usage.records) == 1
    assert usage.records[0]["prompt"] == "SIMILARITY"
    assert usage.records[0]["status"] == "ok"
    assert (tmp_path / "llm_requests").exists()


def test_find_similar_empty_input_short_circuits():
    handle = make_fake_handle(script=[])
    assert find_similar([], handle) == ([], [], [])
    assert handle.client.calls == []


def test_find_similar_unknown_id_in_group():
    handle = make_fake_handle(script=json_script({
        "similar_groups": [["s0", "nope"]], "conflicts": [], "low_value": [],
    }))
    with pytest.raises(ModelOutputError) as excinfo:
        find_similar(SKILLS, handle)
    assert "nope" in str(excinfo.value)
    assert "similar_groups" in str(excinfo.value)


def test_find_similar_unknown_id_in_conflict():
    handle = make_fake_handle(script=json_script({
        "similar_groups": [], "conflicts": [["s0", "ghost"]], "low_value": [],
    }))
    with pytest.raises(ModelOutputError) as excinfo:
        find_similar(SKILLS, handle)
    assert "ghost" in str(excinfo.value)
    assert "conflicts" in str(excinfo.value)


def test_find_similar_group_too_small():
    handle = make_fake_handle(script=json_script({
        "similar_groups": [["s0"]], "conflicts": [], "low_value": [],
    }))
    with pytest.raises(ModelOutputError) as excinfo:
        find_similar(SKILLS, handle)
    assert "at least two" in str(excinfo.value)


def test_find_similar_conflict_wrong_size():
    handle = make_fake_handle(script=json_script({
        "similar_groups": [], "conflicts": [["s0", "s1", "s2"]],
        "low_value": [],
    }))
    with pytest.raises(ModelOutputError) as excinfo:
        find_similar(SKILLS, handle)
    assert "two-element" in str(excinfo.value)


def test_find_similar_non_string_entry():
    handle = make_fake_handle(script=json_script({
        "similar_groups": [["s0", 7]], "conflicts": [], "low_value": [],
    }))
    with pytest.raises(ModelOutputError):
        find_similar(SKILLS, handle)


def test_find_similar_missing_field():
    handle = make_fake_handle(script=json_script({"similar_groups": []}))
    with pytest.raises(ModelOutputError) as excinfo:
        find_similar(SKILLS, handle)
    assert "conflicts" in str(excinfo.value)


def test_find_similar_missing_low_value_field():
    handle = make_fake_handle(script=json_script(
        {"similar_groups": [], "conflicts": []}))
    with pytest.raises(ModelOutputError, match="low_value"):
        find_similar(SKILLS, handle)


@pytest.mark.parametrize("low_value", [["ghost"], "s0", [["s0"]]])
def test_find_similar_bad_low_value(low_value):
    handle = make_fake_handle(script=json_script(
        {"similar_groups": [], "conflicts": [], "low_value": low_value}))
    with pytest.raises(ModelOutputError, match="low_value"):
        find_similar(SKILLS, handle)


def test_find_similar_summary_input_lists_summaries():
    skills = [make_skill("s0", "Long content zero."),
              make_skill("s1", "Long content one.")]
    skills[0].summary = "check the log"
    skills[1].summary = "restart\nsafely"
    handle = make_fake_handle(script=json_script(
        {"similar_groups": [], "conflicts": [], "low_value": []}))

    find_similar(skills, handle, similarity_input="summary")

    expected = get_prompt("SIMILARITY").format(
        skills_listing="- s0: check the log\n- s1: restart safely")
    assert handle.client.calls[0]["user"] == expected


def test_find_similar_summary_input_refuses_a_missing_summary():
    handle = make_fake_handle(script=[])
    with pytest.raises(ConfigError, match="s0"):
        find_similar(SKILLS, handle, similarity_input="summary")
    assert handle.client.calls == []


def test_find_similar_non_object_response():
    handle = make_fake_handle(script=json_script(["s0", "s1"]))
    with pytest.raises(ModelOutputError) as excinfo:
        find_similar(SKILLS, handle)
    assert "JSON object" in str(excinfo.value)


# ---------------------------------------------------------------------------
# G8 merge_skills
# ---------------------------------------------------------------------------

def test_merge_skills_dedup_wiring_and_result():
    handle = make_fake_handle(script=json_script({
        "content": "  Read the log file first.\n\n",
        "summary": "  read the log before acting  "}))

    merged, summary = merge_skills(SKILLS[:2], handle, mode="dedup")

    assert len(handle.client.calls) == 1
    # The skills block keeps the full multi-line content, headed by id.
    expected = get_prompt("MERGE").format(
        mode="dedup",
        skills_block=("### s0\nCheck the log file first.\n\n"
                      "### s1\nLook at the log file\n"
                      "before anything else."),
    )
    assert handle.client.calls[0]["user"] == expected
    assert merged == "Read the log file first."
    assert summary == "read the log before acting"


def test_merge_skills_conflict_mode_string():
    handle = make_fake_handle(script=json_script({
        "content": "Restart only when the config check fails.",
        "summary": "restart only after the config check fails"}))

    merged, _summary = merge_skills(SKILLS[2:], handle, mode="conflict")

    user = handle.client.calls[0]["user"]
    assert "MERGE MODE: conflict" in user
    assert "### s2\nRestart the service." in user
    assert "### s3\nNever restart the service." in user
    assert merged == "Restart only when the config check fails."


@pytest.mark.parametrize("reply,field", [
    ({"summary": "one line"}, "content"),
    ({"content": "Merged."}, "summary"),
    ({"content": "", "summary": "one line"}, "content"),
    ({"content": "Merged.", "summary": " "}, "summary"),
    ({"content": "Merged.", "summary": "two\nlines"}, "ONE line"),
    ({"content": "Merged.", "summary": "x" * 501}, "at most"),
    ({"content": "Merged.", "summary": 3}, "summary"),
])
def test_merge_skills_format_errors(reply, field):
    handle = make_fake_handle(script=json_script(reply))
    with pytest.raises(ModelOutputError, match=field):
        merge_skills(SKILLS[:2], handle, mode="dedup")


def test_merge_skills_wrong_mode_raises_before_any_call():
    handle = make_fake_handle(script=[])
    with pytest.raises(ValueError) as excinfo:
        merge_skills(SKILLS[:2], handle, mode="bogus")
    assert "bogus" in str(excinfo.value)
    assert handle.client.calls == []


def test_merge_skills_too_few_sources_raises_before_any_call():
    handle = make_fake_handle(script=[])
    with pytest.raises(ValueError):
        merge_skills([SKILLS[0]], handle, mode="dedup")
    assert handle.client.calls == []


# ---------------------------------------------------------------------------
# G9 assign_groups
# ---------------------------------------------------------------------------

def test_assign_groups_wiring_and_result():
    mapping = {"s0": "file inspection", "s1": "file inspection",
               "s2": "error recovery", "s3": "error recovery"}
    handle = make_fake_handle(script=json_script({"groups": mapping}))

    result = assign_groups(SKILLS, handle)

    assert len(handle.client.calls) == 1
    expected = get_prompt("GROUP").format(skills_listing=LISTING)
    assert handle.client.calls[0]["user"] == expected
    assert result == mapping


def test_assign_groups_summary_input_lists_summaries():
    skills = [make_skill("s0", "Long content zero."),
              make_skill("s1", "Long content one.")]
    skills[0].summary = "check the log"
    skills[1].summary = "restart\nsafely"
    handle = make_fake_handle(script=json_script(
        {"groups": {"s0": "a", "s1": "a"}}))

    result = assign_groups(skills, handle, grouping_input="summary")

    expected = get_prompt("GROUP").format(
        skills_listing="- s0: check the log\n- s1: restart safely")
    assert handle.client.calls[0]["user"] == expected
    assert result == {"s0": "a", "s1": "a"}


def test_assign_groups_summary_input_refuses_a_missing_summary():
    handle = make_fake_handle(script=[])
    with pytest.raises(ConfigError, match="s0"):
        assign_groups(SKILLS, handle, grouping_input="summary")
    assert handle.client.calls == []


def test_assign_groups_unknown_grouping_input_is_refused():
    handle = make_fake_handle(script=[])
    with pytest.raises(ConfigError, match="grouping_input"):
        assign_groups(SKILLS[:1], handle, grouping_input="titles")
    assert handle.client.calls == []


def test_assign_groups_strips_names():
    handle = make_fake_handle(script=json_script(
        {"groups": {"s0": "  file inspection  "}}))
    assert assign_groups(SKILLS[:1], handle) == {"s0": "file inspection"}


def test_assign_groups_empty_input_short_circuits():
    handle = make_fake_handle(script=[])
    assert assign_groups([], handle) == {}
    assert handle.client.calls == []


def test_assign_groups_missing_skill_raises():
    handle = make_fake_handle(script=json_script(
        {"groups": {"s0": "a", "s1": "a", "s2": "b"}}))
    with pytest.raises(ModelOutputError) as excinfo:
        assign_groups(SKILLS, handle)
    assert "s3" in str(excinfo.value)
    assert "missing" in str(excinfo.value)


def test_assign_groups_unknown_skill_raises():
    handle = make_fake_handle(script=json_script(
        {"groups": {"s0": "a", "ghost": "a", "s1": "a", "s2": "b",
                    "s3": "b"}}))
    with pytest.raises(ModelOutputError) as excinfo:
        assign_groups(SKILLS, handle)
    assert "ghost" in str(excinfo.value)
    assert "unknown" in str(excinfo.value)


def test_assign_groups_empty_name_raises():
    handle = make_fake_handle(script=json_script(
        {"groups": {"s0": "  "}}))
    with pytest.raises(ModelOutputError) as excinfo:
        assign_groups(SKILLS[:1], handle)
    assert "non-empty" in str(excinfo.value)


def test_assign_groups_non_string_name_raises():
    handle = make_fake_handle(script=json_script(
        {"groups": {"s0": 3}}))
    with pytest.raises(ModelOutputError):
        assign_groups(SKILLS[:1], handle)


def test_assign_groups_groups_field_not_object():
    handle = make_fake_handle(script=json_script({"groups": ["s0"]}))
    with pytest.raises(ModelOutputError) as excinfo:
        assign_groups(SKILLS, handle)
    assert "groups" in str(excinfo.value)


def test_find_similar_normalizes_missing_skill_suffix():
    """Mixed-format listings make models harmonize to the shorter id style.

    Round 1+ listings mix stage-3 ids (traj/.../segNN/skill) with merge ids
    (bank/merge/NNNN, no suffix); the model then drops the /skill suffix from
    the stage-3 ids (observed twice on the TB v3 run, 2026-10-12: all 68
    output ids missing the suffix). The deterministic recovery: an unknown id
    that matches exactly one known id after appending "/skill" is that id.
    """
    from expedistill.core.models import Skill
    from expedistill.blocks.curate import find_similar

    s1 = Skill(skill_id="traj/tb:t1/strong/00/seg00/skill", content="alpha",
               subtask_id="traj/tb:t1/strong/00/seg00",
               source_subtasks=["traj/tb:t1/strong/00/seg00"])
    s2 = Skill(skill_id="traj/tb:t1/strong/00/seg01/skill", content="beta",
               subtask_id="traj/tb:t1/strong/00/seg01",
               source_subtasks=["traj/tb:t1/strong/00/seg01"])
    m1 = Skill(skill_id="bank/merge/0000", content="merged", version=1,
               status="accepted", subtask_id="traj/tb:t1/strong/00/seg00",
               source_subtasks=["traj/tb:t1/strong/00/seg00"],
               merged_from=["traj/tb:t1/strong/00/seg00/skill"])
    script = [
        json_script({
            "similar_groups": [
                ["traj/tb:t1/strong/00/seg00", "bank/merge/0000"],
                ["traj/tb:t1/strong/00/seg01", "traj/tb:t1/strong/00/seg00"],
            ],
            "conflicts": [["traj/tb:t1/strong/00/seg01", "bank/merge/0000"]],
            "low_value": ["traj/tb:t1/strong/00/seg01"],
        })
    ]
    handle = make_fake_handle(script=script)
    groups, conflicts, low_value = find_similar([s1, s2, m1], handle)
    assert groups == [
        ["traj/tb:t1/strong/00/seg00/skill", "bank/merge/0000"],
        ["traj/tb:t1/strong/00/seg01/skill", "traj/tb:t1/strong/00/seg00/skill"],
    ]
    assert conflicts == [["traj/tb:t1/strong/00/seg01/skill", "bank/merge/0000"]]
    assert low_value == ["traj/tb:t1/strong/00/seg01/skill"]


def test_find_similar_rejects_ambiguous_suffix_free_ids():
    """A suffix-free id that matches nothing even after '/skill' is still an error."""
    from expedistill.core.models import Skill
    from expedistill.blocks.curate import find_similar

    s1 = Skill(skill_id="traj/tb:t1/strong/00/seg00/skill", content="alpha",
               subtask_id="traj/tb:t1/strong/00/seg00",
               source_subtasks=["traj/tb:t1/strong/00/seg00"])
    script = [json_script({
        "similar_groups": [["traj/tb:t1/strong/00/seg00", "traj/tb:t9/nope"]],
        "conflicts": [], "low_value": [],
    })]
    handle = make_fake_handle(script=script)
    with pytest.raises(ModelOutputError, match="traj/tb:t9/nope"):
        find_similar([s1], handle)
