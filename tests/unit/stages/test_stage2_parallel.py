"""Stage 2 unit tests: dispatch through run_units (workers > 1).

The serial path -- loading, grouping, the trajectory_select rule, the
empty-trajectory exclusion, per-unit dispositions -- is pinned by
test_stage2.py under the default workers=1; this file covers what the
run_units conversion adds or must preserve:

- workers=4 produces the IDENTICAL store and stage summary as workers=1
  (two run directories, diffed field by field);
- workers=4 really runs units concurrently (a barrier inside a stubbed
  segment_checked proves >= 2 in flight at once);
- one unit's InfrastructureError keeps the other units running, lands in
  the summary's infra_failed, and the stage raises after the summary;
- a re-run over a partial store (stored + infra-failed mix) re-offers
  exactly the pending trajectories, with workers=4;
- a context without a ``workers`` attribute runs serially (the default
  every pre-parallel caller still gets).

The scripted client here is thread-safe and keyed by REQUEST TEXT, not by
call order: llm.fake's FakeClient pops its script in serving order, which
only a serial run makes deterministic. Every planted trajectory carries a
unique action marker its rendered view embeds, so under concurrency the
client can still decide deterministically which unit gets which payload.
"""

import sys
import threading

import pytest

from expedistill.benchmarks.registry import _reset_registry
from expedistill.core.ids import make_task_id, make_trajectory_id
from expedistill.core.models import Step, Trajectory
from expedistill.infra.reliability import (
    InfrastructureError,
    UnitFailureRecorder,
)
from expedistill.infra.store import Store, read_json, read_jsonl
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.call import ModelHandle
from expedistill.llm.fake import json_script
from expedistill.stages import stage2_segmentation
from expedistill.stages.context import RunSettings, StageContext

from tests.unit.stages.test_stage2 import (
    _FAKE, StagePlugin, _install_plugin, make_config, seg,
)

_MODULE = f"expedistill.benchmarks.{_FAKE}"

# One marker per planted trajectory of the shared scenario; none is a
# substring of another, so "which unit is this call for" is decidable from
# the request text alone, under any interleaving.
M_T1_STRONG = "markA"
M_T1_WEAK0 = "markB"
M_T1_WEAK1_EMPTY = "markC"   # 0-step trajectory: excluded before the rule
M_T2_STRONG = "markD"
M_T2_WEAK = "markE"
M_T3_STRONG = "markF"        # t3 stays out of F

ALL_MARKERS = [M_T1_STRONG, M_T1_WEAK0, M_T1_WEAK1_EMPTY, M_T2_STRONG,
               M_T2_WEAK, M_T3_STRONG]

_FAILED = [make_task_id(_FAKE, "t1"), make_task_id(_FAKE, "t2")]
_EMPTY_ID = "traj/fakebench:t1/weak/01"
_T2_STRONG_ID = "traj/fakebench:t2/strong/00"


@pytest.fixture(autouse=True)
def clean_registry():
    # Same discipline as test_stage2.py: the fake plugin's registration is
    # per-test and torn down afterwards.
    _reset_registry()
    yield
    sys.modules.pop(_MODULE, None)
    _reset_registry()


class LockedClient:
    """Thread-safe scripted client: answer by request text, never by order.

    ``respond`` maps a user prompt to a response string or an exception to
    raise. Calls are recorded under a lock, so the marker list is complete
    however the pool interleaves the units.
    """

    def __init__(self, respond):
        self._respond = respond
        self._lock = threading.Lock()
        self.prompts = []

    def complete(self, model, system, user, *, temperature=0.0,
                 max_tokens=None, extra=None):
        with self._lock:
            self.prompts.append(user)
            result = self._respond(user)
        if isinstance(result, BaseException):
            raise result
        return result


def make_ctx(tmp_path, config, segmenter, *, run_name="run", workers=None):
    run_dir = tmp_path / run_name
    ctx = StageContext(
        run_dir=run_dir,
        store=Store(run_dir),
        config=config,
        usage=UsageRecorder(run_dir / "llm_requests" / "usage.jsonl"),
        handles={"segmenter": segmenter},
        workdir_root=run_dir / "workdirs",
        run_settings=RunSettings(workers=workers or 1),
    )
    return ctx


def plant_marked(ctx, task, role, marker, *, attempt=0, n_steps=4):
    """Plant a trajectory whose rendered view is recognisable by ``marker``.

    The view (blocks.view) embeds every action verbatim, so marker-bearing
    actions make one unit addressable under concurrency, where serving
    order says nothing about which trajectory a call belongs to.
    """
    task_id = make_task_id(_FAKE, task)
    trajectory = Trajectory(
        trajectory_id=make_trajectory_id(task_id, role, attempt),
        task_id=task_id,
        benchmark=_FAKE,
        model_role=role,
        model_name=f"{role}-model",
        attempt_index=attempt,
        steps=[Step(action=f"{marker}-{index}", observation=f"o{index}")
               for index in range(n_steps)],
        correctness=False,
    )
    ctx.store.save(trajectory, "trajectories")
    return trajectory


def valid_payload():
    """Two segments covering a 4-step trajectory (passes D7)."""
    return [seg(0, 2, "setup", "prepare", True),
            seg(2, 4, "run", "execute", False)]


def gap_payload():
    """Leaves step 1 uncovered: D7 rejects it, retry limit 0 -> skipped."""
    return [seg(0, 1), seg(2, 4)]


def marker_of(user):
    """The one scenario marker a rendered view carries."""
    hits = [marker for marker in ALL_MARKERS if marker in user]
    assert len(hits) == 1, f"expected exactly one marker, got {hits}"
    return hits[0]


def served(client):
    """The markers the client served, one per call, in serving order."""
    return [marker_of(prompt) for prompt in client.prompts]


def store_tree(ctx):
    """Every stored subtask file, id -> parsed content."""
    return {subtask_id: read_json(ctx.store.path_for("subtasks", subtask_id))
            for subtask_id in ctx.store.ids("subtasks")}


def audit_records(run_dir):
    return read_jsonl(
        run_dir / "stage2_segments" / "segmentation_audit.jsonl")


def _plant_scenario(ctx):
    """Failed t1/t2: four non-empty marked trajectories plus one empty;
    t3 stays out of F."""
    plant_marked(ctx, "t1", "strong", M_T1_STRONG)
    plant_marked(ctx, "t1", "weak", M_T1_WEAK0, attempt=0)
    plant_marked(ctx, "t1", "weak", M_T1_WEAK1_EMPTY, attempt=1, n_steps=0)
    plant_marked(ctx, "t2", "strong", M_T2_STRONG)
    plant_marked(ctx, "t2", "weak", M_T2_WEAK)
    plant_marked(ctx, "t3", "strong", M_T3_STRONG)


def _scenario_handle(gap_marker=None, infra_markers=()):
    """A segmenter handle over the shared scenario's response table.

    ``gap_marker`` gets the D7-rejected payload (K2 skips the trajectory);
    ``infra_markers`` get an InfrastructureError; everyone else segments
    cleanly. The empty trajectory and the task outside F must never be
    asked at all.
    """

    def respond(user):
        marker = marker_of(user)
        assert marker not in (M_T1_WEAK1_EMPTY, M_T3_STRONG), \
            "excluded unit was offered to the model"
        if marker == gap_marker:
            return json_script(gap_payload())
        if marker in infra_markers:
            return InfrastructureError("endpoint down")
        return json_script(valid_payload())

    return ModelHandle(name="fake-model", role="segmenter",
                       client=LockedClient(respond))


# ---------------------------------------------------------------------------
# Equivalence: workers=4 == workers=1, store and summary diffed field by field
# ---------------------------------------------------------------------------

def test_workers_4_matches_workers_1_store_and_summary(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    expected = {"segmented": 3, "skipped": 1, "total_subtasks": 6,
                "infra_failed": [], "empty_skipped": [_EMPTY_ID]}
    runs = {}
    for run_name, workers in (("run_serial", 1), ("run_parallel", 4)):
        ctx = make_ctx(tmp_path, make_config(),
                       _scenario_handle(gap_marker=M_T2_STRONG),
                       run_name=run_name, workers=workers)
        _plant_scenario(ctx)
        result = stage2_segmentation.run(ctx, failed_task_ids=_FAILED)
        assert result == expected
        runs[run_name] = ctx

    serial_ctx, parallel_ctx = runs["run_serial"], runs["run_parallel"]

    # Identical summaries (the written files, not just the return value).
    assert read_json(serial_ctx.run_dir / "stage2_summary.json") == \
        read_json(parallel_ctx.run_dir / "stage2_summary.json") == expected
    # Identical stores: the same ids AND the same file content behind each.
    assert store_tree(serial_ctx) == store_tree(parallel_ctx)
    assert len(store_tree(parallel_ctx)) == 6
    # The same units were modeled, however the pool interleaved them.
    assert sorted(served(serial_ctx.handles["segmenter"].client)) == \
        sorted(served(parallel_ctx.handles["segmenter"].client)) == \
        [M_T1_STRONG, M_T1_WEAK0, M_T2_STRONG, M_T2_WEAK]
    # The audit channel: the empty exclusion (written serially, before
    # dispatch) plus K2's skip record -- the same records in both runs.
    assert audit_records(serial_ctx.run_dir) == \
        audit_records(parallel_ctx.run_dir)
    assert [record["trajectory_id"] for record
            in audit_records(parallel_ctx.run_dir)] == \
        [_EMPTY_ID, _T2_STRONG_ID]


# ---------------------------------------------------------------------------
# workers > 1 really dispatches concurrently
# ---------------------------------------------------------------------------

def test_workers_above_one_dispatches_units_concurrently(
        monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    # The barrier only opens when 4 units are in flight at the same time;
    # a serial stage (or a pool of 1) times out and the test fails.
    barrier = threading.Barrier(4, timeout=10)
    threads = set()
    lock = threading.Lock()

    def stub(**kwargs):
        with lock:
            threads.add(threading.get_ident())
        barrier.wait()
        return None  # every unit counts as skipped

    monkeypatch.setattr(stage2_segmentation, "segment_checked", stub)
    ctx = make_ctx(tmp_path, make_config(), segmenter=None, workers=4)
    for attempt in range(4):
        plant_marked(ctx, "t1", "weak", f"mk{attempt}", attempt=attempt)

    result = stage2_segmentation.run(ctx, failed_task_ids=[
        make_task_id(_FAKE, "t1")])

    assert result == {"segmented": 0, "skipped": 4, "total_subtasks": 0,
                      "infra_failed": [], "empty_skipped": []}
    assert len(threads) >= 2


# ---------------------------------------------------------------------------
# InfrastructureError under parallelism: one hole, the rest keep running
# ---------------------------------------------------------------------------

def test_one_infra_failure_does_not_stop_the_others(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    ctx = make_ctx(tmp_path, make_config(),
                   _scenario_handle(infra_markers={M_T2_STRONG}),
                   run_name="run", workers=4)
    _plant_scenario(ctx)

    with pytest.raises(InfrastructureError) as info:
        stage2_segmentation.run(ctx, failed_task_ids=_FAILED)

    assert "1 unit" in str(info.value)
    assert "resume" in str(info.value)
    # The summary was written before the raise and names the hole; the
    # other three trajectories were segmented around it.
    summary = read_json(ctx.run_dir / "stage2_summary.json")
    assert summary == {"segmented": 3, "skipped": 0, "total_subtasks": 6,
                       "infra_failed": [_T2_STRONG_ID],
                       "empty_skipped": [_EMPTY_ID]}
    assert len(ctx.store.ids("subtasks")) == 6
    assert not any("/t2/strong/" in sid for sid in ctx.store.ids("subtasks"))
    # Infra is not a segmentation failure: no audit record, one ledger line.
    assert [record for record in audit_records(ctx.run_dir)
            if record["trajectory_id"] == _T2_STRONG_ID] == []
    ledger = UnitFailureRecorder(ctx.run_dir).list_failures()
    assert [(row["unit_id"], row["stage"], row["error_class"])
            for row in ledger] == \
        [(_T2_STRONG_ID, "stage2_segmentation", "InfrastructureError")]

    # Resume with the endpoint back, still workers=4: exactly the failed
    # trajectory is re-modeled, the stored ones are only reloaded.
    resumed = make_ctx(tmp_path, make_config(), _scenario_handle(),
                       run_name="run", workers=4)
    result = stage2_segmentation.run(resumed, failed_task_ids=_FAILED)

    assert result == {"segmented": 4, "skipped": 0, "total_subtasks": 8,
                      "infra_failed": [], "empty_skipped": [_EMPTY_ID]}
    assert served(resumed.handles["segmenter"].client) == [M_T2_STRONG]


# ---------------------------------------------------------------------------
# Resume over a partial store: stored skipped, pending re-offered
# ---------------------------------------------------------------------------

def test_resume_after_partial_store_reoffers_only_pending(monkeypatch,
                                                          tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    pending = {M_T1_WEAK0, M_T2_WEAK}

    ctx = make_ctx(tmp_path, make_config(),
                   _scenario_handle(infra_markers=pending),
                   run_name="partial", workers=4)
    _plant_scenario(ctx)

    with pytest.raises(InfrastructureError):
        stage2_segmentation.run(ctx, failed_task_ids=_FAILED)
    # The two non-failing trajectories were stored before the stage raised.
    assert len(ctx.store.ids("subtasks")) == 4

    # A fresh full run over the same scenario (workers=4, nothing failing)
    # is the ground truth the resumed store must end up identical to.
    reference = make_ctx(tmp_path, make_config(), _scenario_handle(),
                         run_name="reference", workers=4)
    _plant_scenario(reference)
    expected = stage2_segmentation.run(reference, failed_task_ids=_FAILED)

    # The resume: only the two pending trajectories cost a model call, and
    # the store reaches exactly the fresh run's content.
    resumed = make_ctx(tmp_path, make_config(), _scenario_handle(),
                       run_name="partial", workers=4)
    result = stage2_segmentation.run(resumed, failed_task_ids=_FAILED)

    assert result == expected
    assert sorted(served(resumed.handles["segmenter"].client)) == \
        sorted(pending)
    assert store_tree(resumed) == store_tree(reference)


# ---------------------------------------------------------------------------
# The default: a context without a workers attribute stays serial
# ---------------------------------------------------------------------------

def test_context_without_workers_runs_serially(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, StagePlugin())
    # Every pre-parallel caller builds the context this way: no workers
    # attribute anywhere, so the stage must take the serial path and keep
    # the deterministic sorted processing order.
    def respond(user):
        return json_script(valid_payload())

    ctx = make_ctx(tmp_path, make_config(),
                   ModelHandle(name="fake-model", role="segmenter",
                               client=LockedClient(respond)))
    plant_marked(ctx, "t1", "strong", M_T1_STRONG)
    plant_marked(ctx, "t1", "weak", M_T1_WEAK0, attempt=0)
    plant_marked(ctx, "t1", "weak", M_T1_WEAK1_EMPTY, attempt=1, n_steps=0)

    result = stage2_segmentation.run(ctx, failed_task_ids=[
        make_task_id(_FAKE, "t1")])

    assert result == {"segmented": 2, "skipped": 0, "total_subtasks": 4,
                      "infra_failed": [], "empty_skipped": [_EMPTY_ID]}
    # Serial means the sorted trajectory order: strong before weak.
    assert served(ctx.handles["segmenter"].client) == \
        [M_T1_STRONG, M_T1_WEAK0]
