"""The learn phase end to end: a crash anywhere resumes to the straight run,
and the worker count does not change a byte.

The run is the reference sequence through its third learn phase on the
hand-built run of ``tests/helpers/learn_runs.py``. Task two's trials fail
at step 0 (the search type), task one's at their last step (the answer
type), so every learn phase has more than one card unit: two new cards in
the first, a trigger and a content rewrite in the second, a trigger rewrite
and the delete rule in the third.
"""

import pytest

from tests.helpers.learn_runs import (
    WORK,
    CrashBefore,
    GoldenPlugin,
    LearnRun,
    PinnedClient,
    installed_plugin,
    read_skill,
    run_through,
    snapshot,
    trial,
)

LAST = 5
EARLY = ("task two",)


@pytest.fixture(autouse=True)
def plugin():
    with installed_plugin(GoldenPlugin()):
        yield


def practice(index):
    return [trial("t1", "weak", (read_skill("type/002/skill"),) + WORK,
                  correct=False),
            trial("t2", "weak", WORK, correct=False)]


def _client():
    return PinnedClient(early=EARLY)


def _straight(tmp_path, workers=1):
    run = LearnRun(tmp_path / f"straight{workers}", client=_client(),
                   workers=workers)
    run_through(run, LAST, practice)
    return snapshot(run.run_dir)


def test_the_scenario_reaches_every_card_operation(tmp_path):
    files = _straight(tmp_path)
    outcomes = {name: files[name].decode() for name in files
                if name.endswith("outcomes.jsonl")}
    text = "".join(outcomes.values())
    for outcome in ("written", "trigger_revised", "content_revised",
                    "discarded"):
        assert f'"outcome": "{outcome}"' in text


@pytest.mark.parametrize("index, anchor, occurrence", [
    (1, "You analyze ONE failed trial", 2),       # group A, first phase
    (3, "You analyze ONE failed trial", 2),       # group A, later phase
    (3, "You are given the view", 1),             # group A, a cut
    (1, "You sort segments of ", 1),              # group B
    (1, "You maintain the GLOBAL PLAYBOOK", 1),   # group D
    (1, "You are writing ONE skill card", 2),     # group E, between cards
    (3, "A skill card written for a weaker", 2),  # group E, between rewrites
    (5, "A skill card written for a weaker", 1),  # group E, last phase
])
def test_a_crash_resumes_to_the_straight_run(tmp_path, index, anchor,
                                             occurrence):
    straight = _straight(tmp_path)
    run = LearnRun(tmp_path / "crashed", client=_client())
    run_through(run, index - 1, practice)
    crashing = CrashBefore(_client(), anchor, occurrence)
    with pytest.raises(RuntimeError, match="simulated crash"):
        run.learn(index, client=crashing)
    assert crashing.fired
    run_through(run, LAST, practice, first=index)

    assert snapshot(run.run_dir) == straight


def test_eight_workers_write_the_same_bytes_as_one(tmp_path):
    assert _straight(tmp_path, workers=8) == _straight(tmp_path, workers=1)
