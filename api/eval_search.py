"""Compares semantic search with and without the rerank stage on queries you
have judged, so match thresholds are chosen from data, not guessed.

    PYTHONPATH=api/src python api/eval_search.py queries.json [--k 10]

queries.json (filenames are photos.filename, i.e. relative paths):

    [{"query": "dog on a beach", "relevant": ["trip/IMG_1.jpg", "trip/IMG_7.jpg"]}, ...]

Reads the database named by LUMINARY_DATABASE_* and the models the API
uses; writes nothing. Prints, per mode, mean precision@k, recall@k and
MRR, then precision/recall/F1 at a sweep of score thresholds -- pick the
rows that match chat._MATCH_LEVELS' intent (loose = recall, strict =
precision).
"""

import argparse
import json
import statistics

THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9]


def _prf(returned: list[str], relevant: set[str]):
    hits = len(set(returned) & relevant)
    precision = hits / len(returned) if returned else 0.0
    recall = hits / len(relevant) if relevant else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def _reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for position, name in enumerate(ranked, start=1):
        if name in relevant:
            return 1.0 / position
    return 0.0


def evaluate(cases: list[dict], k: int) -> None:
    from app import storage

    for label, rerank in (("embedding only", False), ("embedding + rerank", True)):
        print(f"\n=== {label} ===")
        # One wide search per query; thresholds are applied afterwards.
        runs = []
        for case in cases:
            results = storage.search_photos(case["query"], 0.0, rerank=rerank)
            runs.append(([r["filename"] for r in results], [r["similarity"] for r in results], set(case["relevant"])))

        p_at_k = [_prf(names[:k], rel)[0] if names else 0.0 for names, _, rel in runs]
        r_at_k = [len(set(names[:k]) & rel) / len(rel) if rel else 0.0 for names, _, rel in runs]
        mrr = [_reciprocal_rank(names, rel) for names, _, rel in runs]
        print(f"precision@{k}: {statistics.mean(p_at_k):.3f}   recall@{k}: {statistics.mean(r_at_k):.3f}   MRR: {statistics.mean(mrr):.3f}")

        print("threshold  precision  recall  f1")
        for threshold in THRESHOLDS:
            rows = [
                _prf([n for n, s in zip(names, scores) if s >= threshold], rel) for names, scores, rel in runs
            ]
            print(
                f"{threshold:9.2f}  {statistics.mean(r[0] for r in rows):9.3f}  "
                f"{statistics.mean(r[1] for r in rows):6.3f}  {statistics.mean(r[2] for r in rows):.3f}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("queries", help="JSON file of {query, relevant[]} cases")
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()
    with open(args.queries) as handle:
        cases = json.load(handle)
    evaluate(cases, args.k)


if __name__ == "__main__":
    main()
