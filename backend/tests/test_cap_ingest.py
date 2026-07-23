import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.modules.alerts import cap
from app.modules.alerts.cap_ingest import map_event_to_hazard, parse_cap
from app.modules.alerts.engine import draft_message
from app.modules.geo.h3utils import cell_for

TSUNAMI_CAP = """<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>IMD-TSUNAMI-0001</identifier>
  <sender>alerts@imd.gov.in</sender>
  <sent>2026-07-23T06:00:00+05:30</sent>
  <status>Actual</status>
  <msgType>Alert</msgType>
  <scope>Public</scope>
  <info>
    <language>en-US</language>
    <category>Met</category>
    <event>Tsunami Warning</event>
    <urgency>Immediate</urgency>
    <severity>Severe</severity>
    <certainty>Observed</certainty>
    <effective>2026-07-23T06:00:00+05:30</effective>
    <expires>2026-07-23T12:00:00+05:30</expires>
    <senderName>India Meteorological Department</senderName>
    <headline>Tsunami Warning for Tamil Nadu coast</headline>
    <description>A tsunami warning is in effect for the Tamil Nadu coastline.</description>
    <area>
      <areaDesc>Chennai coastal strip</areaDesc>
      <polygon>13.00,80.20 13.10,80.20 13.10,80.35 13.00,80.35 13.00,80.20</polygon>
    </area>
  </info>
</alert>
"""

CIRCLE_CAP = """<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>IMD-SURGE-0002</identifier>
  <sender>alerts@imd.gov.in</sender>
  <sent>2026-07-23T06:00:00Z</sent>
  <status>Actual</status>
  <msgType>Alert</msgType>
  <scope>Public</scope>
  <info>
    <language>en-US</language>
    <category>Met</category>
    <event>Storm Surge Warning</event>
    <urgency>Expected</urgency>
    <severity>Moderate</severity>
    <certainty>Likely</certainty>
    <effective>2026-07-23T06:00:00Z</effective>
    <area>
      <areaDesc>Near the pilot gauge</areaDesc>
      <circle>13.05,80.28 15</circle>
    </area>
  </info>
</alert>
"""

CANCEL_CAP = """<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>IMD-TSUNAMI-0001-CANCEL</identifier>
  <sender>alerts@imd.gov.in</sender>
  <sent>2026-07-23T09:00:00Z</sent>
  <status>Actual</status>
  <msgType>Cancel</msgType>
  <references>alerts@imd.gov.in,IMD-TSUNAMI-0001,2026-07-23T06:00:00+05:30</references>
  <scope>Public</scope>
</alert>
"""


def test_map_event_to_hazard_matches_known_keywords_case_insensitively():
    assert map_event_to_hazard("Tsunami Warning") == "tsunami"
    assert map_event_to_hazard("STORM SURGE ADVISORY") == "storm_surge"
    assert map_event_to_hazard("High Surf Advisory") == "high_waves"
    assert map_event_to_hazard("Rip Current Statement") == "rip_current"
    assert map_event_to_hazard("Coastal Flood Warning") == "coastal_flooding"
    assert map_event_to_hazard("Red Tide Bulletin") == "algal_bloom"


def test_map_event_to_hazard_returns_none_for_unrecognized_event():
    assert map_event_to_hazard("Winter Storm Warning") is None
    assert map_event_to_hazard("") is None


def test_parse_cap_extracts_envelope_fields():
    parsed = parse_cap(TSUNAMI_CAP)
    assert parsed.identifier == "IMD-TSUNAMI-0001"
    assert parsed.sender == "alerts@imd.gov.in"
    assert parsed.status == "Actual"
    assert parsed.msg_type == "Alert"
    assert parsed.references == []
    assert len(parsed.infos) == 1
    assert parsed.infos[0].event == "Tsunami Warning"
    assert parsed.infos[0].urgency == "Immediate"


def test_parse_cap_polygon_area():
    parsed = parse_cap(TSUNAMI_CAP)
    area = parsed.infos[0].areas[0]
    assert area.circle is None
    assert area.polygon[0] == (13.00, 80.20)
    assert area.polygon[2] == (13.10, 80.35)
    assert len(area.polygon) == 5


def test_parse_cap_circle_area():
    parsed = parse_cap(CIRCLE_CAP)
    area = parsed.infos[0].areas[0]
    assert area.polygon is None
    assert area.circle == (13.05, 80.28, 15.0)


def test_parse_cap_references_extracts_identifier_only():
    parsed = parse_cap(CANCEL_CAP)
    assert parsed.msg_type == "Cancel"
    assert parsed.references == ["IMD-TSUNAMI-0001"]


def test_parse_cap_defaults_naive_datetime_to_utc():
    naive_doc = TSUNAMI_CAP.replace("2026-07-23T06:00:00+05:30", "2026-07-23T06:00:00").replace(
        "2026-07-23T12:00:00+05:30", "2026-07-23T12:00:00"
    )
    parsed = parse_cap(naive_doc)
    assert parsed.sent.tzinfo is not None
    assert parsed.infos[0].effective.tzinfo == timezone.utc


def test_round_trips_our_own_generator_output():
    """The outbound generator and inbound parser must agree on the wire
    format — the strongest possible check that neither side quietly drifted
    from the other (there's no real NDMA/SACHET feed to test against yet)."""
    alert_id = uuid.uuid4()
    cell = cell_for(13.0512, 80.2831)
    alert = SimpleNamespace(
        id=alert_id,
        hazard_type="tsunami",
        tier="warning",
        h3_cells=[cell],
        message=draft_message("tsunami", "warning", 4),
        status="active",
        created_at=datetime(2026, 7, 23, 6, 0, tzinfo=timezone.utc),
        expires_at=None,
    )
    xml_text = cap.alert_to_cap_xml(
        alert, sender="pilot@oceanping.example", sender_name="OceanPing Pilot", base_url="http://localhost:3000",
    )

    parsed = parse_cap(xml_text)

    assert parsed.identifier == str(alert_id)
    assert parsed.sender == "pilot@oceanping.example"
    assert parsed.msg_type == "Alert"
    info = parsed.infos[0]
    assert info.urgency == "Immediate"
    assert info.severity == "Severe"
    assert info.certainty == "Observed"
    assert map_event_to_hazard(info.event) == "tsunami"
    assert len(info.areas) == 1
    assert info.areas[0].polygon is not None
    assert len(info.areas[0].polygon) >= 4
