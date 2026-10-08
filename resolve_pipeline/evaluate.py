"""Error attribution for a resolver run against a gold set.

    gold in candidates?
    ├── no  -> retrieval_miss            (candidate recall)
    └── yes -> prediction == gold?
              ├── yes -> correct
              ├── prediction is null -> abstained
              └── else -> resolution_error   (resolution accuracy)

Gold files are lists of {"text", "lang", "gold": {mention: {"qid", ...}}}.
Predictions are JSONL rows: {"doc", "mention", "candidates": [ids], "prediction": id|null}.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

# The contract echoes a Wikidata identity in the caller's form: Q312, wd:Q312,
# or a Wikidata URI. For scoring they are one identity. Anything else (an IRI
# of a user dataset, say) is compared as written.
_WIKIDATA = re.compile(r"^(?:wd:|https?://(?:www\.)?wikidata\.org/(?:entity|wiki)/)?(Q\d+)$")


def canonical_id(identifier: str | None) -> str | None:
    if identifier is None:
        return None
    m = _WIKIDATA.match(identifier.strip())
    return m.group(1) if m else identifier


RETRIEVAL_MISS = "retrieval_miss"
CORRECT = "correct"
ABSTAINED = "abstained"
RESOLUTION_ERROR = "resolution_error"
MISSING = "missing_prediction"


def attribute(gold_qid: str, candidates: list[str], prediction: str | None) -> str:
    gold = canonical_id(gold_qid)
    if gold not in {canonical_id(c) for c in candidates}:
        return RETRIEVAL_MISS
    if canonical_id(prediction) == gold:
        return CORRECT
    if prediction is None:
        return ABSTAINED
    return RESOLUTION_ERROR


def evaluate(cases: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    by_key = {(int(p["doc"]), p["mention"]): p for p in predictions}
    rows = []
    counts: Counter[str] = Counter()
    for doc, case in enumerate(cases):
        for mention, info in case["gold"].items():
            gold = info["qid"] if isinstance(info, dict) else str(info)
            pred = by_key.get((doc, mention))
            outcome = MISSING if pred is None else attribute(gold, list(pred.get("candidates") or []), pred.get("prediction"))
            counts[outcome] += 1
            rows.append({"doc": doc, "mention": mention, "gold": gold,
                         "prediction": None if pred is None else pred.get("prediction"), "outcome": outcome})
    n = len(rows)
    reachable = counts[CORRECT] + counts[ABSTAINED] + counts[RESOLUTION_ERROR]
    accepted = counts[CORRECT] + counts[RESOLUTION_ERROR]
    return {
        "mentions": n,
        "counts": dict(counts),
        "candidate_recall": reachable / n if n else None,
        "resolution_coverage": accepted / reachable if reachable else None,
        "accepted_precision": counts[CORRECT] / accepted if accepted else None,
        "end_to_end_accuracy": counts[CORRECT] / n if n else None,
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--predictions", required=True, type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    predictions = [json.loads(line) for line in args.predictions.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = evaluate(cases, predictions)
    summary = {k: v for k, v in report.items() if k != "rows"}
    print(json.dumps(summary, indent=2))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
