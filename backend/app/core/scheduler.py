import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import get_settings
from app.core.db import SessionLocal

log = logging.getLogger(__name__)


def _job(fn):
    def run():
        db = SessionLocal()
        try:
            fn(db)
        except Exception:
            log.exception("Scheduled job %s failed", fn.__name__)
            db.rollback()
        finally:
            db.close()

    run.__name__ = fn.__name__
    return run


def _should_shed_analytics() -> bool:
    """True when the ingest pipeline is backed up enough that this tick's
    analytics job (SITREPs, forecasts, narratives, satellite polling — never
    the ingest/dedup/scoring/alerting path itself) should skip itself rather
    than compete with report processing for DB/CPU capacity (phase 3,
    milestone 8's "ingestion + alerting protected under load-shed" exit
    criterion). Only meaningful in bus pipeline mode: inline mode has no
    consumer-lag concept and never sheds, matching every prior milestone's
    unthrottled behavior."""
    settings = get_settings()
    if settings.pipeline_mode != "bus":
        return False
    from app.modules.ingest import bus  # lazy: only bus mode needs a Kafka client

    backlog = bus.lag(group_id="nlp", topic=bus.TOPIC_RAW)
    if backlog > settings.load_shed_lag_threshold:
        log.warning("Load-shed: nlp consumer lag %d exceeds threshold %d; deferring analytics tick",
                    backlog, settings.load_shed_lag_threshold)
        return True
    return False


def _analytics_job(fn):
    """Same as _job(), plus the load-shed check above. Applied only to jobs
    the plan itself calls out as deferrable analytics, never to ingestion,
    scoring, alerting, or the retention purge."""
    def run():
        if _should_shed_analytics():
            return
        db = SessionLocal()
        try:
            fn(db)
        except Exception:
            log.exception("Scheduled job %s failed", fn.__name__)
            db.rollback()
        finally:
            db.close()

    run.__name__ = fn.__name__
    return run


def build_scheduler() -> BackgroundScheduler:
    from app.modules.fisherman.service import refresh_pfz_advisories
    from app.modules.forecast.service import (
        generate_propagation_forecasts,
        generate_sensor_forecasts,
        validate_forecasts,
    )
    from app.modules.narratives.service import detect_narratives
    from app.modules.opendata.service import anonymize_expired_reports
    from app.modules.recovery.service import purge_expired_missing_persons
    from app.modules.satellite.service import poll_satellite
    from app.modules.scoring.service import rescore_recent
    from app.modules.sensors.service import detect_anomalies, poll_all
    from app.modules.sitrep.service import generate_sitrep

    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    # Core ingest/alerting path — never shed, regardless of pipeline mode.
    scheduler.add_job(_job(poll_all), "interval", minutes=settings.erddap_poll_minutes,
                      id="erddap_poll")
    scheduler.add_job(_job(detect_anomalies), "interval", minutes=settings.erddap_poll_minutes,
                      id="anomaly_detect")
    scheduler.add_job(_job(rescore_recent), "interval", minutes=settings.rescore_minutes,
                      id="rescore_recent")
    scheduler.add_job(_job(purge_expired_missing_persons), "interval", hours=24,
                      id="missing_person_retention_purge")
    scheduler.add_job(_job(anonymize_expired_reports), "interval", hours=24,
                      id="opendata_location_retention")
    # Analytics jobs (phase 3, milestone 8's "defer analytics consumers" exit
    # criterion) — deferred for a tick when the bus pipeline's nlp consumer
    # is backed up past load_shed_lag_threshold; see _should_shed_analytics().
    # No-op in inline mode (the default), same as every prior milestone.
    scheduler.add_job(_analytics_job(poll_satellite), "interval", minutes=settings.satellite_poll_minutes,
                      id="satellite_poll")
    scheduler.add_job(_analytics_job(refresh_pfz_advisories), "interval", hours=settings.pfz_refresh_hours,
                      id="pfz_refresh")
    scheduler.add_job(_analytics_job(generate_sitrep), "interval", hours=settings.sitrep_period_hours,
                      id="sitrep_generate")
    scheduler.add_job(_analytics_job(generate_sensor_forecasts), "interval", minutes=settings.forecast_interval_minutes,
                      id="forecast_sensor_generate")
    scheduler.add_job(_analytics_job(generate_propagation_forecasts), "interval", minutes=settings.forecast_interval_minutes,
                      id="forecast_propagation_generate")
    scheduler.add_job(_analytics_job(validate_forecasts), "interval", minutes=settings.forecast_interval_minutes,
                      id="forecast_validate")
    scheduler.add_job(_analytics_job(detect_narratives), "interval", minutes=settings.narrative_interval_minutes,
                      id="narrative_detect")
    # One-shot initial jobs so the map/sea page have data right after startup.
    scheduler.add_job(_job(poll_all), id="erddap_poll_initial")
    scheduler.add_job(_job(refresh_pfz_advisories), id="pfz_refresh_initial")
    scheduler.add_job(_job(generate_sitrep), id="sitrep_generate_initial")
    scheduler.add_job(_job(generate_sensor_forecasts), id="forecast_sensor_generate_initial")
    scheduler.add_job(_job(generate_propagation_forecasts), id="forecast_propagation_generate_initial")
    scheduler.add_job(_job(detect_narratives), id="narrative_detect_initial")
    return scheduler
