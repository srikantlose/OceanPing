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
  6. Generates and files an auto-SITREP, checking its counts against an
     independently recomputed tally from /analyst/reports.
  7. Injects a directional report sequence near Kasimedu to exercise hazard-
     front propagation forecasting, and backtests a sensor forecast against
     the drill gauge's own calm baseline so the full generate-then-validate
     loop can be checked immediately instead of waiting on real time.
  8. Submits a near-duplicate algal_bloom report cluster near Ennore, checks
     the rumor tracker leaves it unflagged until an analyst rejects one
     member report, then approves the resulting correction draft and
     confirms it was delivered to a subscriber near the rumor's location.
  9. Exercises the mobile offline-sync contract: a backdated report keeps its
     observation time, a retried submission doesn't duplicate, and a Mark
     Safe check-in reaches responders without ever becoming a hazard report.
 10. Recovery module: submits a damage-assessment photo (a hand-built PNG —
     stdlib only, no Pillow on the client side) and checks the CV triage
     reads it as flooding; posts a matching relief request + aid offer and
     confirms the mutual-aid board pairs them; submits a missing-person and a
     near-duplicate-name found-person report and confirms the fuzzy matcher
     surfaces the pair for an analyst to resolve; confirms the registry
     rejects an unauthenticated read.
"""
import argparse
import binascii
import json
import math
import os
import random
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone

MARINA = (13.0500, 80.2824)  # Chennai Marina Beach
DRILL_STATION = {"id": "drill-chennai-tide", "name": "Drill Tide Gauge — Chennai Marina",
                 "lat": 13.06, "lon": 80.30}

# Rumor tracker drill (phase 3, milestone 4): a cluster of near-duplicate
# citizen reports near Ennore, all tagged algal_bloom — a hazard with no
# instrument signal at all (HAZARD_VARIABLES["algal_bloom"] is empty), so its
# only possible contradiction path is an analyst having already rejected a
# member report. Kept well clear of the Marina/Kasimedu clusters above.
RUMOR_LOCATION = (13.2146, 80.3223)  # Ennore
RUMOR_TEXTS = [
    "huge algal bloom killing all the fish near Ennore, entire coast turning green and toxic",
    "massive algal bloom near ennore killing fish everywhere, water has turned green and toxic",
    "algal bloom disaster at ennore, fish dying everywhere, water is green and has a toxic smell",
    "big algal bloom near ennore coast, dead fish everywhere, water turned toxic green",
]

# Hazard-front propagation drill (phase 3, milestone 3): a time-ordered report
# sequence walking due north from south of Kasimedu fishing harbour, well
# clear of the Marina cluster above so the two never merge into one incident.
FRONT_ORIGIN = (13.100, 80.298)
FRONT_REPORT_COUNT = 6
FRONT_STEP_MINUTES = 20
FRONT_LAT_STEP = 0.0035  # ~0.39 km/step — comfortably within the H3 1-ring merge radius
FRONT_TEXT = "sea water rising along the coast road, moving up from the harbour"

# Backtests a sensor forecast far enough in the past that its full horizon has
# already elapsed in real time by the time /drill/tick runs validate_forecasts —
# lets the drill exercise the whole generate-then-validate loop immediately.
BACKTEST_HOURS_AGO = 3.2

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


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _solid_color_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A minimal hand-built solid-color PNG — stdlib only (struct + zlib), no
    Pillow on the drill/client side, mirroring how a real phone photo arrives
    as opaque bytes over the wire. 8-bit RGB, one uncompressed filter-0 row
    per scanline."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = bytes([0]) + bytes(rgb) * width
    idat = zlib.compress(row * height)
    return sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def post_multipart(api: str, path: str, *, token: str | None = None,
                    fields: dict | None = None, files: dict | None = None) -> dict:
    """Minimal multipart/form-data POST — stdlib only. `files` maps field
    name -> (filename, content_bytes, content_type)."""
    boundary = binascii.hexlify(os.urandom(16)).decode()
    parts = []
    for name, value in (fields or {}).items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, (filename, content, content_type) in (files or {}).items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f'Content-Type: {content_type}\r\n\r\n'.encode() + content + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(api + path, data=b"".join(parts), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise SystemExit(f"HTTP {exc.code} on POST {path}: {body}") from exc


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
    marina_report_ids = []
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
        marina_report_ids.append(rep["id"])
        print(f"   [{rep['status']:>11}] conf={rep['confidence']:.2f} "
              f"{rep['hazard_type']:<17} lang={rep['lang']:<5} \"{text[:48]}…\"")

    print("→ Injecting a directional report sequence near Kasimedu (hazard-front propagation drill)…")
    front_reports = [
        {
            "lat": FRONT_ORIGIN[0] + FRONT_LAT_STEP * i,
            "lon": FRONT_ORIGIN[1],
            "hazard_type": "coastal_flooding",
            "client_id": f"drill-front-{i}",
            "text": FRONT_TEXT,
            "created_at": (now - timedelta(minutes=FRONT_STEP_MINUTES * (FRONT_REPORT_COUNT - 1 - i))).isoformat(),
        }
        for i in range(FRONT_REPORT_COUNT)
    ]
    call(args.api, "/drill/inject-reports", method="POST", token=token, json_body={"reports": front_reports})
    print(f"   injected {len(front_reports)} time-ordered reports moving north from {FRONT_ORIGIN}")

    print("→ Backtesting a sensor forecast against the drill gauge's own calm baseline "
          "(so it validates immediately instead of waiting on real time)…")
    backtest = call(args.api, "/drill/backtest-forecast", method="POST", token=token,
                     json_body={"station_id": DRILL_STATION["id"], "variable": "water_level",
                                "hours_ago": BACKTEST_HOURS_AGO})
    print(f"   forecast {backtest['id'][:8]} fit as of {BACKTEST_HOURS_AGO}h ago, "
          f"{len(backtest['content']['points'])} point(s) projected")

    print("→ Forcing pipeline tick (anomaly detection + satellite poll + PFZ refresh + rescore + forecasts)…")
    tick = call(args.api, "/drill/tick", method="POST", token=token)
    print(f"   rescored {tick['rescored_reports']} reports, "
          f"{tick['satellite_observations']} satellite observation(s) recorded, "
          f"{tick['pfz_zones']} PFZ zone(s) (re)issued, "
          f"{tick['sensor_forecasts']} sensor forecast(s), {tick['propagation_forecasts']} propagation forecast(s), "
          f"{tick['validated_forecasts']} forecast(s) validated, {tick['narratives_flagged']} narrative(s) flagged")

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
        en = a["message"].get("en", "")
        text = en.get("standard", "") if isinstance(en, dict) else en
        print(f"   [{a['tier']:>8}] {a['hazard_type']:<17} issued_by={a['issued_by'] or 'auto':<10} "
              f"\"{text[:60]}\"")
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

    print("→ Checking the hazard-front propagation forecast for the directional report sequence…")
    all_incidents = call(args.api, "/analyst/incidents", token=token)
    # >= not == : rerunning the drill against an already-populated dev DB adds
    # another FRONT_REPORT_COUNT reports to the same incident (same location,
    # same text), rather than starting a fresh one each time.
    front_candidates = [
        i for i in all_incidents
        if i["hazard_type"] == "coastal_flooding" and i["report_count"] >= FRONT_REPORT_COUNT
        and FRONT_ORIGIN[0] - 0.01 <= i["centroid"][0] <= FRONT_ORIGIN[0] + FRONT_LAT_STEP * FRONT_REPORT_COUNT + 0.01
    ]
    assert len(front_candidates) == 1, (
        f"expected exactly one incident merging the directional reports near Kasimedu, "
        f"found {len(front_candidates)} — did the reports land too far apart to pass the H3 1-ring merge check, "
        "or merge into an unrelated incident?"
    )
    front_incident = front_candidates[0]
    propagation_forecasts = call(args.api, "/analyst/forecasts?kind=propagation&limit=50", token=token)
    front_forecast = next((f for f in propagation_forecasts if f["subject_id"] == front_incident["id"]), None)
    assert front_forecast, (
        f"expected a propagation forecast for incident {front_incident['id'][:8]} — "
        "did /drill/tick's generate_propagation_forecasts run, or was the fitted front's speed below the noise floor?"
    )
    front = front_forecast["content"]["front"]
    assert front["speed_kmh"] > 0, "fitted front has zero speed — fit_front should reject a stationary sequence, not return one"
    assert -45 <= front["bearing_deg"] <= 45 or front["bearing_deg"] >= 315, (
        f"expected a roughly northward bearing for a due-north-moving report sequence, got {front['bearing_deg']}°"
    )
    origin_cells = set(front_incident["h3_cells"])
    projected_cells = {c for cells in front_forecast["content"]["projected"].values() for c in cells}
    assert projected_cells, "expected at least one cell projected ahead of the front"
    assert projected_cells.isdisjoint(origin_cells), (
        "projected cells should lie ahead of the front, not among the already-reported cells"
    )
    print(f"   incident {front_incident['id'][:8]} ({FRONT_REPORT_COUNT} merged reports) → front speed "
          f"{front['speed_kmh']} km/h, bearing {front['bearing_deg']}°, {len(projected_cells)} cell(s) "
          f"projected ahead of the {len(origin_cells)} reported cell(s) — an unreported village cell, pre-alerted")

    print("→ Checking the backtested sensor forecast validated against its own real baseline…")
    # Scoped to the drill gauge: the backtested forecast is deliberately
    # stamped 3.2h in the past, so on an accumulated dev DB it sorts below a
    # newest-first page of every station's forecasts.
    sensor_forecasts = call(
        args.api,
        f"/analyst/forecasts?kind=sensor&subject_id={DRILL_STATION['id']}&limit=200",
        token=token,
    )
    validated = next((f for f in sensor_forecasts if f["id"] == backtest["id"]), None)
    assert validated, "backtested forecast disappeared — did /drill/tick's validate_forecasts run?"
    assert validated["validated_at"], (
        f"expected the backtested forecast's full horizon (fit as of {BACKTEST_HOURS_AGO}h ago) to have already "
        "elapsed and been scored by now"
    )
    assert validated["validation"]["scored_points"] > 0, (
        "backtested forecast was validated but scored zero points against real readings"
    )
    print(f"   forecast {validated['id'][:8]}: {validated['validation']['scored_points']} point(s) scored, "
          f"mean abs error {validated['validation']['mean_abs_error']} m vs. the drill gauge's real baseline")

    accuracy = call(args.api, "/forecasts/accuracy")
    assert accuracy["sensor"], "expected the public accuracy endpoint to show at least one scored sensor forecast"
    print(f"   /forecasts/accuracy (public, per pilot location): {accuracy['sensor'][0]}")

    print("→ Rumor tracker: checking the gauge-corroborated Marina flood cluster is NOT flagged as a rumor…")
    call(args.api, "/analyst/narratives/detect", method="POST", token=token)
    narratives = call(args.api, "/analyst/narratives?limit=200", token=token)
    marina_match = next((n for n in narratives if set(marina_report_ids) & set(n["report_ids"])), None)
    assert marina_match is None, (
        f"the Marina flood reports were flagged as a rumor (narrative {marina_match['id'][:8] if marina_match else ''}) "
        "even though the drill tide gauge is actively corroborating them — is_contradiction should be False "
        "whenever a live instrument anomaly backs the claim. (A coastal_flooding report rejected in an earlier "
        "session would also explain this.)"
    )
    print(f"   confirmed: {len(marina_report_ids)} repeating flood reports, none flagged — a live instrument "
          "anomaly backs the claim, so volume alone never makes it a rumor")

    print("→ Rumor tracker: submitting a near-duplicate algal_bloom report cluster near Ennore "
          "(no instrument signal exists for this hazard, so only an analyst rejection can flag it)…")
    call(args.api, "/subscribe/sms", method="POST",
         json_body={"lat": RUMOR_LOCATION[0], "lon": RUMOR_LOCATION[1], "phone": "+911234500001", "lang": "en"})
    rumor_report_ids = []
    for i, text in enumerate(RUMOR_TEXTS):
        lat = RUMOR_LOCATION[0] + rng.uniform(-0.003, 0.003)
        lon = RUMOR_LOCATION[1] + rng.uniform(-0.003, 0.003)
        rep = call(args.api, "/reports", method="POST", form={
            "lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
            "client_id": f"drill-rumor-{i}",
            "hazard_type": "algal_bloom",
            "text": text,
        })
        rumor_report_ids.append(rep["id"])
    print(f"   submitted {len(rumor_report_ids)} near-duplicate algal_bloom reports near Ennore")

    print(f"→ Analyst rejects one of the algal_bloom reports (report {rumor_report_ids[0][:8]})…")
    call(args.api, f"/analyst/reports/{rumor_report_ids[0]}/reject", method="POST",
         token=token, json_body={"note": "Drill: no fish kill visible in follow-up, likely a false report"})

    print("→ Checking the rumor tracker now flags the cluster (an analyst has contradicted a member report)…")
    call(args.api, "/analyst/narratives/detect", method="POST", token=token)
    narratives = call(args.api, "/analyst/narratives?limit=200", token=token)
    narrative = next((n for n in narratives if set(rumor_report_ids) & set(n["report_ids"])), None)
    assert narrative, (
        "expected the algal_bloom cluster to be flagged as a narrative after a member report was rejected — "
        "did detect_narratives' rejected-report contradiction path break?"
    )
    assert narrative["hazard_type"] == "algal_bloom"
    assert narrative["rejected_report_count"] >= 1
    assert narrative["status"] == "draft"
    assert "Ennore" in narrative["message"]["en"]["standard"], (
        "correction draft should name the nearest pilot location (Ennore) — did nearest_pilot_location wiring break?"
    )
    print(f"   narrative {narrative['id'][:8]}: {narrative['report_count']} report(s), "
          f"{narrative['rejected_report_count']} rejected, draft_method={narrative['draft_method']}")
    print(f"   draft correction: \"{narrative['message']['en']['standard'][:100]}\"")

    print(f"→ Analyst approves the correction for narrative {narrative['id'][:8]}…")
    approved = call(args.api, f"/analyst/narratives/{narrative['id']}/approve", method="POST", token=token)
    assert approved["status"] == "approved"
    print(f"   approved — {approved['delivered_count']} delivery attempt(s) fanned out")

    print("→ Checking the correction actually reached the Ennore subscriber…")
    # poll_deliveries() is hardcoded to the alerts path; narrative deliveries
    # live at a different endpoint, so poll that one directly instead.
    narrative_deliveries = []
    for _ in range(10):
        narrative_deliveries = call(args.api, f"/analyst/narratives/{narrative['id']}/deliveries", token=token)
        if narrative_deliveries:
            break
        time.sleep(1.0)
    assert narrative_deliveries, (
        f"No delivery attempts recorded for narrative {narrative['id'][:8]} — "
        "is deliver_narrative_correction wired correctly, or did the Ennore subscription not overlap the narrative's cells?"
    )
    d = narrative_deliveries[0]
    print(f"   {len(narrative_deliveries)} delivery attempt(s), e.g. [{d['status']}] via {d['channel']} to {d['address']}")

    # Only alerts this run actually created or upgraded get re-enqueued for
    # delivery — sync_incident_alert() deliberately returns early for an
    # incident already sitting at its final automatic tier. On a rerun against
    # an accumulated dev DB most active alerts are in that state, so asserting
    # against whatever alert happens to be newest would be testing a previous
    # run's leftovers rather than this one's wiring.
    fresh_alerts = [a for a in active_alerts if datetime.fromisoformat(a["created_at"]) >= now]
    if fresh_alerts:
        print("→ Checking the delivery worker fanned the auto-proposed alert out to the drill subscriber…")
        deliveries = poll_deliveries(args.api, token, fresh_alerts[0]["id"])
        assert deliveries, (
            f"No delivery attempts recorded for alert {fresh_alerts[0]['id'][:8]} — "
            "is the delivery worker (docker compose service `worker`) running?"
        )
        d = deliveries[0]
        print(f"   {len(deliveries)} delivery attempt(s), e.g. [{d['status']}] via {d['channel']} to {d['address']}")
    else:
        print("→ No new auto-alert was proposed this run (every incident already sits at its final "
              "automatic tier) — the analyst-issued warning below still exercises delivery end to end.")

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

    print("→ Generating an auto-SITREP and checking it reports only verified DB counts…")
    sitrep = call(args.api, "/analyst/sitreps/generate", method="POST", token=token)
    recon_reports = call(args.api, "/analyst/reports?limit=500", token=token)
    period_start = datetime.fromisoformat(sitrep["period_start"])
    period_end = datetime.fromisoformat(sitrep["period_end"])
    in_window = [r for r in recon_reports if period_start <= datetime.fromisoformat(r["created_at"]) < period_end]
    reported_total = sitrep["content"]["sections"]["reports"]["total"]
    assert reported_total == len(in_window), (
        f"SITREP claims {reported_total} report(s) for its period but independently recomputing from "
        f"/analyst/reports gives {len(in_window)} — did build_snapshot's query drift from what it reports?"
    )
    print(f"   SITREP {sitrep['id'][:8]} ({sitrep['period_start']} → {sitrep['period_end']}): "
          f"{reported_total} report(s), matches an independent recount from /analyst/reports")
    print(f"   \"{sitrep['content']['summary']}\"")

    print(f"→ Analyst files SITREP {sitrep['id'][:8]}…")
    filed = call(args.api, f"/analyst/sitreps/{sitrep['id']}/file", method="POST", token=token, json_body={})
    assert filed["status"] == "filed", "expected the SITREP to move to filed status"
    assert filed["filed_by"] == args.username
    print(f"   filed by {filed['filed_by']}")

    print("→ Mobile offline sync: submitting a report backdated 3h, as a phone that was out of coverage would…")
    observed_at = (now - timedelta(hours=3)).isoformat()
    offline_key = f"drill-offline-{int(time.time())}"
    offline_form = {
        "lat": f"{MARINA[0] + 0.004:.6f}", "lon": f"{MARINA[1] + 0.002:.6f}",
        "client_id": "drill-mobile-1", "hazard_type": "high_waves",
        "text": "waves coming over the wall near the lighthouse, no signal until now",
        "observed_at": observed_at, "client_key": offline_key,
    }
    queued = call(args.api, "/reports", method="POST", form=offline_form)
    skew = abs((datetime.fromisoformat(queued["created_at"]) - datetime.fromisoformat(observed_at)).total_seconds())
    assert skew < 60, (
        f"a queued report must keep the time it was observed, not the time it synced — "
        f"created_at is {skew:.0f}s away from the submitted observed_at"
    )
    print(f"   report {queued['id'][:8]} stored at its observed time ({skew:.0f}s skew), not at sync time")

    print("→ Mobile offline sync: replaying the same submission, as a phone whose reply was lost would…")
    replayed = call(args.api, "/reports", method="POST", form=offline_form)
    assert replayed["id"] == queued["id"], (
        "a retried submission created a second report — the client_key idempotency check isn't working, "
        "and duplicate reports would inflate the coherence signal"
    )
    print(f"   replay resolved to the same report {replayed['id'][:8]} — no duplicate created")

    print("→ Mobile offline sync: rejecting a backdated timestamp beyond the allowed window…")
    ancient = call(args.api, "/reports", method="POST", form={
        **offline_form,
        "client_key": f"{offline_key}-ancient",
        "observed_at": (now - timedelta(days=30)).isoformat(),
    })
    ancient_age_hours = (now - datetime.fromisoformat(ancient["created_at"])).total_seconds() / 3600
    assert ancient_age_hours <= 25, (
        f"a 30-day-old observed_at was accepted as-is ({ancient_age_hours:.1f}h old) — clamping is what stops a "
        "public caller placing reports inside the coherence window of any past event they choose"
    )
    print(f"   30-day-old timestamp clamped to {ancient_age_hours:.1f}h old (the offline window), not accepted as-is")

    print("→ Mark Safe: submitting an 'I need help' check-in…")
    checkin = call(args.api, "/safety/checkin", method="POST", form={
        "lat": f"{MARINA[0]:.6f}", "lon": f"{MARINA[1]:.6f}",
        "client_id": "drill-mobile-1", "status": "need_help",
        "note": "Drill: on the roof, water rising", "client_key": f"{offline_key}-safe",
    })
    assert checkin["status"] == "need_help"
    need_help = call(args.api, "/analyst/safety/checkins?hours=1&status=need_help", token=token)
    assert any(c["id"] == checkin["id"] for c in need_help), (
        "the check-in didn't reach the responder-facing list"
    )
    summary = call(args.api, "/analyst/safety/summary?hours=1", token=token)
    print(f"   check-in {checkin['id'][:8]} visible to responders; last hour: "
          f"{summary['safe']} safe, {summary['need_help']} need help")

    recent_reports = call(args.api, "/analyst/reports?limit=25", token=token)
    assert not any("on the roof, water rising" in (r.get("text") or "") for r in recent_reports), (
        "a safety check-in leaked into the hazard report pipeline — a statement about a person must never "
        "feed confidence scoring or incident clustering"
    )
    print("   confirmed: the check-in never became a hazard report (it can't corroborate anything)")

    print("→ Recovery: submitting a damage-assessment photo (a hand-built solid-blue PNG)…")
    flood_photo = _solid_color_png(64, 64, (20, 40, 200))
    damage = post_multipart(
        args.api, "/recovery/damage",
        fields={"lat": f"{MARINA[0]:.6f}", "lon": f"{MARINA[1] + 0.001:.6f}",
                "client_id": "drill-recovery-1", "note": "Drill: seafront kiosk"},
        files={"photo": ("flood.png", flood_photo, "image/png")},
    )
    assert damage["damage_class"] == "flooding", (
        f"expected the solid-blue photo to read as flooding, got {damage['damage_class']} "
        f"(cv_detail={damage['cv_detail']}) — did the pixel-heuristic thresholds change?"
    )
    print(f"   assessment {damage['id'][:8]}: {damage['damage_class']}/{damage['severity']} "
          f"(cv_mode={damage['cv_mode']}, confidence={damage['cv_confidence']})")
    damage_map = call(args.api, "/map/damage")
    assert any(f["properties"]["id"] == damage["id"] for f in damage_map["features"]), (
        "the submitted damage assessment did not appear on the public /map/damage layer"
    )
    reviewed = call(args.api, f"/analyst/recovery/damage/{damage['id']}/review", method="POST", token=token)
    assert reviewed["status"] == "reviewed"
    print(f"   {len(damage_map['features'])} damage assessment(s) on the public map; analyst marked it reviewed")

    print("→ Recovery: posting a relief request and a matching aid offer…")
    relief = call(args.api, "/recovery/relief-requests", method="POST", form={
        "lat": f"{MARINA[0]:.6f}", "lon": f"{MARINA[1] + 0.002:.6f}",
        "client_id": "drill-recovery-2", "category": "water", "people_count": "5",
    })
    offer = call(args.api, "/recovery/aid-offers", method="POST", form={
        "lat": f"{MARINA[0] + 0.002:.6f}", "lon": f"{MARINA[1] + 0.002:.6f}",
        "client_id": "drill-recovery-3", "category": "water", "capacity": "20",
    })
    matches = call(args.api, "/analyst/recovery/aid-matches", token=token)
    assert any(m["request_id"] == relief["id"] and m["offer_id"] == offer["id"] for m in matches), (
        "the mutual-aid board did not pair a same-category request and offer a few hundred meters apart"
    )
    print(f"   board suggests {len(matches)} match(es), incl. request {relief['id'][:8]} <-> offer {offer['id'][:8]}")

    fulfilled = call(args.api, f"/analyst/recovery/relief-requests/{relief['id']}/fulfill",
                     method="POST", token=token, form={"fulfilled_by": "Drill Relief Team"})
    assert fulfilled["status"] == "fulfilled"
    open_requests = call(args.api, "/analyst/recovery/relief-requests", token=token)
    assert not any(r["id"] == relief["id"] for r in open_requests), (
        "a fulfilled relief request is still showing in the open list"
    )
    print(f"   request {relief['id'][:8]} fulfilled by {fulfilled['fulfilled_by']} and dropped from the open list")

    print("→ Recovery: submitting a missing-person report and a near-duplicate-name found-person report…")
    missing = call(args.api, "/recovery/missing", method="POST", form={
        "client_id": "drill-recovery-4", "report_type": "missing", "name": "Kavya Raman",
        "age": "34", "description": "wearing a yellow saree, last seen near the seafront",
        "lat": f"{MARINA[0]:.6f}", "lon": f"{MARINA[1]:.6f}",
    })
    found = call(args.api, "/recovery/missing", method="POST", form={
        "client_id": "drill-recovery-5", "report_type": "found", "name": "Kavia Raman",
        "description": "found near the fishing harbour, doesn't recall her address",
        "lat": f"{MARINA[0] + 0.01:.6f}", "lon": f"{MARINA[1]:.6f}",
    })
    candidates = call(args.api, f"/analyst/recovery/missing/{missing['id']}/matches", token=token)
    assert any(c["candidate_id"] == found["id"] for c in candidates), (
        f"expected the found-person report (near-duplicate name 'Kavia Raman') to surface as a candidate "
        f"match for missing report {missing['id'][:8]} — candidates={candidates}"
    )
    print(f"   {len(candidates)} candidate match(es) for {missing['id'][:8]}, "
          f"top score {candidates[0]['name_score']} ({candidates[0]['distance_km']} km away)")

    resolved = call(args.api, f"/analyst/recovery/missing/{missing['id']}/resolve", method="POST",
                    token=token, form={"matched_person_id": found["id"]})
    assert resolved["status"] == "resolved" and resolved["matched_person_id"] == found["id"]
    still_open = call(args.api, "/analyst/recovery/missing?report_type=found", token=token)
    assert not any(p["id"] == found["id"] for p in still_open), (
        "resolving a missing/found pair should close both sides, but the matched found-report is still open"
    )
    print(f"   missing report {missing['id'][:8]} resolved against found report {found['id'][:8]} — both closed")

    print("→ Recovery: confirming the missing-person registry rejects an unauthenticated read…")
    unauth_req = urllib.request.Request(args.api + "/analyst/recovery/missing")
    try:
        urllib.request.urlopen(unauth_req, timeout=10)
        raise SystemExit("missing-person registry allowed an unauthenticated read — privacy gate is broken")
    except urllib.error.HTTPError as exc:
        assert exc.code in (401, 403), f"expected 401/403 without a token, got HTTP {exc.code}"
        print(f"   confirmed: unauthenticated read rejected with HTTP {exc.code}")

    purge_result = call(args.api, "/analyst/recovery/missing/purge-expired", method="POST", token=token)
    still_there = call(args.api, "/analyst/recovery/missing?report_type=missing", token=token)
    assert not any(p["id"] == missing["id"] for p in still_there), (
        "the missing report should be gone from the open list (it was resolved above), not because it "
        "was purged — a fresh drill-created row must never be caught by the retention window"
    )
    print(f"   retention purge ran (purged={purge_result['purged']}); today's drill rows untouched")

    chain = call(args.api, "/analyst/audit/verify", token=token)
    print(f"→ Audit chain: intact={chain['intact']} over {chain['entries_checked']} entries")
    if not chain["intact"]:
        sys.exit("AUDIT CHAIN BROKEN")
    print("✓ Drill complete.")


if __name__ == "__main__":
    main()
