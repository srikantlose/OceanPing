from datetime import datetime, timedelta, timezone

import pytest

from app.modules.narratives import engine

T0 = datetime(2026, 7, 21, tzinfo=timezone.utc)


def _report(i, embedding, hazard_type="tsunami", lat=13.0, lon=80.2, status="unverified", text=None, h3_cell="cellA"):
    return {
        "id": f"r{i}",
        "h3_cell": h3_cell,
        "embedding": embedding,
        "text": text or f"text {i}",
        "hazard_type": hazard_type,
        "lat": lat,
        "lon": lon,
        "status": status,
        "created_at": T0 + timedelta(minutes=i),
    }


# --- cluster_reports -----------------------------------------------------------


def test_cluster_reports_groups_similar_embeddings_together():
    a, b, c = [1.0, 0.0, 0.0], [0.99, 0.01, 0.0], [0.98, 0.02, 0.0]
    reports = [_report(0, a), _report(1, b), _report(2, c)]
    clusters = engine.cluster_reports(reports, sim_threshold=0.9)
    assert len(clusters) == 1
    assert clusters[0].report_count == 3


def test_cluster_reports_splits_dissimilar_embeddings():
    a, b = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]
    reports = [_report(i, a) for i in range(3)] + [_report(i + 10, b) for i in range(3)]
    clusters = engine.cluster_reports(reports, sim_threshold=0.9)
    assert len(clusters) == 2
    assert {c.report_count for c in clusters} == {3, 3}


def test_cluster_reports_drops_clusters_below_min_size():
    a = [1.0, 0.0, 0.0]
    reports = [_report(i, a) for i in range(2)]  # below MIN_NARRATIVE_REPORTS
    assert engine.cluster_reports(reports, sim_threshold=0.9) == []


def test_cluster_dominant_hazard_is_majority_vote():
    a = [1.0, 0.0, 0.0]
    reports = [
        _report(0, a, hazard_type="tsunami"),
        _report(1, a, hazard_type="tsunami"),
        _report(2, a, hazard_type="high_waves"),
    ]
    clusters = engine.cluster_reports(reports, sim_threshold=0.9)
    assert clusters[0].dominant_hazard() == "tsunami"


def test_cluster_centroid_averages_lat_lon():
    a = [1.0, 0.0, 0.0]
    reports = [
        _report(0, a, lat=13.0, lon=80.0),
        _report(1, a, lat=13.2, lon=80.2),
        _report(2, a, lat=13.4, lon=80.4),
    ]
    clusters = engine.cluster_reports(reports, sim_threshold=0.9)
    lat, lon = clusters[0].centroid()
    assert lat == pytest.approx(13.2)
    assert lon == pytest.approx(80.2)


def test_cluster_rejected_count():
    a = [1.0, 0.0, 0.0]
    reports = [
        _report(0, a, status="rejected"),
        _report(1, a, status="unverified"),
        _report(2, a, status="rejected"),
    ]
    clusters = engine.cluster_reports(reports, sim_threshold=0.9)
    assert clusters[0].rejected_count() == 2


def test_cluster_representative_text_is_closest_to_centroid():
    center = [1.0, 0.0, 0.0]
    off = [0.8, 0.6, 0.0]
    reports = [
        _report(0, center, text="typical claim A"),
        _report(1, center, text="typical claim B"),
        _report(2, off, text="outlier claim"),
    ]
    clusters = engine.cluster_reports(reports, sim_threshold=0.5)
    assert clusters[0].representative_text() in ("typical claim A", "typical claim B")


def test_cluster_reports_collects_h3_cells():
    a = [1.0, 0.0, 0.0]
    reports = [_report(i, a, h3_cell=f"cell{i}") for i in range(3)]
    clusters = engine.cluster_reports(reports, sim_threshold=0.9)
    assert clusters[0].h3_cells == {"cell0", "cell1", "cell2"}


def test_cluster_reports_processes_oldest_first_regardless_of_input_order():
    a = [1.0, 0.0, 0.0]
    reports = [_report(i, a) for i in range(3)]
    shuffled = [reports[2], reports[0], reports[1]]
    clusters = engine.cluster_reports(shuffled, sim_threshold=0.9)
    assert len(clusters) == 1
    assert clusters[0].report_count == 3


# --- is_contradiction ------------------------------------------------------------


def test_is_contradiction_true_when_analyst_already_rejected():
    assert engine.is_contradiction(instrument_flat=False, hazard_has_instrument_signal=True, rejected_count=1)


def test_is_contradiction_true_when_instrument_flat_and_hazard_has_signal():
    assert engine.is_contradiction(instrument_flat=True, hazard_has_instrument_signal=True, rejected_count=0)


def test_is_contradiction_false_when_instrument_active():
    assert not engine.is_contradiction(instrument_flat=False, hazard_has_instrument_signal=True, rejected_count=0)


def test_is_contradiction_false_for_hazard_with_no_instrument_signal_and_no_rejection():
    """oil_spill/algal_bloom-style hazards: "instruments show nothing" can't
    be a contradiction signal for a hazard no instrument ever measures."""
    assert not engine.is_contradiction(instrument_flat=True, hazard_has_instrument_signal=False, rejected_count=0)


def test_is_contradiction_rejected_count_wins_even_without_instrument_signal():
    assert engine.is_contradiction(instrument_flat=True, hazard_has_instrument_signal=False, rejected_count=3)


# --- compose_correction -----------------------------------------------------------


def test_compose_correction_mentions_hazard_and_location():
    msg = engine.compose_correction("en", "tsunami signs", "Marina Beach", instrument_flat=True, rejected_count=0)
    assert "tsunami signs" in msg["standard"]
    assert "Marina Beach" in msg["standard"]
    assert "instrument" in msg["standard"]


def test_compose_correction_mentions_analyst_when_rejected():
    msg = engine.compose_correction("en", "tsunami signs", "Marina Beach", instrument_flat=False, rejected_count=2)
    assert "analyst" in msg["standard"]


def test_compose_correction_short_variant_is_shorter_than_standard():
    msg = engine.compose_correction("en", "tsunami signs", "Marina Beach", instrument_flat=True, rejected_count=0)
    assert len(msg["short"]) < len(msg["standard"])


def test_compose_correction_all_langs_covers_every_language():
    labels = {"en": "tsunami signs", "ta": "சுனாமி அறிகுறிகள்", "te": "సునామీ సూచనలు"}
    out = engine.compose_correction_all_langs(labels, "Marina Beach", instrument_flat=True, rejected_count=0)
    assert set(out) == {"en", "ta", "te"}
    for lang in out:
        assert out[lang]["standard"]
        assert out[lang]["short"]
        assert "Marina Beach" in out[lang]["standard"]
