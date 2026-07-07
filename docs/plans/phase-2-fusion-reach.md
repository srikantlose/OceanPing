# Phase 2 — Fusion & Reach (blueprint weeks 15–24, gap plan)

**Status: 🔲 planned.** Prereqs: phase 0 (built); phase 1 delivery worker for new channels.
**Independent items** (can land before phase 1 completes): satellite corroboration, INCOIS
dataset IDs, station config → DB, RAG chatbot.

## Goals

Add the third and fourth verification streams (satellite, richer instruments), meet people
where they are (WhatsApp, IVR, fishermen), and ship the first "gives before it asks"
features that keep the platform alive between disasters.

## Already built (do not rebuild)

- Generic ERDDAP poller + anomaly detection — `backend/app/modules/sensors/` (config-driven;
  INCOIS is a disabled template entry in `stations.json`)
- Scoring component seam — `modules/scoring/engine.py::WEIGHTS` + `HAZARD_VARIABLES`;
  `service.py::rescore_report` gathers components
- Single ingest pipeline for any channel — `modules/ingest/service.py::create_report()`
- Bot conversation flow to copy for WhatsApp — `modules/ingest/bot_runner.py`
- pgvector already installed and used (report embeddings) — reuse for RAG

## Gap work breakdown

### 1. Satellite corroboration — new module `backend/app/modules/satellite/`

- Scheduled job (extend `core/scheduler.py`): for each active incident (last 24 h), queue a
  scene lookup over its H3 footprint. Providers behind one interface:
  `SentinelHubProvider` / `EarthEngineProvider` / `StubProvider` (local dev).
- Per-hazard recipes (config, mirroring `HAZARD_VARIABLES`): oil_spill → Sentinel-1 SAR
  dark-slick score; algal_bloom → Sentinel-2 chlorophyll/NDCI anomaly; large-scale
  coastal_flooding → Sentinel-1 water-extent change. Output: `satellite_observations`
  rows (incident_id, provider, score 0–1, scene metadata, url).
- Scoring: add `satellite` component; rebalance toward the blueprint's six-signal table —
  trust .20, coherence .25, instrument .25, media .15, satellite .10, account/device .05.
  The account/device signal (burst detection, account age) is a small pure function over
  existing `reporters` + Redis counters. Update `engine.WEIGHTS` + tests together;
  satellite latency is hours — it corroborates slow hazards, never gates fast ones.

### 2. INCOIS depth (independent, small)

- Fill real dataset IDs from `https://erddap.incois.gov.in/erddap/tabledap/` into station
  config; verify units/variable names against `sensors/erddap.py` parsing.
- Move station config from `stations.json` to a `station_configs` table + analyst CRUD
  endpoints (keep JSON file as seed import). The poller reads DB.
- PFZ (Potential Fishing Zone) advisories: fetcher for INCOIS PFZ product → `pfz_advisories`
  table → consumed by fisherman mode + chatbot.

### 3. Reach: WhatsApp + IVR — extends `modules/ingest/`

- **WhatsApp Business Cloud API adapter**: webhook router (`/webhooks/whatsapp`) mapping
  messages to the same state machine as the Telegram bot — extract the conversation flow in
  `bot_runner.py` into a channel-agnostic `report_conversation.py` first, then both bots are
  thin adapters. Needs Meta business verification (procurement risk — start early, ship
  Telegram-first everywhere).
- **IVR / missed-call**: Exotel (or Twilio) webhook → keypad-driven flow (language → hazard
  digit → location = caller's registered village or cell-tower area) → `create_report()`
  with `source="ivr"`. Keep an `ivr_scripts/` doc with the exact prompt tree per language.

### 4. Fisherman mode

- `reporters.role = "fisherman"` (column exists from phase 1), registered via cooperative
  lists; elevated starting trust 0.65 in `ingest/service.py::get_or_create_reporter`.
- Give-before-ask surfaces: PFZ + sea-state card as bot commands (`/sea`, `/pfz`) and a
  `frontend/app/sea/page.tsx` page — data from `pfz_advisories` + latest station readings
  (`/map/stations` already serves series).
- Voice-first: phase 1 Whisper path already covers it; add Tamil/Telugu prompt localization
  to the bot strings (extract strings table).

### 5. RAG chatbot — new module `backend/app/modules/chat/`

- Corpus: INCOIS advisories (scraped/PFZ), active alerts, shelter list, hazard FAQ —
  chunked into a `rag_documents` table with pgvector embeddings (reuse
  `nlp/classifier.py::embed`).
- LLM adapter interface; default Anthropic API (`claude-sonnet-5`), config-keyed; strict
  system prompt: answer only from retrieved context, else return the helpline fallback —
  enforce with a retrieval-score threshold *in code*, not prompt-only.
- Endpoints `POST /chat` (+ bot `/ask`). Log Q/A pairs for review; never answer
  evacuation-directive questions — canned "follow official alerts" + current alert lookup.

### 6. Evacuation routing — new module `backend/app/modules/routing/`

- Valhalla container in compose + scripted OSM extract download for pilot districts.
- `shelters` table (geom, capacity, status) + analyst CRUD + map layer.
- Route API: origin → nearest open shelter, with Valhalla `exclude_polygons` built from
  active incident cells + warning-alert geometry (`geo/h3utils.py::cell_polygon`).
- Frontend: "Route to safety" on the public map (geolocate → polyline + shelter card).

## Data model changes

`satellite_observations` · `station_configs` · `pfz_advisories` · `rag_documents` ·
`shelters` · `chat_logs`. Weight rebalance is a code+test change, no migration.

## New infra

Valhalla container (+~2 GB OSM/tiles volume). No Kafka yet — **decision gate**: introduce
Redpanda only when (a) ≥3 always-on inbound channels, or (b) sustained >50 reports/min in
drills, or (c) the phase-3 service split begins. Record the decision here when taken.

## External dependencies & risks

- Meta WhatsApp verification: weeks of lead time — start first, don't block the phase.
- Earth Engine/Sentinel Hub quotas & auth: StubProvider keeps local dev free; budget for
  Sentinel Hub if EE research terms don't fit.
- INCOIS ERDDAP flakiness: poller already fails soft per-station; keep NOAA demo station
  as canary.
- Valhalla memory footprint: pilot-district extracts only, not all-India.

## Milestones

1. INCOIS real datasets + station config in DB (independent, do first)
2. Satellite module with StubProvider + six-signal rebalance
3. RAG chatbot (web + Telegram)
4. Channel-agnostic conversation core + WhatsApp adapter + IVR webhook
5. Fisherman mode (roles, PFZ surfaces)
6. Valhalla routing + shelters + public map routing UI

## Verification

- Drill additions: synthetic oil-spill incident + stub satellite observation → satellite
  component appears in `confidence_components`; six-weight tests updated.
- Chat eval set: ~30 questions incl. adversarial ("should I evacuate?") — assert fallback
  behavior; retrieval-threshold unit test.
- IVR: simulated webhook payload → report with `source="ivr"` and correct village geocode.
- Routing: request from a point inside a drill flood zone → route avoids closed cells
  (assert no polyline vertex inside excluded polygons).
