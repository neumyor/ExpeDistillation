"""Assemble a train-lineage reuse run from a finished source run (K-free).

The TB frozen-split reuse experiment (docs/decisions/
retrieval_delivery_tb_split_20261013.md, section 1) re-runs the pipeline
over a NEW frozen split (AgentBrew evolve/test/validation = train/test/
reserved) while reusing, with ZERO model calls, everything the source run
already produced for the train side: the D-phase trajectories, the
segmentation/authoring/acceptance objects, and the curated bank. This
module is the assembly step: an allowlist copy from the source run into a
fresh run directory, guarded by an identity check, negative-list and
integrity assertions, and an ASSEMBLED_FROM.md provenance record.

Schema worlds (R10): the SOURCE run may be a v3 stage-world run (the
frozen config carries the five ``methods`` slots; the bank lives at the
run root as ``skill_bank.jsonl``) or a v4 phase-world run (the config
carries ``sequence``/``phases``; the bank is the last learn phase's
artifact under ``phases/<label>/``). Both read through the same object
store layout (the five kind directories never moved); the v3 source's
config is upgraded for the identity walk through the pure core mappings
(``upgrade_v2_config`` chaining into ``upgrade_v3_config``), and both
sides of the walk are normalised through ``ExperimentConfig.from_dict``
-> ``to_dict`` so an upgraded slot-completed v3 config and a hand-written
v4 config compare isomorphically (the M1 lesson: compare canonical
forms, never raw rows). The ASSEMBLED run is always v4: the filtered
bank lands both at the run root (the frozen run-level currency, read by
the audit tooling and the pre-learn bank fallback) and pre-seeded as
the LAST learn phase's bank artifact, so the phase pipeline's curation
short-circuits on it (K5's non-empty-dest-bank resume path returns
without any model call) instead of re-curating already-curated
material. The only other phases/ entry the assembly may ever create is
that one artifact file -- a hard assertion pins the tree.

Why a composite and not a script: it owns disk IO, locking and resume
bookkeeping by construction (the layer-5 contract), and it must live in
``src/`` so the identity logic is tested like any other block -- the
2026-10-12 fork of the same shape lived in scripts/ and its manifest
whitelist drifted from the real one on the first try.

Two levels (revision 3 of the integration plan, the 2026-10-13 user
ruling): ``bank`` (the default) is the assembly described below -- the
full train-lineage reuse with the filtered bank, zero model calls.
``baseline`` reuses the D phase's trajectories ONLY: the train-side
two-arm trajectories plus the source run's accepted stage-1 infra
shortfall (train side, skipped when absent). Every downstream phase then
runs FRESH under the current protocol, so the identity check pins only
what the trajectories' reproducibility needs -- {benchmark, both executor
model names, both attempt counts, train within the source task set} --
and every key that only acts downstream (rule names, prompt digests, the
phase definitions, budgets, weak_profile, ...) may differ from the
source run; a pinned key that differs is still a loud ConfigError.

Three load-bearing invariants (integration plan rulings 1/4/5):

* Identity (ruling 4): the new config may differ from the source run's
  frozen manifest ONLY in ``{mode, split, tasks, schema_version, the
  final-test phase's delivery, compare and execution name}``. The last
  three are the v4 image of the v3 delivery slot: the knobs that act
  only at the final test, where the mode also decides the control arm
  (a full-mode C compares against D's weak arm, a split-mode C runs
  fresh_bare or reads the external campaign file), and the execution
  name is the delivery's forced twin (the core pairing check -- only
  the NAME leaf, never the depth or budget leaves). Every
  other key -- benchmark, both executor models, both attempt counts, the
  three rule names, the whole sequence and every phase definition, the
  redispatch budget, token_budgets, model_options, weak_profile, the
  tasks digest -- must match, because the reused artifacts were produced
  under those exact inputs. The prompt-digest snapshots are the
  exception the protocol itself declares (the mixed-protocol statement):
  the new run freezes the CURRENT registry's digests without ever
  calling them, so their drift against the source run is disclosed in
  ASSEMBLED_FROM.md and the summary rather than refused -- a
  byte-identical digest requirement would make every reuse after a
  prompt change impossible, and the assembly only exists because prompts
  changed (ruling 14). The check compares the source manifest against
  the prospective manifest the new run's stage 0 WOULD freeze (same
  derivation, duplicated here because composites may not import
  stages).
* Allowlist copy, "not listed means not copied" (ruling 5): only the
  entries in the module's copy list are brought over; the negative-list
  assertion then verifies the assembled directory contains none of the
  forbidden files (manifest, ledgers, stage summaries, the leak /
  rewrite ledgers, the curation journal, the final-test stores, work/,
  any phase record the source wrote, ...). Copying e.g. a curation log
  would make curation resume a journal whose merge serials do not exist
  in the new store; copying a measurement store would leak the source
  run's numbers into the new one.
* One lineage function (ruling 1 / contract 10): a skill is train-side
  iff the FULL chain -- its own subtask plus every original reached
  through ``merged_from`` -- is train (the anchor-only view would let a
  validation-sourced merge into the bank). The bank filter and the skill
  copy set both call :func:`skill_lineage_tasks`, and a hard assertion
  checks they agree.

Integrity invariants (ruling 5): every kept bank row is bit-identical to
the same-named skill in the new store; the new store's accepted set
equals the kept bank's ACCEPTED row ids (a v4 source's final artifact
may also carry pending members -- unfinished material the assembled
run's A/B phases will measure, disclosed by count); no copied object
carries test/reserved lineage. The segmentation audit is copied WHOLE on
purpose (it is a lazy audit record; a validation-side "empty trajectory"
row in it is not bank or store content and rules nothing), which ruling
5 exempts from the zero-test-lineage invariant.

Copy discipline (M1 acceptance ruling 1, 2026-10-15): the skills and
exams records and the bank rows land at the CURRENT object schema -- a
pre-v2 source run's rows migrate on the copy (a v1 merge product derives
its source_subtasks along merged_from, a v1 exam derives its skill_id),
so the assembled run is v2-native and every consumer's plain from_dict
load works on it. The other kinds stay byte-exact copies: their schema
did not move. A row that cannot migrate is a corrupt source and raises
ConfigError, the family of every other source-integrity refusal.

One v3 concept has NO v4 image and is disclosed instead of copied: the
stage-3 generation-failure ledger (subtasks whose skill generation
terminally failed). The v4 learn phase treats a failed add as retryable
(ModelOutputError is recorded and the next learn phase re-offers it), so
the train-side rows are counted in the summary and ASSEMBLED_FROM.md as
units the assembled run's first learn phase will re-offer -- never
silently dropped, never terminalised by a ledger nothing reads.

The assembled run is NOT a finished run: stage 0 still freezes its own
manifest on the first pipeline start (which is also the identity re-check
of last resort -- B4 compares it field by field), the D phase skips the
stored trajectories, the learn phases re-derive their decisions over the
copied records and short-circuit their curation on the pre-seeded
artifact, and the C phase runs fresh. ASSEMBLED_FROM.md at the run root
records the provenance a reader needs before trusting the numbers.

Locking: the run directory is exclusively locked (B7) for the assembly.
A failed assembly can leave partial files behind; re-running the command
is safe -- object files are overwritten and the ledgers (bank, phase
artifact, accepted shortfall) are written atomically in full, never
appended, so a re-assembly cannot duplicate rows.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
from pathlib import Path, PurePath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from expedistill.composites import shortfall
from expedistill.composites.phase_bank import BANK_NAME, load_phase_bank, phase_bank_dir
from expedistill.blocks import tasks as tasks_block
from expedistill.stages.stage0_freeze import build_manifest
from expedistill.core.config import (
    ExecutePhaseSpec,
    LearnPhaseSpec,
    PHASE_ROLE_TEST,
    ExperimentConfig,
    load_config,
    phase_label,
    upgrade_v2_config,
    upgrade_v3_config,
)
from expedistill.core.ids import make_task_id, parse_trajectory_id
from expedistill.core.models import (
    Attempt,
    Exam,
    Skill,
    Subtask,
    Trajectory,
    migrate_skill_record,
    migrate_skill_rows,
)
from expedistill.infra.manifest import MANIFEST_NAME
from expedistill.infra.reliability import ConfigError
from expedistill.infra.runlock import RunLock
from expedistill.infra.store import KIND_DIRS, read_json, read_jsonl, write_json

__all__ = ["assemble_reuse", "skill_lineage_tasks"]

#: Ruling 4, in v4 spelling: the manifest/config keys a reuse config may
#: change regardless of shape -- the mode, the split, the task set and
#: the schema version. The final-test phase's delivery/compare (the v4
#: image of the v3 delivery slot) is added per target config by
#: :func:`_allowed_diff_prefixes`, because the test phase's LOCAL NAME is
#: the config's own choice.
_BASE_ALLOWED_DIFF_PREFIXES = (
    "mode", "config.mode",
    "split", "config.split",
    "tasks", "config.tasks",
    "schema_version", "config.schema_version",
)

#: The prompt-digest snapshots (top-level ``prompt_digests`` and the
#: frozen config's ``prompts_digests``) are the DECLARED mixed part of the
#: reuse protocol, not identity inputs: the source artifacts were produced
#: under the source run's prompts, while the new run freezes the current
#: registry's digests WITHOUT ever calling them (the config header's
#: mixed-protocol declaration). Ruling 14 makes the point from the other
#: side -- the prompt-digest change is exactly why the old run cannot be
#: resumed and must be re-assembled; a byte-identical digest requirement
#: would make every reuse after a protocol bump impossible. The drift is
#: therefore DISCLOSED (per prompt name) in ASSEMBLED_FROM.md and the
#: returned summary, never silently skipped.
_EXCLUDED_DIFF_KEYS = frozenset({"prompt_digests"})
_EXCLUDED_CONFIG_KEYS = frozenset({"prompts_digests"})

#: Ruling 5: none of these may exist in the assembled run directory. Each
#: is either a measurement of the SOURCE run (the final-test stores,
#: ledgers) or a resume journal whose serials do not survive the
#: train-side filter (the curation journal, the leak/rewrite ledgers);
#: copying any of them would corrupt or fake the new run's own records.
#: The paths are the historical stage names -- the phase world kept the
#: store layout (infra.store.KIND_DIRS) and never wrote new files at the
#: run root, so the v3 spellings are still exactly the ones to forbid.
#: The phases/ tree has its own exact-shape assertion instead (a v4
#: source writes phase records there, and the assembled run may carry
#: ONLY the pre-seeded bank artifact).
_FORBIDDEN_PATHS = (
    MANIFEST_NAME,
    "pipeline_log.jsonl",
    "failed_units.jsonl",
    "skip_audit.jsonl",
    "stage0_summary.json",
    "stage1_summary.json",
    "stage2_summary.json",
    "stage3_generation/stage3_summary.json",
    "stage4_acceptance/stage4_summary.json",
    "stage5_curation/stage5_summary.json",
    "stage5_curation/curation_log.json",
    "stage4_acceptance/leak_verdicts.jsonl",
    "stage4_acceptance/rewrite_failures.jsonl",
)

#: Directories that must not exist in the assembled run at all.
_FORBIDDEN_DIRS = ("llm_requests", "stage6_test", "work")

#: The two reuse levels (revision 3). ``bank`` is the original assembly:
#: the train-lineage artifacts + the filtered bank, zero model calls.
#: ``baseline`` is the D phase's trajectories only (+ the source's
#: accepted stage-1 infra shortfall, train side), with every downstream
#: phase running fresh under the current protocol.
LEVELS = ("bank", "baseline")

#: Baseline-only forbidden paths (revision 3): stage-1-only reuse copies
#: no downstream product, so the ledgers and the filtered bank must not
#: exist in the assembled directory either. The bank level writes all
#: three ON PURPOSE, so they cannot live in the shared negative list.
_BASELINE_FORBIDDEN_PATHS = (
    "skill_bank.jsonl",
    "stage2_segments/segmentation_audit.jsonl",
    "stage3_generation/generation_failures.jsonl",
)

#: Baseline-only empty-kind assertion (revision 3): these store kinds
#: must hold no objects in the assembled run. The directories may be
#: absent (the baseline copy never creates them), but any file inside one
#: means the copy went out of its allowlist or the run_dir was not fresh.
_BASELINE_FORBIDDEN_KINDS = ("subtasks", "skills", "exams", "attempts")

#: ASSEMBLED_FROM.md: the provenance record at the run root (Chinese: a
#: documentation artifact, per the repo's language rules).
_ASSEMBLED_FROM_NAME = "ASSEMBLED_FROM.md"

_SKILL_SUFFIX = "/skill"


# ---------------------------------------------------------------------------
# Lineage (contract 10): the single source of "which tasks is this skill from"
# ---------------------------------------------------------------------------

def skill_lineage_tasks(
    skill: Skill,
    subtasks_by_id: Mapping[str, Subtask],
    *,
    skills_by_id: Optional[Mapping[str, Skill]] = None,
) -> Set[str]:
    """Full-chain lineage task ids of ``skill`` (contract 10).

    The lineage is the task set of every original the skill stands for:
    the task of its own ``subtask_id`` plus, recursively, the lineage of
    every skill named in ``merged_from`` (a merge carries only its
    anchor's subtask id, so the anchor-only view of a merge would hide a
    validation-sourced member -- ruling 1's two leaked merges). Task ids
    are the STORED form (what the source run's Subtask records carry, e.g.
    ``"tb:dna-insert"``), so callers compare against the stored form of
    their split lists, not the raw config names.

    ``subtasks_by_id`` must cover the anchor subtask of every skill passed
    in (a missing anchor is source corruption and raises ConfigError).
    ``merged_from`` entries resolve through ``skills_by_id`` when given;
    without it, an ORIGINAL skill id (``<subtask_id>/skill``, the
    core.ids rule) resolves through ``subtasks_by_id`` directly, which is
    enough for any bank without nested merges (a merge consuming another
    merge). Anything else without ``skills_by_id`` is a loud ConfigError,
    never a silent empty lineage.

    One function, both consumers: the bank filter and the skill copy set
    must call THIS (the assembly hard-asserts they agree), so the leak
    criterion can never fork between "what is copied" and "what is kept
    in the bank".
    """
    tasks: Set[str] = set()
    seen: Set[str] = set()
    stack: List[Skill] = [skill]
    while stack:
        current = stack.pop()
        if current.skill_id in seen:
            continue
        seen.add(current.skill_id)
        subtask = subtasks_by_id.get(current.subtask_id)
        if subtask is None:
            raise ConfigError(
                f"skill {current.skill_id} names anchor subtask "
                f"{current.subtask_id}, which the source run does not "
                "store; the lineage is unverifiable and the source is "
                "corrupt")
        tasks.add(subtask.task_id)
        for source_id in current.merged_from:
            source = None if skills_by_id is None \
                else skills_by_id.get(source_id)
            if source is not None:
                stack.append(source)
            elif source_id.endswith(_SKILL_SUFFIX):
                original = subtasks_by_id.get(
                    source_id[:-len(_SKILL_SUFFIX)])
                if original is None:
                    raise ConfigError(
                        f"skill {current.skill_id} names merged source "
                        f"{source_id}, whose subtask the source run does "
                        "not store; the lineage is unverifiable and the "
                        "source is corrupt")
                tasks.add(original.task_id)
            else:
                raise ConfigError(
                    f"cannot resolve merged source {source_id!r} of skill "
                    f"{current.skill_id}: it is a merged-skill id, so pass "
                    "skills_by_id covering it (the assembly always does)")
    return tasks


# ---------------------------------------------------------------------------
# Prospective manifest: what the new run's stage 0 would freeze
# ---------------------------------------------------------------------------

def _explicit_split_union(config: ExperimentConfig) -> Optional[List[str]]:
    """Sorted union of the explicit split lists (reserved included).

    Mirrors stage0_freeze._explicit_split_ids: with explicit lists and no
    ``config.tasks``, this union is what D1 loads -- and it must include
    reserved, because the held-back tasks are loaded even though neither
    arm runs them.
    """
    if (config.mode == "split" and isinstance(config.split, dict)
            and "train" in config.split):
        return sorted(set(config.split.get("train") or [])
                      | set(config.split.get("test") or [])
                      | set(config.split.get("reserved") or []))
    return None


def _load_spec(config: ExperimentConfig) -> Any:
    """What D1 should load for the prospective run (stage0's rule)."""
    if config.tasks is not None:
        return config.tasks
    return _explicit_split_union(config)


def _canonical(value: Any) -> Any:
    """``value`` as deterministic JSON data (stage0_freeze._canonical).

    Duplicated here because composites may not import stages (layering);
    the tasks digest below must equal the one the source run froze, so
    the serialisation is the same discipline: paths, bytes, enums,
    dataclasses and sets all map to their natural JSON form, and anything
    opaque falls back to a type name plus its repr with memory addresses
    stripped.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(
            item, sort_keys=True, ensure_ascii=False))
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": bytes(value).hex()}
    if isinstance(value, enum.Enum):
        return _canonical(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _canonical(dataclasses.asdict(value))
    kind = f"{type(value).__module__}.{type(value).__qualname__}"
    return {"__repr__": kind + ":" + re.sub(r" at 0x[0-9a-fA-F]+", "",
                                            repr(value))}


def _diff_paths(stored: Any, incoming: Any, prefix: str = "") -> List[str]:
    """Dotted key paths where ``stored`` and ``incoming`` disagree.

    Same walk as infra.manifest's (duplicated so the allowed-key rule is
    applied in one place); a key present on only one side counts as a diff
    at that key's path.
    """
    differing: List[str] = []
    if isinstance(stored, dict) and isinstance(incoming, dict):
        for key in sorted(set(stored) | set(incoming), key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in stored or key not in incoming:
                differing.append(path)
            else:
                differing.extend(
                    _diff_paths(stored[key], incoming[key], path))
        return differing
    if stored != incoming:
        differing.append(prefix or "(top-level value)")
    return differing


def _allowed_diff_prefixes(config: ExperimentConfig) -> tuple:
    """Ruling 4's whitelist for one target config: the shape-independent
    keys plus the final-test phase's delivery, compare and execution-NAME
    mappings (the v4 image of the v3 delivery slot -- the knobs that act
    only at the C phase, where the mode also decides the control arm).
    The phase's LOCAL NAME is whatever the target config calls it, so
    the prefixes are derived, never hardcoded.

    ``execution.name`` joined the whitelist with the folder play: core's
    load-time pairing check (config.py checklist item 8) makes the
    execution name the delivery name's FORCED twin -- ``folder`` pairs
    one-to-one with ``real_folder``, every other delivery with plain
    ``real`` -- so whitelisting the delivery without its twin refused
    every folder-delivery reuse on a retrieval source for a knob the
    target config cannot even choose freely. Only the NAME leaf is
    whitelisted: the execution's other leaves (``n_attempts``,
    ``max_injection_tokens``) change what is measured and stay refused.
    """
    prefixes = list(_BASE_ALLOWED_DIFF_PREFIXES)
    for name in config.sequence:
        spec = config.phases[name]
        if isinstance(spec, ExecutePhaseSpec) and spec.role == PHASE_ROLE_TEST:
            prefixes.append(f"config.phases.{name}.delivery")
            prefixes.append(f"config.phases.{name}.compare")
            prefixes.append(f"config.phases.{name}.execution.name")
    return tuple(prefixes)


def _path_allowed(path: str, prefixes: Sequence[str]) -> bool:
    """True for a diff path covered by the ruling-4 whitelist: a
    shape-independent key, or a leaf under the target's final-test phase
    delivery/compare prefixes."""
    return any(path == prefix or path.startswith(prefix + ".")
               for prefix in prefixes)


def _section(path: str, prefixes: Sequence[str]) -> str:
    """Collapse one diff path for the summary/ASSEMBLED_FROM: the
    final-test delivery/compare keep their mapping-level name (the phase
    name is load-bearing -- ``config.phases`` alone would not say WHICH
    knob drifted); everything else collapses to its first key under
    config, or its top key."""
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix + "."):
            if prefix.startswith("config.phases."):
                return prefix
    parts = path.split(".")
    return "config." + parts[1] if parts[0] == "config" else parts[0]


def _last_learn_label(config: ExperimentConfig) -> Optional[str]:
    """The label of the target sequence's LAST learn phase (where the
    assembly pre-seeds the bank artifact so the curation short-circuits),
    or None when the sequence has no learn phase (the run-level bank is
    then the only currency and nothing is pre-seeded)."""
    positions = [index for index, name in enumerate(config.sequence)
                 if isinstance(config.phases[name], LearnPhaseSpec)]
    if not positions:
        return None
    return phase_label(positions[-1], config.sequence[positions[-1]])


def _source_config(source_manifest: Mapping[str, Any],
                   source_run: Path) -> "tuple[ExperimentConfig, int]":
    """The source run's frozen config as a parsed v4 ExperimentConfig,
    upgraded through the pure core mappings when the source was frozen
    under an older schema, plus the ORIGINAL schema version (the summary
    discloses which world the source came from).

    Both sides of the identity walk go through from_dict -> to_dict, so
    an upgrade-completed v3 config and a hand-written v4 config compare
    isomorphically: the parsed shape is the one canonical form (the M1
    lesson applied to configs -- comparing raw rows would flag every
    v3 source as drifting on key sets its own loader completed).
    """
    raw = source_manifest.get("config")
    if not isinstance(raw, dict):
        raise ConfigError(
            f"the source run {source_run} has a manifest without a "
            "frozen config; only a run frozen by stage 0 can be reused")
    version = raw.get("schema_version", 3)
    if not isinstance(version, int) or isinstance(version, bool) \
            or version not in (2, 3, 4):
        raise ConfigError(
            f"the source run {source_run} froze config schema v{version!r}; "
            "only schema v2, v3 and v4 source runs can be reused (v2 "
            "chains through upgrade_v2_config, v3 through "
            "upgrade_v3_config)")
    try:
        if version == 2:
            raw = upgrade_v2_config(raw)
        if version in (2, 3):
            raw = upgrade_v3_config(raw)
        return ExperimentConfig.from_dict(raw), version
    except ValueError as exc:
        raise ConfigError(
            f"the source run {source_run} has a config that cannot be "
            f"upgraded to schema v4 for the identity walk: {exc}; the "
            "source is corrupt or of an unsupported shape") from exc


def _source_bank_rows(source_run: Path, source_config: ExperimentConfig,
                      source_version: int) -> List[Dict[str, Any]]:
    """The source run's curated bank rows (the one artifact whose
    location moved between the worlds): a v4 source keeps its bank as
    the last learn phase's artifact under phases/, a v3 source at the
    run root. A missing bank is a loud refusal, never an empty read --
    the bank IS what bank-level reuse reuses."""
    if source_version == 4:
        label = _last_learn_label(source_config)
        if label is None:
            raise ConfigError(
                f"the source run {source_run} has no learn phase, so it "
                "never produced a bank artifact; bank-level reuse has "
                "nothing to reuse")
        return load_phase_bank(source_run, label)
    path = source_run / "skill_bank.jsonl"
    if not path.is_file():
        raise ConfigError(
            f"the source run {source_run} has no run-level "
            "skill_bank.jsonl; only a finished v3 run can be reused at "
            "the bank level")
    return read_jsonl(path)


def _identity_view(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """The manifest with the excluded keys dropped from the identity walk.

    The prompt-digest snapshots live outside the comparison (see
    _EXCLUDED_DIFF_KEYS); everything else -- every method input, the tasks
    digest, the aux metric -- is walked.
    """
    view = {key: value for key, value in manifest.items()
            if key not in _EXCLUDED_DIFF_KEYS}
    config = view.get("config")
    if isinstance(config, dict):
        view["config"] = {key: value for key, value in config.items()
                          if key not in _EXCLUDED_CONFIG_KEYS}
    return view


# ---------------------------------------------------------------------------
# Source store reading (read-only; never instantiates Store on the source,
# which would create directory skeletons there)
# ---------------------------------------------------------------------------

def _source_objects(source_run: Path,
                    kind: str) -> Dict[str, Dict[str, Any]]:
    """Every stored object of ``kind`` as raw parsed JSON, by decoded id."""
    directory = source_run / KIND_DIRS[kind]
    if not directory.is_dir():
        return {}
    objects: Dict[str, Dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        objects[urllib.parse.unquote(path.stem)] = read_json(path)
    return objects


def _class_checksum(items: Sequence[Any]) -> str:
    """sha256 over sorted (id, content-sha256) pairs -- a digest that
    changes if any copied file's bytes or the id set changes."""
    digest = hashlib.sha256()
    for obj_id, file_digest in sorted(items):
        digest.update(f"{obj_id}\x00{file_digest}".encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl_atomic(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    """Replace ``path`` with exactly ``rows``, one canonical JSON line each.

    The assembly is the sole writer (the run lock is held) and must be
    idempotent: re-running over a half-assembled directory overwrites the
    ledger instead of appending duplicates to it. Same temp-then-rename
    discipline as infra.store.write_json.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(
                    row, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Copy mechanics shared by both levels (behavior-identical extractions of
# the original bank-level code, so the two levels cannot drift apart)
# ---------------------------------------------------------------------------

def _copy_kind(source_run: Path, run_dir: Path, kind: str,
               kept: Mapping[str, Any],
               counts: Dict[str, int],
               checksums: Dict[str, str]) -> None:
    """Byte-exact copy of every kept object into its kind dir.

    Only for the kinds whose stored rows can be copied verbatim (trajectories,
    subtasks, attempts); skills and exams go through _write_migrated_kind
    instead (M1 acceptance ruling 1). A v1 trajectory row copied here keeps
    its v1 bytes and migrates on read (R2b: Trajectory.from_dict derives
    ``correctness``; a v1 ``infra_error`` row refuses loudly)."""
    directory = run_dir / KIND_DIRS[kind]
    directory.mkdir(parents=True, exist_ok=True)
    pairs = []
    for obj_id in kept:
        source_path = (source_run / KIND_DIRS[kind]
                       / (urllib.parse.quote(obj_id, safe="")
                          + ".json"))
        target_path = directory / source_path.name
        shutil.copy2(source_path, target_path)
        pairs.append((obj_id, _file_sha256(target_path)))
    counts[kind] = len(pairs)
    checksums[kind] = _class_checksum(pairs)


def _write_migrated_kind(run_dir: Path, kind: str,
                         kept: Mapping[str, Any],
                         counts: Dict[str, int],
                         checksums: Dict[str, str]) -> None:
    """Write every kept object's record at the CURRENT object schema.

    The objects in ``kept`` are the ones the load step already migrated
    (v2 rows pass through unchanged), and to_dict serializes exactly the
    way the store writes a record, so the assembled skills/exams files
    are what a v2-native run would hold and every consumer's plain
    from_dict read works on them.
    """
    directory = run_dir / KIND_DIRS[kind]
    directory.mkdir(parents=True, exist_ok=True)
    pairs = []
    for obj_id, obj in kept.items():
        target_path = directory / (urllib.parse.quote(obj_id, safe="")
                                   + ".json")
        write_json(target_path, obj.to_dict(), indent=2)
        pairs.append((obj_id, _file_sha256(target_path)))
    counts[kind] = len(pairs)
    checksums[kind] = _class_checksum(pairs)


def _load_shortfall_units(source_run: Path,
                          train_ids: Set[str]) -> List[str]:
    """Stage 1's accepted infra holes, train side only (both levels).

    A hole is a persistent per-run decision, so only the train-side
    units carry over (a hole on a task the new run never dispatches is
    inert, and copying it would smuggle test/reserved lineage into the
    assembled run). A missing file means the source accepted no holes;
    the baseline level skips it, the bank level writes the (possibly
    empty) train-side remainder.
    """
    shortfall_units: List[str] = []
    shortfall_path = source_run / "stage1_baseline" / \
        "accepted_infra_shortfall.json"
    if shortfall_path.is_file():
        record = read_json(shortfall_path)
        for unit_id in record.get("unit_ids") or []:
            try:
                unit_task = parse_trajectory_id(str(unit_id))[0]
            except ValueError:
                raise ConfigError(
                    f"accepted_infra_shortfall names a non-trajectory unit "
                    f"id {unit_id!r}; the source is corrupt") from None
            if unit_task in train_ids:
                shortfall_units.append(str(unit_id))
    return shortfall_units


def _write_shortfall(run_dir: Path, shortfall_units: Sequence[str],
                     counts: Dict[str, int],
                     checksums: Dict[str, str]) -> None:
    """Write the train-side accepted shortfall ledger (both levels)."""
    if shortfall_units:
        target_shortfall = (run_dir / "stage1_baseline"
                            / "accepted_infra_shortfall.json")
        shortfall.write_unit_ids(target_shortfall, shortfall_units)
    counts["accepted_infra_shortfall_units"] = len(shortfall_units)
    checksums["accepted_infra_shortfall"] = (
        _file_sha256(run_dir / "stage1_baseline"
                     / "accepted_infra_shortfall.json")
        if shortfall_units else _class_checksum([]))


def _campaign_seeded(kept_trajectories: Mapping[str, Trajectory]
                     ) -> Dict[str, Any]:
    """Campaign-seeding provenance of the copied trajectories (both
    levels: the trajectories are the one artifact both of them carry)."""
    seeded = {"count": 0, "origin_runs": {}}
    for obj in kept_trajectories.values():
        meta = obj.meta or {}
        if "campaign_origin_run" in meta:
            seeded["count"] += 1
            origin = str(meta["campaign_origin_run"])
            seeded["origin_runs"][origin] = \
                seeded["origin_runs"].get(origin, 0) + 1
    return seeded


def _assert_negative_list(run_dir: Path, *, baseline: bool) -> None:
    """Ruling 5's negative list; the baseline level adds its own entries.

    The bank level copies the filtered bank and the stage-2/3 ledgers on
    purpose, so the baseline-only entries (the bank file, the ledgers,
    the four downstream kind dirs) are asserted only for the baseline
    assembly, where every one of them must be absent (dirs may be absent
    as such, but never non-empty).
    """
    present = [name for name in _FORBIDDEN_PATHS
               if (run_dir / name).exists()]
    present += [name + "/" for name in _FORBIDDEN_DIRS
                if (run_dir / name).is_dir()]
    if baseline:
        present += [name for name in _BASELINE_FORBIDDEN_PATHS
                    if (run_dir / name).exists()]
        for kind in _BASELINE_FORBIDDEN_KINDS:
            directory = run_dir / KIND_DIRS[kind]
            if directory.is_dir() and any(directory.iterdir()):
                present.append(KIND_DIRS[kind] + "/")
    if present:
        raise ConfigError(
            f"the assembled run contains file(s) the assembly must "
            f"never create: {present}; either the run_dir was not "
            "fresh or the assembly copied out of its allowlist -- a "
            "human must look at this directory")


def _assert_phase_tree(run_dir: Path,
                       preseed_label: Optional[str]) -> None:
    """The phases/ tree of an assembled run holds EXACTLY the pre-seeded
    bank artifact (one file), or nothing at all.

    A v4 source run writes its phase records, units, reports, ledgers and
    summaries under phases/ -- every one of them is a measurement or
    resume journal of the SOURCE and must not leak into the new run (the
    phase-world twin of the v3 negative list; the only sanctioned entry
    is the bank artifact the curation short-circuit needs). A stray
    phases/ entry means the run_dir was not fresh or the copy went out
    of its allowlist.
    """
    phases_dir = run_dir / "phases"
    if preseed_label is None:
        if phases_dir.exists():
            raise ConfigError(
                f"the assembled run contains a phases/ tree the assembly "
                f"must never create (the target sequence has no learn "
                f"phase, so nothing is pre-seeded): "
                f"{sorted(p.as_posix() for p in phases_dir.rglob('*'))}; "
                "the run_dir was not fresh -- a human must look at this "
                "directory")
        return
    expected = phase_bank_dir(run_dir, preseed_label) / BANK_NAME
    expected_rel = expected.relative_to(run_dir).as_posix()
    actual = sorted(
        path.relative_to(run_dir).as_posix()
        for path in phases_dir.rglob("*") if path.is_file())
    if actual != [expected_rel]:
        raise ConfigError(
            f"the assembled run's phases/ tree must hold exactly "
            f"{[expected_rel]}, found {actual}; either the run_dir was "
            "not fresh or the assembly copied a source phase record -- "
            "a human must look at this directory")


# ---------------------------------------------------------------------------
# ASSEMBLED_FROM.md
# ---------------------------------------------------------------------------

def _merged_original_groups(
        source_skills: Mapping[str, Skill],
        copied_skill_ids: Set[str],
        kept_bank_ids: Set[str]) -> Dict[str, Any]:
    """Train-side merged originals, split by whether a KEPT merge consumes
    them (ruling 1: 11 consumed + 5 dangling in the real run -- counted
    here, never hardcoded).

    A "merged original" is an original skill the source run's curation
    folded away (``status == "merged"``) whose own lineage is train-only.
    Its consumer merge survived the bank filter or it did not: if every
    consuming merge was filtered out (a validation-sourced member), the
    original dangles in the new store -- status "merged" with nothing left
    that carries its content on.
    """
    consumed_by_kept: Set[str] = set()
    for skill_id in kept_bank_ids:
        # Every original reachable through the kept rows' merged_from
        # chains (a kept row's whole chain is train-side by construction).
        stack = list(source_skills[skill_id].merged_from)
        seen: Set[str] = set()
        while stack:
            source_id = stack.pop()
            if source_id in seen:
                continue
            seen.add(source_id)
            source = source_skills.get(source_id)
            if source is None:
                continue
            if source.merged_from:
                stack.extend(source.merged_from)
            else:
                consumed_by_kept.add(source_id)
    merged_originals = sorted(
        skill_id for skill_id, skill in source_skills.items()
        if skill.status == "merged" and skill_id in copied_skill_ids)
    dangling = sorted(
        skill_id for skill_id in merged_originals
        if skill_id not in consumed_by_kept)
    return {
        "train_merged_originals": len(merged_originals),
        "consumed_by_kept_merges": len(merged_originals) - len(dangling),
        "dangling": dangling,
    }


def _render_assembled_from(
        *,
        source_run: Path,
        source_manifest: Mapping[str, Any],
        config_path: Path,
        run_dir: Path,
        split_sizes: Mapping[str, int],
        allowed_diffs: Sequence[str],
        digest_drift: Sequence[str],
        counts: Mapping[str, Any],
        checksums: Mapping[str, str],
        merged: Mapping[str, Any],
        campaign_seeded: Mapping[str, Any],
        source_version: int,
        preseed_label: Optional[str],
) -> str:
    """ASSEMBLED_FROM.md text (Chinese: a documentation artifact)."""
    lines: List[str] = []
    add = lines.append
    add("# 装配溯源（ASSEMBLED_FROM）")
    add("")
    add(f"- 源 run：`{source_run}`（benchmark {source_manifest['benchmark']!r}，"
        f"mode {source_manifest['mode']!r}，"
        f"tasks_digest {str(source_manifest.get('tasks_digest'))[:12]}…，"
        f"配置 schema v{source_version}）")
    add(f"- 复用配置：`{config_path}`（mode split；train/test/reserved = "
        f"{split_sizes['train']}/{split_sizes['test']}/"
        f"{split_sizes['reserved']}）")
    add(f"- 装配目标：`{run_dir}`（v4 相位世界 run）")
    add("")
    add("## 划分 provenance")
    add("")
    add("- 三列表原样来自 AgentBrew 冻结划分 JSON（seed 42，revision "
        "`2fd12b88aafdd04a52c298e3940bcb189f9766d6`）："
        "train = evolve(41)、test = test(28)、reserved = validation(20)；"
        "两两不交、并集 89。只映射不重跑划分。")
    add("- 知情声明：tasks_digest 只覆盖任务的 id、instruction 与 meta"
        "（TB 的 Task 只携带任务名），**远端任务目录的内容漂移不在其"
        "覆盖范围**；复用假设远端环境与源 run 时点一致"
        "（AgentBrew revision 2fd12b8）。")
    add("- 方案与契约：`docs/decisions/retrieval_delivery_tb_split_20261013.md`"
        "（§0 裁决 1/4/5/6、§1.1–§1.4、§3.9 契约 6/7/10）。")
    add("")
    add("## 同一性校验（白名单 diff，裁决 4）")
    add("")
    add("- 源 manifest 的配置先经纯映射升到 v4 形状（v2 源经 "
        "upgrade_v2_config 链 upgrade_v3_config，v3 源经 upgrade_v3_config，"
        "v4 源原样），两侧再经 from_dict → to_dict 规整为同一规范形状后"
        "逐键比对；仅允许 {mode, split, tasks, schema_version, 终测相位的 "
        "delivery 与 compare} 差异（后两项是 v3 delivery 槽的 v4 对应物，"
        "mode 同时决定终测对照臂的取法）。benchmark、双 executor 模型、"
        "双 n_attempts、三条规则名、整个 sequence 与全部相位定义、"
        "invalid_redispatch_limit、token_budgets、model_options、"
        "weak_profile、tasks_digest 任一不一致即拒绝装配。")
    add(f"- 本次实际发生的允许差异：{', '.join(allowed_diffs) or '（无）'}。")
    add("- 混装声明（协议 v2）：prompt digest 不在同一性校验内——新 run "
        "按当前 prompt 注册表冻结 v2 digest，但复用链零模型调用、这些 "
        "prompt 永不被执行；被拷贝的产物由源 run 在其冻结的旧 prompt 下"
        "产生。与源 run 相比发生 digest 漂移的 prompt："
        f"{', '.join(digest_drift) or '（无）'}。")
    add("")
    add("## v4 装配产物与续跑行为")
    add("")
    if preseed_label is not None:
        add(f"- 过滤后的库同时落在 run 根（skill_bank.jsonl）与末个 learn "
            f"相位的产物（`phases/{preseed_label.replace(':', '_')}/"
            "skill_bank.jsonl，装配预置）：末个 learn 相位的整理在非空"
            "产物上短路返回，零模型调用，不重理已理过的库；phases/ 树"
            "此外一个文件都没有（硬断言）。")
        add("- 披露（Wave 3 评审 B1 裁决，2026-10-16）：末相位产物 = "
            "源 run 的整理结果（合并后的行原样保留）+ 本 run 在末相位"
            "之前新接受的成员按其存储记录原样追加（整理短路前的程序化"
            "刷新，零模型调用），后者**未经整理**——随库带入的待定成员"
            "考过接受、v3 源生成失败行的重试产物都走这条路。")
    else:
        add("- 目标 sequence 不含 learn 相位，库只落在 run 根"
            "（skill_bank.jsonl），无相位产物预置。")
    add("- 首次管线启动：p00 冻结 manifest（即同一性终检），D 相位按"
        "存储轨迹跳过执行，learn 相位在拷贝记录上重推决策、整理短路，"
        "C 相位全新执行。")
    if counts.get("generation_failure_rows"):
        add(f"- 披露：源 run 的 v3 阶段-3 generation_failures 有 "
            f"{counts['generation_failure_rows']} 条 train 溯源行，"
            "v4 没有可承载它的账本（学习相位对失败新增的口径是重试"
            "而非终态），这些单元将由新 run 首个 learn 相位重新尝试"
            "（真实模型调用），不拷贝、不静默。")
    if counts.get("pending_bank_rows"):
        add(f"- 披露：源 run 的末相位库产物携带 "
            f"{counts['pending_bank_rows']} 条 train 溯源的待定成员"
            "（源 run 未完成检验的材料），随库与 store 原样带入，"
            "新 run 的 A/B 相位将真实测量它们。")
    add("")
    add("## 全链口径（契约 10）")
    add("")
    add("- skill 的 train 判定 = `skill_lineage_tasks` 全链口径：自身 "
        "subtask 的任务 + `merged_from` 递归到底的每个原件的任务，"
        "全部落在 train 才保留（锚点口径会漏入含 validation 溯源的 "
        "merge）。库过滤与 skill 拷贝集出自同一溯源函数，装配断言二者"
        "逐 id 一致。")
    add("- segmentation_audit.jsonl 整文件拷贝（含 validation 侧的惰性"
        "审计记录，裁决 5 豁免，不属 store 五类对象与库行）。")
    add("- 对象行已迁移为 schema v2：skills / exams 的存储记录与 "
        "skill_bank 行经 core 迁移函数转 v2 后落盘（v1 源的合并产物沿 "
        "merged_from 推导 source_subtasks、考题换后缀推导 skill_id，"
        "v2 源恒等穿过），装配产物为 v2 原生 run；trajectories / "
        "subtasks / attempts 仍逐字节拷贝。")
    add("")
    add("## 逐类计数与校验和")
    add("")
    add("| 类别 | 计数 | sha256 |")
    add("|---|---|---|")
    for name in ("trajectories", "subtasks", "skills", "exams", "attempts"):
        add(f"| {name} | {counts[name]} | `{checksums[name]}` |")
    add(f"| skill_bank 行 | {counts['bank_rows']} | "
        f"`{checksums['skill_bank']}` |")
    if "phase_bank" in checksums:
        add(f"| 相位库产物（预置） | {counts['bank_rows']} | "
            f"`{checksums['phase_bank']}` |")
    add(f"| v3 generation_failures 行（train 溯源，不拷贝） | "
        f"{counts['generation_failure_rows']} | — |")
    add(f"| segmentation_audit 行 | {counts['segmentation_audit_rows']} | "
        f"`{checksums['segmentation_audit']}` |")
    add(f"| accepted_infra_shortfall 单元 | "
        f"{counts['accepted_infra_shortfall_units']} | "
        f"`{checksums['accepted_infra_shortfall']}` |")
    add("")
    add("## train 侧 merged 原件（裁决 1，装配时计算）")
    add("")
    add(f"- 共 {merged['train_merged_originals']} 条 train 溯源的 merged "
        f"原件：{merged['consumed_by_kept_merges']} 条被过滤后库内的 merge "
        "正常消费；"
        f"{len(merged['dangling'])} 条真悬空（消费它的 merge 因含 "
        "test/reserved 溯源被过滤，原件以 status=merged 留在新 store，"
        "不再有任何存活 merge 承载其内容）。")
    if merged["dangling"]:
        add("- 悬空清单：")
        for skill_id in merged["dangling"]:
            add(f"  - `{skill_id}`")
    add("")
    add("## D 相位播种溯源")
    add("")
    seeded = campaign_seeded["count"]
    total = counts["trajectories"]
    if seeded:
        add(f"- 拷贝的 {total} 条 train 双臂轨迹中 {seeded} 条携带源 run 的 "
            "campaign 播种溯源 meta（TB2.1 campaign 归档经 "
            "`scripts/run/seed_tb_stage1.py` 播种，attempt 0..2 = 归档 rank "
            "1..3）；拷贝逐字节保留各轨迹 meta 的 campaign_* 字段。")
    else:
        add(f"- 拷贝的 {total} 条 train 双臂轨迹均无 campaign 播种溯源 "
            "meta（源 run 的 D 相位为直接执行，非归档播种）。")
    origins = campaign_seeded.get("origin_runs") or {}
    if origins:
        add("- 溯源 origin run 分布：" + "、".join(
            f"{name}（{count} 条）" for name, count
            in sorted(origins.items())) + "。")
    add("")
    add("## 未拷贝清单（“未列即不拷”，裁决 5）")
    add("")
    add("以下文件/目录存在于源 run 也**不**进入新 run（负清单断言逐项"
        "核对不存在）：manifest.json、pipeline_log.jsonl、llm_requests/、"
        "failed_units.jsonl、skip_audit.jsonl、各 stage summary、"
        "stage5_curation/curation_log.json、stage6_test/、work/、"
        "stage4 的 leak_verdicts.jsonl 与 rewrite_failures.jsonl；"
        "v4 源的 phases/ 树（相位记录、units、报告、账本、摘要）整体"
        "不拷——唯一例外是上面预置的相位库产物。"
        "新 run 的 manifest 由首次管线启动时 p00 自行冻结。")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The baseline level (revision 3): stage-1-only reuse
# ---------------------------------------------------------------------------

def _source_task_ids(source_manifest: Mapping[str, Any],
                     source_run: Path) -> Set[str]:
    """Raw task ids of the source run's loaded task set.

    Stage 0 freezes the split with every loaded task in exactly one of
    the three lists (full mode: train == test == everything, reserved
    empty), so the union of the manifest's split lists IS the source
    task set -- recovered from the manifest, not from the plugin, so
    the subset check needs no remote environment and no task loading.
    """
    split = source_manifest.get("split")
    if not isinstance(split, dict):
        raise ConfigError(
            "the source run's manifest records no split mapping; only a "
            "run frozen by stage 0 can be reused")
    ids: Set[str] = set()
    for value in split.values():
        if isinstance(value, list):
            ids.update(item for item in value if isinstance(item, str))
    if not ids:
        raise ConfigError(
            "the source run's manifest split lists are empty; the source "
            "task set is unverifiable and the source is corrupt")
    return ids


def _baseline_identity_check(
        source_manifest: Mapping[str, Any],
        config: ExperimentConfig,
        train_tasks: Sequence[Any],
        source_run: Path,
        config_path: Path) -> Dict[str, Any]:
    """Revision 3's pinned minimum for stage-1-only reuse.

    Baseline reuse re-runs every downstream stage under the CURRENT
    protocol, so the keys that only act downstream (rule names, prompt
    digests, the delivery slot, K, token budgets, weak_profile, ...) may
    differ from the source run. What stage-1 reproducibility needs is
    the minimum set: both executor model names (the two arms whose
    trajectories are copied), both attempt counts (how many stage-1
    trajectories exist per task and arm), and the train list inside the
    source task set (the trajectories to copy must exist in the source;
    the benchmark is checked even earlier, before the plugin import). A
    pinned key that differs is a loud ConfigError naming it, never a
    silent mix.

    Returns the pinned values for the ASSEMBLED_FROM record.
    """
    model_roles = source_manifest.get("model_roles")
    if not isinstance(model_roles, dict):
        raise ConfigError(
            f"the source run {source_run} has a manifest without "
            "model_roles; only a run frozen by stage 0 can be reused")
    source_config = source_manifest.get("config")
    if not isinstance(source_config, dict):
        raise ConfigError(
            f"the source run {source_run} has a manifest without a "
            "frozen config; only a run frozen by stage 0 can be reused")
    for role in ("executor_strong", "executor_weak"):
        source_model = model_roles.get(role)
        new_model = config.models.get(role)
        if source_model is None or source_model != new_model:
            raise ConfigError(
                f"the reuse config {config_path} names executor model "
                f"{role}={new_model!r} but the source run {source_run} "
                f"ran {source_model!r}; the copied stage-1 trajectories "
                "were produced by the source run's executors, so the "
                "executor models are pinned at the baseline level "
                "(revision 3)")
    for key in ("n_attempts_strong", "n_attempts_weak"):
        source_value = source_config.get(key)
        new_value = getattr(config, key)
        if source_value is None or source_value != new_value:
            raise ConfigError(
                f"the reuse config {config_path} sets {key}={new_value!r} "
                f"but the source run {source_run} ran {source_value!r}; "
                "the attempt counts decide how many stage-1 trajectories "
                "exist per task and arm, so they are pinned at the "
                "baseline level (revision 3)")
    source_ids = _source_task_ids(source_manifest, source_run)
    train_raw = {task.task_id for task in train_tasks}
    extra = sorted(train_raw - source_ids)
    if extra:
        raise ConfigError(
            f"the reuse config {config_path} puts task(s) {extra} in "
            "train, but the source run's task set does not contain them; "
            "the baseline level copies the source run's stage-1 "
            "trajectories, so train must be a subset of the source task "
            "set (revision 3)")
    return {
        "executor_strong": model_roles["executor_strong"],
        "executor_weak": model_roles["executor_weak"],
        "n_attempts_strong": source_config["n_attempts_strong"],
        "n_attempts_weak": source_config["n_attempts_weak"],
        "train_count": len(train_raw),
        "source_task_count": len(source_ids),
    }


def _render_assembled_from_baseline(
        *,
        source_run: Path,
        source_manifest: Mapping[str, Any],
        config_path: Path,
        run_dir: Path,
        split_sizes: Mapping[str, int],
        pinned: Mapping[str, Any],
        counts: Mapping[str, Any],
        checksums: Mapping[str, str],
        campaign_seeded: Mapping[str, Any]) -> str:
    """The baseline ASSEMBLED_FROM.md: the stage-1-only provenance record
    (Chinese: a documentation artifact, per the repo's language rules)."""
    lines: List[str] = []
    add = lines.append
    add("# 装配溯源（ASSEMBLED_FROM，baseline 档位）")
    add("")
    add(f"- 源 run：`{source_run}`（benchmark "
        f"{source_manifest['benchmark']!r}，mode "
        f"{source_manifest['mode']!r}，tasks_digest "
        f"{str(source_manifest.get('tasks_digest'))[:12]}…）")
    add(f"- 复用配置：`{config_path}`（mode split；train/test/reserved = "
        f"{split_sizes['train']}/{split_sizes['test']}/"
        f"{split_sizes['reserved']}）")
    add(f"- 装配目标：`{run_dir}`")
    add("- 方案：`docs/decisions/retrieval_delivery_tb_split_20261013.md`"
        " 修订三（2026-10-13 用户裁决：TB 复跑 = D 相位轨迹复用 + "
        "下游相位全新蒸馏）。")
    add("")
    add("## 同一性校验与 train ⊆ 源任务集证明（修订三）")
    add("")
    add("- baseline 档位仅钉 {benchmark、双 executor 模型名、双 "
        "n_attempts、train ⊆ 源任务集}——D 相位轨迹复现性所需的最小集；"
        "规则名、prompt digest、相位定义、K、预算等允许不同"
        "（它们只作用于全部新跑的下游相位），不一致的钉住键仍响亮拒绝。")
    add(f"- 逐项核对通过：benchmark {source_manifest['benchmark']!r}；"
        f"executor_strong {pinned['executor_strong']!r}、executor_weak "
        f"{pinned['executor_weak']!r}；n_attempts_strong/weak = "
        f"{pinned['n_attempts_strong']}/{pinned['n_attempts_weak']}；"
        f"train {pinned['train_count']} ⊆ 源任务集 "
        f"{pinned['source_task_count']}，逐 id 核对通过"
        "（train 中的源外任务：（无））。")
    add("")
    add("## 轨迹计数与校验和")
    add("")
    add("| 类别 | 计数 | sha256 |")
    add("|---|---|---|")
    add(f"| trajectories | {counts['trajectories']} | "
        f"`{checksums['trajectories']}` |")
    add(f"| accepted_infra_shortfall 单元 | "
        f"{counts['accepted_infra_shortfall_units']} | "
        f"`{checksums['accepted_infra_shortfall']}` |")
    add("")
    seeded = campaign_seeded["count"]
    total = counts["trajectories"]
    if seeded:
        add(f"- 拷贝的 {total} 条 train 双臂轨迹中 {seeded} 条携带源 run 的 "
            "campaign 播种溯源 meta（TB2.1 campaign 归档经 "
            "`scripts/run/seed_tb_stage1.py` 播种，attempt 0..2 = 归档 "
            "rank 1..3）；拷贝逐字节保留各轨迹 meta 的 campaign_* 字段。")
    else:
        add(f"- 拷贝的 {total} 条 train 双臂轨迹均无 campaign 播种溯源 "
            "meta（源 run 阶段 1 为直接执行，非归档播种）。")
    origins = campaign_seeded.get("origin_runs") or {}
    if origins:
        add("- 溯源 origin run 分布：" + "、".join(
            f"{name}（{count} 条）" for name, count
            in sorted(origins.items())) + "。")
    add("")
    add("## 未拷贝清单（D 相位轨迹之外零复用）")
    add("")
    add("- 不拷贝任何下游产物：subtask / skill / exam / "
        "attempt、过滤库 skill_bank.jsonl、"
        "stage2_segments/segmentation_audit.jsonl、"
        "stage3_generation/generation_failures.jsonl、v4 源的整棵 "
        "phases/ 树，以及负清单全部"
        "条目（manifest.json、pipeline_log.jsonl、llm_requests/、"
        "failed_units.jsonl、skip_audit.jsonl、各 stage summary、"
        "curation_log.json、stage6_test/、work/、leak_verdicts.jsonl、"
        "rewrite_failures.jsonl）。")
    add("- **D 相位之外的全部相位将按当前协议全新执行**：切段、写作、"
        "验收、整理与终测全部按本配置的相位定义与当前 prompt 注册表"
        "真实调用，本 run 不复用源 run 的任何下游产物；新 run 的 "
        "manifest 由首次管线启动时 p00 自行冻结。")
    return "\n".join(lines) + "\n"


def _assemble_baseline(*,
                       source_run: Path,
                       source_manifest: Mapping[str, Any],
                       config: ExperimentConfig,
                       config_path: Path,
                       run_dir: Path,
                       train_tasks: Sequence[Any],
                       test_tasks: Sequence[Any],
                       reserved_tasks: Sequence[Any],
                       train_ids: Set[str]) -> Dict[str, Any]:
    """The stage-1-only assembly (revision 3): trajectories + shortfall.

    Called by assemble_reuse after the shared guards and the task
    loading, so everything here is level-specific: the pinned identity
    check, the trajectory-only copy (the keep set IS the filter -- a
    copied trajectory's task is in train by construction, so the bank
    level's cross-kind leak walk has nothing to re-check here), the
    baseline negative list and the baseline ASSEMBLED_FROM record.
    """
    pinned = _baseline_identity_check(
        source_manifest, config, train_tasks, source_run, config_path)
    trajectories = {
        obj_id: Trajectory.from_dict(record) for obj_id, record
        in _source_objects(source_run, "trajectories").items()}
    kept_trajectories = {
        obj_id: obj for obj_id, obj in trajectories.items()
        if obj.task_id in train_ids}
    shortfall_units = _load_shortfall_units(source_run, train_ids)

    if (run_dir / MANIFEST_NAME).exists():
        raise ConfigError(
            f"{run_dir} already contains {MANIFEST_NAME}; assemble into a "
            "fresh run directory (the pipeline freezes the manifest on "
            "its first start -- never before)")
    lock = RunLock(run_dir)
    warning = lock.acquire()
    try:
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        if (run_dir / MANIFEST_NAME).exists():
            # Re-checked under the lock: a concurrent process may have
            # frozen one between the check above and the acquisition.
            raise ConfigError(
                f"{run_dir} already contains {MANIFEST_NAME}; assemble "
                "into a fresh run directory")
        counts: Dict[str, int] = {}
        checksums: Dict[str, str] = {}
        _copy_kind(source_run, run_dir, "trajectories",
                   kept_trajectories, counts, checksums)
        _write_shortfall(run_dir, shortfall_units, counts, checksums)
        _assert_negative_list(run_dir, baseline=True)
        # Stage-1-only reuse pre-seeds nothing under phases/ either: the
        # whole downstream runs fresh, so even the bank artifact is absent.
        _assert_phase_tree(run_dir, None)
        campaign_seeded = _campaign_seeded(kept_trajectories)
        assembled_from_path = run_dir / _ASSEMBLED_FROM_NAME
        assembled_from_path.write_text(_render_assembled_from_baseline(
            source_run=source_run,
            source_manifest=source_manifest,
            config_path=config_path,
            run_dir=run_dir,
            split_sizes={
                "train": len(train_tasks), "test": len(test_tasks),
                "reserved": len(reserved_tasks)},
            pinned=pinned,
            counts=counts,
            checksums=checksums,
            campaign_seeded=campaign_seeded), encoding="utf-8")
    finally:
        lock.release()

    return {
        "source_run": str(source_run),
        "config": str(config_path),
        "run_dir": str(run_dir),
        "level": "baseline",
        "split_sizes": {"train": len(train_tasks),
                        "test": len(test_tasks),
                        "reserved": len(reserved_tasks)},
        "counts": dict(counts),
        "checksums": dict(checksums),
        "train_subset_of_source_tasks": {
            "train": pinned["train_count"],
            "source_tasks": pinned["source_task_count"],
            "outside_source": [],
        },
        "assembled_from": _ASSEMBLED_FROM_NAME,
    }


# ---------------------------------------------------------------------------
# The assembly
# ---------------------------------------------------------------------------

def assemble_reuse(source_run: Path, config_path: Path, run_dir: Path,
                   level: str = "bank") -> Dict[str, Any]:
    """Assemble ``run_dir`` from ``source_run`` per the reuse config.

    Contract 6: takes the exclusive run lock, refuses a ``run_dir`` that
    already has a manifest, validates the identity (the ruling-4
    whitelist diff for ``level="bank"``; revision 3's pinned minimum --
    {benchmark, both executor models, both attempt counts, train within
    the source task set} -- for ``level="baseline"``), copies the
    level's allowlist (ruling 5; the baseline level copies the stage-1
    trajectories and the accepted stage-1 shortfall ONLY), asserts the
    negative list and the integrity invariants, and writes
    ``ASSEMBLED_FROM.md``. Returns a JSON-safe summary dict (the level,
    per-class counts, checksums and the level's identity record) for the
    CLI to print.
    """
    if level not in LEVELS:
        raise ConfigError(
            f"level must be one of {LEVELS}, got {level!r}")
    source_run = Path(source_run)
    config_path = Path(config_path)
    run_dir = Path(run_dir)
    if not source_run.is_dir():
        raise ConfigError(f"source run {source_run} does not exist")
    source_manifest_path = source_run / MANIFEST_NAME
    if not source_manifest_path.is_file():
        raise ConfigError(
            f"source run {source_run} has no {MANIFEST_NAME}; only a "
            "finished frozen run can be reused")
    if source_run.resolve() == run_dir.resolve():
        raise ConfigError(
            f"run_dir must differ from the source run (both are "
            f"{run_dir})")
    source_manifest = read_json(source_manifest_path)

    # -- the reuse config and the manifest the new run WOULD freeze ------
    try:
        config = load_config(str(config_path))
    except (ValueError, OSError) as exc:
        raise ConfigError(
            f"invalid reuse config {config_path}: {exc}") from exc
    if config.mode == "split" and isinstance(config.split, dict) \
            and "ratio" in config.split:
        # The copy step is keyed by the frozen lists; a ratio+seed config
        # would have to be derived EXACTLY the way stage 0 derives it, and
        # a silent divergence between the two derivations would copy the
        # wrong tasks. Freeze the lists first (the reuse experiment's
        # configs always carry them).
        raise ConfigError(
            "the reuse config must carry explicit frozen split lists; a "
            "ratio+seed spec would have to be re-derived identically by "
            "stage 0 -- write the lists out (see scripts/run/"
            "freeze_tb_split.py) instead")

    # Baseline's pinned identity starts BEFORE the plugin import: a
    # benchmark mismatch must refuse as an identity error, not surface
    # as a missing-plugin import error on the new benchmark's name.
    if (level == "baseline"
            and config.benchmark != source_manifest.get("benchmark")):
        raise ConfigError(
            f"the reuse config {config_path} names benchmark "
            f"{config.benchmark!r} but the source run {source_run} ran "
            f"{source_manifest.get('benchmark')!r}; the benchmark is "
            "pinned at both levels (revision 3)")

    tasks = tasks_block.load_tasks(config.benchmark, _load_spec(config))
    train_tasks, test_tasks, reserved_tasks = tasks_block.split_tasks(
        tasks, config.mode, config.split)
    # Stored-form task ids (core.ids: "{benchmark}:{raw}"), the form every
    # source object's task_id carries; the raw config names map onto it.
    train_ids = {make_task_id(config.benchmark, task.task_id)
                 for task in train_tasks}
    test_ids = {make_task_id(config.benchmark, task.task_id)
                for task in test_tasks}
    reserved_ids = {make_task_id(config.benchmark, task.task_id)
                    for task in reserved_tasks}
    if level == "baseline":
        return _assemble_baseline(
            source_run=source_run,
            source_manifest=source_manifest,
            config=config,
            config_path=config_path,
            run_dir=run_dir,
            train_tasks=train_tasks,
            test_tasks=test_tasks,
            reserved_tasks=reserved_tasks,
            train_ids=train_ids)

    # -- the bank level: the prospective manifest and the ruling-4
    #    whitelist diff -----------------------------------------------
    source_config, source_version = _source_config(
        source_manifest, source_run)
    # Both sides of the walk in the parsed canonical form (see
    # _source_config): the upgraded v3 source and the v4 target compare
    # isomorphically, never on raw slot-completed key sets.
    canonical_source = {**source_manifest,
                        "config": source_config.to_dict()}
    allowed_prefixes = _allowed_diff_prefixes(config)
    prospective = build_manifest(
        config, tasks, train_ids=[task.task_id for task in train_tasks],
        test_ids=[task.task_id for task in test_tasks],
        reserved_ids=[task.task_id for task in reserved_tasks])
    prompt_digests = prospective["prompt_digests"]

    diff_paths = _diff_paths(
        _identity_view(canonical_source), _identity_view(prospective))
    # Collapsed to the section level for the summary/ASSEMBLED_FROM (the
    # leaf paths under a whitelisted key are noise there); the REFUSAL
    # below still names every offending leaf.
    allowed_diffs = sorted(
        {_section(path, allowed_prefixes) for path in diff_paths
         if _path_allowed(path, allowed_prefixes)})
    unexpected = [path for path in diff_paths
                  if not _path_allowed(path, allowed_prefixes)]
    if unexpected:
        raise ConfigError(
            f"the reuse config {config_path} differs from the source run "
            f"{source_run} beyond the allowed keys "
            f"{list(allowed_prefixes)}: {unexpected}; the reused "
            "artifacts were produced under the source run's frozen inputs, "
            "so align the config with the source run (or use a fresh "
            "source) instead of mixing protocols")
    # The declared mixed part, disclosed per prompt name (module
    # docstring: frozen by the new run, never called).
    source_digests = source_manifest.get("prompt_digests") or {}
    digest_drift = sorted(
        name for name in set(source_digests) | set(prompt_digests)
        if source_digests.get(name) != prompt_digests.get(name))

    # -- the source store, read-only -----------------------------------
    trajectories = {
        obj_id: Trajectory.from_dict(record) for obj_id, record
        in _source_objects(source_run, "trajectories").items()}
    subtasks = {
        obj_id: Subtask.from_dict(record) for obj_id, record
        in _source_objects(source_run, "subtasks").items()}
    # Skills load through the old-schema migration: a pre-v2 source run's
    # merge products cannot go through from_dict alone (their
    # source_subtasks needs the sibling records), so every raw row is
    # migrated first -- current-schema rows pass through unchanged. The
    # batch wrapper
    # owns the sibling map; a row that cannot migrate is a corrupt source
    # (ConfigError, the family of every other source-integrity refusal).
    skill_rows = _source_objects(source_run, "skills")
    try:
        migrated_skill_rows = migrate_skill_rows(list(skill_rows.values()))
        skills = {
            obj_id: Skill.from_dict(row)
            for obj_id, row in zip(skill_rows, migrated_skill_rows)}
        exams = {
            obj_id: Exam.from_dict(record) for obj_id, record
            in _source_objects(source_run, "exams").items()}
    except ValueError as exc:
        raise ConfigError(
            f"an object row of the source run {source_run} cannot "
            f"migrate to the current skill schema: {exc}; the source is "
            "corrupt") from exc
    attempts = {
        obj_id: Attempt.from_dict(record) for obj_id, record
        in _source_objects(source_run, "attempts").items()}
    bank_rows = _source_bank_rows(source_run, source_config, source_version)

    # -- keep sets (pure computation; every refusal below happens BEFORE
    #    anything is written into run_dir) ------------------------------
    kept_trajectories = {
        obj_id: obj for obj_id, obj in trajectories.items()
        if obj.task_id in train_ids}
    kept_subtasks = {
        obj_id: obj for obj_id, obj in subtasks.items()
        if obj.task_id in train_ids}
    lineage_by_id: Dict[str, Set[str]] = {}
    kept_skills: Dict[str, Skill] = {}
    for obj_id, skill in skills.items():
        lineage = skill_lineage_tasks(
            skill, subtasks, skills_by_id=skills)
        lineage_by_id[obj_id] = lineage
        if lineage <= train_ids:
            kept_skills[obj_id] = skill
    kept_exams = {
        obj_id: obj for obj_id, obj in exams.items()
        if obj.subtask_id in kept_subtasks}
    kept_attempts = {
        obj_id: obj for obj_id, obj in attempts.items()
        if obj.exam_id in kept_exams
        and (obj.skill_id is None or obj.skill_id in kept_skills)}

    # Bank rows: content must agree with the store they claim to come
    # from, and the keep decision must be the SAME full-chain predicate as
    # the skill copy set (the hard assertion of contract 10 -- the bank
    # row is an independent record, so it is filtered independently).
    kept_bank_rows: List[Dict[str, Any]] = []
    for row in bank_rows:
        row_skill_id = str(row.get("skill_id"))
        stored = skills.get(row_skill_id)
        if stored is None:
            raise ConfigError(
                f"bank row {row_skill_id!r} names a skill the source run "
                "does not store; the source is corrupt")
        # Both sides through the object roundtrip (isomorphic forms): a
        # raw v1 row of an older source run migrates on load exactly like
        # the stored record did, so a consistent source compares equal --
        # comparing the raw row against ``stored.to_dict()`` would flag
        # every v1 row as corrupt even when the run is consistent. The
        # load doubles as the bank row's can-load check.
        try:
            row_obj = Skill.from_dict(
                migrate_skill_record(row, skill_rows))
        except ValueError as exc:
            raise ConfigError(
                f"bank row {row_skill_id!r} of the source run "
                f"{source_run} cannot migrate to the current skill "
                f"schema: {exc}; the source is corrupt") from exc
        if row_obj.to_dict() != stored.to_dict():
            raise ConfigError(
                f"bank row {row_skill_id!r} differs from the stored skill "
                "of the same id; the source is corrupt")
        row_lineage = skill_lineage_tasks(
            row_obj, subtasks, skills_by_id=skills)
        row_keep = row_lineage <= train_ids
        if row_keep != (row_skill_id in kept_skills):
            # Both call sites went through skill_lineage_tasks with the
            # same inputs; disagreement means the source's own records
            # disagree, and the invariant the assembly promises is void.
            raise ConfigError(
                f"bank filter and skill copy set disagree on "
                f"{row_skill_id!r}; the source run's records are "
                "inconsistent -- a human must look at this run")
        if row_keep:
            # The migrated twin, not the raw source row: the assembled
            # bank is v2-native (M1 acceptance ruling 1), so the kept row
            # is bit-identical to the copied skill record.
            kept_bank_rows.append(row_obj.to_dict())
    kept_bank_ids = {str(row["skill_id"]) for row in kept_bank_rows}
    kept_accepted_bank_ids = {str(row["skill_id"]) for row in kept_bank_rows
                              if row.get("status") == "accepted"}

    # Integrity (ruling 5), checked against the SOURCE now so the failure
    # is named before any copying: the new store's accepted set must equal
    # the kept bank's ACCEPTED row ids (a v4 source's final artifact may
    # also carry pending members -- unfinished material the assembled
    # run's A/B phases measure -- which ride along, disclosed by count).
    new_accepted = {obj_id for obj_id, skill in kept_skills.items()
                    if skill.status == "accepted"}
    if new_accepted != kept_accepted_bank_ids:
        missing = sorted(new_accepted - kept_accepted_bank_ids)
        extra = sorted(kept_accepted_bank_ids - new_accepted)
        raise ConfigError(
            "the assembled store's accepted skills would not equal the "
            f"filtered bank's accepted rows (accepted-but-uncopied: "
            f"{missing}; bank-but-not-accepted: {extra}); the source run's "
            "accepted set and bank disagree -- a human must look at this "
            "run")

    # Train-side rows of the v3 stage-3 generation-failure ledger. The v4
    # learn phase treats a failed add as retryable, so there is no v4
    # ledger to copy these into: they are COUNTED (summary and
    # ASSEMBLED_FROM) as units the assembled run's first learn phase will
    # re-offer, never silently dropped. A v4 source has no such ledger
    # (its exam phases keep per-phase ledgers of their own, which no
    # consumer of the assembled run reads).
    kept_failure_rows = []
    if source_version == 3:
        for row in read_jsonl(source_run / "stage3_generation"
                             / "generation_failures.jsonl"):
            subtask_id = str(row.get("subtask_id"))
            subtask = subtasks.get(subtask_id)
            if subtask is None:
                raise ConfigError(
                    f"generation_failures row names subtask {subtask_id!r}, "
                    "which the source run does not store; the source is "
                    "corrupt")
            if subtask.task_id in train_ids:
                kept_failure_rows.append(row)

    # Stage 1's accepted infra holes (train side only: a hole on a task
    # the new run never dispatches is inert, and copying it would smuggle
    # test/reserved lineage into the assembled run).
    shortfall_units = _load_shortfall_units(source_run, train_ids)

    audit_path = source_run / "stage2_segments" / "segmentation_audit.jsonl"
    audit_rows = read_jsonl(audit_path)

    # -- the copy, under the run lock ------------------------------------
    if (run_dir / MANIFEST_NAME).exists():
        raise ConfigError(
            f"{run_dir} already contains {MANIFEST_NAME}; assemble into a "
            "fresh run directory (the pipeline freezes the manifest on "
            "its first start -- never before)")
    lock = RunLock(run_dir)
    warning = lock.acquire()
    try:
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        if (run_dir / MANIFEST_NAME).exists():
            # Re-checked under the lock: a concurrent process may have
            # frozen one between the check above and the acquisition.
            raise ConfigError(
                f"{run_dir} already contains {MANIFEST_NAME}; assemble "
                "into a fresh run directory")

        counts: Dict[str, int] = {}
        checksums: Dict[str, str] = {}

        _copy_kind(source_run, run_dir, "trajectories",
                   kept_trajectories, counts, checksums)
        _copy_kind(source_run, run_dir, "subtasks",
                   kept_subtasks, counts, checksums)
        # skills/exams land v2-native (M1 acceptance ruling 1): the
        # migrated objects serialize as the store would write them, so
        # the assembled run is consumable by the plain from_dict readers
        # (stage 4's candidate enumeration, stage 5's early exit, the
        # audit tool) without any migration of their own.
        _write_migrated_kind(run_dir, "skills",
                              kept_skills, counts, checksums)
        _write_migrated_kind(run_dir, "exams",
                              kept_exams, counts, checksums)
        _copy_kind(source_run, run_dir, "attempts",
                   kept_attempts, counts, checksums)

        # The filtered bank, twice over the same rows: the run-level
        # skill_bank.jsonl (the frozen run-level currency -- the audit
        # tooling and the pre-learn fallback read it) and, when the
        # target sequence has a learn phase, the LAST learn phase's
        # artifact. The artifact is what makes the assembled run
        # v4-resumable with zero curation calls: K5's dest-mode resume
        # path returns a non-empty bank without any model call, so the
        # phase reuses the source's curation instead of re-curating it.
        # One atomic full-file write each, in the source bank's order.
        bank_path = run_dir / "skill_bank.jsonl"
        _write_jsonl_atomic(bank_path, kept_bank_rows)
        counts["bank_rows"] = len(kept_bank_rows)
        counts["pending_bank_rows"] = len(kept_bank_rows) - len(
            kept_accepted_bank_ids)
        checksums["skill_bank"] = _file_sha256(bank_path)
        phase_label_target = _last_learn_label(config)
        if phase_label_target is not None:
            artifact_dir = phase_bank_dir(run_dir, phase_label_target)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / BANK_NAME
            _write_jsonl_atomic(artifact_path, kept_bank_rows)
            checksums["phase_bank"] = _file_sha256(artifact_path)

        # The v3 stage-3 generation-failure ledger is NOT copied (no v4
        # consumer; the learn phase re-offers the units -- disclosed).
        counts["generation_failure_rows"] = len(kept_failure_rows)

        # The segmentation audit, whole file (ruling 5's lazy-audit
        # exemption; the only allowlist entry whose test/reserved rows are
        # kept on purpose). Byte copy, not reserialisation.
        counts["segmentation_audit_rows"] = len(audit_rows)
        if audit_path.is_file():
            target_audit = (run_dir / "stage2_segments"
                            / "segmentation_audit.jsonl")
            target_audit.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(audit_path, target_audit)
            checksums["segmentation_audit"] = _file_sha256(target_audit)
        else:
            checksums["segmentation_audit"] = _class_checksum([])

        _write_shortfall(run_dir, shortfall_units, counts, checksums)

        # -- negative-list assertion (ruling 5) -------------------------
        _assert_negative_list(run_dir, baseline=False)
        _assert_phase_tree(run_dir, phase_label_target)

        # -- integrity invariants over the ASSEMBLED run ---------------
        for row in kept_bank_rows:
            row_id = urllib.parse.quote(str(row["skill_id"]), safe="")
            stored_path = (run_dir / KIND_DIRS["skills"]
                           / (row_id + ".json"))
            if not stored_path.is_file():
                raise ConfigError(
                    f"kept bank row {row['skill_id']!r} has no copied "
                    f"skill file in {run_dir}")
            if read_json(stored_path) != row:
                raise ConfigError(
                    f"bank row {row['skill_id']!r} is not bit-identical "
                    "to the copied skill of the same id")
        assembled_accepted = set()
        for obj_id, skill in kept_skills.items():
            if skill.status == "accepted":
                assembled_accepted.add(obj_id)
        if assembled_accepted != kept_accepted_bank_ids:
            raise ConfigError(
                "assembled accepted set does not equal the kept bank's "
                "accepted rows "
                f"(accepted: {sorted(assembled_accepted)}, bank: "
                f"{sorted(kept_accepted_bank_ids)})")
        # Zero test/reserved lineage among the copied objects (the five
        # kinds and the bank rows; the segmentation audit is exempt by
        # ruling 5).
        leaks = sorted(
            obj_id for obj_id, lineage in lineage_by_id.items()
            if obj_id in kept_skills and lineage & (test_ids | reserved_ids))
        for obj_id, obj in kept_trajectories.items():
            if obj.task_id not in train_ids:
                leaks.append(obj_id)
        for obj_id, obj in kept_subtasks.items():
            if obj.task_id not in train_ids:
                leaks.append(obj_id)
        for obj_id, obj in kept_exams.items():
            if subtasks[obj.subtask_id].task_id not in train_ids:
                leaks.append(obj_id)
        for obj_id, obj in kept_attempts.items():
            exam = exams[obj.exam_id]
            if subtasks[exam.subtask_id].task_id not in train_ids:
                leaks.append(obj_id)
            if obj.skill_id is not None and obj.skill_id not in kept_skills:
                leaks.append(obj_id)
        if leaks:
            raise ConfigError(
                f"copied objects carry test/reserved lineage: {leaks}; the "
                "assembly's keep sets are inconsistent -- a human must "
                "look at this directory")

        merged = _merged_original_groups(
            skills, set(kept_skills), kept_bank_ids)
        campaign_seeded = _campaign_seeded(kept_trajectories)

        assembled_from_path = run_dir / _ASSEMBLED_FROM_NAME
        assembled_from_path.write_text(_render_assembled_from(
            source_run=source_run,
            source_manifest=source_manifest,
            config_path=config_path,
            run_dir=run_dir,
            split_sizes={
                "train": len(train_tasks), "test": len(test_tasks),
                "reserved": len(reserved_tasks)},
            allowed_diffs=allowed_diffs,
            digest_drift=digest_drift,
            counts=counts,
            checksums=checksums,
            merged=merged,
            campaign_seeded=campaign_seeded,
            source_version=source_version,
            preseed_label=phase_label_target), encoding="utf-8")
    finally:
        lock.release()

    return {
        "source_run": str(source_run),
        "config": str(config_path),
        "run_dir": str(run_dir),
        "level": "bank",
        "source_schema": source_version,
        "preseeded_phase_bank": phase_label_target,
        "split_sizes": {"train": len(train_tasks),
                        "test": len(test_tasks),
                        "reserved": len(reserved_tasks)},
        "counts": dict(counts),
        "checksums": dict(checksums),
        "allowed_identity_diffs": allowed_diffs,
        "prompt_digest_drift": digest_drift,
        "merged_originals": merged,
        "assembled_from": _ASSEMBLED_FROM_NAME,
    }
