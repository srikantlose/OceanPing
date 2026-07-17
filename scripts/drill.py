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

    print("→ Forcing pipeline tick (anomaly detection + rescore)…")
    tick = call(args.api, "/drill/tick", method="POST", token=token)
    print(f"   rescored {tick['rescored_reports']} reports")

    print("→ Post-tick state:")
    reports = call(args.api, "/analyst/reports?limit=20", token=token)
    corroborated = [r for r in reports if r["status"] == "corroborated"]
    for r in reports[:6]:
        c = r["confidence_components"]
        print(f"   [{r['status']:>11}] conf={r['confidence']:.2f} "
              f"(trust={c.get('trust', 0):.2f} coher={c.get('coherence', 0):.2f} "
              f"instr={c.get('instrument', 0):.2f} media={c.get('media', 0):.2f}) "
              f"{r['hazard_type']}")
    incidents = call(args.api, "/analyst/incidents", token=token)
    print(f"   {len(reports)} reports → {len(incidents)} incidents "
          f"(largest merges {max((i['report_count'] for i in incidents), default=0)} reports); "
          f"{len(corroborated)} auto-corroborated by the tide gauge")

    hotspots = call(args.api, "/map/hotspots")
    print(f"   hotspots on map: {len(hotspots['features'])}")

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
