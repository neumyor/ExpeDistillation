"""Learn step 5's rewrites (methods.revise): one side per rewrite, the
version guard and the receipts."""

from types import SimpleNamespace

import pytest

from expedistill.composites import learn_ledger as ledger
from expedistill.composites.provenance import find_provenance
from expedistill.composites.trace import trace_at
from expedistill.core.contracts import TaskListRow
from expedistill.core.models import Skill
from expedistill.infra.reliability import CodeBugError, ModelOutputError
from expedistill.infra.store import Store, read_jsonl
from tests.helpers.fake_llm import json_script, make_fake_handle
from expedistill.methods import revise
from tests.helpers.skills import make_skill
from tests.helpers.traces import execution_sheet, write_sheets

PHASE = SimpleNamespace(label="p04:learn")


@pytest.fixture()
def env(tmp_path):
    store = Store(tmp_path)
    skill = make_skill("type/001", "When the object is held.")
    store.save(skill, "skills")
    sheet = execution_sheet("tb:t1", "weak", 0, phase_label="p03:practice")
    write_sheets(tmp_path, [sheet], "p03:practice")
    phase_dir = tmp_path / "phases" / "p04_learn"
    phase_dir.mkdir(parents=True)
    return store, skill, [trace_at(sheet, 3)], phase_dir


def _row(skill, side):
    return TaskListRow(target_kind="skill", target_id=skill.skill_id,
                       action=f"revise_{side}", reason=f"{side}_evidence",
                       basis_unit="u")


def _apply(env, side, write, final=True):
    store, skill, sources, phase_dir = env
    return revise.apply_revision(
        store=store, phase=PHASE, row=_row(skill, side),
        phase_dir=phase_dir, run_dir=phase_dir.parents[1], sources=sources,
        write=write, final=final)


def _content(skill):
    return revise.Revision(side="content",
                           fields={"steps": ["Check first.", "Then act."],
                                   "not_applicable_when": "never"},
                           reason="added the check")


def test_a_content_rewrite_lands_a_new_version_with_its_sources(env):
    store, skill, sources, phase_dir = env

    assert _apply(env, "content", _content) == "content_revised"

    stored = store.load("skills", skill.skill_id, Skill)
    assert stored.version == 2
    assert stored.steps == ["Check first.", "Then act."]
    assert (stored.summary, stored.when_to_use) == (skill.summary,
                                                    skill.when_to_use)
    assert stored.rewrite_history == [{
        "side": "content",
        "old": {"steps": skill.steps,
                "not_applicable_when": skill.not_applicable_when},
        "reason": "added the check"}]
    origin = find_provenance(phase_dir.parents[1], skill.skill_id, 2)
    assert origin.cause == "revise_content" and origin.sources == sources
    receipt, = read_jsonl(phase_dir / ledger.OUTCOMES_NAME)
    assert (receipt["outcome"], receipt["version"]) == ("content_revised", 2)


def test_a_receipted_operation_is_not_asked_again(env):
    _apply(env, "content", _content)

    def never(skill):
        raise AssertionError("asked again")

    assert _apply(env, "content", never) == "receipted"


def test_a_crash_after_the_save_only_writes_the_receipt(env):
    store, skill, sources, phase_dir = env
    _apply(env, "content", _content)
    (phase_dir / ledger.OUTCOMES_NAME).unlink()

    def never(skill):
        raise AssertionError("asked again")

    assert _apply(env, "content", never) == "content_revised"
    assert store.load("skills", skill.skill_id, Skill).version == 2


def test_a_store_that_drifted_from_the_plan_is_a_bug(env):
    store, skill, sources, phase_dir = env
    ledger.write_revision_plan(PHASE, _row(skill, "content"),
                               skill.skill_id, 5, phase_dir)
    with pytest.raises(CodeBugError, match="drifted"):
        _apply(env, "content", _content)


def test_an_unchanged_rewrite_and_a_failed_one_only_write_receipts(env):
    store, skill, sources, phase_dir = env

    same = revise.Revision(side="trigger",
                           fields={"summary": skill.summary,
                                   "when_to_use": skill.when_to_use},
                           reason="")
    assert _apply(env, "trigger", lambda s: same) == \
        "unchanged_after_revision"

    def broken(s):
        raise ModelOutputError("no valid rewrite")

    assert _apply(env, "content", broken) == "revision_failed"
    assert store.load("skills", skill.skill_id, Skill).version == 1
    outcomes = [row["outcome"]
                for row in read_jsonl(phase_dir / ledger.OUTCOMES_NAME)]
    assert outcomes == ["unchanged_after_revision", "revision_failed"]


def test_a_failed_rewrite_with_runs_left_raises_without_a_receipt(env):
    store, skill, sources, phase_dir = env

    def broken(s):
        raise ModelOutputError("no valid rewrite")

    with pytest.raises(ModelOutputError):
        _apply(env, "content", broken, final=False)
    assert not (phase_dir / ledger.OUTCOMES_NAME).exists()
    # The next run lands it from the same plan.
    assert _apply(env, "content", _content) == "content_revised"


def test_a_rewrite_of_a_discarded_card_is_a_bug(env):
    store, skill, sources, phase_dir = env
    skill.status = "discarded"
    store.save(skill, "skills")
    with pytest.raises(CodeBugError, match="discarded"):
        _apply(env, "content", _content)


def test_a_rewrite_of_the_wrong_side_is_a_bug(env):
    with pytest.raises(CodeBugError):
        _apply(env, "trigger", _content)


def test_the_trigger_prompt_carries_the_evidence_and_keeps_the_steps():
    skill = make_skill("type/001", "When the object is held.")
    reply = json_script({"summary": "Heat the held object.",
                         "when_to_use": "The object is held and cold.",
                         "revision_reason": "named the entry state"})
    handle = make_fake_handle(script=[reply])

    revision = revise.revise_trigger(
        skill, catalog="THE CATALOG", evidence_steps="THE STEPS",
        diagnosis="THE DIAGNOSIS", playbook="THE PLAYBOOK", profile="",
        handle=handle)

    assert revision.side == "trigger"
    assert revision.fields == {"summary": "Heat the held object.",
                               "when_to_use": "The object is held and cold."}
    prompt = handle.client.calls[0]["user"]
    for text in ("THE CATALOG", "THE STEPS", "THE DIAGNOSIS",
                 "THE PLAYBOOK", skill.when_to_use):
        assert text in prompt


def test_the_content_prompt_carries_the_card_and_the_evidence():
    skill = make_skill("type/001", "When the object is held.")
    reply = json_script({"steps": ["Check.", "Act."],
                         "not_applicable_when": "never",
                         "revision_reason": "r"})
    handle = make_fake_handle(script=["not json", reply])

    revision = revise.revise_content(
        skill, evidence_steps="THE STEPS", diagnosis="THE DIAGNOSIS",
        playbook="", profile="", handle=handle)

    assert revision.fields["steps"] == ["Check.", "Act."]
    prompt = handle.client.calls[-1]["user"]
    assert "THE STEPS" in prompt and "[Steps]" in prompt


def test_the_diagnosis_text_numbers_the_failed_trials():
    text = revise.diagnosis_text([(3, "missed the check"), (5, "looped")])
    assert text == ("[failed trial 1, failing at step 3]\nmissed the check"
                    "\n\n[failed trial 2, failing at step 5]\nlooped")
