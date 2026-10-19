#!/usr/bin/env python3
"""Generate the fake-provider plan for the SearchQA live smoke.

Reads the outer searchqa-eval repo's materialized items and writes the
JSON plan the ``fake`` LLM provider consumes
(``EXPEDISTILL_FAKE_PROVIDER_PLAN``; schema in
``src/expedistill/llm/fake_provider.py``). The task ids and their weak-
executor policies mirror ``configs/experiments/searchqa_split_live_smoke_v4.yaml``:

* t1 (Tom Clancy)      -- policy ``skills``: round-0 keep;
* t2 (bubble gum)      -- policy ``revised_skills``: round-0 miss ->
                          G10/G11 revision -> round-1 keep;
* t3 (Angela Bassett)  -- policy ``revised_skills``: same as t2;
* t4 (Sails)           -- policy ``never``: both rounds miss -> the last
                          learn phase deletes the group;
* t5 (finale, test)    -- policy ``skills``: the test-phase with-bank arm
                          passes, the bare control fails.

Inputs : EXPEDISTILL_SEARCHQA_EVAL_DIR (the outer benchmark/searchqa-eval
         checkout), or --eval-dir; the smoke config's task ids (baked in
         below -- they ARE the smoke's experiment identity).
Output: the plan JSON (default tmp/mm_smoke/fake_plan.json), printed path.
Example: .venv/bin/python scripts/smoke/gen_searchqa_fake_plan.py \
             --eval-dir ../../../benchmark/searchqa-eval
"""

import argparse
import json
from pathlib import Path

POLICIES = {
    "221c83e6630f4e7983da48fa28da1882": "skills",
    "a234f47261f14ab9aed867375bddc652": "revised_skills",
    "3909c367f524402d8ba5ec2b520ba206": "revised_skills",
    "db15ab3b562142f1aa6e7cbb0a561e83": "never",
    "979e3f5aac91446b8550161dd89e9e26": "skills",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-dir", default=None,
                        help="the searchqa-eval checkout (default: "
                             "$EXPEDISTILL_SEARCHQA_EVAL_DIR)")
    parser.add_argument("--out", default="tmp/mm_smoke/fake_plan.json",
                        help="output plan path (default: %(default)s)")
    args = parser.parse_args()

    import os
    eval_dir = args.eval_dir or os.environ.get(
        "EXPEDISTILL_SEARCHQA_EVAL_DIR", "")
    if not eval_dir:
        parser.error("no eval dir: pass --eval-dir or set "
                     "EXPEDISTILL_SEARCHQA_EVAL_DIR")
    data = Path(eval_dir).expanduser() / "data" / "searchqa_split"
    items = {}
    for split in ("train", "test"):
        for item in json.loads(
                (data / split / "items.json").read_text(encoding="utf-8")):
            items[str(item["id"])] = item

    plan = {"strong_model": "fake-strong", "weak_model": "fake-weak",
            "revision_marker": "REVISED-BY-G11", "questions": {}}
    for task_id, policy in POLICIES.items():
        item = items[task_id]
        plan["questions"][item["question"]] = {
            "answer": item["answers"][0], "policy": policy}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"wrote {out} with {len(plan['questions'])} questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
