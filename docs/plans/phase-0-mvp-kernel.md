# Phase 0 — MVP Kernel (as built)

**Status: ✅ built (July 2026).** This file is the current-state record: read it first in
any new session. It documents what exists, where, why it deviates from the original
blueprint, and what hardening remains.

## What the MVP does

Citizens report coastal hazards via Telegram bot or web form, in any language. Every
report gets a computed confidence score from four fused signals (reporter trust,
spatiotemporal coherence, instrument corroboration, media forensics). Reports dedup into
incidents; hotspots cluster on a live map; a live ERDDAP tide-gauge/buoy feed corroborates
claims via anomaly detection; analysts verify/reject from a dashboard; everything is
recorded in an append-only hash-chained audit log. A drill injector exercises the whole
pipeline synthetically.

## Architecture as implemented

```
Telegram bot ─┐
Web form ─────┤→ FastAPI monolith ──→ Postgres (PostGIS + TimescaleDB + pgvector)
ERDDAP poller ┘        │               Redis (rate limits, hotspot cache)
                       └→ GeoJSON/REST → Next.js (public map · report form · analyst dashboard)
```

Deliberate deviations from the blueprint (documented decision, July 2026):
- **Modular monolith instead of Kafka + microservices** — module seams mirror the future
  split; Kafka enters in phase 3 (decision gate in phase-2 plan).
- **Telegram instead of WhatsApp** (free API; WhatsApp is a phase-2 adapter).
- **Simple token login instead of Keycloak** (`app/core/security.py`, itsdangerous signed
  tokens, single seeded analyst user). Keycloak enters with the phase-3 split.
- **APScheduler in-process instead of a worker fleet** (`app/core/scheduler.py`).

## Module map (all under `backend/app/`)

| Path | Responsibility |
|---|---|
| `core/config.py` | All tunables as env-backed settings (weights thresholds, radii, windows) |
| `core/db.py`, `models.py` | SQLAlchemy 2 models — see data model below |
| `core/security.py` | Analyst login token issue/verify (`require_analyst` dependency) |
| `core/scheduler.py` | Jobs: ERDDAP poll, anomaly detect, periodic rescore |
| `modules/ingest/service.py` | **`create_report()` — THE single pipeline entry** for every channel: rate limits → reporter → NLP → H3 → media forensics → incident dedup → scoring → audit |
| `modules/ingest/router.py` | `POST /reports` (multipart), `GET /hazard-types` |
| `modules/ingest/bot_runner.py` | Telegram bot (long-poll, own container `--profile bot`), calls `create_report()` directly |
| `modules/ingest/media.py` | pHash recycled-media check, EXIF GPS/time forensics |
| `modules/nlp/classifier.py` | `classify()` / `embed()` — multilingual MiniLM embeddings vs. prototype phrases (`prototypes.py`), keyword fallback (`NLP_MODE=keyword`). **This is the swap seam for MuRIL.** |
| `modules/nlp/dedup.py` | `assign_incident()` — semantic+spatial+temporal merge into incidents |
| `modules/scoring/engine.py` | Pure scoring math: weights, coherence/instrument/media curves, `HAZARD_VARIABLES` corroboration map |
| `modules/scoring/service.py` | `rescore_report()` (escalation gate lives here), `apply_verification()` (trust ladder), `rescore_recent()` |
| `modules/scoring/audit.py` | `append_audit()` / `verify_chain()` — hash-chained audit log |
| `modules/geo/h3utils.py`, `hotspots.py`, `router.py` | H3 res-8, HDBSCAN hotspots (Redis-cached), public `/map/*` GeoJSON (verified-only, cell-fuzzed) |
| `modules/sensors/erddap.py`, `service.py`, `anomaly.py`, `stations.json` | Config-driven ERDDAP tabledap poller → Timescale hypertable → rolling z-score anomalies |
| `modules/analyst/router.py` | Login, full-detail queue, verify/reject/rescore, incidents, audit endpoints, media serving |
| `modules/drill/router.py` + `scripts/drill.py` | Synthetic readings injection + forced tick; stdlib-only end-to-end drill script |

Frontend (`frontend/`): Next.js 15 app router — `components/MapView.tsx` (public map:
hotspots, incident cells, verified reports, stations w/ sparklines), `ReportForm.tsx`,
`AnalystDashboard.tsx` (queue, confidence breakdown bars, verify/reject, audit check).
Palette/labels centralized in `lib/palette.ts`.

## Data model (Postgres; `backend/app/models.py`; migration `alembic/versions/0001_initial.py`)

`reporters` (trust ladder) · `reports` (geom + h3_cell + confidence_components JSONB +
pgvector embedding) · `incidents` (merged duplicates; centroid embedding) · `media_assets`
(phash, exif) · `stations` / `sensor_readings` (hypertable) / `station_anomalies` ·
`verifications` · `audit_log` (prev_hash → hash chain).

## Scoring spec (engine.py)

`confidence = 0.25·trust + 0.30·coherence + 0.30·instrument + 0.15·media`
- coherence: distinct other reporters, same hazard, H3 cell ±1 ring, ±30 min → 0 / 0.4 / +0.2 each, cap 1.0
- instrument: strongest hazard-consistent active anomaly within 25 km; |z|=2.5→0, |z|≥5→1
- media: none=0.5 · recycled pHash=0.0 · EXIF GPS >5 km=0.2 · EXIF time >6 h=0.4 · no EXIF=0.6 · consistent=1.0
- **Gate:** `unverified→corroborated` requires confidence ≥ 0.6 **and** instrument > 0;
  `→verified` only via `apply_verification()` (analyst). Public map = verified only.

## API surface (see `http://localhost:8000/docs`)

Public: `POST /reports`, `GET /hazard-types`, `GET /map/{reports,incidents,hotspots,stations}`, `GET /healthz`.
Analyst (Bearer token from `POST /auth/login`): `GET /analyst/reports`, `POST /analyst/reports/{id}/{verify|reject|rescore}`, `GET /analyst/incidents`, `GET /analyst/audit`, `GET /analyst/audit/verify`, `GET /analyst/media/{id}`.
Drill (analyst-gated): `POST /drill/inject-readings`, `POST /drill/tick`.

## Running it

```bash
cp .env.example .env && docker compose up --build     # db, redis, backend, frontend
docker compose --profile bot up -d                    # + Telegram bot (needs TELEGRAM_BOT_TOKEN)
python scripts/drill.py                               # synthetic end-to-end drill
cd backend && pytest                                  # 26 unit tests
```
Frontend :3000 · API :8000 · analyst login `analyst` / `oceanping-dev` (env-overridable).
First embedding-model load downloads ~470 MB to the `hfcache` volume; offline → keyword fallback.

## Remaining hardening (small, do before/alongside phase 1)

- [ ] `git init` + initial commit + GitHub Actions CI (pytest + frontend build)
- [ ] Real INCOIS ERDDAP dataset IDs in `stations.json` (template entry is disabled)
- [ ] Public deployment story (single VPS compose or Fly.io/Render), CORS tightening
- [ ] Backend Docker image slimming (torch CPU already; consider ONNX for MiniLM)
- [ ] Rate-limit tuning + IP fallback identity for web reports without stable client_id
