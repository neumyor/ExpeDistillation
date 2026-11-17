"""Terminal Bench's diagnosis prompts: the method prompt plus the verifier
report, registered and declared on the TB side only."""

import string

from expedistill.benchmarks.tb.diagnose import (
    GRADER_REPORT_PROMPT_NAME,
    TB_TRIAL_FAILURE_ANALYSIS,
)
from expedistill.benchmarks.tb.plugin import TBPlugin
from expedistill.prompts import METHOD_PROMPT_NAMES, get
from expedistill.prompts.diagnose import TRIAL_FAILURE_ANALYSIS

#: The two additions TB makes to the method prompt, as (anchor, addition).
#: Pinned so an edit to the method prompt fails here until it is carried
#: over to the TB copy.
_REPORT_RULE = (
    "The trial failed.",
    " When the TASK section ends with a VERIFIER REPORT, that\n"
    "report is what the hidden tests rejected: start from it, then find the "
    "step\nwhere the agent's work diverged from what the report checks.")
_NOTES_RULE = (
    "  Empty when no such practice exists.\n",
    "- model_note and playbook_note must not repeat values, file names or\n"
    "  expected outputs from a verifier report; state the habit or practice\n"
    "  behind the failure instead.\n")


def _slots(template):
    return {field for _, field, _, _ in string.Formatter().parse(template)
            if field is not None}


def test_the_tb_prompt_is_the_method_prompt_plus_two_additions():
    expected = TRIAL_FAILURE_ANALYSIS.template
    for anchor, addition in (_REPORT_RULE, _NOTES_RULE):
        assert expected.count(anchor) == 1
        expected = expected.replace(anchor, anchor + addition, 1)
    assert TB_TRIAL_FAILURE_ANALYSIS.template == expected


def test_the_tb_prompt_fills_the_method_prompts_slots():
    # Stage 0 refuses an override whose placeholders differ.
    assert _slots(TB_TRIAL_FAILURE_ANALYSIS.template) \
        == _slots(TRIAL_FAILURE_ANALYSIS.template)


def test_the_pack_declares_the_override_and_the_report_section():
    pack = TBPlugin().prompt_pack
    assert pack.overrides["TRIAL_FAILURE_ANALYSIS"] \
        == TB_TRIAL_FAILURE_ANALYSIS.name
    assert pack.grader_report_prompt == GRADER_REPORT_PROMPT_NAME
    assert _slots(get(GRADER_REPORT_PROMPT_NAME)) == {"report"}
    assert "VERIFIER REPORT" in get(GRADER_REPORT_PROMPT_NAME)


def test_no_benchmark_prompt_is_a_method_prompt():
    # Registered under benchmarks/tb/, after prompts/ computed the method
    # set: the TB names freeze in TB manifests only.
    for name in (TB_TRIAL_FAILURE_ANALYSIS.name, GRADER_REPORT_PROMPT_NAME):
        assert name not in METHOD_PROMPT_NAMES
    assert not any(name.startswith(("TB_", "ALFWORLD_", "SEARCHQA_"))
                   for name in METHOD_PROMPT_NAMES)
