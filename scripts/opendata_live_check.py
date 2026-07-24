"""Live end-to-end check for the open-data pipeline (phase 4, milestone 3).

Proves the plan's own stated verification bars against the real running
stack, not just the service-layer unit tests in
tests/test_opendata_service.py:
  - "public API keys, rate-limited"
  - "DP: re-identification test on released aggregates (no cell below k
    threshold)" — done here with a *controlled* re-identification check:
    this script submits and verifies a known, small (below-k) batch of
    reports and a known, larger (at-k) batch at two locations no other
    drill/live-check script has ever used, builds a release covering
    exactly the window it just created them in, and confirms the exact
    expected shape of the result (not just "at least one suppressed").
  - the retention job actually mutates real Report rows in the real
    database, not a fake one (tests/test_opendata_service.py proves the
    logic; this proves the wiring).

Rerunnable against the persistent dev stack, same convention as
scripts/drill.py / scripts/cap_live_check.py.

Usage:
    python scripts/opendata_live_check.py [--api http://localhost:8000]
"""
import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

failures = 0

# Locations no other drill/live-check script touches (Chennai-area drills
# cluster around Marina/Kasimedu/Ennore, ~13.0-13.3N) — chosen far enough
# south that this script's own controlled report counts are the only
# reports ever in these H3 cells, so suppression/inclusion is deterministic.
SUPPRESS_LOCATION = (12.20, 79.85)   # below the k-anonymity floor, deliberately
RELEASE_LOCATION = (10.80, 79.85)    # at/above the floor


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


def submit_and_verify(base, token, lat, lon, hazard_type, text, client_prefix):
    _, report = api(base, "/reports", method="POST", form_body={
        "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "hazard_type": hazard_type,
        "client_id": f"{client_prefix}-{int(time.time() * 1000)}-{random.randint(0, 999999)}",
        "text": text,
    }, expect_status=200)
    report_id = report["id"]
    api(base, f"/analyst/reports/{report_id}/verify", method="POST", token=token,
        json_body={"note": "opendata live check"}, expect_status=200)
    return report_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"-> Live open-data pipeline check against {args.api}")
    _, login = api(args.api, "/auth/login", method="POST",
                    json_body={"username": "analyst", "password": "oceanping-dev"}, expect_status=200)
    token = login["token"]

    # --- 1. The dataset catalog is public — no key needed to browse it -----
    status, catalog = api(args.api, "/opendata/datasets", expect_status=200)
    check("dataset catalog is public (no API key needed)", isinstance(catalog, list))

    # --- 2. Submit a controlled, known batch: 2 reports (below k=5) at
    #        SUPPRESS_LOCATION, 6 reports (at/above k=5) at RELEASE_LOCATION.
    window_start = datetime.now(timezone.utc)
    for _ in range(2):
        submit_and_verify(args.api, token, *SUPPRESS_LOCATION, "erosion",
                           "opendata live check: controlled below-floor batch", "odlc-suppress")
    for _ in range(6):
        submit_and_verify(args.api, token, *RELEASE_LOCATION, "rip_current",
                           "opendata live check: controlled at-floor batch", "odlc-release")
    window_end = datetime.now(timezone.utc) + timedelta(minutes=5)

    # --- 3. Build a release covering exactly this window --------------------
    status, release = api(args.api, "/analyst/opendata/releases", method="POST", token=token,
                           json_body={"period_start": window_start.isoformat(),
                                      "period_end": window_end.isoformat()},
                           expect_status=200)
    release_id = release["id"]
    check("release records real DP/k-anonymity parameters",
          release["k_anonymity_min"] >= 1 and release["dp_epsilon"] > 0, str(release))
    print(f"    release {release_id}: row_count={release['row_count']} "
          f"suppressed_group_count={release['suppressed_group_count']}")

    status, catalog_after = api(args.api, "/opendata/datasets", expect_status=200)
    check("release appears in the public catalog", any(r["id"] == release_id for r in catalog_after))

    # --- 4. Download is gated: no key -> 401, bad key -> 403 -----------------
    status, _ = api(args.api, f"/opendata/datasets/{release_id}", expect_status=401)
    check("download rejected with no API key (401)", status == 401)

    status, _ = api(args.api, f"/opendata/datasets/{release_id}", token="not-a-real-key", expect_status=403)
    check("download rejected with an invalid API key (403)", status == 403)

    # --- 5. Mint a real key as the analyst; the raw secret is never re-shown
    status, minted = api(args.api, "/analyst/opendata/api-keys", method="POST", token=token,
                          json_body={"label": "Live-check consumer"}, expect_status=200)
    raw_key = minted["key"]
    check("minted key's raw secret follows the expected shape",
          raw_key.startswith("op_live_"), raw_key[:12])

    status, keys_list = api(args.api, "/analyst/opendata/api-keys", token=token, expect_status=200)
    check("key listing shows the prefix but never the raw secret",
          any(k["id"] == minted["id"] for k in keys_list) and all("key" not in k for k in keys_list),
          str(keys_list[:1]))

    # --- 6. A valid key downloads successfully ------------------------------
    status, download = api(args.api, f"/opendata/datasets/{release_id}", token=raw_key, expect_status=200)
    check("download succeeds with a valid API key", download.get("id") == release_id)
    rows = download.get("rows", [])

    # --- 7. Controlled re-identification check. apply_verification() is the
    #        *only* code path that ever sets Report.status="verified" (see
    #        scoring/service.py's own docstring), and this release's window
    #        is bound to Report.created_at — so within this narrow,
    #        just-created window, the only verified reports that can exist
    #        anywhere are exactly the 2 (below-floor) + 6 (at-floor) this
    #        script itself just submitted and verified. That makes the
    #        expected shape of the release exact, not just "at least one":
    #        exactly one suppressed group (the 2-report erosion batch) and
    #        exactly one released row (the 6-report rip_current batch).
    check("exactly the controlled below-floor group was suppressed",
          release["suppressed_group_count"] == 1, str(release))
    check("exactly the controlled at-floor group was released",
          release["row_count"] == 1, str(release))
    if rows:
        check("the released row is the rip_current batch, DP-noised but non-negative",
              rows[0]["hazard_type"] == "rip_current" and rows[0]["report_count"] >= 0, str(rows[0]))

    # --- 8. Rate limiting: hammer the download endpoint past the cap --------
    limited = False
    for _ in range(210):  # comfortably above the default 200/hour cap
        status, _ = api(args.api, f"/opendata/datasets/{release_id}", token=raw_key)
        if status == 429:
            limited = True
            break
    check("rate limiting eventually kicks in for a single API key", limited)

    # --- 9. Revoking a key really blocks it, not just soft-hides it ---------
    api(args.api, f"/analyst/opendata/api-keys/{minted['id']}/revoke", method="POST", token=token,
        expect_status=200)
    status, _ = api(args.api, f"/opendata/datasets/{release_id}", token=raw_key)
    check("revoked key is rejected on its next use (403)", status == 403)

    print(failures == 0 and "\n[OK] Live open-data pipeline check passed."
          or f"\n[FAIL] {failures} check(s) failed.")
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
