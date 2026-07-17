"""Operational entry point for the active-learning loop (phase 1, milestone 4):
reads verified labels accumulated in `training_examples`, retrains the
classify() 'finetuned' artifact via train_classifier.train(), and registers the
result in `model_versions`. Meant to run weekly (cron or manual) per the
phase-1 plan. Promotion into the live classifier is a manual config flip
(NLP_MODE=finetuned, NLP_MODEL_VERSION=<name>) — never automatic, so a bad
retrain can't silently degrade production classification.

Usage (inside the backend container, which has app/ on its PYTHONPATH):
    python -m training.retrain [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import ModelVersion, TrainingExample
from training.train_classifier import train

log = logging.getLogger(__name__)


def export_examples(db) -> list[dict]:
    """Verified rows only — a reject doesn't tell us the *correct* hazard type,
    just that the report wasn't credible (milestone 5's correction UI is what
    turns rejections into usable negative/corrected labels)."""
    rows = db.scalars(
        select(TrainingExample).where(TrainingExample.outcome == "verify")
    ).all()
    return [
        {"text": r.text, "hazard_type": r.hazard_type}
        for r in rows
        if r.text and r.text.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Train and print metrics only; don't write an artifact or model_versions row.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    db = SessionLocal()
    try:
        examples = export_examples(db)
        print(f"Exported {len(examples)} verified training examples.")

        result = train(examples)
        if result is None:
            print("Not enough labeled data to train yet (need >=2 classes with >=2 examples each).")
            return 1

        print(json.dumps(result.metrics, indent=2))
        if args.dry_run:
            print("(dry run — no artifact or model_versions row written)")
            return 0

        import joblib

        version = datetime.now(timezone.utc).strftime("finetuned-%Y%m%d-%H%M%S")
        artifact_dir = os.path.join(settings.training_artifacts_dir, version)
        os.makedirs(artifact_dir, exist_ok=True)
        artifact_path = os.path.join(artifact_dir, "classifier.joblib")
        joblib.dump(result.model, artifact_path)

        db.add(
            ModelVersion(
                name=version,
                artifact_path=artifact_path,
                metrics=result.metrics,
                training_examples_count=len(examples),
            )
        )
        db.commit()
        print(f"Wrote model version '{version}' ({artifact_path}).")
        print(f"To promote: set NLP_MODE=finetuned and NLP_MODEL_VERSION={version}.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
