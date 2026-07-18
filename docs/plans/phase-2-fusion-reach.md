# Phase 2 — Fusion & Reach (blueprint weeks 15–24, gap plan)

**Status: 🟡 in progress (July 2026).** Milestone 2 (satellite + six-signal rebalance)
built. Milestone 1 (INCOIS real datasets) investigated and found blocked on external data
availability — see below — deferred rather than faked. Prereqs: phase 0 (built); phase 1
delivery worker for new channels.
**Independent items** (can land before phase 1 completes): satellite corroboration, INCOIS
dataset IDs, station config → DB, RAG chatbot.

## Milestone 1 — investigated, deferred (not built)

Live-queried `https://erddap.incois.gov.in/erddap/tabledap/` (the exact URL this plan
names) before writing any code, since "fill real dataset IDs" only means something if a
real matching dataset exists. It doesn't, as of this investigation:

- The server is reachable and has 18 real datasets, but only **one** is a `tabledap`
  (station/timeseries CSV — the shape `sensors/erddap.py` parses) dataset:
  `Indian_ARGO_Floats`. Everything else is `griddap` (gridded satellite/model products —
  AMSR-E, ASCAT wind, OceanSat OCM chlorophyll, SST), a fundamentally different query
  shape than the per-station CSV `build_url()`/`fetch_readings()` expect.
- `Indian_ARGO_Floats` is deep-ocean ARGO float profile data (subsurface temperature/
  salinity/pressure from drifting floats), not coastal tide/wave stations — a poor fit for
  "corroborate a coastal citizen report" regardless of format. Its data also ends
  2025-04-23 — over a year stale as of this check, not a live feed.
- **There is no real INCOIS tide-gauge/water-level tabledap dataset to fill into the
  `incois-chennai-tide` stub in `stations.json`.** It stays a disabled template with its
  honest `REPLACE_WITH_DATASET_ID` placeholder; the NDBC demo station remains the only
  live instrument feed. `station_configs` table + CRUD (the other half of this milestone)
  wasn't built either, since it's only worth doing alongside real data to manage.

**Revisit when:** INCOIS opens a tide-gauge dataset on this ERDDAP instance, or a
different real-time coastal feed is identified (a state tide-gauge network, a different
INCOIS API/portal outside ERDDAP, etc.). The griddap satellite/wind/chlorophyll datasets
already on this server are a closer match to milestone 2's satellite work than to
milestone 1's station-corroboration goal.

## Milestone 2 — as built

Satellite corroboration and the six-signal rebalance are live:

- `backend/app/models.py` — `SatelliteObservation` (incident_id, provider, recipe, score,
  scene_time, scene_url). Migration `0006_satellite.py` (follows `0004`'s
  `Base.metadata.create_all` pattern for a new table).
- `backend/app/modules/satellite/providers.py` — `SatelliteProvider` Protocol +
  `HAZARD_RECIPES` (oil_spill, algal_bloom, coastal_flooding, storm_surge only —
  deliberately excludes fast hazards like tsunami/rip_current, so satellite can never gate
  them). `StubProvider` is deterministic (sha256 of `incident_id:recipe`) and needs no
  credentials — it's the only provider actually exercised here. `SentinelHubProvider` /
  `EarthEngineProvider` have the real interface shape and a real credential gate (same
  pattern as `delivery/adapters.py`'s Twilio/Exotel adapters), but the actual scene-scoring
  recipe (deriving a dark-slick/NDCI/water-extent score from raw Sentinel imagery) is a
  raster-processing task this environment has no account or way to verify — deferred
  rather than shipped as unverified "real" code, same honest-scoping call as milestones 4
  and 5 made elsewhere in this project.
- `backend/app/modules/satellite/service.py::poll_satellite()` — for each incident seen in
  the last `satellite_active_incident_hours` (default 24h) whose hazard has a recipe, asks
  the configured provider for an observation and records it. Provider exceptions are
  caught per-incident (mirrors `sensors/service.py`'s resilience) so one bad call can't
  drop the rest of the batch.
- `backend/app/core/scheduler.py` — new `satellite_poll` job (`satellite_poll_minutes`,
  default 60 — satellite passes are hours apart, this doesn't need ERDDAP's cadence).
  `backend/app/modules/drill/router.py::/drill/tick` also force-runs it, same as anomaly
  detection, so drills don't wait an hour.
- `backend/app/modules/scoring/engine.py` — `WEIGHTS` rebalanced to the blueprint's
  six-signal table (trust .20, coherence .25, instrument .25, media .15, satellite .10,
  account_device .05). New pure functions `satellite_score()` (strongest observation, 0
  with none yet — absence of evidence isn't evidence against, same call `instrument_score`
  already makes) and `account_device_score()` (older accounts score higher, saturating at a
  week; a burst of reports from the same account inside the existing rate-limit window
  pulls the score down, capped at a 0.5 penalty).
- `backend/app/modules/scoring/service.py` — `rescore_report()` now also gathers
  `_satellite_observations()` (queries `SatelliteObservation` by incident + recipe) and
  `_account_device_score()` (reporter age + the same Redis `rl:rep:` burst counter
  `ingest/service.py`'s rate limiter already maintains — no new I/O path). **The
  escalation gate now reads `instrument > 0 or satellite > 0`** instead of instrument
  alone: oil_spill and algal_bloom have zero instrument variables (`HAZARD_VARIABLES` was
  already commented "needs satellite/CV" before this milestone), so without this change
  satellite could contribute to their confidence score but could never actually let them
  reach "corroborated" — the gate change is what makes satellite a real second
  corroboration stream rather than a number that never matters. Fast hazards
  (tsunami/rip_current/high_waves) have no satellite recipe at all, so their gate is
  unchanged — still instrument-only, never waits on an hours-latency scene.
- `frontend/components/AnalystDashboard.tsx` — `COMPONENT_LABELS` gained `satellite` and
  `account_device` so both render as confidence bars; a new detail line surfaces satellite
  observations the same way corroborating instrument anomalies already do.
- `scripts/drill.py` — two oil_spill report texts added (oil_spill has no instrument
  signal at all, so it's the clearest live proof satellite corroboration works); the
  post-tick display now prints `sat=`/`acct=` alongside the existing components; a new
  assertion confirms every oil_spill report picked up a satellite observation and that its
  instrument component is exactly 0.
- Tests: `test_satellite_providers.py` (StubProvider determinism/range, credential-gated
  skip for both real provider shells, HAZARD_RECIPES excludes fast hazards),
  `test_satellite_service.py` (recipe filtering, provider-exception resilience, `None`
  observations skipped — fake db/provider objects, no real DB touched, per this suite's
  convention), `test_scoring_engine.py` additions (`satellite_score`,
  `account_device_score`, updated six-weight `combine()` expectations), `test_scoring_
  service.py` additions (`_satellite_observations` incident/recipe filtering and row
  serialization, `_account_device_score`'s Redis-backed and Redis-unavailable paths).

**Verified live, not just under mocks:** rebuilt both images (build-time `COPY`, same as
every prior milestone) and ran `scripts/drill.py` clean end-to-end. Confirmed via the
drill's own new assertions that both oil_spill reports got a real satellite observation
each (`instr=0.00`, `sat=0.64`) — the only corroboration path that hazard has. Checked
backend logs for the run: no exceptions from `poll_satellite` or the rescore path.
Confirmed the scheduler registered all five jobs (including `satellite_poll`) cleanly at
startup. Confirmed the frontend's compiled JS bundle contains the new `Account/device`
label (a build-time proxy check — no browser-automation tool was available in this
session to click through the dashboard itself, same limitation noted in milestone 5).

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
- ✅ **Realized** (not just a risk anymore): INCOIS's public ERDDAP has no real coastal
  tide-gauge tabledap dataset as of this investigation (see milestone 1) — the "fill in
  real dataset IDs" item is blocked on external data availability, not on our code.
- INCOIS ERDDAP flakiness: poller already fails soft per-station; keep NOAA demo station
  as canary.
- Valhalla memory footprint: pilot-district extracts only, not all-India.

## Milestones

1. 🔲 INCOIS real datasets + station config in DB — investigated, blocked: no real
   coastal tide-gauge dataset exists on INCOIS's public ERDDAP (see milestone 1's "as
   built" section above). Revisit if that changes.
2. ✅ Satellite module with StubProvider + six-signal rebalance
3. RAG chatbot (web + Telegram)
4. Channel-agnostic conversation core + WhatsApp adapter + IVR webhook
5. Fisherman mode (roles, PFZ surfaces)
6. Valhalla routing + shelters + public map routing UI

## Verification

- ✅ Drill additions: synthetic oil-spill reports + StubProvider satellite observation →
  satellite component appears in `confidence_components`, instrument stays exactly 0,
  and the assertion is live in `scripts/drill.py` (not just a mocked unit test); six-weight
  tests updated in `test_scoring_engine.py`.
- Chat eval set: ~30 questions incl. adversarial ("should I evacuate?") — assert fallback
  behavior; retrieval-threshold unit test.
- IVR: simulated webhook payload → report with `source="ivr"` and correct village geocode.
- Routing: request from a point inside a drill flood zone → route avoids closed cells
  (assert no polyline vertex inside excluded polygons).
