"""B7 part 2: cross-process token-usage accounting (``llm_requests/usage.jsonl``).

One JSON line per model call, appended through store.append_jsonl, which
serialises writers itself (a per-path thread lock plus an flock on the
``usage.jsonl.lock`` sidecar), so any number of threads and processes (a
resumed run, a parallel worker) can record into the same ledger without
interleaving or losing lines. Totals are always folded
from the file, never from in-process counters: the 2026-09-20 incident
(outer AGENTS.md section 1) had a resumed run report only its last segment's
tokens because its counters lived in memory. Unknown usage is recorded as
null and never as 0 (AGENTS.md section 6 rule 5): 0 is a measurement, null
is a missing one.

Adapted from SkillExpand's persistence/usage.py: the old file kept a
summary JSON plus a request event ledger and folded the latter on start;
here the per-call ledger is the only state, which makes totals correct for
any number of concurrent writers and makes "resume clears the counters"
structurally impossible.
"""

import time
from pathlib import Path
from typing import Any, Dict, Optional

from expedistill.infra.store import append_jsonl, read_jsonl


class UsageRecorder:
    """Append one line per model call; fold totals from the file on demand."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def record(self, role: str, prompt: str, model: str,
               in_tokens: Optional[int], out_tokens: Optional[int],
               request_id: str, status: str = "ok",
               meta: Optional[Dict[str, Any]] = None) -> None:
        """Append one usage line. ``in_tokens``/``out_tokens`` may be None
        (unknown -- stored as null, never as 0)."""
        row = {
            "time": time.time(),
            "role": role,
            "prompt": prompt,
            "model": model,
            "in_tokens": None if in_tokens is None else int(in_tokens),
            "out_tokens": None if out_tokens is None else int(out_tokens),
            "request_id": request_id,
            "status": status,
            "meta": meta,
        }
        # append_jsonl locks the ledger itself; locking here as well would
        # self-deadlock on the shared ``usage.jsonl.lock`` flock.
        append_jsonl(self.path, row)

    def totals(self) -> Dict[str, Any]:
        """{"overall": {in, out, request_count}, "by_role": {...}}, folded
        from the file so every process sees every line (absent file: zeros)."""
        overall = {"in": 0, "out": 0, "request_count": 0}
        by_role: Dict[str, Dict[str, int]] = {}
        for row in read_jsonl(self.path):
            overall["request_count"] += 1
            # None (unknown) contributes nothing; it is not a zero-cost call.
            overall["in"] += row.get("in_tokens") or 0
            overall["out"] += row.get("out_tokens") or 0
            slot = by_role.setdefault(
                str(row.get("role")),
                {"in": 0, "out": 0, "request_count": 0})
            slot["request_count"] += 1
            slot["in"] += row.get("in_tokens") or 0
            slot["out"] += row.get("out_tokens") or 0
        return {"overall": overall, "by_role": by_role}
