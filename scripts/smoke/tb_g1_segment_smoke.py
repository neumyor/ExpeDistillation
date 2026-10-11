#!/usr/bin/env python3
"""Live smoke: one real G1 segmentation call on a real TB failure trajectory.

Reconstructs the sampled trial's trajectory (benchmarks.tb.trajectory),
renders it with the TB plugin's view rules (D5), sends ONE SEGMENT prompt to
the strong model, and checks the result with D7 (validate_segments). Token
cost is accounted through the B7 UsageRecorder into the smoke's run dir.

Usage (env from .env):
    PYTHONPATH=src .venv/bin/python scripts/smoke/tb_g1_segment_smoke.py \
        <trial-dir> [model] [K]
Input:  <trial-dir> = a sampled trial dir holding agent/trajectory.json
Output: printed segmentation + D7 errors + usage; request/response dumps and
        the usage ledger under tmp/tbval/g1_smoke/.
"""

import json
import sys
import time
from pathlib import Path

from expedistill.benchmarks.tb.plugin import TBPlugin
from expedistill.benchmarks.tb.trajectory import reconstruct
from expedistill.blocks.segment import segment, validate_segments
from expedistill.blocks.view import render_steps_view
from expedistill.inject.skills import estimate_tokens
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.call import make_handle
from expedistill.llm.client import OpenAICompatClient

RUN_DIR = Path("tmp/tbval/g1_smoke")


def main():
    trial = Path(sys.argv[1]).expanduser()
    model = sys.argv[2] if len(sys.argv) > 2 else "DEEPSEEK_up5zdj"
    k = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    max_tokens = int(sys.argv[4]) if len(sys.argv) > 4 else 5120
    traj = json.loads((trial / "agent" / "trajectory.json").read_text())
    steps, skipped = reconstruct(traj)
    view = render_steps_view(steps, TBPlugin().view_rules)
    print(f"trial={trial.name} kept_steps={len(steps)} skipped={skipped} "
          f"view_chars={len(view)} est_tokens={estimate_tokens(view)} K={k}")

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    usage = UsageRecorder(RUN_DIR / "usage.jsonl")
    handle = make_handle("strong", model, OpenAICompatClient.from_env(),
                         infra_retries=1, infra_backoff_seconds=3.0,
                         max_tokens=max_tokens,
                         usage=usage, run_dir=RUN_DIR)
    started = time.time()
    segments = segment(view, k, handle, usage=usage, run_dir=RUN_DIR)
    elapsed = time.time() - started
    errors = validate_segments(len(steps), segments, k)
    print(f"\nsegments ({len(segments)}) in {elapsed:.1f}s:")
    for s in segments:
        print(f"  [{s.get('start'):>4},{s.get('end'):>4}) "
              f"success={s.get('success')} {s.get('label')}: {s.get('description')}")
    print(f"\nD7 errors: {errors if errors else 'none'}")
    print(f"usage: {json.dumps(usage.totals())}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
