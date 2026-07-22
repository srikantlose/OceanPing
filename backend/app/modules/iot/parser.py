"""Parse and validate an MQTT telemetry message from a LoRaWAN/IoT node.

Pure functions, no I/O and no DB — the bridge (bridge.py) does the network and
persistence, this decides what a message *means*. Keeping it pure is what lets
the whole contract be unit-tested without standing up a broker.

Wire contract
-------------
Topic:   oceanping/iot/<node_id>/telemetry
Payload: {
    "name": "Ennore Creek buoy",          # optional; station display name
    "lat": 13.21, "lon": 80.32,           # required on the first message from
                                          # a node (that's when the station is
                                          # created); may be resent to update
                                          # a drifting buoy's position
    "readings": [
        {"variable": "water_level", "value": 1.34, "time": "2026-07-21T12:00:00Z"},
        {"variable": "wave_height", "value": 0.8}   # time optional -> now
    ]
}

The node id in the topic is authoritative for the station id; the payload only
carries metadata and readings. A malformed message raises IotMessageError, and
the bridge logs and drops it — one bad node must never take the bridge down.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TOPIC_PREFIX = "oceanping/iot/"
TOPIC_SUFFIX = "/telemetry"


class IotMessageError(ValueError):
    """A telemetry message that can't be trusted enough to ingest."""


@dataclass(frozen=True)
class Reading:
    variable: str
    value: float
    time: datetime


@dataclass(frozen=True)
class Telemetry:
    node_id: str
    station_id: str
    name: str | None
    lat: float | None
    lon: float | None
    readings: list[Reading]


def node_id_from_topic(topic: str) -> str:
    """Extract <node_id> from oceanping/iot/<node_id>/telemetry.

    Rejects an empty id and any id with a further '/' in it — the topic space
    is exactly one level deep, and a node id smuggling slashes could otherwise
    subscribe the bridge's own wildcard to surprising shapes."""
    if not topic.startswith(TOPIC_PREFIX) or not topic.endswith(TOPIC_SUFFIX):
        raise IotMessageError(f"topic not in the telemetry namespace: {topic!r}")
    node_id = topic[len(TOPIC_PREFIX) : -len(TOPIC_SUFFIX)]
    if not node_id or "/" in node_id:
        raise IotMessageError(f"malformed node id in topic: {topic!r}")
    return node_id


def station_id_for(node_id: str) -> str:
    """Namespace IoT nodes so their station ids can't collide with ERDDAP or
    drill station ids sharing the same table."""
    return f"iot-{node_id}"


def _coerce_float(raw, field: str) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise IotMessageError(f"{field} is not a number: {raw!r}")


def _parse_time(raw, now: datetime, max_future_skew_minutes: float) -> datetime:
    """A missing time means 'now' (the node is reporting live). A parseable
    time is honoured but never allowed to sit in the future beyond a small
    skew, so a wrong node clock can't plant a reading that anomaly detection
    treats as the newest sample indefinitely."""
    if raw is None:
        return now
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise IotMessageError(f"unparseable reading time: {raw!r}")
    elif isinstance(raw, datetime):
        parsed = raw
    else:
        raise IotMessageError(f"reading time must be an ISO string: {raw!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    ceiling = now + timedelta(minutes=max_future_skew_minutes)
    return min(parsed, ceiling)


def parse_telemetry(
    topic: str,
    payload: dict,
    *,
    now: datetime | None = None,
    max_future_skew_minutes: float = 5.0,
) -> Telemetry:
    now = now or datetime.now(timezone.utc)
    node_id = node_id_from_topic(topic)

    if not isinstance(payload, dict):
        raise IotMessageError("payload is not a JSON object")

    lat = None if payload.get("lat") is None else _coerce_float(payload["lat"], "lat")
    lon = None if payload.get("lon") is None else _coerce_float(payload["lon"], "lon")
    if lat is not None and not (-90 <= lat <= 90):
        raise IotMessageError(f"lat out of range: {lat}")
    if lon is not None and not (-180 <= lon <= 180):
        raise IotMessageError(f"lon out of range: {lon}")

    raw_readings = payload.get("readings")
    if not isinstance(raw_readings, list) or not raw_readings:
        raise IotMessageError("readings must be a non-empty list")

    readings: list[Reading] = []
    for r in raw_readings:
        if not isinstance(r, dict):
            raise IotMessageError(f"reading is not an object: {r!r}")
        variable = r.get("variable")
        if not isinstance(variable, str) or not variable.strip():
            raise IotMessageError(f"reading variable must be a non-empty string: {variable!r}")
        value = _coerce_float(r.get("value"), f"{variable} value")
        readings.append(
            Reading(
                variable=variable.strip(),
                value=value,
                time=_parse_time(r.get("time"), now, max_future_skew_minutes),
            )
        )

    name = payload.get("name")
    return Telemetry(
        node_id=node_id,
        station_id=station_id_for(node_id),
        name=name if isinstance(name, str) and name.strip() else None,
        lat=lat,
        lon=lon,
        readings=readings,
    )
