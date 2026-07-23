import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from lxml import etree

from app.modules.alerts import cap
from app.modules.alerts.engine import draft_message
from app.modules.geo.h3utils import cell_for

XSD_PATH = Path(__file__).resolve().parents[1] / "app" / "modules" / "alerts" / "schemas" / "CAP-v1.2.xsd"
CAP_SCHEMA = etree.XMLSchema(etree.parse(str(XSD_PATH)))

NOW = datetime(2026, 7, 23, 6, 0, tzinfo=timezone.utc)
CELL = cell_for(13.0512, 80.2831)


def _alert(**overrides):
    base = dict(
        id=uuid.uuid4(),
        hazard_type="tsunami",
        tier="warning",
        h3_cells=[CELL],
        message=draft_message("tsunami", "warning", 5, note="Move to high ground immediately."),
        status="active",
        created_at=NOW,
        expires_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _validate(xml_text: str):
    doc = etree.fromstring(xml_text.encode("utf-8"))
    CAP_SCHEMA.assertValid(doc)
    return doc


def test_warning_tier_validates_against_the_real_cap_xsd():
    alert = _alert()
    xml_text = cap.alert_to_cap_xml(
        alert, sender="pilot@oceanping.example", sender_name="OceanPing Pilot",
        base_url="http://localhost:3000",
    )
    doc = _validate(xml_text)
    ns = {"cap": cap.CAP_NS}
    assert doc.find("cap:identifier", ns).text == str(alert.id)
    assert doc.find("cap:msgType", ns).text == "Alert"
    info = doc.find("cap:info", ns)
    assert info.find("cap:urgency", ns).text == "Immediate"
    assert info.find("cap:severity", ns).text == "Severe"
    assert info.find("cap:certainty", ns).text == "Observed"
    assert len(info.findall("cap:area/cap:polygon", ns)) == 1


@pytest.mark.parametrize("tier,urgency,severity,certainty", [
    ("advisory", "Expected", "Minor", "Possible"),
    ("watch", "Expected", "Moderate", "Likely"),
    ("warning", "Immediate", "Severe", "Observed"),
])
def test_tier_maps_to_cap_urgency_severity_certainty(tier, urgency, severity, certainty):
    alert = _alert(tier=tier, message=draft_message("high_waves", tier, 2))
    xml_text = cap.alert_to_cap_xml(
        alert, sender="s@x.example", sender_name="S", base_url="http://x",
    )
    doc = _validate(xml_text)
    ns = {"cap": cap.CAP_NS}
    info = doc.find("cap:info", ns)
    assert info.find("cap:urgency", ns).text == urgency
    assert info.find("cap:severity", ns).text == severity
    assert info.find("cap:certainty", ns).text == certainty


def test_expired_alert_renders_as_cancel():
    alert = _alert(status="expired")
    xml_text = cap.alert_to_cap_xml(alert, sender="s@x.example", sender_name="S", base_url="http://x")
    doc = _validate(xml_text)
    ns = {"cap": cap.CAP_NS}
    assert doc.find("cap:msgType", ns).text == "Cancel"


def test_missing_expires_falls_back_to_default_horizon():
    # Auto advisory/watch alerts never set expires_at (see alerts/service.py)
    # — the CAP document must still carry a bounded validity window.
    alert = _alert(tier="advisory", expires_at=None, message=draft_message("high_waves", "advisory", 1))
    xml_text = cap.alert_to_cap_xml(
        alert, sender="s@x.example", sender_name="S", base_url="http://x", default_expiry_hours=6.0,
    )
    doc = _validate(xml_text)
    ns = {"cap": cap.CAP_NS}
    expires = datetime.fromisoformat(doc.find("cap:info/cap:expires", ns).text)
    assert expires == NOW + timedelta(hours=6.0)


def test_explicit_expires_is_used_verbatim():
    expires_at = NOW + timedelta(hours=2)
    alert = _alert(expires_at=expires_at)
    xml_text = cap.alert_to_cap_xml(alert, sender="s@x.example", sender_name="S", base_url="http://x")
    doc = _validate(xml_text)
    ns = {"cap": cap.CAP_NS}
    expires = datetime.fromisoformat(doc.find("cap:info/cap:expires", ns).text)
    assert expires == expires_at


def test_polygon_coordinate_order_is_lat_lon_not_geojson_lon_lat():
    alert = _alert(h3_cells=[CELL])
    xml_text = cap.alert_to_cap_xml(alert, sender="s@x.example", sender_name="S", base_url="http://x")
    doc = _validate(xml_text)
    ns = {"cap": cap.CAP_NS}
    polygon_text = doc.find("cap:info/cap:area/cap:polygon", ns).text
    first_lat, first_lon = (float(v) for v in polygon_text.split()[0].split(","))
    # The pilot cell is near 13.05N, 80.28E — a swapped-order bug would put a
    # ~80 where a ~13 is expected.
    assert 5 < first_lat < 25
    assert 70 < first_lon < 90


def test_language_selects_the_translated_event_label():
    alert = _alert(hazard_type="high_waves", tier="watch", message=draft_message("high_waves", "watch", 2))
    xml_text = cap.alert_to_cap_xml(
        alert, sender="s@x.example", sender_name="S", base_url="http://x", lang="ta",
    )
    doc = _validate(xml_text)
    ns = {"cap": cap.CAP_NS}
    assert doc.find("cap:info/cap:language", ns).text == "ta-IN"
    # Tamil hazard label, not the English "High waves".
    assert doc.find("cap:info/cap:event", ns).text != "High waves"


def test_alerts_feed_lists_entries_linking_to_each_cap_document():
    alerts = [_alert(), _alert(tier="watch")]
    xml_text = cap.alerts_feed_xml(alerts, base_url="http://localhost:3000")
    doc = etree.fromstring(xml_text.encode("utf-8"))
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = doc.findall("atom:entry", ns)
    assert len(entries) == 2
    for entry, alert in zip(entries, alerts):
        link = entry.find("atom:link", ns)
        assert link.get("href") == f"http://localhost:3000/cap/alerts/{alert.id}.cap"
