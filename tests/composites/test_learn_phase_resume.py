"""The learn phase end to end: a crash anywhere resumes to the straight run.
The crash points the golden matrix already takes (between two diagnoses of
the first phase and between two new cards) and its eight-worker equality are
not repeated here.

The run is the reference sequence through its third learn phase on the
hand-built run of ``tests/helpers/learn_runs.py``. Task two's trials fail
at step 0 (the search type), task one's at their last step (the answer
type), so every learn phase has more than one card unit: two new cards in
the first, a trigger and a content rewrite in the second, a trigger rewrite
and the delete rule in the third.
"""

import pytest

from tests.helpers.golden_harness import (
    CrashBefore,
    PinnedClient,
)
from tests.helpers.learn_runs import (
    WORK,
    LearnRun,
    read_skill,
    run_through,
    snapshot,
    trial,
)

pytestmark = pytest.mark.usefixtures("golden_plugin")

LAST = 5
EARLY = ("task two",)


def practice(index):
    return [trial("t1", "weak", (read_skill("type/002/skill"),) + WORK,
                  correct=False),
            trial("t2", "weak", WORK, correct=False)]


def _client():
    return PinnedClient(early=EARLY)


def _straight(tmp_path):
    run = LearnRun(tmp_path / "straight", client=_client())
    run_through(run, LAST, practice)
    return snapshot(run.run_dir)


@pytest.mark.parametrize("index, anchor, occurrence", [
    (3, "You analyze ONE failed trial", 2),       # group A, later phase
    (3, "You are given the view", 1),             # group A, a cut
    (1, "You sort segments of ", 1),              # group B
    (1, "You maintain the GLOBAL PLAYBOOK", 1),   # group D
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
