from pytest import approx

from app.modules.scoring import engine


def test_weights_sum_to_one():
    assert abs(sum(engine.WEIGHTS.values()) - 1.0) < 1e-9


def test_coherence_curve():
    assert engine.coherence_score(0) == 0.0
    assert engine.coherence_score(1) == approx(0.4)
    assert engine.coherence_score(2) == approx(0.6)
    assert engine.coherence_score(4) == 1.0
    assert engine.coherence_score(10) == 1.0


def test_coherence_hearsay_discount():
    # A secondhand account's coherence contribution is halved...
    assert engine.coherence_score(4, hearsay=True) == approx(0.5)
    assert engine.coherence_score(1, hearsay=True) == approx(0.2)
    # ...but zero independent corroboration stays zero either way.
    assert engine.coherence_score(0, hearsay=True) == 0.0
    # and non-hearsay reports are unaffected by the flag at all.
    assert engine.coherence_score(4, hearsay=False) == engine.coherence_score(4)


def test_instrument_mapping():
    assert engine.instrument_score([]) == 0.0
    assert engine.instrument_score([1.0]) == 0.0          # below anomaly floor
    assert engine.instrument_score([2.5]) == 0.0
    assert 0.0 < engine.instrument_score([3.5]) < 1.0
    assert engine.instrument_score([5.0]) == 1.0
    assert engine.instrument_score([-6.0]) == 1.0          # sign-agnostic
    assert engine.instrument_score([1.0, 4.0]) == engine.instrument_score([4.0])


def test_media_forensics_scores():
    assert engine.media_score(has_media=False) == engine.MEDIA_NEUTRAL
    assert engine.media_score(True, phash_reused=True) == 0.0
    assert engine.media_score(True, exif_gps_km=50.0) == 0.2
    assert engine.media_score(True, exif_time_offset_hours=24.0) == 0.4
    assert engine.media_score(True) == 0.6                 # media but no EXIF
    assert engine.media_score(True, exif_gps_km=0.5, exif_time_offset_hours=0.2) == 1.0


def test_satellite_score():
    assert engine.satellite_score([]) == 0.0
    assert engine.satellite_score([0.3]) == 0.3
    assert engine.satellite_score([0.2, 0.7, 0.4]) == 0.7  # strongest observation wins


def test_official_score():
    assert engine.official_score(None) == 0.0
    assert engine.official_score({"certainty": "Observed"}) == 1.0
    assert engine.official_score({"certainty": "Likely"}) == 0.7
    assert engine.official_score({"certainty": "Possible"}) == 0.4
    assert engine.official_score({"certainty": "Unknown"}) == 0.2
    assert engine.official_score({"certainty": "not-a-real-cap-value"}) == 0.2  # defensive default


def test_account_device_score():
    # A week-old (or older) account with just this one report scores full marks.
    assert engine.account_device_score(24 * 7, 1) == 1.0
    assert engine.account_device_score(24 * 30, 1) == 1.0
    # Brand new account, single report — no burst penalty yet, just low age.
    assert engine.account_device_score(0, 1) == 0.0
    # A burst of reports from the same account pulls the score down regardless of age.
    assert engine.account_device_score(24 * 7, 3) == approx(1.0 - 2 * 0.15)
    assert engine.account_device_score(24 * 7, 100) == 0.5  # penalty caps at 0.5
    assert engine.account_device_score(0, 100) == 0.0  # never goes negative


def test_combine_bounds_and_blend():
    assert engine.combine({}) == 0.0
    assert engine.combine({k: 1.0 for k in engine.WEIGHTS}) == 1.0
    mid = engine.combine({"trust": 0.5, "coherence": 1.0, "instrument": 1.0, "media": 0.5})
    assert abs(mid - (0.085 + 0.21 + 0.21 + 0.065)) < 1e-6


def test_citizen_only_reports_cannot_reach_corroborated_threshold_without_instruments():
    # Even a perfect trust/coherence/media/account-device report stays below
    # auto-escalation unless instruments, satellite, or an official advisory
    # agree — the no-citizen-only-escalation rule holds numerically as well
    # as by the explicit instrument/satellite/official>0 gate in
    # scoring/service.py.
    best_citizen_only = engine.combine(
        {
            "trust": 0.95,
            "coherence": 1.0,
            "instrument": 0.0,
            "media": 1.0,
            "satellite": 0.0,
            "account_device": 1.0,
            "official": 0.0,
        }
    )
    assert best_citizen_only < 0.7


def test_official_advisory_can_tip_a_corroborated_report_over_the_threshold():
    # A report with decent (not perfect) citizen-side signals plus an active,
    # highly-certain official advisory crosses the auto-corroboration
    # threshold (0.6, see core/config.py::corroborated_threshold) where the
    # same citizen-side signals alone do not — proving "official" carries
    # real numeric weight, not just gate-only significance (phase 4,
    # milestone 1's own framing: "official advisory active over the cell ->
    # strong prior").
    citizen_only = engine.combine(
        {"trust": 0.8, "coherence": 1.0, "media": 0.8, "account_device": 0.5}
    )
    with_official = engine.combine(
        {"trust": 0.8, "coherence": 1.0, "media": 0.8, "account_device": 0.5, "official": 1.0}
    )
    assert citizen_only < 0.6
    assert with_official >= 0.6
