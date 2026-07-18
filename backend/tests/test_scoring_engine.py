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
    assert abs(mid - (0.10 + 0.25 + 0.25 + 0.075)) < 1e-6


def test_citizen_only_reports_cannot_reach_corroborated_threshold_without_instruments():
    # Even a perfect trust/coherence/media/account-device report stays below
    # auto-escalation unless instruments or satellite agree — the
    # no-citizen-only-escalation rule holds numerically as well as by the
    # explicit instrument>0-or-satellite>0 gate in scoring/service.py.
    best_citizen_only = engine.combine(
        {
            "trust": 0.95,
            "coherence": 1.0,
            "instrument": 0.0,
            "media": 1.0,
            "satellite": 0.0,
            "account_device": 1.0,
        }
    )
    assert best_citizen_only < 0.7
