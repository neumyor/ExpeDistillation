"""Skill-folder M3, the pairing checks: the ``real_folder`` execution
entry and its load-time pairing with the ``folder`` delivery.

Three surfaces, one rule (the alignment ruling: the folder delivery and
the real_folder execution are ONE play, pinned to the test phase):

* the registry resolves ``real_folder`` to
  ``RealFolderExecutionParams`` (an empty params class, like the exam
  execution's -- the M6 review removed ``n_attempts`` and
  ``max_injection_tokens``, which no legal phase ever consumed: folder
  is pinned to the test role and the test phase samples at the global
  ``n_attempts_weak`` depth exactly as it always did);
* ``parse_phases`` accepts the folder/real_folder pair on a test-role
  phase and refuses every mismatch: folder under the plain ``real``,
  ``real_folder`` over another delivery, either name on a non-test
  role;
* ``_execution_params`` (the stage's resolution seam) passes the folder
  params through and re-checks the role/params pairing for a directly
  constructed spec -- a mismatched pair is a ConfigError there too, not
  an AttributeError or a silently different execution path.
"""

import pytest

from expedistill.core.config import (
    FOLDER_BY_TASK_DELIVERY_NAME,
    FOLDER_DELIVERY_NAME,
    FOLDER_EXECUTION_NAME,
    ConfigError,
    parse_phases,
)
from expedistill.methods import resolve_params
from expedistill.infra.reliability import ConfigError as RunConfigError
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
    # The two former fields are gone (dead parameters, the M6 removal):
    # consumed by no legal phase, they must fail loudly here rather
    # than be accepted and silently ignored.
    for key in ("n_attempts", "max_injection_tokens"):
        with pytest.raises(ConfigError, match="unknown"):
            resolve_params("execution", {"name": "real_folder", key: 1})


def test_real_folder_params_are_empty_like_the_exams():
    # No fields at all: a test phase samples at the global n_attempts_weak
    # and nothing about the folder run is budgeted at execution time.
    assert not RealFolderExecutionParams.__dataclass_fields__
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
        "sequence": ["baseline", "test"],
        "phases": {
            "baseline": {"kind": "execute", "role": "baseline",
                  "tasks": "train", "delivery": {"name": "none"},
                  "execution": {"name": "real",
                                "arms": ["executor_strong",
                                         "executor_weak"]}},
            "test": {"kind": "execute", "role": role, "tasks": tasks,
                  "delivery": {"name": delivery},
                  "execution": dict(execution, name=execution["name"]),
                  **({"compare": {"against": "baseline"}}
                     if role == "live" else {})},
        },
    }


def _parse_pair(delivery, execution, role="test", tasks="test"):
    raw = _raw_sequence(delivery=delivery, execution=execution, role=role,
                        tasks=tasks)
    return parse_phases(raw["phases"], raw["sequence"],
                        n_attempts_weak=1)


def test_folder_pairs_with_real_folder_on_a_c_phase():
    specs = _parse_pair(FOLDER_DELIVERY_NAME,
                        {"name": FOLDER_EXECUTION_NAME})
    assert specs["test"].execution["name"] == FOLDER_EXECUTION_NAME
    assert specs["test"].delivery["name"] == FOLDER_DELIVERY_NAME


def test_whole_bank_with_plain_real_still_parses():
    # The old test phase is untouched: nothing about the pairing rule
    # touches a non-folder delivery under the real execution.
    specs = _parse_pair("whole_bank", {"name": "real"})
    assert specs["test"].execution["name"] == "real"


def test_folder_under_the_plain_real_execution_is_refused():
    with pytest.raises(ConfigError, match="test-phase skill-folder"):
        _parse_pair(FOLDER_DELIVERY_NAME, {"name": "real"})


def test_folder_by_task_pairs_with_the_plain_real_execution_on_live():
    specs = _parse_pair(FOLDER_BY_TASK_DELIVERY_NAME, {"name": "real"},
                        role="live", tasks="train")
    assert specs["test"].delivery["name"] == FOLDER_BY_TASK_DELIVERY_NAME
    assert specs["test"].execution["name"] == "real"


def test_folder_by_task_is_live_only():
    with pytest.raises(ConfigError, match="live-acceptance"):
        _parse_pair(FOLDER_BY_TASK_DELIVERY_NAME, {"name": "real"},
                    role="test", tasks="test")


def test_folder_by_task_under_real_folder_is_refused():
    with pytest.raises(ConfigError, match="plain 'real' execution"):
        _parse_pair(FOLDER_BY_TASK_DELIVERY_NAME,
                    {"name": FOLDER_EXECUTION_NAME},
                    role="live", tasks="train")


def test_real_folder_over_whole_bank_is_refused():
    with pytest.raises(ConfigError,
                       match="delivery name 'whole_bank' with execution"):
        _parse_pair("whole_bank", {"name": FOLDER_EXECUTION_NAME})


def test_real_folder_on_a_baseline_role_is_refused():
    # A baseline phase is two-armed and runs the real execution; the
    # folder play never leaves the test phase.
    with pytest.raises(ConfigError):
        _parse_pair("none", {"name": FOLDER_EXECUTION_NAME},
                    role="baseline", tasks="train")


def test_real_folder_on_a_live_role_is_refused():
    with pytest.raises(ConfigError):
        _parse_pair("group_by_task", {"name": FOLDER_EXECUTION_NAME},
                    role="live", tasks="train")


def test_folder_on_a_non_test_role_still_refused_first():
    # The M2 half of checklist item 8 holds on its own: the folder
    # delivery on a live phase is refused before the pairing rule is
    # even reached (the delivery is parsed first).
    with pytest.raises(ConfigError, match="legal only for role 'test'"):
        _parse_pair(FOLDER_DELIVERY_NAME,
                    {"name": FOLDER_EXECUTION_NAME},
                    role="live", tasks="train")


def test_real_folder_n_attempts_gets_the_control_depth_cap():
    # The generic depth cap (checklist item 4) is parse-level and covers
    # any execution's stray n_attempts, real_folder's included -- since
    # the M6 removal the key never resolves (stage 0 refuses it as an
    # unknown parameter), but the parse-level cap still fires first for
    # an out-of-range value.
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
        execution={"name": FOLDER_EXECUTION_NAME},
        compare=None, acceptance=None, kind="execute")
    # The arms consistency is real's own rule; the empty folder params
    # carry nothing to check, so the seam returns them untouched.
    assert _execution_params(spec) == RealFolderExecutionParams()


@pytest.mark.parametrize("role,tasks,execution_name", [
    ("baseline", "train", "exam"),
    ("baseline", "train", "real_folder"),
    ("exam", "train", "real"),
    ("exam", "train", "real_folder"),
    ("live", "train", "exam"),
    ("live", "train", "real_folder"),
    ("test", "test", "exam"),
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
        _execution_params(spec)
