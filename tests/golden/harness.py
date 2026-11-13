"""Golden-matrix harness: the "behaviour unchanged" instrument.

What this pins. Two combos of the paper method, differing in the test
phase's delivery: ``whole_bank`` (whole_bank + real, playbook off) and
``folder`` (folder + real_folder, the evolved playbook shown). Both run the
paper's seven-phase sequence ``baseline, learn, practice, learn, practice,
learn, test`` (one ``learn`` definition reused) on a split stub benchmark;
both practices always deliver the previous learn phase's bank as a skill
folder (``folder`` + ``real_folder``) with the evolved playbook. Every learn
phase diagnoses the failed weak trials of the execute phase before it,
cuts and types their segments (and, the first time, the strong baseline
trajectories), routes each failure to gap / trigger / content / none
evidence, evolves the profile and the playbook, and writes a card per gap
type or rewrites one side of a card per trigger or content evidence (the
delete rule in place of a second content rewrite in a row); the test phase
runs the bank against a fresh bare control. EVERY durable artifact of the
run directory is snapshotted -- manifest, phase summaries, object stores,
ledgers, skill bank, answer sheets, llm request dumps -- and a change
passes only if it reproduces the snapshots after the volatile-field
normalisation below.

The scripted scenario. The stub plugin makes no model call; its verdicts
and steps are a fixed function of the task, the role and what the unit
delivers (``GoldenPlugin``): the strong model always succeeds; the weak
model fails every training task, opening every listed card first on task
one and none on tasks two and four, and succeeds on the test task only
with skills in reach. The model side is ``llm/fake_provider.py``'s
FakeProviderClient with ONE pinned choice (``GoldenClient``): which step a
diagnosis blames and the diagnosis scope follow the task (task two fails at
step 0, the search type; task four is ``general``; every other failure is
a ``subtask`` failure at its last step, the answer type) instead of the
fake's prompt hash, so a prompt edit that re-rolls the hash cannot silently
drop a route from the matrix. Every learn phase therefore sees evidence:
two gap cards in the first, a content and a trigger rewrite in the second,
the delete rule and a trigger rewrite in the third
(``test_golden_matrix`` checks this from the outcomes ledgers).

Resume equivalence at artifact level. Each combo is also run with a
simulated crash (the client raises before a chosen model call -- a plain
RuntimeError, so no stage retries it) and then resumed with the same
command; the final artifacts must equal the straight run's snapshot. The
crash points sit inside the learn-phase steps that make model calls per
item (``CRASH_POINTS``): between two diagnoses, before a rewrite and
between two new cards.

Snapshot discipline. A change that deliberately alters run artifacts
regenerates the matrix (``generate.py``) in the same commit, and the diff
is the review artifact; a change that alters nothing must not touch it.
Fake models only: remote links and real endpoints are validated by the
live suite and the formal runs.

Normalisation is deliberately minimal and lives ONLY here: epoch ``time``
/ ``duration_ms`` / uuid ``request_id`` values are blanked; the run
directory's absolute path is elided from any string;
``pipeline_log.jsonl`` is canonicalised to the last line per stage (a
resumed run legitimately appends fresh lines). Nothing else may differ; a
genuine post-crash difference is a finding, not noise to hide.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.machinery
import json
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import yaml

from expedistill.benchmarks.base import (
    BenchmarkPlugin,
    RunOutcome,
    Task,
    ViewRules,
)
from expedistill.cli import run_experiment
from expedistill.llm.fake_provider import FakeProviderClient

#: The stub benchmark name; served through the real import machinery so
#: every stage resolves it exactly like a checked-in plugin.
FAKE_BENCH = "fakebench"
_MODULE = f"expedistill.benchmarks.{FAKE_BENCH}"

_TASK_PROMPT = "FAKE_TASK"
_FOLDER_PROMPT = "FAKE_TASK_FOLDER"

_INSTRUCTIONS = {
    "t1": "inspect the reported state of the service for task one",
    "t2": "inspect the reported state of the service for task two",
    "t3": "inspect the reported state of the service for task three",
    "t4": "inspect the reported state of the service for task four",
}
_SPLIT = {"train": ["t1", "t2", "t4"], "test": ["t3"]}

STRONG_MODEL = "strong-model"
WEAK_MODEL = "weak-model"

#: The fake provider's plan. The stub plugin never asks an execution
#: prompt, so no question is planned; ``segment_steps: 1`` cuts every
#: trajectory into one segment per step (up to the K budget): the last
#: one is the answer type, the others the search type.
_PLAN: Dict[str, Any] = {
    "strong_model": STRONG_MODEL,
    "weak_model": WEAK_MODEL,
    "segment_steps": 1,
    "questions": {},
}

#: The task words ``GoldenClient`` pins its diagnosis on (module
#: docstring): a failure on the first is blamed on step 0, one on the
#: second is ``general``.
EARLY_TASK = "task two"
GENERAL_TASK = "task four"

#: The matrix: one combo per test-phase delivery, labelled by it (the
#: fixture file names).
COMBOS: Tuple[Dict[str, str], ...] = (
    {"delivery": "whole_bank"},
    {"delivery": "folder"},
)


def combo_label(combo: Dict[str, str]) -> str:
    return combo["delivery"]


#: The prompt openings the crash points anchor on.
DIAGNOSIS_ANCHOR = "You analyze ONE failed trial"
REVISE_ANCHOR = "A skill card written for a weaker"
CARD_ANCHOR = "You are writing ONE skill card"

#: Where the crash+resume variants crash: (name, the first words of a
#: prompt, which occurrence of it). The crash happens before that call.
CRASH_POINTS: Tuple[Tuple[str, str, int], ...] = (
    ("diagnosis", DIAGNOSIS_ANCHOR, 2),
    ("revise", REVISE_ANCHOR, 1),
    ("new_cards", CARD_ANCHOR, 2),
)


def crash_points(prompts: List[str]) -> List[int]:
    """The 1-based call numbers of ``CRASH_POINTS`` in a straight run's
    ordered prompt log."""
    points = []
    for name, anchor, occurrence in CRASH_POINTS:
        hits = [index for index, user in enumerate(prompts, start=1)
                if user.startswith(anchor)]
        if len(hits) < occurrence:
            raise AssertionError(
                f"crash point {name!r} needs {occurrence} call(s) starting "
                f"{anchor!r}; the straight run made {len(hits)}")
        points.append(hits[occurrence - 1])
    return points


# ---------------------------------------------------------------------------
# The stub benchmark plugin
# ---------------------------------------------------------------------------

#: Which listed cards the weak model opens, per task: every one or the
#: first one. A task not listed opens none.
_READS = {"t1": "all", "t3": "first"}

#: The one task the weak model can solve, and only with skills in reach.
_SOLVABLE = "t3"


class GoldenPlugin(BenchmarkPlugin):
    """Four tasks. The strong model always succeeds bare. The weak model
    opens the cards ``_READS`` names from a skill folder (an empty-bank
    folder lists none), then works two steps; it fails every training
    task and succeeds on the test task only with skills in reach (a
    skills block, or a folder listing at least one card)."""

    name = FAKE_BENCH
    skill_folder_prompt_name = _FOLDER_PROMPT

    def load_tasks(self, tasks_spec):
        tasks = [Task(task_id=task_id, instruction=text)
                 for task_id, text in _INSTRUCTIONS.items()]
        if tasks_spec is None:
            return tasks
        wanted = set(tasks_spec)
        return [task for task in tasks if task.task_id in wanted]

    def run(self, task, handle, skills_block, workdir):
        strong = handle.role == "executor_strong"
        folder = task.meta.get("skill_folder") or {}
        files = list(folder.get("files") or [])
        steps: List[Dict[str, str]] = []
        if not strong:
            reads = {"all": files, "first": files[:1]}.get(
                _READS.get(task.task_id), [])
            for entry in reads:
                location = entry["filename"]
                card = Path(task.meta["skill_folder_dir"]) / location
                steps.append({"action": f"ReadSkill[{location}]",
                              "observation": card.read_text(
                                  encoding="utf-8")})
        steps.extend([
            {"action": "inspect", "observation": "the service reports an "
                                                 "unexpected state"},
            {"action": "run", "observation": "the environment state"},
        ])
        correctness = strong or (task.task_id == _SOLVABLE
                                 and (bool(skills_block) or bool(files)))
        return RunOutcome(steps=steps, correctness=correctness)

    @property
    def view_rules(self):
        return ViewRules()

    @property
    def task_prompt_name(self):
        return _TASK_PROMPT


@contextmanager
def installed_plugin(plugin: BenchmarkPlugin) -> Iterator[BenchmarkPlugin]:
    """Serve ``plugin`` under the stub package name through the real
    import machinery (registration happens at import, like a checked-in
    plugin) and register its two prompts, which stage 0 digests into the
    manifest. Usable under pytest and standalone."""
    from expedistill.benchmarks.registry import _reset_registry, register_plugin
    from expedistill.prompts import PROMPTS, PromptSpec

    class Loader(importlib.abc.Loader):
        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            register_plugin(plugin)

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == _MODULE:
                return importlib.machinery.ModuleSpec(name, Loader())
            return None

    prompts = {
        _TASK_PROMPT: PromptSpec(
            name=_TASK_PROMPT,
            template="Solve the task: {instruction}\n{skills_block}\n"
                     "{history}",
            description="golden-matrix stub task prompt"),
        _FOLDER_PROMPT: PromptSpec(
            name=_FOLDER_PROMPT,
            template="Solve the task: {instruction}\n{available_skills}\n"
                     "{history}",
            description="golden-matrix stub skill-folder task prompt"),
    }
    sys.modules.pop(_MODULE, None)
    finder = Finder()
    sys.meta_path.insert(0, finder)
    previous = {name: PROMPTS.get(name) for name in prompts}
    PROMPTS.update(prompts)
    try:
        yield plugin
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop(_MODULE, None)
        for name, spec in previous.items():
            if spec is None:
                PROMPTS.pop(name, None)
            else:
                PROMPTS[name] = spec
        _reset_registry()


# ---------------------------------------------------------------------------
# The model side
# ---------------------------------------------------------------------------

class GoldenClient(FakeProviderClient):
    """The fake provider with its diagnosis routing pinned to the task
    (module docstring): a failure on ``EARLY_TASK`` is blamed on step 0,
    one on ``GENERAL_TASK`` is ``general``, every other one is a
    ``subtask`` failure at the fake's own step (the last). The reason and
    the notes stay the fake's own."""

    def __init__(self) -> None:
        super().__init__(dict(_PLAN))

    def _trial_analysis(self, user: str) -> str:
        fenced = FakeProviderClient._trial_analysis(user)
        reply = json.loads(fenced.split("\n", 1)[1].rsplit("\n", 1)[0])
        task = user.split("TASK:\n", 1)[1].split("\n\n", 1)[0]
        if EARLY_TASK in task:
            reply["failing_step"] = 0
        reply["scope"] = "general" if GENERAL_TASK in task else "subtask"
        return "```json\n" + json.dumps(reply, ensure_ascii=False) + "\n```"


class _ClientLog:
    """make_client hook handing out golden clients that share one ordered
    call log."""

    def __init__(self):
        self.clients: List[FakeProviderClient] = []
        self.calls: List[Dict[str, Any]] = []

    def make_client(self, model_name: str) -> FakeProviderClient:
        client = GoldenClient()
        client.calls = self.calls
        self.clients.append(client)
        return client

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def prompts(self) -> List[str]:
        return [call["user"] for call in self.calls]


class SimulatedCrash(RuntimeError):
    """Raised by the crash-injection client before the model call is sent.

    A plain RuntimeError on purpose: no stage retries it and no disposition
    swallows it (only InfrastructureError is retried), so it escapes
    run_experiment exactly like a process death, and the harness resumes.
    """


class CrashAfter:
    """make_client hook crashing before the ``n``-th model call overall."""

    def __init__(self, n: int):
        self.n = n
        self.seen = 0

    def make_client(self, model_name: str) -> Any:
        budget = self

        class _CrashingClient:
            def __init__(self):
                self.inner = GoldenClient()

            @property
            def calls(self):
                return self.inner.calls

            def complete(self, *args, **kwargs):
                budget.seen += 1
                if budget.seen >= budget.n:
                    raise SimulatedCrash(
                        f"simulated crash before model call #{budget.seen}")
                return self.inner.complete(*args, **kwargs)

        return _CrashingClient()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

#: The paper's reference sequence: one ``learn`` and one ``practice``
#: definition, each reused.
SEQUENCE = ("baseline", "learn", "practice", "learn", "practice", "learn",
            "test")


def _learn_phase() -> Dict[str, Any]:
    return {
        "kind": "learn",
        "review": {"name": "review", "K": 5, "retry_limit": 2,
                   "write_lessons": True},
        "author": {"name": "subtask_cards",
                   "typing_chunk_size": 100,
                   "max_segments_per_card": 8,
                   "max_chars_per_segment": 1200,
                   "write_retry_limit": 2},
    }


def _folder(execution: Dict[str, Any]) -> Dict[str, Any]:
    """The skill-folder presentation every practice and the folder test
    share: the active cards as a folder plus the evolved playbook."""
    return {"delivery": {"name": "folder",
                         "description_mode": "summary_then_when_to_use"},
            "execution": execution,
            "playbook": "evolved"}


def _test_phase(delivery: str) -> Dict[str, Any]:
    if delivery == "folder":
        method = _folder({"name": "real_folder"})
    else:
        method = {"delivery": {"name": "whole_bank"},
                  "execution": {"name": "real"}}
    return {"kind": "execute", "role": "test", "tasks": "test",
            "compare": {"against": "fresh_bare"}, **method}


def config_dict(combo: Dict[str, str]) -> Dict[str, Any]:
    """The combo's schema-9 config: the paper reference shape
    (``scripts/run/build_paper_configs.py``) on the stub benchmark."""
    big = {"max_output_tokens": 65536, "max_input_tokens": 524288}
    return {
        "benchmark": FAKE_BENCH,
        "models": {"executor_strong": STRONG_MODEL,
                   "executor_weak": WEAK_MODEL},
        "n_attempts_strong": 1,
        "n_attempts_weak": 1,
        "split": {key: list(value) for key, value in _SPLIT.items()},
        "rules": {"segment_select": "strong_success_weak_failed",
                  "trajectory_select": "strong1_weak1"},
        "token_budgets": {"default_max_output_tokens": 65536,
                          "roles": {"author": dict(big),
                                    "rewriter": dict(big)}},
        "sequence": list(SEQUENCE),
        "phases": {
            "baseline": {"kind": "execute", "role": "baseline",
                         "tasks": "train",
                         "delivery": {"name": "none"},
                         "execution": {"name": "real",
                                       "arms": ["executor_strong",
                                                "executor_weak"]}},
            "learn": _learn_phase(),
            "practice": {"kind": "execute", "role": "practice",
                         "tasks": "train",
                         **_folder({"name": "real_folder", "attempts": 1})},
            "test": _test_phase(combo["delivery"]),
        },
        "schema_version": 9,
    }


def write_config(path: Path, combo: Dict[str, str]) -> Path:
    path.write_text(yaml.safe_dump(config_dict(combo)), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Running and snapshotting
# ---------------------------------------------------------------------------

def run_straight(config_path: Path, run_dir: Path,
                 workers: int = 1) -> List[str]:
    """One uninterrupted run of the whole phase sequence (``workers`` is
    the run's ``--workers``); returns the prompts of its model calls, in
    call order."""
    log = _ClientLog()
    run_experiment(config_path=Path(config_path), run_dir=Path(run_dir),
                   make_client=log.make_client, workers=workers)
    return log.prompts


def is_simulated_crash(exc: BaseException) -> bool:
    """True when ``exc`` is SimulatedCrash or wraps one: worker pools
    collect item failures into ParallelError.failures, and some stage
    drivers re-raise a pool failure as another exception type."""
    seen = set()
    pending = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, SimulatedCrash):
            return True
        pending.extend((current.__cause__, current.__context__))
        pending.extend((getattr(current, "failures", None) or {}).values())
    return False


def run_crash_and_resume(config_path: Path, run_dir: Path,
                         crash_at: int) -> None:
    """Crash before call ``crash_at``, then resume with the same command."""
    budget = CrashAfter(crash_at)
    try:
        run_experiment(config_path=Path(config_path), run_dir=Path(run_dir),
                       make_client=budget.make_client)
    except Exception as exc:
        if not is_simulated_crash(exc):
            raise
    else:
        raise AssertionError(
            f"crash point {crash_at} never fired ({budget.seen} calls seen)")
    run_straight(config_path, run_dir)


#: Values blanked wherever these KEYS appear (module docstring), plus the
#: per-invocation counter ``processed_this_call``, which a crash+resume
#: legitimately splits across calls.
_VOLATILE_KEYS = {"time": "<time>", "duration_ms": "<duration_ms>",
                  "request_id": "<request_id>",
                  "processed_this_call": "<processed_this_call>"}

_LEDGER = "pipeline_log.jsonl"

#: The request history (llm request dumps and the usage ledger) is a
#: chronological call log, not result state: a crash+resume re-asks the
#: interrupted call, so sequence numbers and row order shift while the
#: multiset of calls stays identical. Both are snapshotted as a sorted
#: multiset (every dump's content is still pinned after normalisation).
_USAGE = "llm_requests/usage.jsonl"
_REQUESTS_DIR = "llm_requests/"


def _normalize(value: Any, run_dir: Path) -> Any:
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if key in _VOLATILE_KEYS:
                normalized[key] = _VOLATILE_KEYS[key]
            else:
                normalized[key] = _normalize(item, run_dir)
        return normalized
    if isinstance(value, list):
        return [_normalize(item, run_dir) for item in value]
    if isinstance(value, str):
        prefix = str(run_dir)
        return value.replace(prefix, "<RUN_DIR>") if prefix in value else value
    return value


def snapshot(run_dir: Path) -> Dict[str, Any]:
    """The normalised artifact snapshot: every file under ``run_dir``
    except the run lock, keyed by POSIX-relative path. A missing or extra
    file is a snapshot difference."""
    run_dir = Path(run_dir)
    out: Dict[str, Any] = {}
    request_dumps: List[Dict[str, Any]] = []
    usage_rows: List[Dict[str, Any]] = []
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(run_dir).as_posix()
        if rel == "run.lock":
            continue
        text = path.read_text(encoding="utf-8")
        if rel == _USAGE:
            usage_rows = _normalize(
                [json.loads(line) for line in text.splitlines()
                 if line.strip()], run_dir)
        elif rel.startswith(_REQUESTS_DIR) and path.suffix == ".json":
            # "seq" is the dump's chronological position: call history,
            # carried by the sorted multiset below.
            dump = json.loads(text)
            dump.pop("seq", None)
            request_dumps.append(_normalize(dump, run_dir))
        elif path.suffix == ".jsonl":
            rows = [json.loads(line) for line in text.splitlines()
                    if line.strip()]
            if rel == _LEDGER:
                last = {}
                for row in rows:
                    last[row["stage"]] = row
                rows = [last[stage] for stage in sorted(last)]
            out[rel] = _normalize(rows, run_dir)
        elif path.suffix == ".json":
            out[rel] = _normalize(json.loads(text), run_dir)
        else:
            out[rel] = {"__sha256__": hashlib.sha256(
                text.encode("utf-8")).hexdigest()}
    if usage_rows or (run_dir / _USAGE).exists():
        out[_USAGE] = sorted(usage_rows, key=_canonical)
    if request_dumps:
        out[_REQUESTS_DIR + "dumps"] = sorted(request_dumps, key=_canonical)
    return out


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _is_crash_row(record: Dict[str, Any]) -> bool:
    """True for the simulated crash's own bookkeeping rows (the in-process
    crash gets recorded as a failed attempt; a real process death would
    record nothing). Usage rows carry the error class under
    ``meta.error``; request dumps carry it at the top level."""
    return (record.get("error") == SimulatedCrash.__name__
            or (record.get("meta") or {}).get("error")
            == SimulatedCrash.__name__)


def assert_resume_equivalent(golden: Dict[str, Any],
                             resumed: Dict[str, Any]) -> None:
    """The crash+resume comparator.

    Result state -- every snapshot key except the request history -- must
    be identical. The request history may differ only by (a) the
    simulated crash's own error rows and (b) DUPLICATED re-asks of calls
    whose results were never persisted. A missing call or a NOVEL call
    after resume is a failure.

    K1's ``skip_audit.jsonl`` is in-process-injection bookkeeping too: a
    crashing unit sets the error-stop gate, which appends one row per
    still-queued sibling before the exception surfaces -- rows a real
    process death never writes. The resumed run may carry those rows as
    extras; it may never MISS a row the straight run wrote.
    """
    history = (_USAGE, _REQUESTS_DIR + "dumps")

    def _skip_audit(key: str) -> bool:
        return (key == "skip_audit.jsonl"
                or key.endswith("/skip_audit.jsonl")
                or key == "skip_audit.jsonl.lock"
                or key.endswith("/skip_audit.jsonl.lock"))

    for key in sorted(set(golden) | set(resumed)):
        if key in history or _skip_audit(key):
            continue
        if golden.get(key) != resumed.get(key):
            raise AssertionError(f"resume inequivalence in {key}")
    for key in history:
        want = [row for row in golden.get(key, []) if not _is_crash_row(row)]
        got = [row for row in resumed.get(key, []) if not _is_crash_row(row)]
        missing = list(want)
        extras = []
        for row in got:
            try:
                missing.remove(row)
            except ValueError:
                extras.append(row)
        if missing:
            raise AssertionError(
                f"{key}: {len(missing)} call(s) missing after resume")
        known = {_canonical(row) for row in want}
        for row in extras:
            if _canonical(row) not in known:
                raise AssertionError(
                    f"{key}: novel call after resume: "
                    f"{_canonical(row)[:200]}")
    for key in sorted(key for key in set(golden) | set(resumed)
                      if _skip_audit(key)):
        missing = list(golden.get(key, []))
        for row in resumed.get(key, []):
            if row in missing:
                missing.remove(row)
        if missing:
            raise AssertionError(
                f"{key}: {len(missing)} skip row(s) missing after resume")
