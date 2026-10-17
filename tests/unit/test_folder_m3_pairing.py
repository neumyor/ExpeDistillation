"""Skill-folder M3, the pairing checks: the ``real_folder`` execution
entry and its load-time pairing with the ``folder`` delivery.

Three surfaces, one rule (the alignment ruling: the folder delivery and
the real_folder execution are ONE play, pinned to the C phase):

* the registry resolves ``real_folder`` to
  ``RealFolderExecutionParams`` (``real``'s two single-arm fields, no
  ``arms``);
* ``parse_phases`` accepts the folder/real_folder pair on a test-role
  phase and refuses every mismatch: folder under the plain ``real``,
  ``real_folder`` over another delivery, either name on a non-test
  role;
* ``_execution_params`` (the stage's resolution seam) passes the folder
  params through -- it has no ``arms`` to check, and the load-time
  checklist already owns the role rules.
"""

import pytest

from expedistill.core.config import (
    FOLDER_DELIVERY_NAME,
    FOLDER_EXECUTION_NAME,
    ConfigError,
    parse_phases,
)
from expedistill.methods import resolve_params
from expedistill.methods.execution import (
    ExamExecutionParams,
    RealExecutionParams,
    RealFolderExecutionParams,
)
from expedistill.stages.phase_execute.resolve import _execution_params
from expedistill.core.config import ExecutePhaseSpec


# ---------------------------------------------------------------------------
# The registry entry and its params class
# ---------------------------------------------------------------------------

def test_real_folder_resolves_its_params():
    assert resolve_params("execution", {"name": "real_folder"}) == \
        RealFolderExecutionParams()
    assert resolve_params("execution", {"name": "real_folder",
                                        "n_attempts": 2,
                                        "max_injection_tokens": 512}) == \
        RealFolderExecutionParams(n_attempts=2,
                                  max_injection_tokens=512)


def test_real_folder_params_have_reals_fields_and_no_arms():
    # real's two single-arm fields, same contracts; no arms field (a
    # folder phase is a C phase -- single-armed, weak model).
    assert set(RealFolderExecutionParams.__dataclass_fields__) == {
        "n_attempts", "max_injection_tokens"}
    with pytest.raises(ConfigError, match="real_folder n_attempts"):
        RealFolderExecutionParams(n_attempts=0)
    with pytest.raises(ConfigError, match="real_folder max_injection"):
        RealFolderExecutionParams(max_injection_tokens=0)
    # The three execution params classes stay distinct types.
    assert RealFolderExecutionParams() != RealExecutionParams()
    assert not isinstance(RealFolderExecutionParams(),
                          RealExecutionParams)
    assert not isinstance(RealFolderExecutionParams(),
                          ExamExecutionParams)


def test_real_folder_refuses_unknown_keys():
    with pytest.raises(ConfigError, match="unknown"):
        resolve_params("execution", {"name": "real_folder",
                                     "arms": ["executor_weak"]})


# ---------------------------------------------------------------------------
# parse_phases: the folder <-> real_folder pairing (checklist item 8)
# ---------------------------------------------------------------------------

def _raw_sequence(*, delivery, execution, role="test", tasks="test"):
    """One execute phase over the given delivery/execution pair -- the
    minimal raw mapping parse_phases accepts shape-wise; the pairing
    rule is what the tests vary."""
    return {
        "sequence": ["D", "C"],
        "phases": {
            "D": {"kind": "execute", "role": "baseline",
                  "tasks": "train", "delivery": {"name": "none"},
                  "execution": {"name": "real",
                                "arms": ["executor_strong",
                                         "executor_weak"]}},
            "C": {"kind": "execute", "role": role, "tasks": tasks,
                  "delivery": {"name": delivery},
                  "execution": dict(execution, name=execution["name"])},
        },
    }


def _parse_pair(delivery, execution, role="test", tasks="test"):
    raw = _raw_sequence(delivery=delivery, execution=execution, role=role,
                        tasks=tasks)
    return parse_phases(raw["phases"], raw["sequence"],
                        n_attempts_strong=1, n_attempts_weak=1)


def test_folder_pairs_with_real_folder_on_a_c_phase():
    specs = _parse_pair(FOLDER_DELIVERY_NAME,
                        {"name": FOLDER_EXECUTION_NAME})
    assert specs["C"].execution["name"] == FOLDER_EXECUTION_NAME
    assert specs["C"].delivery["name"] == FOLDER_DELIVERY_NAME


def test_whole_bank_with_plain_real_still_parses():
    # The old C phase is untouched: nothing about the pairing rule
    # touches a non-folder delivery under the real execution.
    specs = _parse_pair("whole_bank", {"name": "real"})
    assert specs["C"].execution["name"] == "real"


def test_folder_under_the_plain_real_execution_is_refused():
    with pytest.raises(ConfigError, match="pairs delivery 'folder'"):
        _parse_pair(FOLDER_DELIVERY_NAME, {"name": "real"})


def test_real_folder_over_whole_bank_is_refused():
    with pytest.raises(ConfigError,
                       match="delivery name 'whole_bank' with execution"):
        _parse_pair("whole_bank", {"name": FOLDER_EXECUTION_NAME})


def test_real_folder_on_a_baseline_role_is_refused():
    # A baseline phase is two-armed and runs the real execution; the
    # folder play never leaves the C phase.
    with pytest.raises(ConfigError):
        _parse_pair("none", {"name": FOLDER_EXECUTION_NAME},
                    role="baseline", tasks="train")


def test_real_folder_on_a_live_role_is_refused():
    with pytest.raises(ConfigError):
        _parse_pair("group_by_task", {"name": FOLDER_EXECUTION_NAME},
                    role="live", tasks="train")


def test_folder_on_a_non_test_role_still_refused_first():
    # The M2 half of checklist item 8 holds on its own: the folder
    # delivery on a B phase is refused before the pairing rule is
    # even reached (the delivery is parsed first).
    with pytest.raises(ConfigError, match="legal only for role 'test'"):
        _parse_pair(FOLDER_DELIVERY_NAME,
                    {"name": FOLDER_EXECUTION_NAME},
                    role="live", tasks="train")


def test_real_folder_n_attempts_gets_the_control_depth_cap():
    # The generic depth cap (checklist item 4) covers the folder
    # execution's n_attempts like real's: the C comparison never asks
    # for executions the control did not run.
    with pytest.raises(ConfigError, match="exceeds its control's"):
        _parse_pair(FOLDER_DELIVERY_NAME,
                    {"name": FOLDER_EXECUTION_NAME, "n_attempts": 3})


# ---------------------------------------------------------------------------
# The stage's resolution seam
# ---------------------------------------------------------------------------

def test_execution_params_passes_folder_params_through():
    spec = ExecutePhaseSpec(
        role="test", tasks="test",
        delivery={"name": FOLDER_DELIVERY_NAME},
        execution={"name": FOLDER_EXECUTION_NAME, "n_attempts": 1},
        compare=None, acceptance=None, kind="execute")
    # The arms consistency is real's own rule; the folder params carry
    # no arms, so the seam returns them untouched.
    assert _execution_params(spec) == RealFolderExecutionParams(
        n_attempts=1)
