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


def build_scheduler() -> BackgroundScheduler:
    from app.modules.fisherman.service import refresh_pfz_advisories
    from app.modules.forecast.service import (
        generate_propagation_forecasts,
        generate_sensor_forecasts,
        validate_forecasts,
    )
    from app.modules.narratives.service import detect_narratives
    from app.modules.satellite.service import poll_satellite
    from app.modules.scoring.service import rescore_recent
    from app.modules.sensors.service import detect_anomalies, poll_all
    from app.modules.sitrep.service import generate_sitrep

    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(_job(poll_all), "interval", minutes=settings.erddap_poll_minutes,
                      id="erddap_poll")
    scheduler.add_job(_job(detect_anomalies), "interval", minutes=settings.erddap_poll_minutes,
                      id="anomaly_detect")
    scheduler.add_job(_job(rescore_recent), "interval", minutes=settings.rescore_minutes,
                      id="rescore_recent")
    scheduler.add_job(_job(poll_satellite), "interval", minutes=settings.satellite_poll_minutes,
                      id="satellite_poll")
    scheduler.add_job(_job(refresh_pfz_advisories), "interval", hours=settings.pfz_refresh_hours,
                      id="pfz_refresh")
    scheduler.add_job(_job(generate_sitrep), "interval", hours=settings.sitrep_period_hours,
                      id="sitrep_generate")
    scheduler.add_job(_job(generate_sensor_forecasts), "interval", minutes=settings.forecast_interval_minutes,
                      id="forecast_sensor_generate")
    scheduler.add_job(_job(generate_propagation_forecasts), "interval", minutes=settings.forecast_interval_minutes,
                      id="forecast_propagation_generate")
    scheduler.add_job(_job(validate_forecasts), "interval", minutes=settings.forecast_interval_minutes,
                      id="forecast_validate")
    scheduler.add_job(_job(detect_narratives), "interval", minutes=settings.narrative_interval_minutes,
                      id="narrative_detect")
    # One-shot initial jobs so the map/sea page have data right after startup.
    scheduler.add_job(_job(poll_all), id="erddap_poll_initial")
    scheduler.add_job(_job(refresh_pfz_advisories), id="pfz_refresh_initial")
    scheduler.add_job(_job(generate_sitrep), id="sitrep_generate_initial")
    scheduler.add_job(_job(generate_sensor_forecasts), id="forecast_sensor_generate_initial")
    scheduler.add_job(_job(generate_propagation_forecasts), id="forecast_propagation_generate_initial")
    scheduler.add_job(_job(detect_narratives), id="narrative_detect_initial")
    return scheduler
