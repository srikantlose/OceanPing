import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.db import SessionLocal
from app.core.scheduler import build_scheduler
from app.modules.alerts.router import router as alerts_router
from app.modules.delivery.router import router as delivery_router
from app.modules.analyst.router import router as analyst_router
from app.modules.drill.router import router as drill_router
from app.modules.geo.router import router as geo_router
from app.modules.ingest.router import router as ingest_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.modules.sensors.service import sync_stations

    db = SessionLocal()
    try:
        sync_stations(db)
    except Exception:
        log.exception("Station sync failed at startup")
        db.rollback()
    finally:
        db.close()

    scheduler = build_scheduler()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="OceanPing", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev; tighten for deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest_router)
app.include_router(geo_router)
app.include_router(analyst_router)
app.include_router(alerts_router)
app.include_router(delivery_router)
app.include_router(drill_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
