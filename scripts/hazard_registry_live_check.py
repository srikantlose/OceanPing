"""Live end-to-end check for the hazard registry refactor (phase 4, milestone 2).

Proves the plan's own literal verification bar against the real running
stack, not just the derivation-function unit tests in
tests/test_hazard_registry.py: "add a toy hazard purely via config -> report
-> score -> alert path works in drill with zero code diff outside the
registry."

This script assumes a throwaway hazard definition has already been dropped
into backend/app/modules/hazards/definitions/ and the backend image rebuilt
from it — it does not touch the filesystem or Docker itself, since doing
that from inside a script that's also making live HTTP calls would conflate
two different kinds of "did this work" evidence. The actual sequence run
once for this milestone was:

    1. write definitions/_live_check_toy_hazard.yaml (see the milestone's
       as-built doc for its exact contents)
    2. docker compose build backend && docker compose up -d backend
    3. python scripts/hazard_registry_live_check.py
    4. rm definitions/_live_check_toy_hazard.yaml
    5. docker compose build backend && docker compose up -d backend   (restore)

Usage:
    python scripts/hazard_registry_live_check.py [--api http://localhost:8000] [--hazard-key KEY]
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    if not ok:
        failures += 1


def api(base, path, *, method="GET", token=None, json_body=None, form_body=None,
        expect_status=None):
    hdrs = {}
    data = None
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif form_body is not None:
        data = urllib.parse.urlencode(form_body).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
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
    parser.add_argument("--hazard-key", default="king_tide",
                         help="key of the toy hazard already added to definitions/ and rebuilt in")
    args = parser.parse_args()
    hz = args.hazard_key

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"→ Live hazard-registry check against {args.api} (toy hazard: {hz!r})")
    _, login = api(args.api, "/auth/login", method="POST",
                    json_body={"username": "analyst", "password": "oceanping-dev"}, expect_status=200)
    token = login["token"]

    # --- 1. The toy hazard is really visible through the public API --------
    status, hazard_types = api(args.api, "/hazard-types", expect_status=200)
    check("toy hazard appears in GET /hazard-types (registry-driven, not a hardcoded list)",
          hz in hazard_types, str(hazard_types))

    # --- 2. POST /reports accepts it — validation reads the registry, not a
    #        second hardcoded HAZARD_TYPES copy in the ingest router --------
    run_id = int(time.time())
    client_id = f"hazard-live-{run_id}"
    status, report = api(args.api, "/reports", method="POST", form_body={
        "lat": "10.900000", "lon": "79.500000", "hazard_type": hz,
        "client_id": client_id, "text": "Hazard registry live check: unusually high tide reported.",
    }, expect_status=200)
    check("POST /reports accepts the toy hazard type (not rejected with 422)",
          report.get("hazard_type") == hz, str(report))

    # --- 3. It flows through scoring without any hazard-specific code ------
    api(args.api, "/drill/tick", method="POST", token=token, expect_status=200)
    _, reports = api(args.api, "/analyst/reports?limit=10", token=token, expect_status=200)
    scored = next((r for r in reports if r["id"] == report["id"]), None)
    check("report is visible to analysts and made it through a scoring pass",
          scored is not None and "confidence_components" in scored, str(scored))
    incident_id = scored["incident_id"] if scored else None
    check("report auto-assigned to an incident (inline pipeline, same as any other hazard)",
          incident_id is not None)

    # --- 4. An alert can be issued and rendered for it, using nothing but
    #        the registry's fallback label (this toy hazard defines no
    #        alert_label_en, so the fallback path itself is under test) -----
    if incident_id is not None:
        _, alert = api(args.api, f"/analyst/incidents/{incident_id}/warning", method="POST",
                        json_body={"note": "Hazard registry live check."}, token=token, expect_status=200)
        alert_id = alert["id"]
        status, cap_xml = api(args.api, f"/cap/alerts/{alert_id}.cap", expect_status=200)
        check("CAP document renders for the toy hazard without error", status == 200)
        check("CAP document carries some rendering of the toy hazard's name (fallback label)",
              hz.replace("_", " ") in cap_xml.lower(), cap_xml[:400])

    print(failures == 0 and "\n✓ Live hazard-registry check passed." or f"\n✗ {failures} check(s) failed.")
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
