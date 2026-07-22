from datetime import datetime, timedelta, timezone

import pytest

from app.modules.iot import parser
from app.modules.iot.parser import IotMessageError, parse_telemetry

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
TOPIC = "oceanping/iot/buoy-ennore-01/telemetry"


def _payload(**overrides):
    base = {
        "name": "Ennore Creek buoy",
        "lat": 13.21,
        "lon": 80.32,
        "readings": [{"variable": "water_level", "value": 1.34, "time": NOW.isoformat()}],
    }
    base.update(overrides)
    return base


# --- topic parsing ----------------------------------------------------------


def test_node_id_extracted_from_topic():
    assert parser.node_id_from_topic(TOPIC) == "buoy-ennore-01"


def test_station_id_is_namespaced():
    assert parser.station_id_for("buoy-ennore-01") == "iot-buoy-ennore-01"


def test_topic_outside_namespace_is_rejected():
    with pytest.raises(IotMessageError):
        parser.node_id_from_topic("weather/buoy/telemetry")


def test_topic_with_empty_node_id_is_rejected():
    with pytest.raises(IotMessageError):
        parser.node_id_from_topic("oceanping/iot//telemetry")


def test_topic_with_extra_path_segment_is_rejected():
    with pytest.raises(IotMessageError):
        parser.node_id_from_topic("oceanping/iot/a/b/telemetry")


# --- payload validation -----------------------------------------------------


def test_parses_a_well_formed_message():
    tel = parse_telemetry(TOPIC, _payload(), now=NOW)
    assert tel.node_id == "buoy-ennore-01"
    assert tel.station_id == "iot-buoy-ennore-01"
    assert tel.name == "Ennore Creek buoy"
    assert tel.lat == 13.21 and tel.lon == 80.32
    assert len(tel.readings) == 1
    assert tel.readings[0].variable == "water_level"
    assert tel.readings[0].value == 1.34
    assert tel.readings[0].time == NOW


def test_readings_must_be_a_non_empty_list():
    with pytest.raises(IotMessageError):
        parse_telemetry(TOPIC, _payload(readings=[]), now=NOW)
    with pytest.raises(IotMessageError):
        parse_telemetry(TOPIC, _payload(readings="water_level=1.3"), now=NOW)


def test_missing_reading_time_defaults_to_now():
    tel = parse_telemetry(TOPIC, _payload(readings=[{"variable": "wave_height", "value": 0.8}]), now=NOW)
    assert tel.readings[0].time == NOW


def test_future_reading_time_is_clamped_to_skew_ceiling():
    future = (NOW + timedelta(hours=3)).isoformat()
    tel = parse_telemetry(
        TOPIC, _payload(readings=[{"variable": "water_level", "value": 2.0, "time": future}]),
        now=NOW, max_future_skew_minutes=5.0,
    )
    assert tel.readings[0].time == NOW + timedelta(minutes=5)


def test_a_past_reading_time_is_preserved_for_backfill():
    past = (NOW - timedelta(days=2)).isoformat()
    tel = parse_telemetry(
        TOPIC, _payload(readings=[{"variable": "water_level", "value": 1.0, "time": past}]), now=NOW
    )
    assert tel.readings[0].time == NOW - timedelta(days=2)


def test_naive_timestamps_are_treated_as_utc():
    naive = "2026-07-21T11:00:00"
    tel = parse_telemetry(TOPIC, _payload(readings=[{"variable": "water_level", "value": 1.0, "time": naive}]), now=NOW)
    assert tel.readings[0].time == datetime(2026, 7, 21, 11, 0, tzinfo=timezone.utc)


def test_non_numeric_value_is_rejected():
    with pytest.raises(IotMessageError):
        parse_telemetry(TOPIC, _payload(readings=[{"variable": "water_level", "value": "high"}]), now=NOW)


def test_blank_variable_is_rejected():
    with pytest.raises(IotMessageError):
        parse_telemetry(TOPIC, _payload(readings=[{"variable": "  ", "value": 1.0}]), now=NOW)


def test_out_of_range_coordinates_are_rejected():
    with pytest.raises(IotMessageError):
        parse_telemetry(TOPIC, _payload(lat=200), now=NOW)
    with pytest.raises(IotMessageError):
        parse_telemetry(TOPIC, _payload(lon=-999), now=NOW)


def test_location_is_optional_for_a_streaming_node():
    """A node already registered can stream readings without resending its
    position — the service, not the parser, enforces that the *first* message
    carries one."""
    tel = parse_telemetry(
        TOPIC, {"readings": [{"variable": "water_level", "value": 1.1}]}, now=NOW
    )
    assert tel.lat is None and tel.lon is None
    assert tel.name is None
    assert len(tel.readings) == 1


def test_unparseable_time_is_rejected():
    with pytest.raises(IotMessageError):
        parse_telemetry(TOPIC, _payload(readings=[{"variable": "water_level", "value": 1.0, "time": "yesterday"}]), now=NOW)
