"""Live end-to-end check for the bus pipeline mode (phase 3, milestone 8 —
the architecture split).

scripts/drill.py's main scenario assumes synchronous scoring (the default
"inline" pipeline mode) throughout — half its assertions read a report's
hazard_type/confidence/status right after POST /reports, which is only
meaningful when nothing is still catching up in the background. Running that
whole scenario against pipeline_mode=bus would trip assertions on eventual-
consistency timing, not on anything actually broken. This script instead
verifies the split itself, standalone, the same way scripts/iot/
iot_live_check.py verifies the IoT pilot standalone rather than folding real
MQTT publishing into the main drill.

Needs the stack up with the "split" profile and PIPELINE_MODE=bus:
    docker compose up -d
    PIPELINE_MODE=bus docker compose --profile split up -d nlp-consumer dedup-consumer scoring-consumer
    python scripts/bus_pipeline_check.py [--api http://localhost:8000]

For the 50x-load exit criterion itself, see scripts/drill.py --load-only
--scale 50, which works against either pipeline mode.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MARINA = (13.0500, 80.2824)

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    if not ok:
        failures += 1


def call(api: str, path: str, *, method: str = "GET", token: str | None = None,
          json_body: dict | None = None, form: dict | None = None) -> dict | list:
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
    req = urllib.request.Request(api + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def wait_scored(api: str, report_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    report = call(api, f"/reports/{report_id}")
    while report["processing_stage"] != "scored" and time.monotonic() < deadline:
        time.sleep(0.5)
        report = call(api, f"/reports/{report_id}")
    return report


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--username", default="analyst")
    parser.add_argument("--password", default="oceanping-dev")
    args = parser.parse_args()

    print("→ Checking API health…")
    call(args.api, "/healthz")
    token = call(args.api, "/auth/login", method="POST",
                 json_body={"username": args.username, "password": args.password})["token"]

    print("→ Submitting a report with no explicit hazard_type (nlp consumer must classify it)…")
    ts = int(time.time())
    unclassified = call(args.api, "/reports", method="POST", form={
        "lat": f"{MARINA[0]:.6f}", "lon": f"{MARINA[1]:.6f}",
        "client_id": f"bus-check-{ts}-1",
        "text": "tsunami warning sirens going off, huge wave approaching the coast, everyone is running",
    })
    check("gateway returned before the pipeline finished (genuinely async, not just fast)",
          unclassified["processing_stage"] != "scored",
          f"got processing_stage={unclassified['processing_stage']} immediately — is PIPELINE_MODE=bus set?")
    print(f"   report {unclassified['id'][:8]} submitted, processing_stage={unclassified['processing_stage']}")

    print("→ Submitting a report WITH an explicit hazard_type and unrelated text "
          "(the reporter's pick must survive nlp reclassification)…")
    explicit = call(args.api, "/reports", method="POST", form={
        "lat": f"{MARINA[0] + 0.001:.6f}", "lon": f"{MARINA[1]:.6f}",
        "client_id": f"bus-check-{ts}-2",
        "hazard_type": "oil_spill",
        "text": "tsunami warning sirens going off, huge wave approaching the coast",
    })

    print("→ Waiting for both reports to reach processing_stage=scored (nlp -> dedup -> scoring)…")
    unclassified_final = wait_scored(args.api, unclassified["id"])
    explicit_final = wait_scored(args.api, explicit["id"])

    check("unclassified report reached processing_stage=scored",
          unclassified_final["processing_stage"] == "scored",
          f"stuck at {unclassified_final['processing_stage']}")
    # Embedding classification against nearest hazard prototypes can land on a
    # semantically adjacent class (e.g. "high_waves" for a tsunami-flavored
    # text) — that's real classification working, not a bug. The thing worth
    # asserting is that it moved off the "other" placeholder the gateway
    # wrote at submit time.
    check("nlp consumer replaced the 'other' placeholder with a real classification",
          unclassified_final["hazard_type"] != "other",
          f"still 'other' — did the nlp consumer run at all?")
    check("dedup consumer assigned it to an incident",
          unclassified_final["incident_id"] is not None)
    check("scoring consumer produced a non-zero confidence",
          unclassified_final["confidence"] > 0, f"got {unclassified_final['confidence']}")

    check("explicit report reached processing_stage=scored",
          explicit_final["processing_stage"] == "scored",
          f"stuck at {explicit_final['processing_stage']}")
    check("the reporter's explicit hazard_type pick was never overwritten by nlp reclassification",
          explicit_final["hazard_type"] == "oil_spill",
          f"got {explicit_final['hazard_type']!r} — hazard_locked semantics broken")
    check("dedup consumer still assigned the explicit-hazard report to an incident",
          explicit_final["incident_id"] is not None)

    chain = call(args.api, "/analyst/audit/verify", token=token)
    check("audit chain intact", chain["intact"], f"{chain['entries_checked']} entries checked")

    print(f"\n{'✓' if failures == 0 else '✗'} {failures} failure(s).")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
