# Phase 4 — Scale, Openness & Sustainability (months 11+, gap plan)

**Status: 🟡 milestone 1 (CAP + official interop) built.** Prereqs: phase 3 (service
split, mobile app, verified-event corpus) — all met. This phase is thematic rather than
strictly sequential — items are largely independent tracks; pick by pilot/partner pull.
The remaining six milestones (federated learning, AR, hazard-registry refactor, open
data, multi-state tenanting, insurance API) are all still planned.

## Goals

Turn a working pilot into infrastructure: privacy-preserving learning, official alerting
interop, open data, revenue hooks, and multi-state operation.

## Already built to lean on

- Verified-event dataset with full audit provenance (insurance/open-data raw material)
- Multi-hazard scoring recipes as config — `scoring/engine.py::HAZARD_VARIABLES` +
  phase-2 satellite recipes
- Mobile app with on-device queue (phase 3) — the federated-learning host
- Alerts engine with tier gates (phase 1) — CAP maps onto it

## Gap work breakdown

### 1. CAP + official interop (highest institutional value)

- CAP 1.2 generator: every issued alert also renders as a CAP XML document
  (`modules/alerts/cap.py`); endpoint + push to SACHET/NDMA gateway when partnership
  lands. Being CAP-compliant makes agency integration a config, not a project — build the
  generator before the partnership exists.
- Inbound CAP: ingest official alerts as first-class corroboration (a new scoring signal
  source: official advisory active over the cell → strong prior).

### 2. Federated learning (research-grade, timeboxed track)

- On-device image/text classifier improvement via federated averaging (Flower framework);
  server aggregates gradient updates only — photos never leave phones. Start with the
  image damage classifier (clearest label source). DPDP story: document data-flow diagram
  alongside implementation.

### 3. AR flood visualization (mobile)

- Camera view renders predicted flood line at forecast surge height on buildings
  (phase-3 inundation cells + device pose). Expo/ViroReact or native ARCore module.
  Communicates risk viscerally; demo-gold for preparedness drills.

### 4. Multi-hazard as pure config

- Refactor per-hazard behavior (corroboration variables, satellite recipe, alert copy,
  chatbot FAQ) into one `hazards/<type>.yaml` registry consumed by scoring, satellite,
  alerts, chat. Adding "king tide" becomes a config PR. Includes rip-current beach-cam CV
  recipe and HAB (fish-kill reports + chlorophyll) as the validating examples.

### 5. Open data & research API

- Anonymized event datasets: H3-aggregated, k-anonymity floor + differential-privacy noise
  on counts; retention/anonymization jobs (DPDP: auto-anonymize exact locations after N
  months — implement as a scheduled job with audit entries).
- Public API keys, rate-limited; dataset DOIs for researchers. The fused
  citizen+instrument+satellite corpus is academically novel — publish a datasheet.

### 6. Parametric-insurance trigger API

- Read-only signed API over the verified-event store: water levels, wind, timestamps,
  geo-extent, with audit-chain proof bundles (`verify_chain` extract per event window).
  This is a revenue candidate: design the contract with one insurer pilot; never expose
  personal data, events only.

### 7. Multi-state rollout & ops maturity

- Tenanting: `district`/`state` scoping on reports, alerts, analyst roles (Keycloak
  realms/roles from phase 3); per-district model + threshold overrides.
- Per-district precision/recall dashboards (from `verifications` outcomes) published
  internally; alert-budget governance.
- Community preparedness score per panchayat (volunteers registered, drill participation,
  shelter mapping completeness) with public leaderboard; drill mode becomes a scheduled
  per-district readiness program with scores.
- Vulnerability-aware alerting: household profiles (elderly/disability/no-vehicle) →
  earlier lead time + assisted-evacuation list per ward (strict access control; the most
  privacy-sensitive table in the system — DPO sign-off required).

## Data model changes

`cap_documents` · `hazard_registry` (or YAML in repo) · `api_keys` · `dataset_releases` ·
`households` (vulnerability profiles, encrypted at rest) · tenant columns on core tables.

## External dependencies & risks

- NDMA/SACHET partnership timelines — CAP generator ships regardless.
- DPDP Act compliance is a program, not a feature: appoint DPO process, consent audits,
  retention enforcement — schedule a compliance review before any real-household data.
- Federated learning ROI is uncertain — timebox; the centralized active-learning loop
  (phase 1) already improves models.
- Insurance API creates legal exposure — contract review before first external consumer.

## Milestone 1 — as built (CAP + official interop)

Built exactly to the plan's own framing: "CAP-compliant makes agency integration a
config, not a project — build the generator before the partnership exists." No
NDMA/SACHET partnership exists in this environment, so both directions were verified
against the real, OASIS-published CAP 1.2 spec rather than a partner sandbox.

**Outbound — `modules/alerts/cap.py`.** `alert_to_cap_xml(alert, ...)` renders any
`Alert` row as a real CAP 1.2 document (stdlib `xml.etree.ElementTree`, no new runtime
dependency): tier maps onto CAP's urgency/severity/certainty triple (`warning` — the
only analyst-issued tier, see `alerts/service.py::issue_warning` — is the only one that
ever renders `Immediate`/`Severe`/`Observed`; automatic `advisory`/`watch` render
`Expected`/`Minor-or-Moderate`/`Possible-or-Likely`, since an automatic tier can't
honestly claim more certainty than that). `h3_cells` become one real `<polygon>` per
cell via `geo/h3utils.py::cell_polygon()`, reordered from GeoJSON's `[lon, lat]` to
CAP's `lat,lon` vertex format. An expired alert renders `msgType=Cancel` instead of
`Alert` — a real, if simplified, piece of CAP's own update semantics (a genuine
tier-upgrade in place, e.g. advisory→watch, still renders as a fresh `Alert` rather
than an `Update`, since this generator renders an alert's *current* row from scratch
each time rather than tracking prior-render history; acceptable for a document a
partner system is expected to poll and replace wholesale, not diff — see the module's
docstring). `alerts_feed_xml()` renders an Atom index of active alerts linking to each
one's CAP document, the same aggregation pattern real public CAP sources use (e.g.
NWS's alerts.weather.gov) so a partner has one URL to poll for what's new. Both are
exposed publicly (same trust boundary as `/map/alerts` — a CAP document is *meant* for
wide redistribution): `GET /cap/alerts/{id}.cap`, `GET /cap/feed`.

Validated in tests (`tests/test_cap.py`) against the actual OASIS-published
`CAP-v1.2.xsd` — fetched verbatim from docs.oasis-open.org and committed at
`modules/alerts/schemas/CAP-v1.2.xsd`, not hand-transcribed — via `lxml.etree.XMLSchema`
(the one new dependency this milestone adds; stdlib `ElementTree` can generate CAP fine
but can't validate against an XSD). Every tier's urgency/severity/certainty triple,
language selection, polygon coordinate order, and the missing-`expires_at` fallback
(auto advisory/watch alerts never set one — see `alerts/service.py` — so the generator
fills a bounded default horizon rather than emitting an unbounded document) are covered.

**Inbound — `modules/alerts/cap_ingest.py` + `cap_service.py`.** `parse_cap()` is a
pure parser (no I/O) for a real CAP 1.2 document: envelope fields, per-`<info>` block
fields, and per-`<area>` polygons *and* circles (`<circle>lat,lon radius</circle>` —
plausible for a seismic-origin tsunami warning, where a real agency might not yet have
fit a polygon). `map_event_to_hazard()` is a best-effort keyword match from a real
agency's `<event>` text onto this app's hazard vocabulary (tsunami, storm surge, high
waves, rip current, coastal flooding, oil spill, algal bloom, erosion) — an event that
matches nothing is not stored at all, rather than guessed at; an unmapped advisory has
nothing to corroborate. `cap_service.py::ingest_cap_document()` stores one
`OfficialAdvisory` row per (info, area) pair, geo-shape stored as-is (`area_polygon` or
`area_circle` JSONB); a message whose `<references>` points at earlier identifiers
(a real Cancel or Update) expires those rows first — a Cancel then stores nothing
further of its own, an Update goes on to store its own new rows. `POST /webhooks/cap`
is the ingestion endpoint, gated on a shared `X-Api-Key` header
(`cap_ingest_api_key`) with the same credential-checked-if-set,
skipped-with-a-warning-if-not posture as `whatsapp_app_secret`'s signature check, since
no real partner credential exists yet to require one.

`cap_service.py::official_advisory_for(db, hazard_type, lat, lon)` is the read side: the
first *active* (not expired — filtered at read time, no sweep job needed, same posture
as `Alert.expires_at` elsewhere) advisory whose polygon (real `shapely` point-in-polygon
test) or circle (real `haversine_km`, already used for shelter/station distance
elsewhere) covers the point, for that hazard. `GET /analyst/official-advisories` lets an
analyst confirm an inbound document actually landed, mirroring
`/analyst/alerts/{id}/deliveries`'s role for the outbound side.

**Scoring integration — the seven-signal rebalance (`scoring/engine.py`).** An active
official advisory is a real, weighted confidence component (`official_score()`, scaled
by the issuing agency's own stated `<certainty>` — Observed=1.0, Likely=0.7,
Possible=0.4, Unknown=0.2 — same "absence of evidence, not evidence against" posture as
`satellite_score`), *and* it joins `instrument`/`satellite` in the escalation gate
(`components["official"] > 0`) — both roles, not one. An earlier draft of this
milestone tried to add it as a bare gate condition with **no** weight, reasoning the
six-signal table was the blueprint's own fixed spec; live-testing that draft showed it
was structurally almost inert (a lone report's confidence can't cross
`corroborated_threshold` from an unweighted signal no matter how strong, since the gate
also requires the numeric confidence to already reach 0.6 from the *other* signals —
exactly how instrument/satellite already work, and why they carry real weight too). So
`official` is a genuine seventh weight (0.15), and every other weight was trimmed by the
same proportional 15% cut (0.85×, then rounded to clean two-decimal values) rather than
one signal absorbing the whole cut — an official advisory is meant to sit *alongside*
instrument/satellite as a non-citizen-controlled check, not replace either. New table:
`trust .17, coherence .21, instrument .21, media .13, satellite .09, account_device .04,
official .15`. `docs/plans/phase-2-fusion-reach.md`'s six-signal record is left as the
historical account of that rebalance; this one is documented here, where it happened —
current values live in `engine.py`, as ever.

**Live-verified** (`scripts/cap_live_check.py`, rerunnable against the persistent dev
stack) — 15 checks, all passing:
- An alert issued through the real `/analyst/incidents/{id}/warning` endpoint renders
  through the real `/cap/alerts/{id}.cap` endpoint as `Immediate`/`Severe`/`Observed`
  with a real `<polygon>`; the real `/cap/feed` links to it; expiring it flips the same
  document to `msgType=Cancel`.
- A hand-built, schema-realistic CAP document posted to the real `/webhooks/cap`
  lands in the real Postgres `official_advisories` table with its `<event>` correctly
  mapped to `tsunami` and its `<certainty>` carried through.
- A citizen report submitted *inside* the advisory's polygon picks up
  `confidence_components.detail.official_advisory` (real SQL query + real `shapely`
  point-in-polygon test, not the unit tests' fakes) — a report *outside* it does not,
  proving the geo-scoping is real, not a global "any advisory anywhere" flag.
- Cancelling the advisory (a real `<references>`-bearing Cancel document) really stops
  it from corroborating a subsequent report at the same spot — the expiry path is
  load-bearing against the real database, not just stored and ignored.

Backend suite: 472 → 503 tests (31 new: 10 in `test_cap.py`, 8 in `test_cap_ingest.py`,
9 in `test_cap_service.py`, 2 in `test_scoring_engine.py`, 2 in `test_scoring_service.py`
— plus two pre-existing engine tests updated in place for the rebalanced weights), all
passing, plus a clean `alembic upgrade head` (migration `0018_official_advisories`) and
a clean `docker compose build` for both `backend` (new `lxml` dependency) and `frontend`
(the analyst dashboard's confidence-bars view now also renders the `official` component
and a matched advisory's sender/event/certainty, alongside the existing
satellite/instrument detail lines). Mobile's 44-test suite is untouched by this
milestone.

**Not built, deliberately:** the real NDMA/SACHET partnership and its actual event-code
taxonomy — `map_event_to_hazard()`'s keyword table is a reasonable-effort mapping onto
plain English CAP `<event>` text, not a real agency's coded vocabulary, since no such
table is public yet. `cap_sender`/`cap_sender_name` are pilot placeholders, swappable
via env the day a partnership lands — exactly the plan's own point in building this
before one exists.

## Milestones

1. CAP generator + inbound CAP corroboration — ✅ built, see above
2. Hazard registry refactor (config-only new hazards)
3. Open-data pipeline with DP + retention jobs
4. Multi-state tenanting + per-district metrics
5. Vulnerability-aware alerting (with DPO review)
6. AR mode; federated learning spike → go/no-go
7. Insurance trigger API pilot

## Verification

- CAP: generated documents validate against the CAP 1.2 XSD; round-trip inbound test —
  ✅ done (`tests/test_cap.py` validates every tier against the real OASIS XSD;
  `tests/test_cap_ingest.py::test_round_trips_our_own_generator_output` is the round
  trip; `scripts/cap_live_check.py` proves the same against the real running stack,
  including geo-scoped corroboration and cancel/expiry).
- DP: re-identification test on released aggregates (no cell below k threshold).
- Tenanting: cross-state analyst cannot read another state's exact coordinates (authz test).
- Hazard registry: add a toy hazard purely via config → report→score→alert path works in
  drill with zero code diff outside the registry.
