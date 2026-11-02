"""A stand-in for the outer searchqa-eval repo, installed through the seam
the SearchQA plugin tests are meant to patch (``env.import_eval_repo``).

The stub evaluator is case-insensitive exact match, the semantics the
recorded transcript fixture was produced under.
"""

import json
import types
from pathlib import Path
from typing import Any, Dict, List

from expedistill.benchmarks.searchqa import env as searchqa_env

__all__ = ["FIXTURE_DIR", "fixture_items", "install_eval_repo_stub",
           "write_eval_dir"]

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "searchqa"


def fixture_items() -> List[Dict[str, Any]]:
    return json.loads((FIXTURE_DIR / "items.json").read_text(encoding="utf-8"))


def install_eval_repo_stub(monkeypatch) -> types.ModuleType:
    evaluator = types.ModuleType("searchqa_stub.evaluator")

    def evaluate(prediction, gold_answers):
        correct = prediction.strip().lower() in {
            str(gold).strip().lower() for gold in gold_answers}
        score = 1.0 if correct else 0.0
        return {
            "em": score,
            "f1": score,
            "sub_em": score,
            "predicted_answer": prediction,
            "gold_answers": list(gold_answers),
        }

    evaluator.evaluate = evaluate
    data = types.ModuleType("searchqa_stub.data")
    data.load_items = lambda path: json.loads(
        Path(path).read_text(encoding="utf-8"))
    package = types.ModuleType("searchqa_stub")
    package.evaluator = evaluator
    package.data = data
    monkeypatch.setattr(searchqa_env, "import_eval_repo", lambda: package)
    return package


def write_eval_dir(tmp_path: Path, split_items: Dict[str, list]) -> Path:
    """Lay out a fake materialized eval repo: data/searchqa_split/<split>/."""
    for split, items in split_items.items():
        split_dir = tmp_path / "data" / "searchqa_split" / split
        split_dir.mkdir(parents=True)
        (split_dir / "items.json").write_text(
            json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return tmp_path
