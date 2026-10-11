"""G1 segmentation and D7 segment validation.

Two blocks in one file (AGENTS.md layer-4 layout):

- ``segment`` (G1): hand a rendered trajectory view and a step budget K to a
  model via the SEGMENT prompt, then check the SCHEMA of every entry the
  model returned (types, bounds, required fields). It does NOT check the
  entries against the trajectory -- coverage is D7's job, kept separate per
  AGENTS.md block rule 6 (format validation vs business validation).
- ``validate_segments`` (D7): pure structural validation of a segment list
  against a step count and K -- no gaps, no overlaps, exact coverage, field
  existence/type only. It NEVER judges whether a ``success`` value is
  semantically correct (README stage 2: only field presence is checked).

Layer 4; depends on llm (call_model) and infra (ModelOutputError) only.
"""

from typing import Any, Dict, List

from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import call_model

__all__ = ["segment", "validate_segments"]

_REQUIRED_FIELDS = ("label", "description", "success")


def _is_int(value: Any) -> bool:
    # bool is a subclass of int; the model answering "true"/1 must not slip
    # through as a step index.
    return isinstance(value, int) and not isinstance(value, bool)


def segment(steps_view: str, K: int, handle: Any, *,
            usage: Any = None, run_dir: Any = None) -> List[Dict[str, Any]]:
    """G1: ask the model to split ``steps_view`` into at most K subtasks.

    Returns the model's segment dicts as-is (schema-checked, but NOT checked
    against the trajectory: start/end may still fail to cover it -- D7 does
    that). Any schema violation raises ModelOutputError after call_model's
    own format retries have produced parseable JSON.
    """
    parsed = call_model(
        handle,
        "SEGMENT",
        {"trajectory_view": steps_view, "K": str(K)},
        output_json=True,
        usage=usage,
        run_dir=run_dir,
    )
    if not isinstance(parsed, list):
        raise ModelOutputError(
            f"SEGMENT output must be a JSON array of objects, got {type(parsed).__name__}")

    errors: List[str] = []
    for index, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            errors.append(
                f"segment {index}: must be an object, got {type(entry).__name__}")
            continue
        start, end = entry.get("start"), entry.get("end")
        if not _is_int(start):
            errors.append(
                f"segment {index}: 'start' must be an integer, got {start!r}")
        if not _is_int(end):
            errors.append(
                f"segment {index}: 'end' must be an integer, got {end!r}")
        elif _is_int(start) and not 0 <= start < end:
            errors.append(
                f"segment {index}: needs 0 <= start < end, got start={start}, end={end}")
        for name in _REQUIRED_FIELDS:
            value = entry.get(name)
            if name == "success":
                if not isinstance(value, bool):
                    errors.append(
                        f"segment {index}: 'success' must be a boolean, got {value!r}")
            elif not isinstance(value, str) or not value.strip():
                errors.append(
                    f"segment {index}: {name!r} must be a non-empty string, got {value!r}")
    if errors:
        raise ModelOutputError("SEGMENT output failed schema validation: " + "; ".join(errors))
    return parsed


def validate_segments(total_steps: int, segments: List[Dict[str, Any]],
                      K: int) -> List[str]:
    """D7: validate a segment list against a trajectory of ``total_steps``.

    Pure function. Returns a list of human-readable error strings; an empty
    list means the segmentation is structurally valid. Checks: non-empty
    list; at most K segments; every boundary inside [0, total_steps); no
    empty range (start >= end); exact coverage when sorted by start (first
    starts at 0, last ends at total_steps, consecutive segments touch -- no
    gaps, no overlaps); and required fields present with the right types.
    The VALUES of ``success`` are never judged.
    """
    errors: List[str] = []
    if not segments:
        return ["segments list is empty"]
    if len(segments) > K:
        errors.append(f"{len(segments)} segments exceed the maximum of K={K}")

    boundaries_ok = True
    checked: List[tuple] = []  # (start, end, position in the input list)
    for index, entry in enumerate(segments):
        if not isinstance(entry, dict):
            errors.append(f"segment {index}: must be an object, got {type(entry).__name__}")
            boundaries_ok = False
            continue
        start, end = entry.get("start"), entry.get("end")
        for name in ("start", "end"):
            if not _is_int(entry.get(name)):
                errors.append(
                    f"segment {index}: {name!r} must be an integer, got {entry.get(name)!r}")
        if not _is_int(start) or not _is_int(end):
            boundaries_ok = False
            continue
        if start < 0 or end > total_steps:
            errors.append(
                f"segment {index}: range [{start}, {end}) is outside the "
                f"trajectory of {total_steps} steps")
        if start >= end:
            errors.append(f"segment {index}: empty range, start={start} >= end={end}")
        checked.append((start, end, index))

    for index, entry in enumerate(segments):
        if not isinstance(entry, dict):
            continue
        for name in _REQUIRED_FIELDS:
            value = entry.get(name)
            if name == "success":
                if not isinstance(value, bool):
                    errors.append(
                        f"segment {index}: 'success' must be a boolean, got {value!r}")
            elif not isinstance(value, str) or not value.strip():
                errors.append(
                    f"segment {index}: {name!r} must be a non-empty string, got {value!r}")

    # Coverage is only decidable when every boundary parsed as an integer;
    # otherwise the per-segment errors above already explain the problem.
    if boundaries_ok:
        checked.sort()
        if checked[0][0] != 0:
            errors.append(
                f"coverage error: first segment starts at step {checked[0][0]}, expected 0")
        if checked[-1][1] != total_steps:
            errors.append(
                f"coverage error: last segment ends at step {checked[-1][1]}, "
                f"expected {total_steps}")
        for (_, prev_end, prev_pos), (next_start, _, next_pos) in zip(checked, checked[1:]):
            if next_start != prev_end:
                errors.append(
                    f"coverage error: gap or overlap between segment {prev_pos} "
                    f"(ends at {prev_end}) and segment {next_pos} (starts at {next_start})")
    return errors
