from types import SimpleNamespace

from app.modules.alerts import service


# --- _flooded_cells_for gating (HAZARD_VARIABLES) ---------------------------

def test_flooded_cells_for_skips_hazard_with_no_water_level_signal(monkeypatch):
    monkeypatch.setattr(service, "predicted_flooded_cells", lambda db: {"should", "not", "be", "used"})
    assert service._flooded_cells_for(object(), "oil_spill") == []


def test_flooded_cells_for_queries_prediction_for_water_level_hazard(monkeypatch):
    monkeypatch.setattr(service, "predicted_flooded_cells", lambda db: {"b", "a"})
    assert service._flooded_cells_for(object(), "coastal_flooding") == ["a", "b"]  # sorted, stable JSON


def test_flooded_cells_for_empty_without_fresh_gauge_reading(monkeypatch):
    monkeypatch.setattr(service, "predicted_flooded_cells", lambda db: set())
    assert service._flooded_cells_for(object(), "storm_surge") == []


# --- sync_incident_alert / issue_warning wiring -----------------------------

class _FakeScalarsResult:
    def __init__(self, first_value=None):
        self._first_value = first_value

    def first(self):
        return self._first_value


class _AlertDb:
    def __init__(self, active_alert=None):
        self._active_alert = active_alert
        self.added = []
        self.committed = False

    def scalars(self, stmt):
        return _FakeScalarsResult(self._active_alert)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True


def test_sync_incident_alert_attaches_predicted_flooded_cells(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(alert_min_watch_reporters=3))
    monkeypatch.setattr(service, "append_audit", lambda *a, **k: None)
    monkeypatch.setattr(service, "enqueue_alert", lambda alert_id: None)
    monkeypatch.setattr(service, "_flooded_cells_for", lambda db, hazard_type: ["cell_x", "cell_y"])
    incident = SimpleNamespace(
        id="inc-1", status="corroborated", hazard_type="coastal_flooding", report_count=5,
        h3_cells=["cell_1"],
        reports=[SimpleNamespace(reporter_id="r1", confidence_components={"instrument": 0.8})],
    )
    alert = service.sync_incident_alert(_AlertDb(active_alert=None), incident)
    assert alert.predicted_flooded_cells == ["cell_x", "cell_y"]


def test_sync_incident_alert_refreshes_predicted_flooded_cells_on_tier_upgrade(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(alert_min_watch_reporters=1))
    monkeypatch.setattr(service, "append_audit", lambda *a, **k: None)
    monkeypatch.setattr(service, "enqueue_alert", lambda alert_id: None)
    monkeypatch.setattr(service, "_flooded_cells_for", lambda db, hazard_type: ["cell_new"])
    incident = SimpleNamespace(
        id="inc-1", status="corroborated", hazard_type="coastal_flooding", report_count=5,
        h3_cells=["cell_1"],
        reports=[SimpleNamespace(reporter_id="r1", confidence_components={"instrument": 0.8})],
    )
    active = SimpleNamespace(
        id="alert-1", tier="advisory", issued_by=None, message={}, h3_cells=[],
        predicted_flooded_cells=["cell_old"],
    )
    alert = service.sync_incident_alert(_AlertDb(active_alert=active), incident)
    assert alert is active
    assert alert.predicted_flooded_cells == ["cell_new"]


def test_issue_warning_attaches_predicted_flooded_cells(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(alert_default_expiry_hours=6.0))
    monkeypatch.setattr(service, "append_audit", lambda *a, **k: None)
    monkeypatch.setattr(service, "enqueue_alert", lambda alert_id: None)
    monkeypatch.setattr(service, "_flooded_cells_for", lambda db, hazard_type: ["cell_z"])
    incident = SimpleNamespace(id="inc-2", hazard_type="storm_surge", report_count=8, h3_cells=["cell_2"])
    alert = service.issue_warning(_AlertDb(active_alert=None), incident, analyst="alice")
    assert alert.tier == "warning"
    assert alert.predicted_flooded_cells == ["cell_z"]
