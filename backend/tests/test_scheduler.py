"""Load-shed check (phase 3, milestone 8): analytics jobs (SITREPs, forecasts,
narratives, satellite polling) defer themselves for a tick when the bus
pipeline's nlp consumer is backed up — never in inline mode, which has no
consumer-lag concept at all."""
from types import SimpleNamespace

from app.core import scheduler


def _settings(pipeline_mode="bus", load_shed_lag_threshold=500):
    return SimpleNamespace(pipeline_mode=pipeline_mode, load_shed_lag_threshold=load_shed_lag_threshold)


def test_should_shed_analytics_is_always_false_in_inline_mode(monkeypatch):
    monkeypatch.setattr(scheduler, "get_settings", lambda: _settings(pipeline_mode="inline"))
    assert scheduler._should_shed_analytics() is False


def test_should_shed_analytics_false_when_lag_below_threshold(monkeypatch):
    monkeypatch.setattr(scheduler, "get_settings", lambda: _settings())
    import app.modules.ingest.bus as bus
    monkeypatch.setattr(bus, "lag", lambda group_id, topic: 10)
    assert scheduler._should_shed_analytics() is False


def test_should_shed_analytics_true_when_lag_exceeds_threshold(monkeypatch):
    monkeypatch.setattr(scheduler, "get_settings", lambda: _settings(load_shed_lag_threshold=100))
    import app.modules.ingest.bus as bus
    monkeypatch.setattr(bus, "lag", lambda group_id, topic: 999)
    assert scheduler._should_shed_analytics() is True


def test_analytics_job_skips_fn_entirely_while_shedding(monkeypatch):
    monkeypatch.setattr(scheduler, "_should_shed_analytics", lambda: True)
    called = []
    job = scheduler._analytics_job(lambda db: called.append(db))
    job()
    assert called == []


def test_analytics_job_runs_fn_when_not_shedding(monkeypatch):
    monkeypatch.setattr(scheduler, "_should_shed_analytics", lambda: False)
    fake_db = SimpleNamespace(rollback=lambda: None, close=lambda: None)
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: fake_db)
    called = []
    job = scheduler._analytics_job(lambda db: called.append(db))
    job()
    assert called == [fake_db]
