"""The ``real_folder`` execution entry and its pairing with the ``folder``
delivery.

Three surfaces, one rule (the folder delivery and the real_folder
execution are ONE play, used by the practice and test phases):

* the registry resolves ``real_folder`` to ``RealFolderExecutionParams``
  (``executor``: weak by default, or the strong model; ``attempts``: the
  sampling depth, the executor's global depth when absent);
* ``parse_phases`` accepts the folder/real_folder pair on a practice or
  test phase and refuses every mismatch: folder under the plain ``real``,
  ``real_folder`` over another delivery, the pair on a baseline;
* ``execution_params`` (the stage's resolution seam) passes the folder
  params through and re-checks the role/params pairing for a directly
  constructed spec -- a mismatched pair is a ConfigError there too, not
  an AttributeError or a silently different execution path.
"""

import pytest

from expedistill.core.config import (
    FOLDER_DELIVERY_NAME,
    FOLDER_EXECUTION_NAME,
    ConfigError,
    ExecutePhaseSpec,
    parse_phases,
)
from expedistill.infra.reliability import ConfigError as RunConfigError
from expedistill.methods import resolve_params
from expedistill.methods.execution import (
    RealExecutionParams,
    RealFolderExecutionParams,
)
from expedistill.stages.phase_execute.resolve import execution_params


# ---------------------------------------------------------------------------
# The registry entry and its params class
# ---------------------------------------------------------------------------

def test_real_folder_resolves_its_params():
    assert resolve_params("execution", {"name": "real_folder"}) == \
        RealFolderExecutionParams()
    for key in ("n_attempts", "max_injection_tokens"):
        with pytest.raises(ConfigError, match="unknown"):
            resolve_params("execution", {"name": "real_folder", key: 1})


def test_real_folder_params_carry_the_executor_and_the_attempts():
    assert list(RealFolderExecutionParams.__dataclass_fields__) == [
        "executor", "attempts"]
    assert RealFolderExecutionParams().executor == "executor_weak"
    assert RealFolderExecutionParams().attempts is None
    assert resolve_params(
        "execution", {"name": "real_folder", "executor": "executor_strong",
                      "attempts": 3}
    ) == RealFolderExecutionParams(executor="executor_strong", attempts=3)
    # The execution params classes stay distinct types.
    assert RealFolderExecutionParams() != RealExecutionParams()
    assert not isinstance(RealFolderExecutionParams(),
                          RealExecutionParams)


@pytest.mark.parametrize("attempts", [0, -1, True, "3"])
def test_real_folder_refuses_a_bad_attempts(attempts):
    with pytest.raises(ConfigError, match="attempts"):
        resolve_params("execution", {"name": "real_folder",
                                     "attempts": attempts})


def test_real_folder_refuses_unknown_keys():
    with pytest.raises(ConfigError, match="unknown"):
        resolve_params("execution", {"name": "real_folder",
                                     "arms": ["executor_weak"]})


# ---------------------------------------------------------------------------
# parse_phases: the folder <-> real_folder pairing
# ---------------------------------------------------------------------------

_BASELINE = {"kind": "execute", "role": "baseline", "tasks": "train",
             "delivery": {"name": "none"},
             "execution": {"name": "real",
                           "arms": ["executor_strong", "executor_weak"]}}


def _parse_pair(delivery, execution, role="test", tasks="test"):
    """The baseline plus one execute phase over the given pair (a practice
    phase sits between a learn phase and the test)."""
    phase = {"kind": "execute", "role": role, "tasks": tasks,
             "delivery": {"name": delivery}, "execution": dict(execution)}
    if role == "practice":
        phases = {
            "baseline": _BASELINE,
            "learn": {"kind": "learn",
                      "review": {"name": "review", "K": 5,
                                 "retry_limit": 2},
                      "author": {"name": "subtask_cards"}},
            "practice": phase,
            "test": {"kind": "execute", "role": "test", "tasks": "test",
                     "delivery": {"name": "whole_bank"},
                     "execution": {"name": "real"}},
        }
        sequence = ["baseline", "learn", "practice", "learn", "test"]
        return parse_phases(phases, sequence)["practice"]
    return parse_phases({"baseline": _BASELINE, "test": phase},
                        ["baseline", "test"])["test"]


def test_folder_pairs_with_real_folder_on_a_test_phase():
    spec = _parse_pair(FOLDER_DELIVERY_NAME, {"name": FOLDER_EXECUTION_NAME})
    assert spec.execution["name"] == FOLDER_EXECUTION_NAME
    assert spec.delivery["name"] == FOLDER_DELIVERY_NAME


def test_folder_pairs_with_real_folder_on_a_practice_phase():
    spec = _parse_pair(FOLDER_DELIVERY_NAME,
                       {"name": FOLDER_EXECUTION_NAME, "attempts": 3},
                       role="practice", tasks="train")
    assert spec.role == "practice"
    assert spec.execution == {"name": FOLDER_EXECUTION_NAME, "attempts": 3}


def test_whole_bank_with_plain_real_still_parses():
    spec = _parse_pair("whole_bank", {"name": "real"})
    assert spec.execution["name"] == "real"


def test_folder_under_the_plain_real_execution_is_refused():
    with pytest.raises(ConfigError, match="pairs with"):
        _parse_pair(FOLDER_DELIVERY_NAME, {"name": "real"})


def test_real_folder_over_whole_bank_is_refused():
    with pytest.raises(ConfigError,
                       match="delivery name 'whole_bank' with execution"):
        _parse_pair("whole_bank", {"name": FOLDER_EXECUTION_NAME})


def test_a_practice_phase_without_the_folder_is_refused():
    with pytest.raises(ConfigError, match="practice"):
        _parse_pair("whole_bank", {"name": "real"}, role="practice",
                    tasks="train")


def test_real_folder_on_a_baseline_role_is_refused():
    # A baseline phase is two-armed and runs the bare real execution.
    with pytest.raises(ConfigError):
        parse_phases({"baseline": {**_BASELINE,
                                   "delivery": {"name": "folder"},
                                   "execution": {"name": "real_folder"}}},
                     ["baseline"])


# ---------------------------------------------------------------------------
# The stage's resolution seam
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role,tasks", [("test", "test"),
                                        ("practice", "train")])
def test_execution_params_passes_folder_params_through(role, tasks):
    spec = ExecutePhaseSpec(
        role=role, tasks=tasks,
        delivery={"name": FOLDER_DELIVERY_NAME},
        execution={"name": FOLDER_EXECUTION_NAME, "attempts": 2},
        compare=None, acceptance=None, kind="execute")
    assert execution_params(spec) == RealFolderExecutionParams(attempts=2)


@pytest.mark.parametrize("role,tasks,execution_name", [
    ("baseline", "train", "real_folder"),
    ("practice", "train", "real"),
])
def test_execution_params_refuse_a_role_execution_mismatch(
        role, tasks, execution_name):
    """A directly constructed spec gets the same loud refusal the YAML
    loader gives: a mismatched pair must not reach a driver as an
    AttributeError or a silently different execution path."""
    spec = ExecutePhaseSpec(
        role=role, tasks=tasks, delivery={"name": "none"},
        execution={"name": execution_name}, compare=None,
        acceptance=None, kind="execute")
    with pytest.raises(RunConfigError, match="does not accept execution"):
        execution_params(spec)
