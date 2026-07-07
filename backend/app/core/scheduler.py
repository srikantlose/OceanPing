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
    from app.modules.scoring.service import rescore_recent
    from app.modules.sensors.service import detect_anomalies, poll_all

    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(_job(poll_all), "interval", minutes=settings.erddap_poll_minutes,
                      id="erddap_poll")
    scheduler.add_job(_job(detect_anomalies), "interval", minutes=settings.erddap_poll_minutes,
                      id="anomaly_detect")
    scheduler.add_job(_job(rescore_recent), "interval", minutes=settings.rescore_minutes,
                      id="rescore_recent")
    # One-shot initial poll so the map has sensor data right after startup.
    scheduler.add_job(_job(poll_all), id="erddap_poll_initial")
    return scheduler
