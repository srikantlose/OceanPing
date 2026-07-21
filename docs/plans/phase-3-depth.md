# Phase 3 — Depth: Modeling, Ops Automation, Physical Edge, and the Service Split (months 7–10, gap plan)

**Status: 🟡 in progress.** Milestones 1 (inundation model), 2 (auto-SITREPs), 3
(forecasting + propagation pre-alerts), and 4 (rumor tracker + alert drafting) built —
see their "as built" sections below.
Prereqs: phases 1–2 (alerts, delivery, satellite, routing), both done.
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

## Milestone 3 — as built

- **New module `modules/forecast/`**, same `engine.py` (pure) / `service.py`
  (DB I/O) / `router.py` (thin) split as every other module here. Two
  independent forecast kinds share one `forecasts` table (`kind` discriminator
  — `sensor` | `propagation`), the same pattern `sitreps`/`alerts` use for a
  JSONB `content` payload plus a `validation` field filled in later.
- **Sensor forecasting deliberately isn't Prophet**, despite the plan naming
  it: harmonic-trend least-squares regression (linear trend + M2 semidiurnal
  12.42h / K1 diurnal 23.93h tidal constituents, a single `numpy.linalg.lstsq`
  call) instead. Prophet's Stan-compilation backend is heavy for a pilot
  deployment, and its daily/weekly seasonal components don't match this
  data's actual ~12.4h period — real short-horizon tide/wave nowcasting
  overwhelmingly uses harmonic constituent analysis anyway, so this is the
  more defensible choice here, not just the lighter one (same class of call
  as the bathtub model replacing ANUGA in milestone 1).
  `engine.py::fit_sensor_forecast()` is a pure function (`MIN_SENSOR_POINTS =
  20` floor, same data-gated-degrade pattern as anomaly detection's
  baseline); `service.py::generate_sensor_forecast()` fits it against a
  station/variable's trailing history (`forecast_sensor_baseline_days`, 7)
  and stores the projected points.
- **Hazard-front propagation**: `engine.py::fit_front()` fits a
  constant-velocity front (least-squares in a local km-projected plane,
  reusing the same equirectangular-projection convention as
  `geo/hotspots.py`) to an incident's own time-ordered report sequence — the
  simplest kinematically-defensible model, same "upgrade path noted, not
  built" honesty as the bathtub model's missing flow routing.
  `engine.py::project_front_cells()` translates the incident's current H3
  cells forward by the fitted velocity at 1/2/3h horizons
  (`service.PROPAGATION_HORIZONS_HOURS`) — the plan's own "project 1-3h
  ahead" framing. Both floors (`MIN_FRONT_POINTS = 4`, `MIN_FRONT_SPEED_KMH =
  0.1`) mean a tightly-jittered, non-moving report cluster (most incidents)
  correctly yields no propagation forecast at all.
- **Pre-alert wiring, without touching routing or the confirmed-incident
  layer**: a new `Alert.projected_cells` column (mirrors
  `predicted_flooded_cells`'s fixed-snapshot semantics exactly) is set
  whenever `alerts/service.py` creates or upgrades an alert, from the
  incident's freshest propagation forecast if one exists
  (`forecast/service.py::latest_projected_cells`). This is deliberately kept
  separate from `Alert.h3_cells` (the confirmed, report-backed area) so a
  forecast's uncertainty never contaminates routing exclusion
  (`routing/service.py::exclude_polygons`) or the public confirmed-incident
  map layer — it only *adds* to delivery-worker geofence matching
  (`delivery/worker.py::_matches()`), so subscribers directly ahead of a
  moving hazard front get the same already-automatic advisory/watch alert
  before they've reported anything themselves. Warning tier is unaffected —
  `engine.eligible_tier()` still can't return it automatically, so this can
  only ever widen an *already-automatic* tier's reach, never invent a new
  escalation.
- **Forecast validation loop**: `service.py::validate_forecasts()` scores
  every unvalidated forecast whose full horizon has already elapsed — a
  sensor forecast against the nearest actual reading within a 20-minute
  tolerance window at each predicted timestamp (mean absolute error), a
  propagation forecast against whether any report actually landed in its
  projected cells within the horizon window (hit rate). Once scored,
  `validation`/`validated_at` are set once and never edited again — same
  immutable-after-the-fact discipline as a filed SITREP. A backtest path
  (`generate_sensor_forecast(..., as_of=<past time>)`, exposed only via
  `POST /drill/backtest-forecast`) fits against history *as of* a past
  timestamp instead of now, so its full horizon has already elapsed by the
  time validation runs — this is what let the drill exercise the entire
  generate-then-validate loop immediately instead of waiting hours of real
  wall-clock time.
- **"Per-district" accuracy, without a district field**: this app's data
  model has no administrative-district concept anywhere (same gap
  `modules/ivr/locations.py` already worked around for caller location) — so
  `service.py::nearest_pilot_location()` buckets a lat/lon to the nearest of
  the same five named Chennai coastal landmarks IVR already uses, and `GET
  /forecasts/accuracy` (public) aggregates scored forecasts by that bucket +
  variable/hazard, exposing mean absolute error (sensor) or mean hit rate
  (propagation) per location — the "how right were we" metric the plan
  names.
- **Real Timescale continuous aggregate, not a plain view**:
  `sensor_readings_hourly` (`CREATE MATERIALIZED VIEW ... WITH
  (timescaledb.continuous)` over the `sensor_readings` hypertable from
  0001, hourly per-station/variable avg/min/max/count, `add_continuous_aggregate_policy`
  refreshing hourly) is real infrastructure, closing the plan's own
  "Timescale continuous aggregates per station/variable" line — though the
  actual forecast fit still reads raw `sensor_readings` rows directly (the
  aggregate's hourly bucketing is coarser than what a 1-3h-horizon harmonic
  fit needs); it's there for future dashboard/analyst use, not (yet) wired
  as the fit's input series. Gotcha hit and fixed: Timescale continuous
  aggregate creation can't run inside a transaction block — the migration
  wraps it in `op.get_context().autocommit_block()`.
- **Closes a named milestone-1 gap**: `inundation/service.py::
  forecast_flooded_cells_geojson()` applies the bathtub model to a
  *forecasted* future water level (from the freshest sensor forecast for
  `inundation_reference_variable`) instead of only ever the current
  instantaneous reading — `GET /map/inundation/forecast?hours_ahead=` — the
  gauge-forecast integration milestone 1 explicitly deferred to this
  milestone.
- **Frontend**: `MapView.tsx` gets a dashed orange "projected" layer
  (`/map/propagation`) with its own popup (hazard, front speed, horizon) and
  legend entry; a station's sparkline popup now also draws a dashed
  continuation of its sensor forecast (`lib/sparkline.ts` grew an optional
  second series, reused for exactly this); `AnalystDashboard.tsx` gets a
  Forecasts card (list, generate-now, per-forecast validation readout, and
  the public accuracy rollup).
- **Live-verified end-to-end** via `scripts/drill.py`: a new directional
  report sequence (6 reports walking north from south of Kasimedu fishing
  harbour, `POST /drill/inject-reports` — a drill-only endpoint since the
  public `/reports` API always stamps "now" and can't build a controlled
  historical sequence) merges into one incident and fits a real front —
  confirmed speed 1.16 km/h, bearing 360° (due north, as built), 5 cells
  projected ahead of the 3 actually-reported cells, disjoint from them (an
  unreported village cell, pre-alerted). A backtested sensor forecast
  (`POST /drill/backtest-forecast`, fit 3.2h in the past against the drill
  gauge's own real 7-day calm baseline) validated immediately: 3 points
  scored, mean abs error 0.48m against the gauge's real readings. The public
  `/forecasts/accuracy` endpoint also picked up the *real* NDBC buoy
  station's own forecasts scored against its real accumulated readings,
  independent of anything the drill injected. 331 backend tests passing (up
  from 298).
- **Not built** (explicit gaps, not oversights): TFT (the plan's named
  upgrade path beyond Prophet) — out of scope, same as ANUGA for inundation;
  the continuous aggregate isn't yet the forecast fit's actual input series
  (see above); no forecast-driven UI countdown/ETA display beyond the raw
  front speed/bearing shown in the popup and analyst card; the new frontend
  additions (dashed propagation layer, forecast sparkline overlay) weren't
  visually verified interactively in a browser in this environment (no
  browser automation tool available here) — verified via the live API
  responses and a clean production build instead.

## Milestone 4 — as built

- **New module `modules/narratives/`**, same `engine.py` (pure) / `service.py`
  (DB I/O) / `router.py` (thin) split as every other module here, plus a
  `narratives` table (the name the "Data model changes" line above already
  reserved) and a `narrative_deliveries` log.
- **Clustering is embedding-based, not spatial** — deliberately unlike
  `nlp/dedup.py`'s incident merge. A rumor's defining shape is that the *same
  claim* reappears in places that have nothing to do with each other, so
  `engine.cluster_reports()` greedily groups `Report.embedding` vectors by
  cosine similarity (reusing `nlp/dedup.py::cosine`, and the same
  fetch-then-rank-in-Python approach `chat/service.py` uses — no pgvector
  distance operator anywhere in this app yet) with no spatial gate at all. A
  cluster under `MIN_NARRATIVE_REPORTS` (3) is dropped: one secondhand text
  isn't a narrative spreading anywhere.
- **A cluster is only persisted if it contradicts something real.**
  `engine.is_contradiction()` takes two paths: an analyst has already
  rejected a member report (the strongest signal available — a human looked
  and disagreed), or the claimed hazard *has* an instrument signal
  (`scoring/engine.py::HAZARD_VARIABLES`) and nothing active corroborates it
  nearby. Hazards with no instrument signal at all (oil_spill, algal_bloom)
  can only ever be flagged via the rejection path — "the instruments show
  nothing" is meaningless for a hazard no instrument measures, and treating
  it as evidence would have been the easy wrong answer here. An
  unremarkable cluster of true reports never gets a row.
- **The contradiction check reuses scoring's own query, not a copy of it**:
  `scoring/service.py`'s private `_instrument_zscores(db, report)` was
  generalized to a public `instrument_zscores_near(db, hazard_type, lat,
  lon)` (the private one now delegates to it), so the rumor tracker asks
  "does instrument data back this claim here" through scoring's seam rather
  than reaching into `Station`/`StationAnomaly` itself — the module-boundary
  rule this project holds everywhere.
- **Corrections are drafted, never auto-sent.** `service._draft_correction()`
  builds a deterministic per-language template (`engine.compose_correction`)
  naming the hazard, the nearest pilot location, and *which* contradiction
  applies, then optionally asks the Anthropic adapter
  (`chat/llm.py::get_adapter().complete()`, the phase-2 seam the plan said to
  extend) to smooth **only the English** wording under a system prompt
  forbidding any factual change. No key configured, or the call fails →
  `complete()` returns None and the template stands, with `draft_method`
  recording which happened. Tamil/Telugu are never LLM-rewritten: those
  strings are already flagged as unreviewed by a native speaker
  (`ingest/report_conversation.py`), and making them *less* predictable than
  a fixed template would be the wrong direction.
- **Approval is the only path to delivery.** `POST /analyst/narratives/{id}/
  approve` flips status, audit-logs the analyst, and fans out through the
  real channel adapters (`delivery/adapters.get_adapter`) to every
  Subscription geofenced over the narrative's cells — deliberately ignoring
  `min_tier`, since "stand down, this wasn't real" is at least as relevant
  to a subscriber as a new hazard alert. It is **not** written as an `Alert`
  row: a correction isn't a hazard-tier proposal, and letting one sit in
  `alerts` would put it in reach of `sync_incident_alert`'s tier-upgrade
  logic, which could later overwrite it. Adapters only ever read `.message`/
  `.tier`/`.hazard_type`, so a small duck-typed stand-in carries the
  correction through them unchanged.
- **Re-detection semantics** (each status means something different): a
  `draft` match absorbs newly-joined reports rather than queueing a second
  draft for the same rumor; a `dismissed` match is left alone forever (an
  analyst already judged it — re-raising it every 30 minutes would be spam);
  an `approved` match gets a *fresh* draft instead of being mutated, because
  its correction has already gone out and a rumor resurging past it is new
  information, not an edit to a sent message (same immutable-once-acted-on
  discipline as a filed SITREP).
- **Alert drafting, the milestone's other half**: `alerts/engine.py::
  draft_message()` now returns `{lang: {"standard", "short"}}` for every
  language in `SUPPORTED_LANGS` instead of `{"en": str}`, and a new
  `message_text(message, lang, channel)` resolves one string at send time —
  "short" for character-constrained channels (sms, whatsapp), "standard"
  otherwise, falling back to English and then to whatever variant exists.
  Hazard names come from `report_conversation.py`'s existing
  `HAZARD_SPEECH_LABELS_BY_LANG` (the plain-text, no-emoji set, right for a
  formal alert sentence) rather than a fourth hand-maintained copy. Tier
  words stay English in every variant on purpose — that's the vocabulary
  Indian disaster SMS already uses, and translating it risks inventing
  terminology nobody recognizes. `delivery/adapters.py::_text_for` was the
  only send-side change needed, since `Subscription.lang` and the
  `alert.message[lang]` lookup were already there from phase 1;
  `message_text` also transparently reads pre-milestone-4 rows whose
  `message["en"]` is still a flat string, so no data migration was required.
  Warning tier is unaffected — it remains analyst-only via `issue_warning()`.
- **Fixed a real, pre-existing audit-chain race found by this milestone's
  drill** (not introduced by it): `append_audit()` serialized writers with
  `SELECT ... ORDER BY id DESC LIMIT 1 FOR UPDATE`, which doesn't work.
  Under READ COMMITTED, Postgres chooses the row to lock *before* blocking
  and, once unblocked, re-checks only that row instead of re-scanning for
  rows inserted meanwhile — so a second writer wakes up still holding the
  stale tail and computes the same `prev_hash`, silently forking the chain.
  The dev database had two such forks, both between `forecast.generated`
  entries ~30 ms apart, i.e. the sensor and propagation forecast scheduler
  jobs (which share an interval and therefore fire together); adding the
  narrative-detection job made it reproducible. Fixed with a
  transaction-scoped advisory lock (`pg_advisory_xact_lock`) taken *before*
  the tail read, plus a `UNIQUE` index on `audit_log.prev_hash` (migration
  0014) so a fork can never persist even if the locking is ever wrong again
  — two entries claiming one predecessor is exactly what a duplicate
  prev_hash means. Migration 0014 refuses to run on an already-forked chain
  and says so explicitly rather than recomputing the stored hashes:
  a "repair" tool that rewrites hashes until a chain verifies is precisely
  the capability a tamper-evident log exists to deny, so this project
  doesn't ship one. Verified by hammering 40 report submissions across 12
  concurrent workers (119 new audit entries, chain intact) and by two
  consecutive full drill runs.
- **Live-verified end-to-end** via `scripts/drill.py`, which now asserts both
  branches of the contradiction rule against real data: the 16-report Marina
  flood cluster is **not** flagged (the drill tide gauge is actively
  anomalous, so repetition alone never makes it a rumor — the deterministic
  negative case), while a 4-report near-duplicate algal_bloom cluster near
  Ennore *is* flagged the moment an analyst rejects one member, drafts a
  correction naming Ennore, and on approval delivers to the subscriber
  geofenced there ("[sent] via sms"). 378 backend tests passing (up from
  331).
- **Not built** (explicit gaps, not oversights): clustering is a single
  greedy pass per detection tick rather than incremental, so a narrative's
  membership is recomputed from scratch each time (fine at pilot volume,
  O(reports × clusters) — HDBSCAN over embeddings, as `geo/hotspots.py` does
  over coordinates, is the upgrade path); no cross-language clustering check
  (the multilingual sentence-transformer embeds Tamil and English text into
  one space, so it *should* group a rumor spreading across languages, but
  this environment has no real multilingual rumor corpus to verify that
  against, so it's untested rather than claimed); no per-channel-length
  variant beyond "standard"/"short" (no real 160-char SMS segmentation);
  the LLM polish path is unexercised live since no `ANTHROPIC_API_KEY` is
  configured here — `draft_method` reads `template` in every drill run, and
  the `llm` branch is covered by unit tests only; and the new analyst
  Narratives card wasn't visually verified in a browser (no browser
  automation available here) — verified via live API responses and a clean
  production build instead.

## Milestones

1. Inundation model + alert/routing wiring (independent) — ✅ built, see above
2. Auto-SITREPs (independent, high agency value) — ✅ built, see above
3. Forecasting + propagation pre-alerts — ✅ built, see above
4. Rumor tracker + alert drafting — ✅ built, see above
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
  front, pre-alert proposed for an unreported village cell. ✅ `test_forecast_engine.py`
  + `test_forecast_service.py`, plus a real live check in `scripts/drill.py` (a genuine
  fitted front — 1.16 km/h, due north — with projected cells confirmed disjoint from the
  reported ones) and a backtested sensor forecast validated against the drill gauge's own
  real baseline in the same run.
- Rumor tracker: repeated claims contradicting instrument state get clustered and flagged;
  a claim instruments corroborate never does. ✅ `test_narratives_engine.py` +
  `test_narratives_service.py`, plus both branches checked live in `scripts/drill.py`
  (Marina's gauge-backed flood cluster stays unflagged; an algal_bloom cluster is flagged
  once an analyst rejects a member, then approved and delivered to a real subscriber).
- Alert drafting: per-tier/language/channel-length variants resolve correctly at send
  time, including for rows predating the change. ✅ `test_alerts_engine.py`.
- Audit chain under concurrency: parallel writers must never fork the chain.
  ✅ `test_audit_chain.py` (lock-before-read ordering, no row-level tail lock, and the
  UNIQUE prev_hash backstop), plus a live 12-worker concurrent-write check.
- Split: replay identical drill on monolith vs. split deployment → identical end state
  (reports, incidents, alerts, audit chain length).
