"""CAP 1.2 alert generation (phase 4, milestone 1) — every issued alert also
renders as a real Common Alerting Protocol document, so agency integration
(NDMA/SACHET) becomes a config/partnership step whenever it lands, not an
engineering project — the plan's own framing: "build the generator before
the partnership exists." Pure XML construction, no I/O. Validated in tests
against the actual OASIS-published schema (schemas/CAP-v1.2.xsd, fetched
verbatim from docs.oasis-open.org, not hand-transcribed).

msgType is "Cancel" for an expired alert and "Alert" otherwise — a real
simplification: CAP distinguishes a first issue ("Alert") from a follow-up
("Update") to the same identifier, and this generator always renders the
alert's *current* row fresh rather than tracking that history, so a tier
upgrade (advisory -> watch -> warning, handled in-place by
alerts/service.py) still renders as "Alert" rather than "Update". That's an
acceptable gap for a document a partner system is expected to poll and
replace wholesale, not diff.
"""
import xml.etree.ElementTree as ET
from datetime import timedelta

from app.models import Alert
from app.modules.alerts.engine import HAZARD_LABELS_BY_LANG, message_text
from app.modules.geo.h3utils import cell_centroid, cell_polygon

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"

CAP_LANG = {"en": "en-US", "ta": "ta-IN", "te": "te-IN"}

# Our three tiers map onto CAP's urgency/severity/certainty triples. "warning"
# is analyst-issued only (alerts/service.py::issue_warning) so it's the only
# tier ever rendered as Observed/Immediate/Severe — an automatic tier can
# never claim that level of certainty, mirroring the no-citizen-only-
# escalation rule this maps onto rather than duplicates.
TIER_TO_CAP = {
    "advisory": {"urgency": "Expected", "severity": "Minor", "certainty": "Possible"},
    "watch": {"urgency": "Expected", "severity": "Moderate", "certainty": "Likely"},
    "warning": {"urgency": "Immediate", "severity": "Severe", "certainty": "Observed"},
}


def _polygon_str(cell: str) -> str:
    """CAP <polygon> text is space-separated "lat,lon" vertex pairs, closed
    ring — cell_polygon() returns [lon, lat] (GeoJSON order), the opposite."""
    ring = cell_polygon(cell)
    return " ".join(f"{lat:.6f},{lon:.6f}" for lon, lat in ring)


def alert_to_cap_xml(
    alert: Alert,
    *,
    sender: str,
    sender_name: str,
    base_url: str,
    lang: str = "en",
    default_expiry_hours: float = 6.0,
) -> str:
    """Render one Alert row as a complete CAP 1.2 XML document (str, UTF-8,
    with an XML declaration). `default_expiry_hours` only fills in a CAP
    <expires> when the alert itself has none (auto advisory/watch tiers never
    set Alert.expires_at — see alerts/service.py — but CAP recipients expect
    a bounded validity window); it never writes back to the Alert row."""
    cap = TIER_TO_CAP[alert.tier]
    label = HAZARD_LABELS_BY_LANG.get(lang, HAZARD_LABELS_BY_LANG["en"]).get(
        alert.hazard_type, alert.hazard_type.replace("_", " ").title()
    )

    root = ET.Element("alert", {"xmlns": CAP_NS})
    ET.SubElement(root, "identifier").text = str(alert.id)
    ET.SubElement(root, "sender").text = sender
    ET.SubElement(root, "sent").text = alert.created_at.isoformat()
    ET.SubElement(root, "status").text = "Actual"
    ET.SubElement(root, "msgType").text = "Cancel" if alert.status == "expired" else "Alert"
    ET.SubElement(root, "scope").text = "Public"

    info = ET.SubElement(root, "info")
    ET.SubElement(info, "language").text = CAP_LANG.get(lang, lang)
    ET.SubElement(info, "category").text = "Met"
    ET.SubElement(info, "category").text = "Safety"
    ET.SubElement(info, "event").text = label
    ET.SubElement(info, "urgency").text = cap["urgency"]
    ET.SubElement(info, "severity").text = cap["severity"]
    ET.SubElement(info, "certainty").text = cap["certainty"]

    event_code = ET.SubElement(info, "eventCode")
    ET.SubElement(event_code, "valueName").text = "OceanPing"
    ET.SubElement(event_code, "value").text = alert.hazard_type

    ET.SubElement(info, "effective").text = alert.created_at.isoformat()
    expires = alert.expires_at or (alert.created_at + timedelta(hours=default_expiry_hours))
    ET.SubElement(info, "expires").text = expires.isoformat()
    ET.SubElement(info, "senderName").text = sender_name
    ET.SubElement(info, "headline").text = f"{alert.tier.title()}: {label}"
    ET.SubElement(info, "description").text = message_text(alert.message, lang, channel="push")
    ET.SubElement(info, "web").text = f"{base_url}/map"

    area = ET.SubElement(info, "area")
    cells = alert.h3_cells or []
    if cells:
        lat, lon = cell_centroid(cells[0])
        ET.SubElement(area, "areaDesc").text = f"{len(cells)} coastal cell(s) near {lat:.4f}, {lon:.4f}"
        for cell in cells:
            ET.SubElement(area, "polygon").text = _polygon_str(cell)
    else:
        ET.SubElement(area, "areaDesc").text = "Pilot coastal area"

    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}'


def alerts_feed_xml(alerts: list[Alert], *, base_url: str) -> str:
    """An Atom index of currently-active alerts, each entry linking to its own
    CAP document — the same aggregation pattern real public CAP sources use
    (e.g. NWS's alerts.weather.gov) so a partner system has one URL to poll
    for "what's new" rather than needing to already know every alert id."""
    feed = ET.Element("feed", {"xmlns": "http://www.w3.org/2005/Atom"})
    ET.SubElement(feed, "title").text = "OceanPing active alerts"
    ET.SubElement(feed, "id").text = f"{base_url}/cap/feed"
    for alert in alerts:
        entry = ET.SubElement(feed, "entry")
        ET.SubElement(entry, "id").text = str(alert.id)
        ET.SubElement(entry, "title").text = f"{alert.tier}: {alert.hazard_type}"
        ET.SubElement(entry, "updated").text = alert.created_at.isoformat()
        ET.SubElement(
            entry, "link", {"href": f"{base_url}/cap/alerts/{alert.id}.cap", "type": "application/cap+xml"}
        )
    body = ET.tostring(feed, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}'
