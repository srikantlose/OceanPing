# Phase 3 — Depth: Modeling, Ops Automation, Physical Edge, and the Service Split (months 7–10, gap plan)

**Status: 🔲 planned.** Prereqs: phases 1–2 (alerts, delivery, satellite, routing).
**Independent items**: inundation model, auto-SITREPs, CoastSnap/IoT pilot, drill scale-up.

## Goals

Predict instead of just detect (inundation, propagation, forecasts); automate the analyst's
paperwork (SITREPs, rumor response); extend the sensing edge into the physical world
(mobile app + mesh, CoastSnap, LoRaWAN); and pay the architecture debt deliberately — this
is the phase where the monolith splits.

## Already built to lean on

- Timescale hypertable + anomaly pipeline — `backend/app/modules/sensors/`
- Incident/hotspot time-sequenced clusters — `modules/geo/hotspots.py`, `incidents` table
- Drill mode — `modules/drill/router.py`, `scripts/drill.py` (extend, don't fork)
- Audit chain + verified-event dataset (feeds SITREPs and insurance later)
- H3 machinery for all geometry — `modules/geo/h3utils.py`

## Gap work breakdown

### 1. Inundation model — new module `modules/inundation/`

- Ingest CartoDEM/Bhuvan DEM tiles for pilot districts → per-H3-cell (res 9/10) elevation
  stats table (offline preprocessing script, rasterio).
- Bathtub model: given water level (forecast or gauge reading), return flooded cell set +
  depth — pure function over the elevation table; expose `GET /map/inundation?level=`.
- Wire to alerts: warning composer shows predicted flooded cells for the alert's gauge
  forecast; routing (`modules/routing/`) consumes the same cell set for closures.
- Upgrade path note: ANUGA hydrodynamic sim per priority district — out of scope here.

### 2. Digital twin & timeline

- CesiumJS view (`frontend/app/twin/page.tsx`): terrain + inundation rendered at a chosen
  surge height — preparedness/briefing tool ("show me +1.5 m in this town").
- Timeline scrubber on the analyst dashboard: replay any window from existing tables
  (reports, anomalies, alerts, audit) — API `GET /analyst/replay?from=&to=` returning
  keyframed state; drives post-mortems and training.

### 3. Forecasting — extends `modules/sensors/`

- Timescale continuous aggregates per station/variable; short-horizon water-level/sea-state
  forecasts: start with Prophet (cheap, explainable), TFT later if it earns its keep.
- Hazard-front propagation: fit a moving front to time-ordered report clusters (incident
  cell sequence), project 1–3 h → pre-alert cells *ahead* of reports; render as dashed
  "projected" layer, and feed watch-tier proposals (never auto-warning).
- Forecast validation loop: score citizen reports against prior forecasts; public
  per-district "how right were we" metric endpoint.

### 4. LLM ops layer — extends `modules/chat/` (adapter from phase 2)

- **Auto-SITREPs**: hourly draft in NDMA format from verified data only (counts, hotspot
  movement, alerts issued, resources) → analyst one-click review/file; store in `sitreps`
  with the generating data snapshot hash (audit-linkable). The killer feature for agency
  adoption — prioritize.
- **Rumor tracker**: cluster inbound text (existing embeddings) into narratives; flag
  narratives contradicting instrument state (e.g. "another wave coming" while gauges flat);
  draft correction message for analyst approval → delivery fan-out.
- **Alert drafting**: per-tier, per-language, per-channel-length variants; human-approved
  at warning tier always.

### 5. Post-disaster mode — new module `modules/recovery/`

- Damage assessment: photo reports post-event → CV adapter (YOLOv8/SAM, same lazy-load
  pattern as `nlp/classifier.py`) → damage class + severity → damage map layer.
- Relief requests with fulfillment status; mutual-aid board (offers/needs + proximity
  match); missing/found-person registry (photo + fuzzy name match, strict privacy: analyst
  visibility only, retention limit).

### 6. Physical edge

- **React Native app** (`mobile/`, Expo): offline-first SQLite queue → existing
  `POST /reports`; map packs; Mark Safe; this is where CRDT sync and **BLE/Wi-Fi Direct
  mesh relay** land (encrypted report bundles, hop metadata honored by ingest timestamps —
  `create_report(created_at=…)` already accepts client timestamps).
- **CoastSnap stations**: fixed-position photo cradles; per-station ingest path with exact
  frame registration → shoreline/water-line pixel series → erosion time series (public
  "coastline change" explorer page).
- **LoRaWAN IoT pilot**: EMQX (MQTT) container → bridge service → `sensor_readings` (same
  hypertable + anomaly path as ERDDAP; a node is just a station with provider `iot`).

### 7. The architecture split (do LAST in this phase, as one tracked effort)

- Introduce Redpanda; `create_report()` becomes: gateway validates → produce
  `reports.raw` → consumers (nlp, scoring, dedup) as separate deployments of the same
  codebase (consumer groups), each owning its tables. Module seams already match — the
  split is repackaging, not rewriting.
- k3s manifests (or keep compose if single-node pilot is holding — decide on real load),
  Keycloak replaces `core/security.py` tokens (OIDC; analyst roles, DPO role), MinIO
  replaces the media volume, OpenSearch for report/social full-text.
- Exit criterion: drill at 50× MVP load (extend `scripts/drill.py` with a `--scale` flag)
  passes with ingestion + alerting protected under load-shed (defer analytics consumers).

## Data model changes

`elevation_cells` · `forecasts` · `sitreps` · `narratives` · `damage_assessments` ·
`relief_requests` · `aid_offers` · `missing_persons` · `coastsnap_stations/_frames`.

## New infra

Redpanda, Keycloak, MinIO, OpenSearch, EMQX (each enters only with its feature). Mobile
app toolchain (Expo/EAS).

## External dependencies & risks

- CartoDEM/Bhuvan access + licensing; fallback: SRTM/Copernicus 30 m DEM (coarser).
- CV model quality on Indian coastal damage imagery — budget hand-labeling.
- Mesh relay is research-grade: timebox a spike; ship offline queue first (mesh is
  additive).
- Split risk: freeze features during the cutover; run monolith + consumers in parallel
  against drills before switching ingest.

## Milestones

1. Inundation model + alert/routing wiring (independent)
2. Auto-SITREPs (independent, high agency value)
3. Forecasting + propagation pre-alerts
4. Rumor tracker + alert drafting
5. Mobile app with offline queue (mesh spike separate)
6. CoastSnap + IoT pilot in one district
7. Recovery module
8. Service split + 50× drill exit test

## Verification

- Inundation: known DEM fixture → assert flooded-cell set at levels L1<L2 nests correctly.
- SITREP: drill event → generated SITREP contains only verified-data numbers (assert
  against DB counts).
- Propagation: drill with directional report sequence → projected cells lie ahead of the
  front, pre-alert proposed for an unreported village cell.
- Split: replay identical drill on monolith vs. split deployment → identical end state
  (reports, incidents, alerts, audit chain length).
