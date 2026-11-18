"""SWE-bench's diagnosis prompts and what stage 0 freezes for the plugin:
the method prompt plus the grader report, registered and declared on the
SWE-bench side only."""

import string

from expedistill.benchmarks.swebench.diagnose import (
    GRADER_REPORT_PROMPT_NAME,
    SWEBENCH_TRIAL_FAILURE_ANALYSIS,
)
from expedistill.prompts import METHOD_PROMPT_NAMES, get
from expedistill.prompts.diagnose import TRIAL_FAILURE_ANALYSIS
from expedistill.prompts.registry import digest
from expedistill.stages.stage0_freeze import _frozen_pack, _prompt_digests
from tests.helpers.plugins import ensure_plugin

#: The two additions SWE-bench makes to the method prompt (Terminal Bench's
#: two, in SWE-bench terms), as (anchor, addition). Pinned so an edit to the
#: method prompt fails here until it is carried over to the SWE-bench copy.
_REPORT_RULE = (
    "The trial failed.",
    " When the TASK section ends with a GRADER REPORT, that\n"
    "report is how the hidden FAIL_TO_PASS and PASS_TO_PASS tests judged the\n"
    "submitted patch: start from it, then find the step where the agent's "
    "work\ndiverged from what those tests check.")
_NOTES_RULE = (
    "  Empty when no such practice exists.\n",
    "- model_note and playbook_note must not repeat test names, values, file\n"
    "  names or expected outputs from a grader report; state the habit or\n"
    "  practice behind the failure instead.\n")

#: Every prompt the plugin declares, so every one stage 0 freezes for it.
_DECLARED = {
    "SWEBENCH_INSTANCE_TEMPLATE", "SWEBENCH_SYSTEM_TEMPLATE",
    "SWEBENCH_SKILL_READ", "SWEBENCH_SKILL_READ_MANDATORY",
    "SWEBENCH_DISCIPLINE",
    "SWEBENCH_TRIAL_FAILURE_ANALYSIS", "SWEBENCH_DIAGNOSIS_GRADER_REPORT",
}


def _slots(template):
    return {field for _, field, _, _ in string.Formatter().parse(template)
            if field is not None}


def test_the_swebench_prompt_is_the_method_prompt_plus_two_additions():
    expected = TRIAL_FAILURE_ANALYSIS.template
    for anchor, addition in (_REPORT_RULE, _NOTES_RULE):
        assert expected.count(anchor) == 1
        expected = expected.replace(anchor, anchor + addition, 1)
    assert SWEBENCH_TRIAL_FAILURE_ANALYSIS.template == expected


def test_the_swebench_prompt_fills_the_method_prompts_slots():
    # Stage 0 refuses an override whose placeholders differ.
    assert _slots(SWEBENCH_TRIAL_FAILURE_ANALYSIS.template) \
        == _slots(TRIAL_FAILURE_ANALYSIS.template)


def test_the_pack_declares_the_override_and_the_report_section():
    pack = ensure_plugin("swebench").prompt_pack
    assert pack.overrides == {
        "TRIAL_FAILURE_ANALYSIS": SWEBENCH_TRIAL_FAILURE_ANALYSIS.name}
    assert pack.grader_report_prompt == GRADER_REPORT_PROMPT_NAME
    assert _slots(get(GRADER_REPORT_PROMPT_NAME)) == {"report"}
    assert "GRADER REPORT" in get(GRADER_REPORT_PROMPT_NAME)
    # The method's own PLAYBOOK_UPDATE, with no facts and no checks.
    assert (pack.playbook_seed, pack.playbook_facts, pack.playbook_checks) \
        == ("", None, ())


def test_no_swebench_prompt_is_a_method_prompt():
    assert not any(name.startswith("SWEBENCH_")
                   for name in METHOD_PROMPT_NAMES)


def test_stage0_freezes_every_declared_prompt():
    ensure_plugin("swebench")
    digests = _prompt_digests("swebench")
    assert {name for name in digests if name.startswith("SWEBENCH_")} \
        == _DECLARED
    for name in _DECLARED:
        assert digests[name] == digest(name)
    # No other benchmark's executor prompt rides a SWE-bench manifest.
    assert not any(name.startswith(("TB_", "ALFWORLD_", "SEARCHQA_"))
                   for name in digests)
    frozen = _frozen_pack(ensure_plugin("swebench").prompt_pack)
    assert frozen["grader_report_prompt"] == GRADER_REPORT_PROMPT_NAME
    assert frozen["vocabulary"]["name"] == "swebench"
    assert "/skills/<name>/SKILL.md" in frozen["protocol"]
