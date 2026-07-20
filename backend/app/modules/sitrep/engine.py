"""NDMA-style situation report drafting — pure function, no I/O.

Every field in the output is copied straight from the snapshot service.py
assembles from verified DB state (report/incident counts, alerts, hotspot
movement, shelter resources, audit-chain integrity); nothing here invents or
infers a number, so an analyst reviewing a draft is checking wording, not
double-checking arithmetic.
"""


def build_sitrep(snapshot: dict) -> dict:
    reports = snapshot["reports"]
    incidents = snapshot["incidents"]
    alerts = snapshot["alerts"]
    resources = snapshot["resources"]

    summary = (
        f"{reports['total']} citizen report(s) received "
        f"({reports['by_status'].get('verified', 0)} verified, "
        f"{reports['by_status'].get('corroborated', 0)} corroborated) across "
        f"{incidents['active_in_period']} tracked incident(s) "
        f"({incidents['new']} new this period). "
        f"{len(alerts['active_now'])} alert(s) active now; "
        f"{len(alerts['issued'])} issued this period. "
        f"{resources['shelters_open']}/{resources['shelters_total']} shelter(s) open."
    )

    return {
        "title": f"Situation Report — {snapshot['period_start']} to {snapshot['period_end']}",
        "summary": summary,
        "sections": {
            "reports": reports,
            "incidents": incidents,
            "alerts": alerts,
            "hotspots": snapshot["hotspots"],
            "resources": resources,
            "data_integrity": snapshot["audit"],
        },
    }
