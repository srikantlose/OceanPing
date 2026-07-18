import os

from app.models import TrainingExample
from training import retrain, train_classifier


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows
        self.added = []
        self.committed = False

    def scalars(self, stmt):
        return _FakeScalars(self._rows)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _example(text, hazard_type="coastal_flooding", outcome="verify", corrected_hazard_type=None):
    return TrainingExample(
        text=text,
        lang="en",
        hazard_type=hazard_type,
        outcome=outcome,
        corrected_hazard_type=corrected_hazard_type,
    )


def test_export_examples_drops_blank_text():
    db = _FakeDb([_example("water on the road"), _example("  "), _example("")])
    assert retrain.export_examples(db) == [
        {"text": "water on the road", "hazard_type": "coastal_flooding"}
    ]


def test_export_examples_preserves_hazard_type_per_row():
    db = _FakeDb([_example("oil everywhere", hazard_type="oil_spill")])
    assert retrain.export_examples(db) == [{"text": "oil everywhere", "hazard_type": "oil_spill"}]


def test_export_examples_uses_corrected_hazard_type_when_present():
    # Rejected because the hazard type was wrong, not because it wasn't credible —
    # the analyst's correction should stand in for the original (mis)classification.
    db = _FakeDb([
        _example(
            "green water and dead fish smell",
            hazard_type="oil_spill",
            outcome="reject",
            corrected_hazard_type="algal_bloom",
        )
    ])
    assert retrain.export_examples(db) == [
        {"text": "green water and dead fish smell", "hazard_type": "algal_bloom"}
    ]


def test_main_returns_1_when_not_enough_data(monkeypatch):
    monkeypatch.setattr(retrain, "SessionLocal", lambda: _FakeDb([]))
    monkeypatch.setattr(retrain, "train", lambda examples: None)
    assert retrain.main(["--dry-run"]) == 1


def test_main_dry_run_reports_metrics_without_writing(monkeypatch, capsys):
    db = _FakeDb([_example("water everywhere")])
    fake_result = train_classifier.TrainResult(
        model=object(), metrics={"f1_macro": 1.0, "n_classes": 2}, n_train=1, n_eval=1
    )
    monkeypatch.setattr(retrain, "SessionLocal", lambda: db)
    monkeypatch.setattr(retrain, "train", lambda examples: fake_result)

    assert retrain.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert not db.added
    assert not db.committed


def test_main_writes_artifact_and_model_version_without_dry_run(monkeypatch, tmp_path):
    db = _FakeDb([_example("water everywhere")])
    fake_result = train_classifier.TrainResult(
        model={"fake": "model"}, metrics={"f1_macro": 1.0}, n_train=1, n_eval=1
    )
    monkeypatch.setattr(retrain, "SessionLocal", lambda: db)
    monkeypatch.setattr(retrain, "train", lambda examples: fake_result)
    settings = retrain.get_settings()
    monkeypatch.setattr(settings, "training_artifacts_dir", str(tmp_path))

    assert retrain.main([]) == 0
    assert len(db.added) == 1
    version_row = db.added[0]
    assert db.committed
    assert os.path.exists(version_row.artifact_path)
    assert version_row.training_examples_count == 1
