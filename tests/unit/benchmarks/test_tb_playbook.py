"""TB's learned-playbook facts, requirements and writing prompt."""

import json
import string

from expedistill.benchmarks.tb import TBPlugin
from expedistill.benchmarks.tb.playbook import (
    PLAYBOOK_PROMPT_NAME, REQUIRED_LABELS, facts_from_sheets,
    playbook_requirements)
from expedistill.methods.evolve_text import missing_requirements
from expedistill.core.answer_sheet import (
    AnswerSheet, CoverageRecord, ExecutionRecord, GradingRecord)
from expedistill.core.models import Step
from expedistill.prompts import get as get_prompt


def _bash(text):
    return "bash_command(" + json.dumps(
        {"duration": 0.1, "keystrokes": text + "\n"}) + ")"


def _sheet(task, role, attempt, commands, verdict, *, complete=False,
           observations=None, metrics=None):
    task_ref = f"tb:{task}"
    sheet_id = f"{task_ref}:{role}:{attempt}"
    observations = observations or ["ok"] * len(commands)
    steps = [Step(action=_bash(c), observation=o)
             for c, o in zip(commands, observations)]
    if complete:
        steps.append(Step(action="mark_task_complete({})",
                          observation="done"))
    execution = ExecutionRecord(
        sheet_id=sheet_id, unit_id=f"u:{role}", phase_label="p01:baseline",
        task_ref=task_ref, role=role, arm=role, model_role=role,
        model_name="m", sample_index=attempt, steps=steps,
        metrics=metrics or {})
    return AnswerSheet(
        sheet_id=sheet_id, phase_label="p01:baseline",
        coverage=CoverageRecord(unit_id="u", attempt_index=attempt,
                                present=True, status="present"),
        execution=execution,
        grading=GradingRecord(verdict=verdict, basis="b",
                              rule_name="tb_scorer"))


LOOP = ["make", "make", "make", "make"]


def test_facts_describe_a_failing_trial_against_a_success():
    sheets = [
        _sheet("build-thing", "weak", 0, LOOP, "failure", complete=True,
               observations=["make: error: no rule"] * 4),
        _sheet("build-thing", "strong", 0,
               ["ls", "make -j4", "pytest tests"], "success", complete=True),
    ]
    text = facts_from_sheets(sheets)
    assert "build-thing: strong 1/1, weak 0/1" in text
    assert "1 tasks where the strong model succeeded more often" in text
    assert "median share of commands that repeat an earlier identical " \
        "command 0.75" in text
    assert "median longest run of consecutive error steps 4" in text
    assert "declared completion, verifier scored it a failure: 1 (median steps 5)" in text
    assert "ran a check-like command" in text
    assert "longest weak failure (build-thing): make | make | make | make" \
        in text


def test_facts_count_trial_exceptions_and_unparsable_rounds():
    sheets = [_sheet("t", "weak", 0, ["ls"], "failure",
                     metrics={"exception_type": "AgentTimeoutError",
                              "skipped_parse_failures": 3})]
    text = facts_from_sheets(sheets)
    assert "trial exception AgentTimeoutError: 1 (median steps 1)" in text
    assert "median rounds lost to unparsable replies 3" in text


def test_facts_do_not_depend_on_input_order():
    sheets = [
        _sheet("a", "weak", 0, ["ls"], "failure"),
        _sheet("a", "weak", 1, ["ls", "cat x"], "success", complete=True),
        _sheet("b", "strong", 0, ["pwd"], "success"),
    ]
    assert facts_from_sheets(sheets) == facts_from_sheets(reversed(sheets))


def test_unscored_and_missing_trials_are_skipped():
    scored = _sheet("a", "weak", 0, ["ls"], "success")
    no_grade = _sheet("a", "weak", 1, ["ls"], "success")
    no_grade.grading = None
    missing = _sheet("a", "weak", 2, ["ls"], "success")
    missing.execution = None
    assert "1 success / 0 failure of 1 trials" in facts_from_sheets(
        [scored, no_grade, missing])
    assert facts_from_sheets([no_grade, missing]) \
        == "(no scored baseline trials)"


def test_requirements_name_every_labelled_part():
    requirements = playbook_requirements()
    assert [r.name for r in requirements] == [n for n, _, _ in REQUIRED_LABELS]
    full = "\n".join(label for _, label, _ in REQUIRED_LABELS)
    assert missing_requirements(full, requirements) == []
    assert missing_requirements("## recovery\nx", requirements) == [
        "orientation", "working loop", "verification", "prohibitions"]


def test_the_pack_overrides_the_playbook_update_with_matching_slots():
    plugin = TBPlugin()
    pack = plugin.prompt_pack
    assert pack.overrides == {"PLAYBOOK_UPDATE": PLAYBOOK_PROMPT_NAME}
    assert pack.playbook_facts([]) == "(no scored baseline trials)"
    assert [check.name for check in pack.playbook_checks] \
        == [check.name for check in playbook_requirements()]
    assert _slots(get_prompt(PLAYBOOK_PROMPT_NAME)) \
        == _slots(get_prompt("PLAYBOOK_UPDATE"))
    # The prompt tells the writer what the agent already receives.
    assert get_prompt("TB_TASK_PROMPT_READ") in get_prompt(
        PLAYBOOK_PROMPT_NAME)
    assert not plugin.playbook_in_skills_block


def _slots(template):
    return {field for _, field, _, _ in string.Formatter().parse(template)
            if field is not None}
