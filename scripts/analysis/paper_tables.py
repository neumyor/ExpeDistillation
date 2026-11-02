#!/usr/bin/env python3
"""Build the paper's comparison tables from finished runs (no model calls).

Thin wrapper over ``expedistill.stages.paper_tables``: reads the test phase
of every run named in the spec, compares each arm with its own control (and
with the spec's reference arm), and writes markdown and JSON.  Run
directories are only read; an output path inside any of them is refused.

Usage (from the repo root):
    .venv/bin/python scripts/analysis/paper_tables.py --spec tmp/tables.json \
        --out-md tmp/tables.md --out-json tmp/tables_out.json \
        [--n-boot 10000] [--seed 0] [--metric avg_at_n]

Input:  a JSON spec, experiment -> benchmark -> {arm label: run directory},
        plus an optional "references": {experiment: arm label}.
Output: markdown (stdout when --out-md is absent) and, with --out-json, the
        same numbers as JSON.  Exit 0 on success, 2 on a bad spec or run.
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from expedistill.stages import paper_tables  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Paper tables from finished runs' test phases.")
    parser.add_argument("--spec", required=True,
                        help="JSON table spec (experiment -> benchmark -> "
                             "arm label -> run directory)")
    parser.add_argument("--out-md", help="write the markdown tables here "
                        "(default: print to stdout)")
    parser.add_argument("--out-json", help="write the numbers as JSON here")
    parser.add_argument("--n-boot", type=int, default=10000,
                        help="bootstrap resamples over tasks (default 10000)")
    parser.add_argument("--seed", type=int, default=0,
                        help="bootstrap seed (default 0)")
    parser.add_argument("--metric", choices=paper_tables.METRICS,
                        default="avg_at_n",
                        help="metric the markdown shows (default avg_at_n; "
                             "the JSON always carries all three)")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        text = paper_tables.run(
            args.spec, out_md=args.out_md, out_json=args.out_json,
            n_boot=args.n_boot, seed=args.seed, metric=args.metric)
    except (ValueError, OSError) as exc:
        print(f"paper_tables: {exc}", file=sys.stderr)
        return 2
    if args.out_md is None:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
