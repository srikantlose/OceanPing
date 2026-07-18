from app.modules.satellite import providers


class _FakeIncident:
    def __init__(self, id):
        self.id = id


def test_stub_provider_is_deterministic():
    incident = _FakeIncident("11111111-1111-1111-1111-111111111111")
    provider = providers.StubProvider()
    first = provider.observe(incident, "sentinel1_sar_dark_slick")
    second = provider.observe(incident, "sentinel1_sar_dark_slick")
    assert first.score == second.score


def test_stub_provider_score_in_range():
    provider = providers.StubProvider()
    for incident_id in ("a", "b", "c", "d"):
        obs = provider.observe(_FakeIncident(incident_id), "sentinel2_ndci_anomaly")
        assert 0.0 <= obs.score <= 1.0
        assert obs.provider == "stub"
        assert obs.recipe == "sentinel2_ndci_anomaly"


def test_stub_provider_differs_by_recipe():
    incident = _FakeIncident("same-incident")
    provider = providers.StubProvider()
    a = provider.observe(incident, "sentinel1_sar_dark_slick")
    b = provider.observe(incident, "sentinel2_ndci_anomaly")
    assert a.score != b.score


def test_sentinel_hub_provider_skips_without_credentials(monkeypatch):
    settings = providers.get_settings()
    monkeypatch.setattr(settings, "sentinel_hub_client_id", "")
    monkeypatch.setattr(settings, "sentinel_hub_client_secret", "")
    result = providers.SentinelHubProvider().observe(_FakeIncident("x"), "sentinel1_sar_dark_slick")
    assert result is None


def test_earth_engine_provider_skips_without_credentials(monkeypatch):
    settings = providers.get_settings()
    monkeypatch.setattr(settings, "earth_engine_service_account_json", "")
    result = providers.EarthEngineProvider().observe(_FakeIncident("x"), "sentinel2_ndci_anomaly")
    assert result is None


def test_get_provider_defaults_to_stub(monkeypatch):
    settings = providers.get_settings()
    monkeypatch.setattr(settings, "satellite_provider", "stub")
    assert isinstance(providers.get_provider(), providers.StubProvider)


def test_get_provider_unknown_falls_back_to_stub(monkeypatch):
    settings = providers.get_settings()
    monkeypatch.setattr(settings, "satellite_provider", "not_a_real_provider")
    assert isinstance(providers.get_provider(), providers.StubProvider)


def test_hazard_recipes_only_cover_slow_hazards_with_no_instrument_signal():
    # oil_spill and algal_bloom have zero instrument variables (engine.HAZARD_VARIABLES),
    # so satellite must be their only corroboration path - confirm both are covered.
    assert "oil_spill" in providers.HAZARD_RECIPES
    assert "algal_bloom" in providers.HAZARD_RECIPES
    # Fast hazards must never get a recipe - satellite latency is hours, it must
    # never gate escalation for hazards where waiting for a scene is dangerous.
    assert "tsunami" not in providers.HAZARD_RECIPES
    assert "rip_current" not in providers.HAZARD_RECIPES
