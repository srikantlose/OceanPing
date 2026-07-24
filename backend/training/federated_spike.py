"""Operational entry point for the federated-learning spike (phase 4,
milestone 6: "federated learning spike -> go/no-go"). Reads the same
training_examples corpus retrain.py already retrains the centralized
classifier on, runs both the federated simulation and the centralized
baseline over an identical held-out split, and prints both so the numbers in
docs/plans/phase-4-scale.md's go/no-go write-up are reproducible against the
real dev database rather than made up.

Usage (inside the backend container, which has app/ and training/ on its
PYTHONPATH):
    python -m training.federated_spike [--shards N] [--seed N]
"""
from __future__ import annotations

import argparse
import json

from app.core.db import SessionLocal
from training.federated import run_spike
from training.retrain import export_examples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=int, default=5, help="number of simulated devices")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        examples = export_examples(db)
        print(f"Exported {len(examples)} verified training examples.")

        result = run_spike(examples, n_shards=args.shards, seed=args.seed)
        if result is None:
            print("Not enough labeled data to run the spike yet (need >=2 classes with >=2 examples each).")
            return 1

        print(json.dumps({
            "n_examples": result.n_examples,
            "n_shards": result.n_shards,
            "per_shard_counts": result.per_shard_counts,
            "federated_metrics": result.federated_metrics,
            "centralized_metrics": result.centralized_metrics,
        }, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
