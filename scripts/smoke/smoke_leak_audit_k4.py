"""Smoke test: the K4 acceptance loop's LLM-judge leak audit, real models.

The post-merge validation runs (runs/smoke_searchqa_full_*) never reached the
leak audit inside `expedistill run`: every stage-4 candidate died at the
bare-exam step (weak_already_knows), so `stage4_acceptance/leak_verdicts.jsonl`
was never written by a real-model run. This smoke drives `accept_skill` (K4)
directly with real endpoint handles -- the same pattern as
smoke_stage01_searchqa.py driving stage0/stage1 -- over two hand-built
(skill, exam) pairs:

1. a clean skill: generic method advice only. Expected: one leak verdict
   row with leaked=false, then the with-skill attempt.
2. a leaking skill: embeds the reference answer verbatim. Expected: a
   leaked=true verdict (whose reason must NOT restate the reference
   answer), a G6 rewrite to version 2, a fresh verdict on v2, then the
   with-skill attempt.

The exam is a real-world question the weak model answers wrong (probed
2026-10-11: qwen3.6-flash-distill said "Jezerní jeskyně"), so the bare
attempt fails and the loop proceeds to the audit, as in a real run.

Usage (from the repo root, with .env loaded into the environment):
    set -a; . ./.env; set +a
    .venv/bin/python scripts/smoke/smoke_leak_audit_k4.py            # new run dir
    .venv/bin/python scripts/smoke/smoke_leak_audit_k4.py --run-dir runs/smoke_leak_audit_XXXX  # resume

No file under src/, tests/, README or AGENTS.md is modified; the script
only writes its run directory under runs/.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from expedistill.core import ids
from expedistill.core.models import Exam, Skill
from expedistill.composites.acceptance_leak import LEAK_VERDICTS_LEDGER
from expedistill.composites.acceptance_submit import accept_skill
from expedistill.infra.runlock import RunLock
from expedistill.infra.store import Store
from expedistill.infra.usage import UsageRecorder
from expedistill.llm.call import make_handle
from expedistill.llm.client import OpenAICompatClient

STRONG_MODEL = "DEEPSEEK_up5zdj"
WEAK_MODEL = "qwen3.6-flash-distill"
MAX_TOKENS_STRONG = 32768

#: The exam both units share. The weak model answers this wrong (probed),
#: so the bare attempt fails and K4 proceeds to the leak audit.
QUESTION = ("What is the name of the deepest known underwater cave, "
            "located in the Czech Republic?")
REFERENCE = "the Hranice Abyss (Czech: Hranická propast)"

CLEAN_SKILL = """How to answer record-holder geography questions:
1. Identify the record category (deepest, largest, highest...) and the region named in the question.
2. Search with the record category and region as the query, e.g. "deepest underwater cave" plus the country.
3. Prefer results that give a specific proper name and a depth figure; cross-check the depth claim against a second passage.
4. Finish with the proper name only, as a concise noun phrase, e.g. Finish[<name>].
"""

LEAKY_SKILL = """How to answer questions about the deepest known underwater cave:
1. The answer to the deepest-known-underwater-cave question is the Hranice Abyss (Hranická propast) in the Czech Republic.
2. Quote that name in the Finish[...] action.
"""

WEAK_PROFILE = (
    "The weak model is a small distilled model. It follows short, concrete "
    "instructions well but is weak on obscure trivia and tends to answer "
    "with a sentence instead of the short answer phrase."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run-dir", type=Path, default=None,
        help="existing run dir to resume; default runs/smoke_leak_audit_<timestamp>")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    if args.run_dir is not None:
        run_dir = Path(args.run_dir)
        if not run_dir.is_absolute():
            run_dir = repo_root / run_dir
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = repo_root / "runs" / f"smoke_leak_audit_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"run_dir: {run_dir}")

    store = Store(run_dir)
    lock = RunLock(run_dir)
    warning = lock.acquire()
    try:
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
        usage = UsageRecorder(run_dir / "llm_requests" / "usage.jsonl")
        client = OpenAICompatClient.from_env()
        handles = {
            "answerer": make_handle(
                "answerer", WEAK_MODEL, client, max_tokens=5120,
                infra_retries=3, infra_backoff_seconds=5.0),
            "judge": make_handle(
                "judge", STRONG_MODEL, client, max_tokens=MAX_TOKENS_STRONG,
                extra_body={"reasoning_effort": "max"},
                infra_retries=3, infra_backoff_seconds=5.0),
            "rewriter": make_handle(
                "rewriter", STRONG_MODEL, client, max_tokens=MAX_TOKENS_STRONG,
                extra_body={"reasoning_effort": "max"},
                infra_retries=3, infra_backoff_seconds=5.0),
            "auditor": make_handle(
                "auditor", STRONG_MODEL, client, max_tokens=MAX_TOKENS_STRONG,
                extra_body={"reasoning_effort": "max"},
                infra_retries=3, infra_backoff_seconds=5.0),
        }

        units = [
            ("smoke/clean/00/seg00", CLEAN_SKILL),
            ("smoke/leaky/00/seg00", LEAKY_SKILL),
        ]
        for subtask_id, content in units:
            exam = Exam(exam_id=ids.make_exam_id(subtask_id),
                        question_text=QUESTION,
                        reference_answer=REFERENCE,
                        subtask_id=subtask_id,
                        skill_id=ids.make_skill_id(subtask_id))
            skill = Skill(skill_id=ids.make_skill_id(subtask_id),
                          content=content, subtask_id=subtask_id,
                          source_subtasks=[subtask_id])
            result = accept_skill(
                store=store, skill=skill, exam=exam,
                weak_profile=WEAK_PROFILE,
                answerer_handle=handles["answerer"],
                judge_handle=handles["judge"],
                rewriter_handle=handles["rewriter"],
                auditor_handle=handles["auditor"],
                retry_limit=2, usage=usage, run_dir=run_dir)
            print(f"[unit {subtask_id}] status={result.status} "
                  f"version={result.version} discard={result.discard_reason}")

        verdicts_path = Path(store.dir_for("attempts")).parent / LEAK_VERDICTS_LEDGER
        rows = [json.loads(line) for line in verdicts_path.read_text().splitlines() if line.strip()]
        print(f"[ledger] {verdicts_path}: {len(rows)} rows")
        protected = ["hranice", "hranická", "propast"]
        ok = True
        for row in rows:
            reason = row.get("reason", "")
            leak_in_reason = [p for p in protected if p in reason.lower()]
            print(f"[verdict] {row['skill_id']} v{row['skill_version']} "
                  f"leaked={row['leaked']} reason={reason[:160]!r}")
            if leak_in_reason:
                print(f"  FAIL: reason restates protected material: {leak_in_reason}")
                ok = False
            if not reason.strip():
                print("  FAIL: empty reason")
                ok = False
        if not rows:
            print("FAIL: no leak verdicts were written")
            ok = False

        totals = usage.totals()
        print(f"[usage] {json.dumps(totals, ensure_ascii=False, sort_keys=True)}")
        print("SMOKE " + ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
