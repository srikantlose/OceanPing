from app.modules.fisherman import pfz, roster


# --- roster ------------------------------------------------------------------

def test_member_for_phone_matches_various_formats():
    assert roster.member_for_phone("+919840012345") is not None
    assert roster.member_for_phone("919840012345") is not None
    assert roster.member_for_phone("09840012345") is not None
    assert roster.member_for_phone("9840-012-345") is not None


def test_member_for_phone_returns_cooperative_name():
    member = roster.member_for_phone("+919840012345")
    assert member["cooperative"] == "Kasimedu Fishermen Welfare Cooperative"


def test_member_for_phone_unknown_number_returns_none():
    assert roster.member_for_phone("+910000000000") is None


# --- pfz -----------------------------------------------------------------------

def test_stub_pfz_provider_returns_one_zone_per_landing_site():
    zones = pfz.StubPfzProvider().fetch(pfz.PILOT_SECTOR)
    assert len(zones) == len(pfz.LANDING_SITES)
    for zone in zones:
        assert 0 < zone["depth_m"]
        assert 0 < zone["distance_km"]
        assert "bearing" in zone and zone["bearing"]


def test_stub_pfz_provider_is_deterministic_within_the_same_week():
    first = pfz.StubPfzProvider().fetch(pfz.PILOT_SECTOR)
    second = pfz.StubPfzProvider().fetch(pfz.PILOT_SECTOR)
    assert first == second


def test_stub_pfz_provider_varies_by_sector():
    a = pfz.StubPfzProvider().fetch("North Tamil Nadu")
    b = pfz.StubPfzProvider().fetch("Kerala")
    assert a != b


def test_get_provider_returns_stub():
    assert isinstance(pfz.get_provider(), pfz.StubPfzProvider)
