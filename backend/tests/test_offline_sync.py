"""The offline-queue ingest contract (phase 3, milestone 5).

These guard two properties the mobile queue depends on and that are easy to
regress silently: a queued report keeps the time it was *observed* rather
than the time it synced, and a client-supplied timestamp can't be used to
place a report anywhere the caller likes.
"""
from datetime import datetime, timedelta, timezone

from app.modules.ingest.service import clamp_observed_at

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
MAX_AGE = 24.0


def test_missing_observed_at_defaults_to_now():
    assert clamp_observed_at(None, MAX_AGE, now=NOW) == NOW


def test_a_queued_observation_keeps_its_own_time():
    """The whole point of the offline queue: a report held for three hours
    must not land stamped 'now' and corroborate whatever is happening at sync
    time."""
    observed = NOW - timedelta(hours=3)
    assert clamp_observed_at(observed, MAX_AGE, now=NOW) == observed


def test_future_timestamps_collapse_to_now():
    """A phone with a skewed clock stays usable, but can't place a report in
    the future — where it would sit in the coherence window of an event that
    hasn't happened yet."""
    assert clamp_observed_at(NOW + timedelta(hours=5), MAX_AGE, now=NOW) == NOW


def test_timestamps_older_than_the_queue_could_hold_collapse_to_the_floor():
    ancient = NOW - timedelta(days=90)
    assert clamp_observed_at(ancient, MAX_AGE, now=NOW) == NOW - timedelta(hours=MAX_AGE)


def test_clamping_is_bounded_on_both_sides_for_any_input():
    floor, ceiling = NOW - timedelta(hours=MAX_AGE), NOW
    candidates = [
        NOW - timedelta(days=365),
        NOW - timedelta(hours=MAX_AGE + 0.1),
        NOW - timedelta(hours=MAX_AGE),
        NOW - timedelta(hours=1),
        NOW,
        NOW + timedelta(seconds=1),
        NOW + timedelta(days=365),
    ]
    for candidate in candidates:
        assert floor <= clamp_observed_at(candidate, MAX_AGE, now=NOW) <= ceiling


def test_naive_timestamps_are_treated_as_utc_rather_than_crashing():
    """Form-encoded datetimes can arrive without a timezone; comparing those
    against an aware `now` would raise instead of clamping."""
    naive = (NOW - timedelta(hours=2)).replace(tzinfo=None)
    assert clamp_observed_at(naive, MAX_AGE, now=NOW) == NOW - timedelta(hours=2)


def test_boundary_timestamp_exactly_at_the_floor_is_preserved():
    exactly_floor = NOW - timedelta(hours=MAX_AGE)
    assert clamp_observed_at(exactly_floor, MAX_AGE, now=NOW) == exactly_floor
