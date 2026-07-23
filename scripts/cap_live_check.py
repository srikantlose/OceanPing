"""Live end-to-end check for CAP interop (phase 4, milestone 1).

Exercises the parts that only a real running stack can prove: an alert
issued through the real API renders as CAP through the real endpoint (not
just a unit test against a hand-built SimpleNamespace); a real CAP document
posted to the ingestion webhook lands in the real Postgres official_
advisories table with its geometry round-tripped through JSONB; a citizen
report's confidence_components picks up that advisory through the real
scoring path (real SQL query, real shapely point-in-polygon test — the fakes
in test_cap_service.py bypass both); a point outside the advisory's polygon
does not; and cancelling the advisory really stops it from corroborating a
later report, proving the CAP <references>-expiry path is load-bearing
against the real database, not just stored and ignored.

Not part of the unit suite (needs the stack up):
    python scripts/cap_live_check.py [--api http://localhost:8000]
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    if not ok:
        failures += 1


def api(base, path, *, method="GET", token=None, json_body=None, form_body=None, raw_body=None,
        headers=None, expect_status=None):
    hdrs = dict(headers or {})
    data = None
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif form_body is not None:
        data = urllib.parse.urlencode(form_body).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif raw_body is not None:
        data = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode()
    if expect_status is not None and status != expect_status:
        raise RuntimeError(f"{method} {path} -> HTTP {status} (expected {expect_status}): {body[:300]}")
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"→ Live CAP interop check against {args.api}")
    _, login = api(args.api, "/auth/login", method="POST",
                    json_body={"username": "analyst", "password": "oceanping-dev"}, expect_status=200)
    token = login["token"]

    run_id = int(time.time())

    # --- 1. Outbound: a real issued alert renders as real, schema-shaped CAP ---
    client_id = f"cap-live-{run_id}"
    _, report = api(args.api, "/reports", method="POST", form_body={
        "lat": "11.500000", "lon": "79.900000", "hazard_type": "tsunami",
        "client_id": client_id, "text": "CAP live check: tsunami signs reported directly offshore.",
    }, expect_status=200)
    _, reports_list = api(args.api, "/analyst/reports?limit=5", token=token, expect_status=200)
    submitted = next((r for r in reports_list if r["id"] == report["id"]), None)
    check("test report submitted and visible to analysts", submitted is not None)
    incident_id = submitted["incident_id"] if submitted else None
    check("report auto-assigned to an incident (inline pipeline)", incident_id is not None)

    _, alert = api(args.api, f"/analyst/incidents/{incident_id}/warning", method="POST",
                    json_body={"note": "CAP live check warning."}, token=token, expect_status=200)
    alert_id = alert["id"]

    status, cap_xml = api(args.api, f"/cap/alerts/{alert_id}.cap", expect_status=200)
    check("CAP document fetch returns 200", status == 200)
    check("CAP document carries this alert's identifier", f"<identifier>{alert_id}</identifier>" in cap_xml)
    check("warning tier renders Immediate/Severe/Observed",
          "<urgency>Immediate</urgency>" in cap_xml and "<severity>Severe</severity>" in cap_xml
          and "<certainty>Observed</certainty>" in cap_xml)
    check("area carries at least one real polygon", "<polygon>" in cap_xml)

    _, feed_xml = api(args.api, "/cap/feed", expect_status=200)
    check("active-alerts feed links to this alert's CAP document", f"/cap/alerts/{alert_id}.cap" in feed_xml)

    api(args.api, f"/analyst/alerts/{alert_id}/expire", method="POST", token=token, expect_status=200)
    _, cancelled_xml = api(args.api, f"/cap/alerts/{alert_id}.cap", expect_status=200)
    check("expiring the alert flips the CAP document to a Cancel message", "<msgType>Cancel</msgType>" in cancelled_xml)

    # --- 2. Inbound: a real CAP document lands in the real DB, geo-scoped ---
    identifier = f"LIVE-CHECK-{run_id}"
    now = datetime.now(timezone.utc)
    # A polygon around a pilot point well away from the outbound alert above
    # (so the two parts of this script can't interfere with each other's
    # coherence/rate-limit counters), nudged by a run-specific offset so a
    # rerun's box doesn't overlap a previous run's still-on-disk advisory —
    # this script's own state persists in the real Postgres between runs,
    # unlike the unit tests' fakes.
    offset = (run_id % 1000) / 1000.0 * 3.0
    inside_lat, inside_lon = 12.050 + offset, 79.850 + offset
    outside_lat, outside_lon = 15.500 + offset, 82.000 + offset
    cap_doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>{identifier}</identifier>
  <sender>alerts@imd.gov.in</sender>
  <sent>{now.isoformat()}</sent>
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
    <effective>{now.isoformat()}</effective>
    <expires>{(now + timedelta(hours=6)).isoformat()}</expires>
    <senderName>India Meteorological Department (live check)</senderName>
    <headline>Tsunami Warning — CAP live check</headline>
    <description>Live check advisory.</description>
    <area>
      <areaDesc>CAP live-check coastal box</areaDesc>
      <polygon>{12.00 + offset:.3f},{79.80 + offset:.3f} {12.10 + offset:.3f},{79.80 + offset:.3f} {12.10 + offset:.3f},{79.90 + offset:.3f} {12.00 + offset:.3f},{79.90 + offset:.3f} {12.00 + offset:.3f},{79.80 + offset:.3f}</polygon>
    </area>
  </info>
</alert>
"""
    status, ingest_result = api(args.api, "/webhooks/cap", method="POST", raw_body=cap_doc,
                                 headers={"Content-Type": "application/cap+xml"}, expect_status=200)
    check("inbound CAP document ingested", ingest_result.get("advisories_created") == 1,
          str(ingest_result))

    _, advisories = api(args.api, "/analyst/official-advisories?limit=10", token=token, expect_status=200)
    stored = next((a for a in advisories if a["cap_identifier"] == identifier), None)
    check("ingested advisory really landed in Postgres", stored is not None)
    if stored:
        check("hazard mapped from the CAP <event> text", stored["hazard_type"] == "tsunami", stored["hazard_type"])
        check("certainty carried through from the document", stored["certainty"] == "Observed")

    # --- 3. The real scoring path picks it up, geo-scoped -----------------
    def submit_and_score(lat, lon, suffix):
        cid = f"cap-live-geo-{run_id}-{suffix}"
        _, r = api(args.api, "/reports", method="POST", form_body={
            "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "hazard_type": "tsunami",
            "client_id": cid, "text": "CAP live check: water receding fast from the shore.",
        }, expect_status=200)
        api(args.api, "/drill/tick", method="POST", token=token, expect_status=200)
        _, reports = api(args.api, "/analyst/reports?limit=10", token=token, expect_status=200)
        return next((x for x in reports if x["id"] == r["id"]), None)

    inside_report = submit_and_score(inside_lat, inside_lon, "inside")
    outside_report = submit_and_score(outside_lat, outside_lon, "outside")

    inside_official = (inside_report or {}).get("confidence_components", {}).get("detail", {}).get("official_advisory")
    outside_official = (outside_report or {}).get("confidence_components", {}).get("detail", {}).get("official_advisory")

    check("a report inside the advisory's polygon picks up the corroboration signal",
          inside_official is not None and inside_official.get("event") == "Tsunami Warning",
          str(inside_official))
    check("a report outside the advisory's polygon does not (real geo-scoping, not a global flag)",
          outside_official is None, str(outside_official))

    # --- 4. Cancelling the advisory really stops it from corroborating ----
    cancel_sent = datetime.now(timezone.utc)
    cancel_doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>{identifier}-CANCEL</identifier>
  <sender>alerts@imd.gov.in</sender>
  <sent>{cancel_sent.isoformat()}</sent>
  <status>Actual</status>
  <msgType>Cancel</msgType>
  <references>alerts@imd.gov.in,{identifier},{now.isoformat()}</references>
  <scope>Public</scope>
</alert>
"""
    api(args.api, "/webhooks/cap", method="POST", raw_body=cancel_doc,
        headers={"Content-Type": "application/cap+xml"}, expect_status=200)

    after_cancel_report = submit_and_score(inside_lat, inside_lon, "after-cancel")
    after_cancel_official = (after_cancel_report or {}).get("confidence_components", {}).get("detail", {}).get(
        "official_advisory"
    )
    check("a cancelled advisory no longer corroborates a new report at the same spot",
          after_cancel_official is None, str(after_cancel_official))

    print(failures == 0 and "\n✓ Live CAP interop check passed." or f"\n✗ {failures} check(s) failed.")
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
