import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.db import SessionLocal
from app.core.scheduler import build_scheduler
from app.modules.alerts.router import router as alerts_router
from app.modules.chat.router import router as chat_router
from app.modules.delivery.router import router as delivery_router
from app.modules.analyst.router import router as analyst_router
from app.modules.drill.router import router as drill_router
from app.modules.fisherman.router import router as fisherman_router
from app.modules.forecast.router import router as forecast_router
from app.modules.geo.router import router as geo_router
from app.modules.ingest.router import router as ingest_router
from app.modules.ivr.router import router as ivr_router
from app.modules.narratives.router import router as narratives_router
from app.modules.routing.router import router as routing_router
from app.modules.sitrep.router import router as sitrep_router
from app.modules.whatsapp.router import router as whatsapp_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.modules.chat.corpus import seed_corpus
    from app.modules.inundation.seed import seed_elevation_cells
    from app.modules.routing.seed import seed_shelters
    from app.modules.sensors.service import sync_stations

    db = SessionLocal()
    try:
        sync_stations(db)
    except Exception:
        log.exception("Station sync failed at startup")
        db.rollback()
    finally:
        db.close()

    db = SessionLocal()
    try:
        seed_corpus(db)
    except Exception:
        log.exception("Chat corpus seed failed at startup")
        db.rollback()
    finally:
        db.close()

    db = SessionLocal()
    try:
        seed_shelters(db)
    except Exception:
        log.exception("Shelter seed failed at startup")
        db.rollback()
    finally:
        db.close()

    db = SessionLocal()
    try:
        seed_elevation_cells(db)
    except Exception:
        log.exception("Elevation cell seed failed at startup")
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
app.include_router(chat_router)
app.include_router(whatsapp_router)
app.include_router(ivr_router)
app.include_router(fisherman_router)
app.include_router(routing_router)
app.include_router(sitrep_router)
app.include_router(forecast_router)
app.include_router(narratives_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
