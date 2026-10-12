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

import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from expedistill.infra.store import append_jsonl, read_jsonl

#: Bucket for error rows whose ``meta.error`` is missing (old ledgers, TB
#: plugin rows): counted in the breakdown, never silently dropped.
UNKNOWN_ERROR_TYPE = "(unknown)"


def _median(values: List[Union[int, float]]) -> Optional[Union[int, float]]:
    """statistics.median guarded against an empty list (None = unknown)."""
    return statistics.median(values) if values else None


class _GroupAcc:
    """Fold the rows of one (role, prompt) group into the pre-registered
    metrics; UsageStats.by_role_prompt documents each one."""

    __slots__ = ("n_calls", "n_ok", "n_error", "errors_by_type", "_in_values",
                 "_out_values", "total_in", "total_out", "cap_violations",
                 "length_truncations", "_estimated_in", "_ratios", "unknown")

    def __init__(self) -> None:
        self.n_calls = 0
        self.n_ok = 0
        self.n_error = 0
        self.errors_by_type: Dict[str, int] = {}
        self._in_values: List[Union[int, float]] = []
        self._out_values: List[Union[int, float]] = []
        self.total_in = 0
        self.total_out = 0
        self.cap_violations = 0
        self.length_truncations = 0
        self._estimated_in: List[Union[int, float]] = []
        self._ratios: List[float] = []
        self.unknown: Dict[str, int] = {}

    def _miss(self, key: str) -> None:
        self.unknown[key] = self.unknown.get(key, 0) + 1

    def add(self, row: Dict[str, Any]) -> None:
        self.n_calls += 1
        in_tokens = row.get("in_tokens")
        out_tokens = row.get("out_tokens")
        # Totals are over ALL rows: a failed attempt that burned tokens is
        # real cost. Unknown contributes nothing; it is not a zero-cost call.
        if in_tokens is not None:
            self.total_in += in_tokens
        if out_tokens is not None:
            self.total_out += out_tokens
        meta = row.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        if row.get("status") == "ok":
            self.n_ok += 1
            if in_tokens is None:
                self._miss("in_tokens")
            else:
                self._in_values.append(in_tokens)
            if out_tokens is None:
                self._miss("out_tokens")
            else:
                self._out_values.append(out_tokens)
            finish_reason = meta.get("finish_reason")
            if finish_reason is None:
                self._miss("finish_reason")
            elif finish_reason == "length":
                self.length_truncations += 1
            cap_violation = meta.get("cap_violation")
            if cap_violation is None:
                self._miss("cap_violation")
            elif cap_violation:
                self.cap_violations += 1
            estimated_in = meta.get("estimated_in")
            if estimated_in is None:
                self._miss("estimated_in")
            else:
                self._estimated_in.append(estimated_in)
                # The ratio needs both a measured in and a nonzero estimate.
                if in_tokens is not None and estimated_in:
                    self._ratios.append(in_tokens / estimated_in)
        else:
            self.n_error += 1
            error_type = meta.get("error")
            if error_type is None:
                self._miss("error")
                error_type = UNKNOWN_ERROR_TYPE
            elif not isinstance(error_type, str):
                error_type = str(error_type)
            self.errors_by_type[error_type] = \
                self.errors_by_type.get(error_type, 0) + 1

    def finish(self) -> Dict[str, Any]:
        return {
            "n_calls": self.n_calls,
            "n_ok": self.n_ok,
            "n_error": self.n_error,
            "errors_by_type": dict(sorted(self.errors_by_type.items())),
            "in_median": _median(self._in_values),
            "in_max": max(self._in_values) if self._in_values else None,
            "total_in": self.total_in,
            "out_median": _median(self._out_values),
            "out_max": max(self._out_values) if self._out_values else None,
            "total_out": self.total_out,
            "cap_violations": self.cap_violations,
            "length_truncations": self.length_truncations,
            "estimated_in_median": _median(self._estimated_in),
            "in_over_estimated_median": _median(self._ratios),
            "unknown": dict(sorted(self.unknown.items())),
        }


class UsageStats:
    """Read-only statistics over the usage ledger (the recorder's reader).

    PRE-REGISTERED METRICS -- the choices below were fixed BEFORE looking
    at any data (outer AGENTS.md section 2 rule 1); ``by_role_prompt``
    reports exactly these, nothing picked post hoc:

    * ``n_calls`` counts ALL rows. ``n_ok`` counts status "ok" rows;
      ``n_error`` every other row (``error`` and ``format_error`` alike):
      anything but an explicit "ok" is a failed attempt, so
      ``n_calls == n_ok + n_error``. ``errors_by_type`` groups non-ok rows
      by ``meta.error``; rows without it fall under ``UNKNOWN_ERROR_TYPE``.
    * Medians and maxima (``in_median``/``in_max``, ``out_median``/
      ``out_max``, ``estimated_in_median``, ``in_over_estimated_median``)
      are over OK rows only: they describe the size of a successful call
      of this (role, prompt). ``in_over_estimated_median`` is the median of
      the per-row ratio in_tokens / meta.estimated_in over ok rows where
      BOTH are known (a zero estimate cannot divide and is excluded from
      the ratio only). A median over no known value is None -- unknown,
      never 0.
    * ``total_in``/``total_out`` are over ALL rows: cost accounting
      includes failed attempts that really burned tokens.
    * ``cap_violations``: ok rows with truthy ``meta.cap_violation`` (the
      endpoint ignored the requested cap). ``length_truncations``: ok rows
      with ``meta.finish_reason == "length"`` (the cap was honored but too
      small). Read together they separate "cap ignored" from "cap too
      small"; a truncation on a failed attempt is already an error row and
      counts in neither.
    * Every missing or null datum (old ledgers without the new meta keys,
      TB plugin rows that never carry them) is absent from the medians and
      tallied per key in ``unknown`` instead -- never coerced to 0. A key
      present with a null value counts as missing; one present with a real
      value (including False) is a report.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def by_role_prompt(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Per (role, prompt) metrics, one entry per group in the ledger."""
        groups: Dict[Tuple[str, str], _GroupAcc] = {}
        for row in read_jsonl(self.path):
            key = (str(row.get("role")), str(row.get("prompt")))
            groups.setdefault(key, _GroupAcc()).add(row)
        return {key: acc.finish() for key, acc in groups.items()}


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
        """{"overall": {in, out, cached_tokens, request_count},
        "by_role": {...}}, folded from the file so every process sees every
        line (absent file: zeros).

        ``cached_tokens`` is folded from the rows' ``meta.cached_tokens``
        (present only when the provider reported gateway-cache hits): 0 when
        never reported. The provider already counts cached prompt tokens
        inside ``in``, so the cached total is reported alongside, never
        folded into in/out (that would double-count).
        """
        overall = {"in": 0, "out": 0, "cached_tokens": 0, "request_count": 0}
        by_role: Dict[str, Dict[str, int]] = {}
        for row in read_jsonl(self.path):
            meta = row.get("meta")
            cached = (meta.get("cached_tokens")
                      if isinstance(meta, dict) else None) or 0
            overall["request_count"] += 1
            # None (unknown) contributes nothing; it is not a zero-cost call.
            overall["in"] += row.get("in_tokens") or 0
            overall["out"] += row.get("out_tokens") or 0
            overall["cached_tokens"] += cached
            slot = by_role.setdefault(
                str(row.get("role")),
                {"in": 0, "out": 0, "cached_tokens": 0, "request_count": 0})
            slot["request_count"] += 1
            slot["in"] += row.get("in_tokens") or 0
            slot["out"] += row.get("out_tokens") or 0
            slot["cached_tokens"] += cached
        return {"overall": overall, "by_role": by_role}
