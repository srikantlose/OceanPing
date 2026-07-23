from types import SimpleNamespace

from app.modules.alerts import cap_service
from app.modules.alerts.cap_service import ingest_cap_document, official_advisory_for

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


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.added: list = []
        self.executed: list = []
        self.committed = False
        self.flushed = False

    def scalars(self, stmt):
        return _Rows(self._rows)

    def add(self, obj):
        self.added.append(obj)

    def execute(self, stmt):
        self.executed.append(stmt)

    def flush(self):
        self.flushed = True

    def commit(self):
        self.committed = True


def _patch_audit(monkeypatch):
    monkeypatch.setattr(cap_service, "append_audit", lambda db, **kw: None)


# --- official_advisory_for: geometry --------------------------------------------


def _polygon_advisory(hazard_type="tsunami", expires_at=None):
    # Roughly the Chennai coastal box: 13.00-13.10N, 80.20-80.35E.
    ring = [[13.00, 80.20], [13.10, 80.20], [13.10, 80.35], [13.00, 80.35], [13.00, 80.20]]
    return SimpleNamespace(
        id="adv-1", hazard_type=hazard_type, area_polygon=ring, area_circle=None, expires_at=expires_at,
    )


def _circle_advisory(hazard_type="storm_surge", expires_at=None):
    return SimpleNamespace(
        id="adv-2", hazard_type=hazard_type, area_polygon=None,
        area_circle={"lat": 13.05, "lon": 80.28, "radius_km": 15.0}, expires_at=expires_at,
    )


def test_official_advisory_for_matches_point_inside_polygon():
    db = _Db([_polygon_advisory()])
    advisory = official_advisory_for(db, "tsunami", lat=13.05, lon=80.28)
    assert advisory is not None
    assert advisory.id == "adv-1"


def test_official_advisory_for_rejects_point_outside_polygon():
    db = _Db([_polygon_advisory()])
    advisory = official_advisory_for(db, "tsunami", lat=20.0, lon=90.0)
    assert advisory is None


def test_official_advisory_for_matches_point_within_circle_radius():
    db = _Db([_circle_advisory()])
    # ~1.1 km from the circle's center — well inside a 15 km radius.
    advisory = official_advisory_for(db, "storm_surge", lat=13.06, lon=80.28)
    assert advisory is not None
    assert advisory.id == "adv-2"


def test_official_advisory_for_rejects_point_beyond_circle_radius():
    db = _Db([_circle_advisory()])
    advisory = official_advisory_for(db, "storm_surge", lat=14.5, lon=80.28)
    assert advisory is None


def test_official_advisory_for_returns_none_with_no_candidates():
    assert official_advisory_for(_Db([]), "tsunami", lat=13.05, lon=80.28) is None


# --- ingest_cap_document ----------------------------------------------------------


def test_ingest_cap_document_stores_a_row_per_recognized_area(monkeypatch):
    _patch_audit(monkeypatch)
    db = _Db()
    created = ingest_cap_document(db, TSUNAMI_CAP)
    assert len(created) == 1
    row = created[0]
    assert row.cap_identifier == "IMD-TSUNAMI-0001"
    assert row.hazard_type == "tsunami"
    assert row.area_polygon[0] == [13.00, 80.20]
    assert db.committed


def test_ingest_cap_document_stores_circle_area(monkeypatch):
    _patch_audit(monkeypatch)
    db = _Db()
    created = ingest_cap_document(db, CIRCLE_CAP)
    assert len(created) == 1
    assert created[0].hazard_type == "storm_surge"
    assert created[0].area_circle == {"lat": 13.05, "lon": 80.28, "radius_km": 15.0}


def test_ingest_cap_document_skips_events_with_no_hazard_mapping(monkeypatch):
    _patch_audit(monkeypatch)
    unmapped = TSUNAMI_CAP.replace("Tsunami Warning", "Winter Storm Warning")
    db = _Db()
    created = ingest_cap_document(db, unmapped)
    assert created == []
    assert db.added == []
    assert db.committed


def test_ingest_cap_document_cancel_expires_referenced_rows_and_stores_nothing(monkeypatch):
    _patch_audit(monkeypatch)
    db = _Db()
    created = ingest_cap_document(db, CANCEL_CAP)
    assert created == []
    assert db.added == []
    assert len(db.executed) == 1  # the UPDATE ... expires_at = sent statement
    assert db.committed
