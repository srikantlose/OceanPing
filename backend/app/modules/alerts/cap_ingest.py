"""Inbound CAP 1.2 parsing (phase 4, milestone 1) — pure functions, no I/O.

Turns a real Common Alerting Protocol document (an official IMD/NDMA/SACHET
warning, in production) into a normalized structure cap_service.py can store
and scoring/service.py can treat as an authoritative corroboration signal. No
real feed exists to poll without a partnership, so this is exercised in tests
against hand-built, schema-valid CAP documents — the same "real format, no
real upstream yet" honesty as satellite/, fisherman/pfz.py, and whatsapp/
elsewhere in this app.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from xml.etree import ElementTree as ET

from app.modules.hazards.registry import cap_event_keywords_table

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"

# Best-effort keyword match against a CAP <event> string onto the hazard
# vocabulary this app models (modules/hazards/ — phase 4, milestone 2) —
# deliberately not a lookup against a real agency's event-code table, since
# no such table exists for any Indian issuing authority yet. An event that
# matches nothing is not stored at all (see cap_service.py) rather than
# guessed at — an unmapped advisory has nothing to corroborate.
EVENT_HAZARD_KEYWORDS: list[tuple[str, str]] = cap_event_keywords_table()


def map_event_to_hazard(event_text: str) -> str | None:
    lowered = (event_text or "").lower()
    for keyword, hazard in EVENT_HAZARD_KEYWORDS:
        if keyword in lowered:
            return hazard
    return None


def _tag(name: str) -> str:
    return f"{{{CAP_NS}}}{name}"


def _text(el: ET.Element, name: str) -> str | None:
    child = el.find(_tag(name))
    return child.text.strip() if child is not None and child.text else None


def _parse_dt(value: str | None) -> datetime | None:
    """CAP dateTime values are supposed to always carry a timezone offset;
    a document that doesn't is assumed UTC rather than rejected, same
    defensive posture as every other externally-sourced field in this app."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_polygon(text: str) -> list[tuple[float, float]]:
    """CAP <polygon> text: space-separated "lat,lon" pairs."""
    points = []
    for pair in text.split():
        lat_str, lon_str = pair.split(",")
        points.append((float(lat_str), float(lon_str)))
    return points


def _parse_circle(text: str) -> tuple[float, float, float]:
    """CAP <circle> text: "lat,lon radius" (radius in km)."""
    point, radius = text.split()
    lat_str, lon_str = point.split(",")
    return (float(lat_str), float(lon_str), float(radius))


def _parse_references(text: str | None) -> list[str]:
    """CAP <references>: space-separated "sender,identifier,sent" triples —
    only the identifier (middle field) matters for cancel/update matching."""
    if not text:
        return []
    out = []
    for triple in text.split():
        parts = triple.split(",")
        if len(parts) >= 2:
            out.append(parts[1])
    return out


@dataclass
class CapArea:
    area_desc: str
    polygon: list[tuple[float, float]] | None = None  # [(lat, lon), ...], closed ring
    circle: tuple[float, float, float] | None = None  # (lat, lon, radius_km)


@dataclass
class CapInfo:
    language: str
    event: str
    urgency: str
    severity: str
    certainty: str
    headline: str | None
    description: str | None
    effective: datetime | None
    expires: datetime | None
    areas: list[CapArea] = field(default_factory=list)


@dataclass
class ParsedCapAlert:
    identifier: str
    sender: str
    sent: datetime
    status: str
    msg_type: str
    references: list[str]
    infos: list[CapInfo]


def parse_cap(xml_text: str) -> ParsedCapAlert:
    root = ET.fromstring(xml_text)
    identifier = _text(root, "identifier") or ""
    sender = _text(root, "sender") or ""
    sent = _parse_dt(_text(root, "sent")) or datetime.now(timezone.utc)
    status = _text(root, "status") or "Actual"
    msg_type = _text(root, "msgType") or "Alert"
    references = _parse_references(_text(root, "references"))

    infos = []
    for info_el in root.findall(_tag("info")):
        areas: list[CapArea] = []
        for area_el in info_el.findall(_tag("area")):
            area_desc = _text(area_el, "areaDesc") or ""
            polygons = [_parse_polygon(p.text) for p in area_el.findall(_tag("polygon")) if p.text]
            circles = [_parse_circle(c.text) for c in area_el.findall(_tag("circle")) if c.text]
            for poly in polygons:
                areas.append(CapArea(area_desc=area_desc, polygon=poly))
            for circ in circles:
                areas.append(CapArea(area_desc=area_desc, circle=circ))
        infos.append(
            CapInfo(
                language=_text(info_el, "language") or "en",
                event=_text(info_el, "event") or "",
                urgency=_text(info_el, "urgency") or "Unknown",
                severity=_text(info_el, "severity") or "Unknown",
                certainty=_text(info_el, "certainty") or "Unknown",
                headline=_text(info_el, "headline"),
                description=_text(info_el, "description"),
                effective=_parse_dt(_text(info_el, "effective")) or sent,
                expires=_parse_dt(_text(info_el, "expires")),
                areas=areas,
            )
        )

    return ParsedCapAlert(
        identifier=identifier,
        sender=sender,
        sent=sent,
        status=status,
        msg_type=msg_type,
        references=references,
        infos=infos,
    )
