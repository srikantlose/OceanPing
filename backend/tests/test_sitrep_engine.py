from app.modules.sitrep import engine

SNAPSHOT = {
    "period_start": "2026-07-20T10:00:00+00:00",
    "period_end": "2026-07-20T11:00:00+00:00",
    "reports": {"total": 14, "by_status": {"verified": 1, "corroborated": 8, "unverified": 5}, "by_hazard": {"coastal_flooding": 12, "oil_spill": 2}},
    "incidents": {"active_in_period": 3, "new": 2, "by_status": {"corroborated": 2, "unverified": 1}},
    "alerts": {
        "issued": [{"tier": "watch", "hazard_type": "coastal_flooding", "issued_by": "automatic", "created_at": "2026-07-20T10:30:00+00:00"}],
        "active_now": [{"tier": "watch", "hazard_type": "coastal_flooding", "issued_by": "automatic"}],
    },
    "hotspots": {"current": [{"lat": 13.05, "lon": 80.28, "report_count": 8, "dominant_hazard": "coastal_flooding", "intensity": 5.2}], "tagged": [], "cleared": []},
    "resources": {"shelters_total": 4, "shelters_open": 3, "open_capacity_total": 900, "open_capacity_unknown_count": 0},
    "audit": {"chain_intact": True, "entries_checked": 42},
}


def test_build_sitrep_title_spans_the_reporting_period():
    out = engine.build_sitrep(SNAPSHOT)
    assert "2026-07-20T10:00:00+00:00" in out["title"]
    assert "2026-07-20T11:00:00+00:00" in out["title"]


def test_build_sitrep_summary_uses_only_snapshot_numbers():
    out = engine.build_sitrep(SNAPSHOT)
    summary = out["summary"]
    assert "14 citizen report(s)" in summary
    assert "1 verified" in summary
    assert "8 corroborated" in summary
    assert "3 tracked incident(s)" in summary
    assert "2 new this period" in summary
    assert "1 alert(s) active now" in summary
    assert "1 issued this period" in summary
    assert "3/4 shelter(s) open" in summary


def test_build_sitrep_sections_pass_through_every_snapshot_field_unmodified():
    out = engine.build_sitrep(SNAPSHOT)
    sections = out["sections"]
    assert sections["reports"] == SNAPSHOT["reports"]
    assert sections["incidents"] == SNAPSHOT["incidents"]
    assert sections["alerts"] == SNAPSHOT["alerts"]
    assert sections["hotspots"] == SNAPSHOT["hotspots"]
    assert sections["resources"] == SNAPSHOT["resources"]
    assert sections["data_integrity"] == SNAPSHOT["audit"]


def test_build_sitrep_handles_a_quiet_period_with_no_activity():
    quiet = {
        "period_start": "2026-07-20T10:00:00+00:00",
        "period_end": "2026-07-20T11:00:00+00:00",
        "reports": {"total": 0, "by_status": {}, "by_hazard": {}},
        "incidents": {"active_in_period": 0, "new": 0, "by_status": {}},
        "alerts": {"issued": [], "active_now": []},
        "hotspots": {"current": [], "tagged": [], "cleared": []},
        "resources": {"shelters_total": 4, "shelters_open": 4, "open_capacity_total": 1200, "open_capacity_unknown_count": 0},
        "audit": {"chain_intact": True, "entries_checked": 42},
    }
    out = engine.build_sitrep(quiet)
    assert "0 citizen report(s)" in out["summary"]
    assert "0 verified" in out["summary"]
    assert "4/4 shelter(s) open" in out["summary"]
