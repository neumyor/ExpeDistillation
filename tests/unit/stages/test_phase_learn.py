"""The learn phase driver: the five steps on a hand-built run.

Every scenario runs ``stages.phase_learn`` on the reference sequence
(``baseline, learn, practice, learn, practice, learn, test``) with the stub
benchmark and the fake provider (``tests/helpers/learn_runs.py``). The fake
segmenter cuts one segment per step and labels the last one "answer the
question", the others "search for the evidence"; the typing puts a label
into one type, so ``type/001`` is the search and ``type/002`` the answer.
A diagnosis blames the trial's last step (step 0 for "task two" when the
client is built with ``early``).
"""

from types import SimpleNamespace

import pytest

from expedistill.composites import learn_ledger as ledger
from expedistill.composites import locate
from expedistill.composites.phase_bank import load_phase_bank
from expedistill.composites.provenance import PROVENANCE_NAME
from expedistill.core.config import ExecutePhaseSpec, LearnPhaseSpec
from expedistill.core.models import Skill
from expedistill.infra.reliability import (
    CodeBugError, InfrastructureError, UnitFailureRecorder)
from expedistill.infra.store import read_json, read_jsonl
from expedistill.stages import phase_learn
from tests.helpers.learn_runs import (
    WORK,
    CrashBefore,
    GoldenPlugin,
    LearnRun,
    PinnedClient,
    baseline_trials,
    installed_plugin,
    read_skill,
    run_through,
    trial,
)

CARD = "type/002/skill"


@pytest.fixture(autouse=True)
def plugin():
    with installed_plugin(GoldenPlugin()):
        yield


def practice(index):
    """t1 opens the answer card before failing; t2 never opens it."""
    return [trial("t1", "weak", (read_skill(CARD),) + WORK, correct=False),
            trial("t2", "weak", WORK, correct=False)]


def _rows(run, index, name):
    return read_jsonl(run.phase_dir(index) / name)


def _prompts(client, anchor):
    return [call["user"] for call in client.calls
            if call["user"].startswith(anchor)]


# -- the first learn phase -------------------------------------------------------

def test_the_first_learn_phase_writes_a_card_for_each_gap(tmp_path):
    run = LearnRun(tmp_path / "run")
    run_through(run, 1)

    summary = read_json(run.phase_dir(1) / phase_learn.SUMMARY_NAME)
    assert (summary["input_phase"], summary["first_learn"]) == (
        "p01:baseline", True)
    assert (summary["failed_sheets"], summary["diagnosed"],
            summary["located"]) == (2, 2, 2)
    # Both weak failures and both strong baseline trajectories are cut.
    assert summary["segments_cut"] == 12
    assert summary["types"] == {"table": 2, "new": 2}
    assert summary["evidence"] == {"content": 0, "gap": 2, "none": 0,
                                   "trigger": 0}
    assert summary["outcomes"] == {"written": 1}
    assert summary["bank"]["members"] == [CARD]

    card = run.store.load("skills", CARD, Skill)
    assert (card.subtask_type, card.status, card.version) == (
        "type/002", "active", 1)
    assert card.when_to_use == "The agent is about to answer the question."
    assert all("/strong/" in subtask_id or "/weak/" in subtask_id
               for subtask_id in card.source_subtasks)
    evidence = _rows(run, 1, phase_learn.EVIDENCE_NAME)
    assert [row["sheet_id"] for row in evidence] == sorted(
        row["sheet_id"] for row in evidence)
    assert {row["type_id"] for row in evidence} == {"type/002"}
    provenance, = _rows(run, 1, PROVENANCE_NAME)
    assert provenance["cause"] == "add"
    # The material segments' starts and the two failing steps.
    assert len(provenance["sources"]) == 4
    assert [row["sheet_id"] for row in _rows(
        run, 1, phase_learn.FINDINGS_NAME)] == [
        row["sheet_id"] for row in evidence]


def test_the_first_playbook_reads_the_baseline_facts_only(tmp_path):
    client = PinnedClient()
    run = LearnRun(tmp_path / "run", client=client)
    run_through(run, 1)
    prompt, = _prompts(client, "You maintain the GLOBAL PLAYBOOK")
    # The stub's pack computes no baseline facts.
    assert "Cards the agent had in its catalog" not in prompt


def test_a_gap_without_strong_material_writes_no_card(tmp_path):
    run = LearnRun(tmp_path / "run")
    run.execute(0, baseline_trials(strong_correct=False))
    summary = run.learn(1)
    assert summary["outcomes"] == {"skipped_no_material": 1}
    assert summary["bank"]["members"] == []


# -- later learn phases ------------------------------------------------------------

def test_content_then_trigger_evidence_revises_both_sides(tmp_path):
    client = PinnedClient()
    run = LearnRun(tmp_path / "run", client=client)
    run_through(run, 3, practice)

    summary = read_json(run.phase_dir(3) / phase_learn.SUMMARY_NAME)
    assert (summary["input_phase"], summary["first_learn"]) == (
        "p03:practice", False)
    assert summary["evidence"]["content"] == 1
    assert summary["evidence"]["trigger"] == 1
    assert summary["types"] == {"table": 2, "new": 0}
    outcomes = _rows(run, 3, ledger.OUTCOMES_NAME)
    assert [(row["action"], row["outcome"], row["version"])
            for row in outcomes] == [
        ("revise_content", "content_revised", 2),
        ("revise_trigger", "trigger_revised", 3)]
    card = run.store.load("skills", CARD, Skill)
    assert [entry["side"] for entry in card.rewrite_history] == [
        "content", "trigger"]
    plans = _rows(run, 3, ledger.REVISIONS_NAME)
    assert [row["from_version"] for row in plans] == [1, 2]
    assert [row["cause"] for row in _rows(run, 3, PROVENANCE_NAME)] == [
        "revise_content", "revise_trigger"]

    content, trigger = _prompts(client, "A skill card written for a weaker")
    assert "first read at step 0" not in content   # the rewrite prompt
    assert "ReadSkill[type-002-skill]" in content  # from the card read
    assert "[failed trial 1, failing at step 3]" in content
    assert "THE SKILL CATALOG THE AGENT SAW" in trigger
    assert "type-002-skill/SKILL.md" in trigger
    playbook = _prompts(client, "You maintain the GLOBAL PLAYBOOK")[-1]
    assert '"answer the question": 1 failed trial(s)' in playbook


def test_the_diagnosis_sees_the_catalog_and_the_opened_card(tmp_path):
    client = PinnedClient()
    run = LearnRun(tmp_path / "run", client=client)
    run_through(run, 3, practice)
    diagnoses = _prompts(client, "You analyze ONE failed trial")[-2:]
    opened = [prompt for prompt in diagnoses
              if "first read at step 0" in prompt]
    assert len(opened) == 1
    assert "[When to use] The agent is about to answer" in opened[0]
    assert all("type-002-skill/SKILL.md |" in prompt
               for prompt in diagnoses)


def test_content_evidence_twice_in_a_row_deletes_the_card(tmp_path):
    run = LearnRun(tmp_path / "run")
    run_through(run, 5, practice)

    outcomes = _rows(run, 5, ledger.OUTCOMES_NAME)
    assert [(row["action"], row["outcome"], row["reason"])
            for row in outcomes] == [
        ("delete", "discarded", "content_failed_twice")]
    card = run.store.load("skills", CARD, Skill)
    assert (card.status, card.discard_reason, card.version) == (
        "discarded", "content_failed_twice", 3)
    bank = load_phase_bank(run.run_dir, run.label(5))
    assert [(row["skill_id"], row["status"]) for row in bank] == [
        (CARD, "discarded")]


def test_a_new_gap_in_a_later_phase_gets_a_new_card(tmp_path):
    run = LearnRun(tmp_path / "run")
    run_through(run, 2, practice)
    run.learn(3, client=PinnedClient(early=("task two",)))
    outcomes = _rows(run, 3, ledger.OUTCOMES_NAME)
    assert [(row["target_id"], row["outcome"]) for row in outcomes] == [
        ("type/001", "written"), (CARD, "content_revised")]
    assert load_phase_bank(run.run_dir, run.label(3))[0]["skill_id"] == \
        "type/001/skill"


# -- failures -----------------------------------------------------------------------

class _BadDiagnosis(PinnedClient):
    """Every diagnosis of task two is unusable."""

    def _trial_analysis(self, user):
        if "task two" in user.split("TASK:\n", 1)[1][:200]:
            return "```json\n{\"reason\": \"\"}\n```"
        return super()._trial_analysis(user)


def test_an_unusable_diagnosis_is_terminal_and_gives_no_evidence(tmp_path):
    client = _BadDiagnosis()
    run = LearnRun(tmp_path / "run", client=client)
    run_through(run, 1)

    summary = read_json(run.phase_dir(1) / phase_learn.SUMMARY_NAME)
    assert (summary["diagnosed"], summary["diagnosis_failed"],
            summary["located"]) == (1, 1, 1)
    failures = UnitFailureRecorder(run.run_dir).list_failures()
    assert [row["error_class"] for row in failures] == ["ModelOutputError"]
    asked = len(client.calls)
    (run.phase_dir(1) / phase_learn.SUMMARY_NAME).unlink()
    run.learn(1)
    assert len(client.calls) == asked   # nothing asked again


class _Unsegmentable(PinnedClient):
    """The segmenter cannot cut task two's weak trajectory."""

    def _segment(self, user):
        if "task: fakebench:t2 (final status: failure)" in user:
            return "```json\n[]\n```"
        return super()._segment(user)


def test_a_failure_that_cannot_be_located_is_recorded(tmp_path):
    run = LearnRun(tmp_path / "run", client=_Unsegmentable())
    run_through(run, 1)

    summary = read_json(run.phase_dir(1) / phase_learn.SUMMARY_NAME)
    assert (summary["diagnosed"], summary["located"],
            summary["unlocated"]) == (2, 1, 1)
    failures = UnitFailureRecorder(run.run_dir).list_failures()
    assert [row["error_class"] for row in failures] == ["Unlocated"]
    audit = read_jsonl(run.run_dir / "stage2_segments"
                       / "segmentation_audit.jsonl")
    assert [row["status"] for row in audit] == ["skipped"]


class _DownForCards(PinnedClient):
    """The card writer's endpoint is down."""

    def _card_write(self, user):
        raise InfrastructureError("endpoint down")


def test_an_infrastructure_failure_stops_before_the_bank(tmp_path):
    run = LearnRun(tmp_path / "run")
    run.execute(0, baseline_trials())
    with pytest.raises(InfrastructureError, match="card unit"):
        run.learn(1, client=_DownForCards())

    summary = read_json(run.phase_dir(1) / phase_learn.SUMMARY_NAME)
    assert summary["infra_failed"] == ["type/002"] and "bank" not in summary
    assert not (run.phase_dir(1) / "skill_bank.jsonl").exists()
    assert not (run.phase_dir(1) / ledger.OUTCOMES_NAME).exists()

    resumed = run.learn(1)
    assert resumed["outcomes"] == {"written": 1}
    assert "infra_failed" not in resumed


def test_a_finished_phase_is_skipped_whole(tmp_path):
    client = PinnedClient()
    run = LearnRun(tmp_path / "run", client=client)
    run_through(run, 1)
    asked = len(client.calls)
    before = read_json(run.phase_dir(1) / phase_learn.SUMMARY_NAME)

    assert run.learn(1) == before
    assert len(client.calls) == asked


def test_a_crash_in_typing_resumes_to_the_same_table(tmp_path):
    straight = LearnRun(tmp_path / "straight")
    run_through(straight, 1)
    crashed = LearnRun(tmp_path / "crashed")
    crashed.execute(0, baseline_trials())
    with pytest.raises(RuntimeError, match="simulated crash"):
        crashed.learn(1, client=CrashBefore(PinnedClient(),
                                            "You sort segments of "))
    crashed.learn(1)
    for path in (locate.SUBTASK_TYPES_PATH, locate.SEGMENT_TYPES_PATH):
        assert (crashed.run_dir / path).read_bytes() == \
            (straight.run_dir / path).read_bytes()


# -- the input phase -------------------------------------------------------------------

def _specs(*roles):
    names, specs = [], {}
    for index, role in enumerate(roles):
        name = f"phase{index}"
        names.append(name)
        specs[name] = (LearnPhaseSpec() if role == "learn"
                       else SimpleNamespace(role=role))
    return names, specs


def test_the_input_is_the_nearest_baseline_or_practice(monkeypatch):
    monkeypatch.setattr(phase_learn, "ExecutePhaseSpec", SimpleNamespace)
    names, specs = _specs("baseline", "learn", "practice", "learn")
    assert phase_learn._input_position(names, specs, 1) == 0
    assert phase_learn._input_position(names, specs, 3) == 2
    names, specs = _specs("baseline", "learn", "test", "learn")
    with pytest.raises(CodeBugError, match="'test' phase"):
        phase_learn._input_position(names, specs, 3)
    names, specs = _specs("learn")
    with pytest.raises(CodeBugError, match="no preceding execute phase"):
        phase_learn._input_position(names, specs, 0)


def test_a_learn_phase_refuses_an_execute_spec():
    run = SimpleNamespace(run_dir=None)
    phase = SimpleNamespace(spec=ExecutePhaseSpec.__new__(ExecutePhaseSpec),
                            label="p01:baseline", index=0, name="baseline")
    with pytest.raises(CodeBugError, match="not a LearnPhaseSpec"):
        phase_learn.run(run, phase)
