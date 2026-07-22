from app.modules.recovery.engine import (
    AidParty,
    MissingCandidate,
    fuzzy_name_score,
    match_aid,
    rank_missing_matches,
)

# Roughly 1.1 km per 0.01 degree latitude near the equator.
NEAR_LAT, NEAR_LON = 13.0, 80.0
FAR_LAT, FAR_LON = 13.5, 80.5  # tens of km away


# --- match_aid ----------------------------------------------------------------


def test_match_aid_pairs_same_category_within_radius():
    requests = [AidParty(id="r1", category="water", lat=NEAR_LAT, lon=NEAR_LON)]
    offers = [AidParty(id="o1", category="water", lat=NEAR_LAT + 0.01, lon=NEAR_LON)]

    matches = match_aid(requests, offers, max_km=5.0)

    assert len(matches) == 1
    assert matches[0].request_id == "r1" and matches[0].offer_id == "o1"
    assert matches[0].distance_km < 5.0


def test_match_aid_ignores_mismatched_categories():
    requests = [AidParty(id="r1", category="water", lat=NEAR_LAT, lon=NEAR_LON)]
    offers = [AidParty(id="o1", category="medical", lat=NEAR_LAT, lon=NEAR_LON)]

    assert match_aid(requests, offers, max_km=5.0) == []


def test_match_aid_excludes_offers_beyond_max_km():
    requests = [AidParty(id="r1", category="food", lat=NEAR_LAT, lon=NEAR_LON)]
    offers = [AidParty(id="o1", category="food", lat=FAR_LAT, lon=FAR_LON)]

    assert match_aid(requests, offers, max_km=5.0) == []


def test_match_aid_one_offer_can_match_several_requests():
    """Deliberately not an exclusive assignment — see engine.py's docstring:
    picking which request an offer actually goes to is a human call."""
    requests = [
        AidParty(id="r1", category="shelter", lat=NEAR_LAT, lon=NEAR_LON),
        AidParty(id="r2", category="shelter", lat=NEAR_LAT + 0.005, lon=NEAR_LON),
    ]
    offers = [AidParty(id="o1", category="shelter", lat=NEAR_LAT, lon=NEAR_LON)]

    matches = match_aid(requests, offers, max_km=5.0)

    assert {m.request_id for m in matches} == {"r1", "r2"}


def test_match_aid_sorts_nearest_first():
    requests = [AidParty(id="r1", category="water", lat=NEAR_LAT, lon=NEAR_LON)]
    offers = [
        AidParty(id="far", category="water", lat=NEAR_LAT + 0.03, lon=NEAR_LON),
        AidParty(id="near", category="water", lat=NEAR_LAT + 0.005, lon=NEAR_LON),
    ]

    matches = match_aid(requests, offers, max_km=10.0)

    assert [m.offer_id for m in matches] == ["near", "far"]


# --- fuzzy_name_score -----------------------------------------------------------


def test_fuzzy_name_score_identical_strings_is_1():
    assert fuzzy_name_score("Kavya Raman", "Kavya Raman") == 1.0


def test_fuzzy_name_score_is_case_and_whitespace_insensitive():
    assert fuzzy_name_score("  Kavya Raman ", "kavya raman") == 1.0


def test_fuzzy_name_score_unrelated_names_is_low():
    assert fuzzy_name_score("Kavya Raman", "Suresh Pillai") < 0.4


def test_fuzzy_name_score_catches_a_likely_misspelling():
    assert fuzzy_name_score("Kavya Raman", "Kavia Raman") > 0.85


# --- rank_missing_matches -----------------------------------------------------


def test_rank_missing_matches_drops_candidates_below_threshold():
    candidates = [MissingCandidate(id="c1", name="Totally Different Name", lat=None, lon=None)]

    matches = rank_missing_matches(
        "Kavya Raman", None, None, candidates, name_threshold=0.72, max_km=25.0
    )

    assert matches == []


def test_rank_missing_matches_keeps_a_strong_name_match():
    candidates = [MissingCandidate(id="c1", name="Kavya Raman", lat=None, lon=None)]

    matches = rank_missing_matches(
        "Kavya Raman", None, None, candidates, name_threshold=0.72, max_km=25.0
    )

    assert len(matches) == 1
    assert matches[0].candidate_id == "c1"
    assert matches[0].distance_km is None


def test_rank_missing_matches_applies_geo_gate_when_both_sides_have_a_location():
    candidates = [MissingCandidate(id="far", name="Kavya Raman", lat=FAR_LAT, lon=FAR_LON)]

    matches = rank_missing_matches(
        "Kavya Raman", NEAR_LAT, NEAR_LON, candidates, name_threshold=0.72, max_km=5.0
    )

    assert matches == []


def test_rank_missing_matches_skips_geo_gate_when_a_side_lacks_location():
    """A phone-in report with no location shouldn't be penalized for it."""
    candidates = [MissingCandidate(id="c1", name="Kavya Raman", lat=None, lon=None)]

    matches = rank_missing_matches(
        "Kavya Raman", NEAR_LAT, NEAR_LON, candidates, name_threshold=0.72, max_km=5.0
    )

    assert len(matches) == 1
    assert matches[0].distance_km is None


def test_rank_missing_matches_sorts_best_score_first():
    candidates = [
        MissingCandidate(id="weaker", name="Kavya Ramana", lat=None, lon=None),
        MissingCandidate(id="exact", name="Kavya Raman", lat=None, lon=None),
    ]

    matches = rank_missing_matches(
        "Kavya Raman", None, None, candidates, name_threshold=0.72, max_km=25.0
    )

    assert matches[0].candidate_id == "exact"
