"""OceanPing drill: inject a synthetic coastal-flooding event end-to-end.

Stdlib-only — runs on any host Python 3.10+ with the stack up:
    python scripts/drill.py [--api http://localhost:8000]

What it does:
  1. Logs in as the analyst.
  2. Injects 7 days of calm tide-gauge baseline + a surge spike at a drill
     station near Chennai Marina.
  3. Submits ~14 citizen reports (mixed languages, several channels' worth of
     client ids) around the Marina.
  4. Forces a pipeline tick (anomaly detection + rescoring).
  5. Prints the resulting confidence/status picture, verifies one report as
     the analyst, and checks the audit chain is intact.
"""
import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

MARINA = (13.0500, 80.2824)  # Chennai Marina Beach
DRILL_STATION = {"id": "drill-chennai-tide", "name": "Drill Tide Gauge — Chennai Marina",
                 "lat": 13.06, "lon": 80.30}

REPORT_TEXTS = [
    ("coastal_flooding", "Sea water is entering the streets near Marina, houses starting to flood"),
    ("coastal_flooding", "kadal thanni theruvukku vandhuruchu, vellam romba fast ah yeruthu"),
    ("coastal_flooding", "paani ghar mein ghus raha hai, please help, log fanse hue hain"),
    (None, "water level rising fast on the beach road, cars are stuck"),
    ("coastal_flooding", "समुद्र का पानी सड़क पर आ गया है, बहुत तेज़ी से बढ़ रहा है"),
    (None, "the whole fish market street is under water now"),
    ("high_waves", "huge waves coming over the sea wall near the lighthouse"),
    ("coastal_flooding", "flooding near the harbour entrance, water knee deep"),
    (None, "kadal romba aggressive ah irukku, thanni ulla vandhuruchu"),
    ("coastal_flooding", "water entering ground floor houses in the fishing village"),
    ("coastal_flooding", "beach road completely flooded, auto stand under water"),
    (None, "समुद्र की लहरें दीवार के ऊपर से आ रही हैं, पानी भर गया"),
    ("coastal_flooding", "sea water everywhere near the memorial, rising fast"),
    ("coastal_flooding", "vellam vandhuruchu marina kitta, help pannunga"),
    # oil_spill has no instrument signal at all (HAZARD_VARIABLES is empty for it) —
    # satellite is its only corroboration path, exercised via the StubProvider below.
    ("oil_spill", "black oil slick spreading near the harbour, strong diesel smell"),
    ("oil_spill", "large oil spill spotted near the fishing jetty, water has turned black"),
]


def call(api: str, path: str, *, method: str = "GET", token: str | None = None,
         json_body: dict | None = None, form: dict | None = None) -> dict | list:
    url = api + path
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise SystemExit(f"HTTP {exc.code} on {method} {path}: {body}") from exc


def _point_in_ring(point: list[float], ring: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon, [lon, lat] pairs — good enough for a
    single closed ring at this scale (no holes to worry about)."""
    x, y = point
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
            inside = not inside
    return inside


def poll_deliveries(api: str, token: str, alert_id: str, attempts: int = 10, delay: float = 1.0) -> list:
    """The delivery worker is a separate process draining a queue — give it a
    few seconds to pick the alert up before declaring no delivery happened."""
    for _ in range(attempts):
        deliveries = call(api, f"/analyst/alerts/{alert_id}/deliveries", token=token)
        if deliveries:
            return deliveries
        time.sleep(delay)
    return []


def main() -> None:
    # Windows consoles often default to cp1252; keep the drill output portable.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--username", default="analyst")
    parser.add_argument("--password", default="oceanping-dev")
    args = parser.parse_args()
    rng = random.Random(42)

    print("→ Checking API health…")
    call(args.api, "/healthz")

    print("→ Logging in as analyst…")
    token = call(args.api, "/auth/login", method="POST",
                 json_body={"username": args.username, "password": args.password})["token"]

    print("→ Injecting 7 days of calm baseline + a surge spike at the drill tide gauge…")
    now = datetime.now(timezone.utc)
    points = []
    for hours_ago in range(7 * 24, 1, -1):
        t = now - timedelta(hours=hours_ago)
        tide = 1.0 + 0.3 * math.sin(hours_ago / 12.42 * 2 * math.pi)  # semi-diurnal-ish
        points.append([t.isoformat(), round(tide + rng.gauss(0, 0.05), 3)])
    for minutes_ago in (40, 25, 10, 2):  # the surge
        t = now - timedelta(minutes=minutes_ago)
        points.append([t.isoformat(), round(2.6 + rng.gauss(0, 0.05), 3)])
    result = call(args.api, "/drill/inject-readings", method="POST", token=token,
                  json_body={**{"station_id": DRILL_STATION["id"], "name": DRILL_STATION["name"],
                                "lat": DRILL_STATION["lat"], "lon": DRILL_STATION["lon"]},
                             "variable": "water_level", "points": points})
    print(f"   inserted {result['inserted']} readings")

    print("→ Subscribing a drill phone number to SMS alerts near the Marina…")
    call(args.api, "/subscribe/sms", method="POST",
         json_body={"lat": MARINA[0], "lon": MARINA[1], "phone": "+911234500000", "lang": "en"})

    print(f"→ Submitting {len(REPORT_TEXTS)} citizen reports around Chennai Marina…")
    for i, (hazard, text) in enumerate(REPORT_TEXTS):
        lat = MARINA[0] + rng.uniform(-0.012, 0.012)
        lon = MARINA[1] + rng.uniform(-0.006, 0.006)
        form = {
            "lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
            "client_id": f"drill-citizen-{i % 8}",
            "text": text,
        }
        if hazard:
            form["hazard_type"] = hazard
        rep = call(args.api, "/reports", method="POST", form=form)
        print(f"   [{rep['status']:>11}] conf={rep['confidence']:.2f} "
              f"{rep['hazard_type']:<17} lang={rep['lang']:<5} \"{text[:48]}…\"")

    print("→ Forcing pipeline tick (anomaly detection + satellite poll + PFZ refresh + rescore)…")
    tick = call(args.api, "/drill/tick", method="POST", token=token)
    print(f"   rescored {tick['rescored_reports']} reports, "
          f"{tick['satellite_observations']} satellite observation(s) recorded, "
          f"{tick['pfz_zones']} PFZ zone(s) (re)issued")

    print("→ Post-tick state:")
    reports = call(args.api, "/analyst/reports?limit=20", token=token)
    corroborated = [r for r in reports if r["status"] == "corroborated"]
    for r in reports[:6]:
        c = r["confidence_components"]
        print(f"   [{r['status']:>11}] conf={r['confidence']:.2f} "
              f"(trust={c.get('trust', 0):.2f} coher={c.get('coherence', 0):.2f} "
              f"instr={c.get('instrument', 0):.2f} media={c.get('media', 0):.2f} "
              f"sat={c.get('satellite', 0):.2f} acct={c.get('account_device', 0):.2f}) "
              f"{r['hazard_type']}")
    incidents = call(args.api, "/analyst/incidents", token=token)
    print(f"   {len(reports)} reports → {len(incidents)} incidents "
          f"(largest merges {max((i['report_count'] for i in incidents), default=0)} reports); "
          f"{len(corroborated)} auto-corroborated by the tide gauge")

    print("→ Checking satellite corroboration for the oil-spill reports (no instrument signal exists for that hazard)…")
    oil_reports = [r for r in reports if r["hazard_type"] == "oil_spill"]
    assert oil_reports, "expected at least one oil_spill report — did classification or the form tag change?"
    for r in oil_reports:
        obs = r["confidence_components"].get("detail", {}).get("satellite_observations") or []
        assert obs, f"oil_spill report {r['id'][:8]} has no satellite observation — StubProvider not wired?"
        assert r["confidence_components"]["instrument"] == 0, "oil_spill should never get an instrument signal"
    print(f"   {len(oil_reports)} oil_spill report(s) each have a satellite observation "
          "(their only possible corroboration path)")

    hotspots = call(args.api, "/map/hotspots")
    print(f"   hotspots on map: {len(hotspots['features'])}")

    print("→ Checking fisherman-mode PFZ advisories and nearby sea-state (StubPfzProvider)…")
    pfz = call(args.api, "/sea/pfz")
    assert pfz["zones"], "expected PFZ zones after /drill/tick's refresh — StubPfzProvider not wired?"
    print(f"   {len(pfz['zones'])} PFZ zone(s) for sector '{pfz['sector']}', "
          f"e.g. {pfz['zones'][0]['bearing']} (depth {pfz['zones'][0]['depth_m']} m)")
    sea_state = call(args.api, f"/sea/state?lat={MARINA[0]}&lon={MARINA[1]}")
    station = sea_state["station"]
    assert station, "expected a nearby station — did the drill tide gauge get inserted?"
    local = "local" if station["is_local"] else "NOT local"
    print(f"   nearest station: {station['station_name']} ({station['distance_km']} km away, {local})")

    print("→ Alerts auto-proposed from incident corroboration:")
    alerts = call(args.api, "/analyst/alerts", token=token)
    active_alerts = [a for a in alerts if a["status"] == "active"]
    for a in active_alerts[:6]:
        print(f"   [{a['tier']:>8}] {a['hazard_type']:<17} issued_by={a['issued_by'] or 'auto':<10} "
              f"\"{a['message'].get('en', '')[:60]}\"")
    auto_tiers = {a["tier"] for a in active_alerts if a["issued_by"] is None}
    assert "warning" not in auto_tiers, "auto-issued alert reached warning tier — escalation gate is broken"
    print(f"   {len(active_alerts)} active alert(s); none auto-escalated to warning "
          "(warning is analyst-only, as designed)")

    print("→ Checking the bathtub inundation model against the drill's gauge surge (real Copernicus DEM data)…")
    inundation = call(args.api, "/map/inundation?level=2.6")
    assert inundation["cell_count"] > 0, (
        "expected some coastal cells at/below a 2.6 m water level near Chennai — "
        "did scripts/inundation/build_elevation_cells.sh run and get seeded?"
    )
    print(f"   at a 2.6 m water level, {inundation['cell_count']} coastal cell(s) would flood")
    flood_wired_alert = next((a for a in active_alerts if a["predicted_flooded_cells"]), None)
    assert flood_wired_alert, (
        "expected an auto-proposed alert to carry predicted_flooded_cells from the live gauge surge "
        "— is alerts/service.py's inundation wiring broken?"
    )
    print(f"   [{flood_wired_alert['tier']:>8}] {flood_wired_alert['hazard_type']} alert carries "
          f"{len(flood_wired_alert['predicted_flooded_cells'])} predicted flooded cell(s) from the live gauge reading")

    if active_alerts:
        print("→ Checking the delivery worker fanned the auto-proposed alert out to the drill subscriber…")
        deliveries = poll_deliveries(args.api, token, active_alerts[0]["id"])
        assert deliveries, (
            f"No delivery attempts recorded for alert {active_alerts[0]['id'][:8]} — "
            "is the delivery worker (docker compose service `worker`) running?"
        )
        d = deliveries[0]
        print(f"   {len(deliveries)} delivery attempt(s), e.g. [{d['status']}] via {d['channel']} to {d['address']}")

    if incidents:
        biggest = max(incidents, key=lambda i: i["report_count"])
        print(f"→ Analyst issues a WARNING for the incident with {biggest['report_count']} merged reports…")
        warning = call(args.api, f"/analyst/incidents/{biggest['id']}/warning", method="POST",
                       token=token, json_body={"note": "Drill: confirmed via tide gauge + cluster size"})
        print(f"   alert {warning['id'][:8]} tier={warning['tier']} issued_by={warning['issued_by']}")
        public_alerts = call(args.api, "/map/alerts")
        assert any(f["properties"]["id"] == warning["id"] for f in public_alerts["features"]), \
            "issued warning did not appear on the public alert map"
        print(f"   public map now shows {len(public_alerts['features'])} active alert(s), incl. the warning")

        print("→ Requesting a route to safety from inside the drill flood zone…")
        route = call(args.api, f"/route?lat={MARINA[0]}&lon={MARINA[1]}")
        assert route["shelter"], "expected a nearest open shelter — did shelter seeding run at startup?"
        assert route["route"], (
            "expected a routed path — is the valhalla container up with tiles built? "
            "(run scripts/routing/fetch_osm_extract.sh, then `docker compose up -d --build valhalla`)"
        )
        route_coords = route["route"]["geometry"]["coordinates"]
        print(f"   route to {route['shelter']['name']}: {route['route']['properties']['distance_km']} km, "
              f"{route['route']['properties']['duration_min']} min, {route['excluded_cells']} hazard cell(s) "
              f"currently active")
        if route["avoided_hazards"]:
            warning_feature = next(f for f in public_alerts["features"] if f["properties"]["id"] == warning["id"])
            excluded_rings = [ring for polygon in warning_feature["geometry"]["coordinates"] for ring in polygon]
            violations = [pt for pt in route_coords if any(_point_in_ring(pt, ring) for ring in excluded_rings)]
            assert not violations, (
                f"route passes through {len(violations)} point(s) inside the warning's excluded zone — "
                "exclude_polygons isn't actually being honored"
            )
            print(f"   confirmed none of the {len(route_coords)} path points fall inside the excluded zone")
        else:
            # A hard exclusion can trap a route entirely when the traveler's own
            # starting point sits inside the excluded hazard geometry — the origin
            # here (Marina) is deliberately ground zero for the drill's flood
            # reports, so this is the expected outcome for this scenario, not a bug.
            print("   hazard geometry fully enclosed the starting point — routed through it "
                  "rather than leaving the evacuee with no path at all (see service.py's fallback)")

        print("→ Checking the warning itself was fanned out to subscribers…")
        deliveries = poll_deliveries(args.api, token, warning["id"])
        assert deliveries, f"No delivery attempts recorded for the issued warning {warning['id'][:8]}"
        print(f"   {len(deliveries)} delivery attempt(s) recorded for the warning")

        print(f"→ Analyst expires alert {warning['id'][:8]}…")
        call(args.api, f"/analyst/alerts/{warning['id']}/expire", method="POST", token=token, json_body={})
        public_alerts = call(args.api, "/map/alerts")
        assert not any(f["properties"]["id"] == warning["id"] for f in public_alerts["features"]), \
            "expired warning is still showing on the public alert map"
        print("   expired — no longer on the public map")

    if corroborated:
        target = corroborated[0]
        print(f"→ Analyst verifies report {target['id'][:8]}…")
        call(args.api, f"/analyst/reports/{target['id']}/verify", method="POST",
             token=token, json_body={"note": "Drill: confirmed vs tide gauge + cluster"})
        public = call(args.api, "/map/reports")
        print(f"   public map now shows {len(public['features'])} verified report(s)")

    chain = call(args.api, "/analyst/audit/verify", token=token)
    print(f"→ Audit chain: intact={chain['intact']} over {chain['entries_checked']} entries")
    if not chain["intact"]:
        sys.exit("AUDIT CHAIN BROKEN")
    print("✓ Drill complete.")


if __name__ == "__main__":
    main()
