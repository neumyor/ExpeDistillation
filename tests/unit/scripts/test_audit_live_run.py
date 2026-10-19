"""audit_live_run reads TODAY's runs: the v1-object regression (M1).

scripts/audit/audit_live_run.py is the AGENTS 6.7 standard-flow tool, and
every formal run of today predates schema v2 -- the audit's full-store
skill loads must go through the v1->v2 migration (M1 acceptance ruling 2),
or the first stored merge product kills the audit with from_dict's
designed v1 rejection. The script is loaded from its path (scripts/ is
not a package); the fixture is a hand-built minimal v1 live run, per the
tests/fixtures discipline (never a real run directory).
"""

import hashlib
import importlib.util
import json
from pathlib import Path

from expedistill.core import ids
from expedistill.infra.store import Store, write_json
from expedistill.inject.skills import estimate_tokens, format_skills_block

_SCRIPT = (Path(__file__).resolve().parents[3]
           / "scripts" / "audit" / "audit_live_run.py")


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_live_run", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit_live_run = _load_audit_module()


def _build_v1_live_run(root: Path) -> Path:
    """A minimal v1-object live run: one task, one candidate group, a
    stored v1 merge product (a bank member) and a round-1 frozen
    injection row naming it -- the post-curation bank injection shape."""
    run_dir = root / "run"
    store = Store(run_dir)
    task_id = ids.make_task_id("tb", "t1")
    subtask_id = ids.make_subtask_id(
        ids.make_trajectory_id(task_id, "strong", 0), 0)
    content = ("[When to use] x\n[Steps]\n1. do\n[Verify] y\n"
               "[Do not use when] z")

    def v1_skill_row(skill_id, merged_from):
        return {
            "skill_id": skill_id, "content": content,
            "subtask_id": subtask_id, "version": 1, "status": "accepted",
            "discard_reason": None, "group": None, "rewrite_history": [],
            "merged_from": merged_from, "schema_version": 1,
        }

    candidate = v1_skill_row(ids.make_skill_id(subtask_id), [])
    merged = v1_skill_row(ids.make_merged_skill_id(0),
                          [candidate["skill_id"]])
    write_json(store.path_for("skills", candidate["skill_id"]),
               candidate, indent=2)
    write_json(store.path_for("skills", merged["skill_id"]), merged, indent=2)

    # The manifest fragment the audit reads (config.methods.acceptance).
    (run_dir / "manifest.json").write_text(json.dumps({
        "config": {"methods": {"acceptance": {
            "name": "live_run", "n_attempts": 1}}},
    }), encoding="utf-8")

    # The live subtree must exist or the audit exits early; the round-1
    # frozen row names the MERGED bank member, so the member loads ride
    # the migration too, not just the full-store enumeration.
    block = format_skills_block([{
        "skill_id": merged["skill_id"], "version": 1,
        "content": content, "group": None}])
    audit_live_run._append_jsonl_raw(
        audit_live_run._round_dir(run_dir, 1)
        / audit_live_run.INJECTION_AUDIT,
        {
            "schema": "injection-audit-v1", "task_id": task_id,
            "round": 1,
            "members": [{"skill_id": merged["skill_id"], "version": 1}],
            "injection_block_sha256": hashlib.sha256(
                block.encode("utf-8")).hexdigest(),
            "injection_tokens": estimate_tokens(block),
        })
    return run_dir


def test_audit_reads_a_v1_live_run_with_merge_products(tmp_path):
    """M1 acceptance ruling 2: the audit must read a v1-object run -- the
    only kind of formal run that exists today -- through the migration,
    not die at the first stored merge product (the full-load group
    enumeration or the frozen-row member loads both hit v1 merge rows)."""
    run_dir = _build_v1_live_run(tmp_path)

    audit = audit_live_run.audit_run(run_dir)

    # The run is deliberately incomplete (open round, no verdict ledgers),
    # so findings are expected -- what must not happen is a crash, and
    # the merged member must reconstruct, proving its v1 row loaded.
    messages = " | ".join(finding["message"] for finding in audit.findings)
    assert "unknown skill" not in messages
    assert "cannot reconstruct" not in messages
