# Phase 3 — Depth: Modeling, Ops Automation, Physical Edge, and the Service Split (months 7–10, gap plan)

**Status: 🟡 in progress.** Milestones 1 (inundation model) and 2 (auto-SITREPs) built —
see their "as built" sections below. Prereqs: phases 1–2 (alerts, delivery, satellite,
routing), both done.
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

## Milestone 1 — as built

Real data, not a stub: unlike the credential-gated satellite/WhatsApp/PFZ adapters
elsewhere in this project, a DEM extract is public and downloadable without an
account, so this milestone is the genuine bathtub model over real elevation data,
mirroring the Valhalla precedent (phase 2, milestone 6) — the gate is a one-time
data-prep step, not credentials.

- **DEM source**: Copernicus DEM GLO-30, served from a public AWS Open Data S3
  bucket (`copernicus-dem-30m`) over plain HTTPS — no account, no API key.
  `scripts/inundation/fetch_dem_extract.sh` downloads the two 1°×1° tiles
  covering a Chennai coastal pilot bbox (`80.10,12.85,80.40,13.30` — narrower
  than the routing OSM bbox since inundation only matters near the coast, but
  covers every named pilot location elsewhere in this project: Marina, Besant
  Nagar, Kasimedu, Injambakkam, Ennore) and clips/merges them with GDAL, run in
  a throwaway container (not baked into any long-lived image, same call as
  osmium-tool in the routing milestone).
- **Elevation table**: `scripts/inundation/build_elevation_cells.sh` enumerates
  H3 res-9 cells over the bbox (throwaway `python:3.12-slim` + pip-installed
  `h3`, no GDAL needed) and samples the DEM at each centroid via GDAL's Python
  bindings (throwaway `ghcr.io/osgeo/gdal` image, no `rasterio` dependency
  added anywhere). Output: `backend/app/modules/inundation/
  elevation_cells_chennai.json` — 14,382 real cells, committed like
  `routing/shelters_seed.json`, loaded into the new `elevation_cells` table by
  `inundation/seed.py` once at startup (unlike shelters this isn't
  analyst-editable, but "once, if empty" is still right — nothing to
  reconcile against a live edit). Some cells over open water carry small
  negative elevations, a known Copernicus DEM radar artifact — harmless here
  since those cells are already permanently "flooded" either way.
- **Bathtub model** (`inundation/engine.py`): pure function, no I/O — a cell
  floods once its elevation is at or below the water level; depth is the
  difference. Deliberately the simplest hydrologically-defensible model (no
  flow routing, no connectivity check); ANUGA hydrodynamic simulation remains
  the named upgrade path.
  `GET /map/inundation?level=` (in `geo/router.py`) exposes it publicly for
  "what if" queries — a preparedness slider on the map, not tied to any live
  reading.
- **Live wiring, gated on a real gauge reading**: `inundation/service.py`'s
  `predicted_flooded_cells()` looks up the freshest `water_level` sensor
  reading (`inundation_wire_hours`, default 2h) and applies the bathtub model
  — empty if nothing fresh exists. There is no live INCOIS tide gauge
  configured (`stations.json`'s `incois-chennai-tide` is disabled — see the
  phase-2 milestone-1 note), so in an untouched environment this stays a
  no-op until a real gauge is configured or a drill injects a reading — same
  credential/data-gated-degrade pattern as every other real integration here.
  - **Alerts**: `alerts/service.py` snapshots the predicted flooded-cell set
    onto the `Alert` row (new `predicted_flooded_cells` column) whenever an
    alert is created or its tier upgrades, gated on the hazard type actually
    having a water-level signal (`scoring/engine.py`'s `HAZARD_VARIABLES`) —
    oil spills never get a flood prediction attached. A fixed snapshot, not
    recomputed on every read, so a later tide change doesn't retroactively
    change what an already-issued alert claimed (same semantics `h3_cells`
    already has).
  - **Routing**: `routing/service.py::exclude_polygons()` unions the live
    predicted flooded cells into the same set as corroborated-incident and
    warning-alert cells — legitimate to include unconditionally since it's
    gauge + DEM data, not citizen reports, so it carries no escalation-gate
    concern.
- **Frontend**: `MapView.tsx` gets a blue flooded-cell layer, a "what if"
  water-level slider (`GET /map/inundation?level=`, debounced), a click popup
  showing depth, and the alert popup now shows a predicted-flooded-cell count
  when present.
- **Live-verified**: `scripts/drill.py` now checks `/map/inundation?level=2.6`
  against the drill's injected tide-gauge surge (real DEM data: 5,663–5,689
  coastal cells flood at that level) and asserts an auto-proposed
  coastal_flooding alert actually carries `predicted_flooded_cells`. Full
  drill run against a freshly reset dev DB (migrations 0001→0010 replayed
  clean) confirmed: elevation seed (14,382 cells), inundation endpoint, alert
  wiring, routing exclusion count including flood cells, and the existing
  hazard-enclosure fallback all working together end-to-end. 276 backend
  tests passing.
- **Not built** (explicit gaps, not oversights): DEM coverage is pilot-scoped
  (Chennai coastal strip only, same as OSM/PFZ/IVR pilot scoping elsewhere);
  no flow routing/connectivity check (a cell cut off from the sea by a ridge
  still "floods," same as a real bathtub); no forecast integration yet — the
  "gauge reading" driving live wiring is the current instantaneous reading,
  not a predicted future level (that's milestone 3's job); frontend slider
  wasn't visually verified in a browser in this environment (no browser
  automation tool available here) — verified via the live API responses and
  a successful Next.js dev-server render instead.

## Milestone 2 — as built

- **New module `modules/sitrep/`**: `engine.py` is a pure function
  (`build_sitrep(snapshot)`) that copies every number straight through from a
  data snapshot into an NDMA-style draft (title, one-paragraph summary,
  section dict) — it never invents or infers a figure, so an analyst
  reviewing a draft is checking wording, not arithmetic. `service.py` builds
  that snapshot from real DB state and owns the generate/file lifecycle;
  `router.py` exposes it to the analyst dashboard.
- **What's in a snapshot** (`service.py::build_snapshot`), all pulled fresh
  from the DB for the report's period: citizen report counts (total, by
  status, by hazard), incident counts (touched-in-period vs. newly first-seen
  — one query, split in Python), alerts (issued this period vs. currently
  active, tier/hazard/issued-by), shelter resources (open/total counts, known
  open capacity), and the audit chain's own integrity check
  (`scoring/audit.py::verify_chain`, reused as-is).
- **Hotspot movement** (the plan's fourth required category) is real, not a
  snapshot stub: each current hotspot from `geo/hotspots.py::compute_hotspots`
  is matched against the *previous* SITREP's hotspot list by dominant hazard +
  proximity (≤3 km, `geo/distance.py::haversine_km`) and tagged `new` or
  `persisting`; previous hotspots with no current match are listed as
  `cleared`. This needs no new table — each SITREP carries forward the
  hotspot list it was generated against, so the next one has a baseline to
  diff against. Live-verified: generating two SITREPs back to back over the
  drill's Marina flood cluster correctly tagged both hotspots `persisting` on
  the second call.
- **Cadence and windowing**: hourly by default (`sitrep_period_hours`), via
  `core/scheduler.py`'s existing APScheduler pattern (plus a startup one-shot,
  same as the ERDDAP/PFZ jobs). A period's `period_start` is the *previous*
  SITREP's `period_end` (falling back to `now - sitrep_period_hours` only for
  the very first one ever), so periods tile back-to-back with no gaps or
  overlap regardless of the configured cadence or restarts.
- **Audit-linkable, not analyst-editable**: `sitreps.data_snapshot_hash` is a
  sha256 of the exact snapshot dict the draft was built from
  (`service.py::snapshot_hash`), and both `sitrep.generated` and
  `sitrep.filed` audit-log entries carry that hash — so a filed SITREP is
  traceable back to the precise numbers behind it, the same audit-chain
  discipline every other decision in this app already gets. Filing
  (`POST /analyst/sitreps/{id}/file`) only flips status/filed_by/filed_at; it
  can never edit the drafted content, and a second file attempt on the same
  SITREP is rejected (409).
- **Frontend**: `AnalystDashboard.tsx` gets a SITREPs card — chronological
  list with status chips, a one-line summary per SITREP, a "File" button on
  drafts, an expandable raw-sections view, and a "Generate now" button for an
  analyst who doesn't want to wait for the hourly tick (the same
  `generate_sitrep()` the scheduler and the drill call).
- **Live-verified**: `scripts/drill.py` now generates a SITREP after its full
  sequence of reports/incidents/alerts/verification, independently
  recomputes the report count for the SITREP's own declared period from
  `/analyst/reports`, and asserts they match exactly — then files it and
  confirms the filed status and analyst attribution. Ran clean against the
  live stack (no dev-DB reset needed this time): 16 reports matched an
  independent recount, both real Marina hotspots correctly tagged
  `persisting` on a second generate call, audit chain intact over 117
  entries. 298 backend tests passing (up from 276).
- **Not built** (explicit gaps, not oversights): no casualty/relief-measure
  section (that data doesn't exist yet — it's the recovery module, milestone
  7); no PDF/Word export, `content` is structured JSON rendered by the
  dashboard; no automatic delivery/emailing of filed SITREPs to NDMA or
  district authorities (this app has no such integration yet); the frontend
  SITREPs card wasn't visually verified interactively in a browser in this
  environment (no browser automation tool available here) — verified via the
  live API responses (including the two-generation hotspot-movement check
  above) and a successful Next.js dev-server render instead.

## Milestones

1. Inundation model + alert/routing wiring (independent) — ✅ built, see above
2. Auto-SITREPs (independent, high agency value) — ✅ built, see above
3. Forecasting + propagation pre-alerts
4. Rumor tracker + alert drafting
5. Mobile app with offline queue (mesh spike separate)
6. CoastSnap + IoT pilot in one district
7. Recovery module
8. Service split + 50× drill exit test

## Verification

- Inundation: known DEM fixture → assert flooded-cell set at levels L1<L2 nests correctly.
  ✅ `test_inundation_engine.py`, plus a real-DEM live check in `scripts/drill.py`.
- SITREP: drill event → generated SITREP contains only verified-data numbers (assert
  against DB counts). ✅ `test_sitrep_engine.py` + `test_sitrep_service.py`, plus a real
  live check in `scripts/drill.py` (independently recomputed report count) and a
  two-generation hotspot-movement check against real data.
- Propagation: drill with directional report sequence → projected cells lie ahead of the
  front, pre-alert proposed for an unreported village cell.
- Split: replay identical drill on monolith vs. split deployment → identical end state
  (reports, incidents, alerts, audit chain length).
