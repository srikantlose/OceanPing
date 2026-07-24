# Phase 4 — Scale, Openness & Sustainability (months 11+, gap plan)

**Status: 🟡 milestones 1–3 built** (CAP + official interop; hazard registry refactor;
open-data pipeline with DP + retention jobs). Prereqs: phase 3 (service split, mobile
app, verified-event corpus) — all met. This phase is thematic rather than strictly
sequential — items are largely independent tracks; pick by pilot/partner pull. The
remaining four milestones (federated learning, AR, multi-state tenanting, insurance
API) are all still planned.

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

## Milestone 2 — as built (hazard registry refactor)

Before this milestone, "what does hazard X mean" was answered by nine hand-maintained
Python dicts scattered across five modules — `scoring/engine.py::HAZARD_VARIABLES`,
`satellite/providers.py::HAZARD_RECIPES`, `alerts/engine.py`'s label tables,
`ingest/report_conversation.py`'s menu/speech tables, `alerts/cap_ingest.py::
EVENT_HAZARD_KEYWORDS`, and `chat/corpus.py`'s per-hazard FAQ entries — each keyed by
the same nine hazard strings, each needing its own edit to add a hazard. Auditing all
of them (to scope this milestone) turned up real drift already: the frontend's short
"Erosion" legend label didn't match the alert engine's "Coastal erosion" for the same
hazard, and `alerts/engine.py`'s docstring claimed to reuse `report_conversation.py`'s
speech labels verbatim but actually carried its own, slightly different copy for two
hazards (`algal_bloom`, `other`). Neither was a bug exactly — just the natural result
of nine independent dicts with no single source of truth.

**`modules/hazards/registry.py` + `modules/hazards/definitions/*.yaml`.** One YAML
file per hazard now holds everything: `key`, `order` (menu/legend position — "other"
sits at `999` so any real hazard added with a normal order value always lists before
it), `menu_label`/`speech_label` per language (falls back to English if a language is
missing), `alert_label_en` (the alert-body copy, which this app has never translated
separately from speech copy — see below), `instrument_variables`, `satellite_recipe`,
`cap_event_keywords`, and an optional `faq` list. `load_registry(directory)` is a pure
function — reads every `*.yaml` in a directory, validates required fields, returns a
`dict[str, HazardDef]` ordered by `order` — so tests can point it at a fixture
directory without touching the real, shipped one. The module-level `HAZARDS`/
`HAZARD_TYPES` are just `load_registry(DEFINITIONS_DIR)` computed once at import, the
same "config loaded once at process start" posture every other static table in this
app already has (`WEIGHTS`, `TIER_RANK`, and so on) — adding a hazard means adding a
YAML file and restarting the process, not a hot-reload feature, which was never asked
for and would be a different, riskier kind of change.

**Every consumer now derives from the registry instead of hand-maintaining a copy:**
`models.py::HAZARD_TYPES`, `scoring/engine.py::HAZARD_VARIABLES`,
`satellite/providers.py::HAZARD_RECIPES`, `alerts/engine.py`'s label tables,
`ingest/report_conversation.py`'s menu/speech tables,
`alerts/cap_ingest.py::EVENT_HAZARD_KEYWORDS`, and `chat/corpus.py`'s per-hazard FAQ
entries are each now a one-line call into `registry.py` (`instrument_variables_table()`,
`satellite_recipes_table()`, `alert_labels_by_lang()`, `cap_event_keywords_table()`,
`faq_entries()`, and so on) instead of a hardcoded dict — every existing name
(`HAZARD_VARIABLES`, `HAZARD_RECIPES`, `HAZARD_LABELS_BY_LANG`, ...) is preserved so
none of their ~15 call sites across the app needed to change. `chat/corpus.py` keeps a
small `GENERAL_FAQ` list of its own for the entries that aren't about any one hazard
(alert tiers, report statuses, trust score, helpline); its "what hazard types does
OceanPing track" overview entry now lists the registry's own hazard names at seed time
instead of a hand-typed count, so it can't go stale the next time a hazard is added.

The nine shipped hazard files are a faithful port of the pre-refactor content — no
label, keyword, or corroboration rule changed, including the two curated cases where
`alerts/engine.py`'s English copy genuinely differed from `report_conversation.py`'s
speech copy (`algal_bloom`, `other`) — both are preserved via `alert_label_en` rather
than silently merged, since that's a real content decision this refactor's job wasn't
to relitigate.

**Verified in two layers**, matching this project's established unit-vs-live split:
- `tests/test_hazard_registry.py` — parity tests assert every derived table
  (instrument variables, satellite recipes, all nine hazards' labels in all three
  languages, CAP keyword mapping, FAQ ids) exactly matches the pre-refactor hardcoded
  content; loader tests cover duplicate-key rejection and a missing required field;
  and a minimal-hazard test (only `key`, `order`, one English `menu_label`) proves
  every optional table — translations, satellite recipe, CAP keywords, FAQ — degrades
  to an empty/English-fallback value rather than raising, exactly the bar the plan
  itself set for "adding a hazard is a config PR."
- `scripts/hazard_registry_live_check.py` — proves the same claim against the real
  running stack, not just the derivation functions: a throwaway
  `definitions/_live_check_toy_hazard.yaml` (key `king_tide`, nothing but `key`,
  `order`, and an English `menu_label` — no translations, no satellite recipe, no CAP
  keywords, no FAQ) was added, the backend image rebuilt and restarted, and the live
  check confirmed — with zero code changes anywhere outside that one file — that
  `GET /hazard-types` lists it, `POST /reports` accepts it (proving hazard-type
  validation reads the registry, not a second hardcoded list in the ingest router),
  it survives a real scoring pass and gets assigned to an incident, an analyst can
  issue a warning for it, and the resulting CAP document renders its fallback label
  without error. The toy file was then deleted and the image rebuilt again to restore
  the shipped nine-hazard state — confirmed via `GET /hazard-types` and a full rerun
  of the backend test suite (519 passed both before and after).

Backend suite: 519 → 519 tests, same total both before and after the live-check
excursion (the toy hazard was never part of the committed test fixtures); 519 is up
from milestone 1's 503 (16 new: 14 in `test_hazard_registry.py`, 2 added to
`test_chat_corpus.py` for the `GENERAL_FAQ`/registry-FAQ split), plus one new
dependency (`PyYAML`), all passing, plus a clean `docker compose build` for `backend`.
No migration needed (no schema change) and no frontend change (see below).

**Not built, deliberately:** `frontend/lib/palette.ts`'s hazard color/label maps are
untouched — the plan's own text names "scoring, satellite, alerts, chat" as this
registry's consumers, not the frontend, and `GET /hazard-types` already existed
pre-refactor returning a bare string list with no frontend caller to begin with.
Wiring the frontend to fetch hazard metadata (so a new hazard gets a real map color
and dashboard label with zero frontend edits too) is a reasonable, clearly-scoped
follow-up, not folded in here. `nlp/prototypes.py`'s classifier training phrases/
keywords are also untouched — those are ML training data, not behavioral config, so a
genuinely new hazard still needs its own classifier examples the same way it always
has; that's inherent to supervised classification, not a registry limitation.

## Milestone 3 — as built (open-data pipeline with DP + retention jobs)

Built to the plan's own two-part framing: "anonymized event datasets: H3-aggregated,
k-anonymity floor + differential-privacy noise on counts" plus "retention/anonymization
jobs... implement as a scheduled job with audit entries." Both halves are real, live-
verified pipelines against the real running stack, not illustrative stubs.

**`modules/opendata/service.py::aggregate_events()` + `build_dataset_release()`.**
Verified reports (`status="verified"` only — the same trust bar `/map/reports` already
applies) in a requested `[period_start, period_end)` window are grouped by
`(H3 cell coarsened to open_data_h3_resolution, hazard_type, calendar day)` — resolution
6 by default, deliberately coarser than the internal resolution-8 report grid (see
`geo/h3utils.py::cell_to_parent`, new in this milestone), so groups start out bigger
before k-anonymity even has to suppress anything. A group whose *true* report count
falls below `open_data_k_anonymity_min` (default 5) is dropped outright — not noised —
since Laplace noise cannot retroactively hide that a raw count of 1-4 was ever computed;
the floor has to be enforced before noise, not instead of it. Every surviving group then
gets independent Laplace-mechanism DP noise (`scale = 1/open_data_dp_epsilon`, default
epsilon 1.0), rounded to the nearest non-negative integer. `build_dataset_release()`
freezes one run of this as a `DatasetRelease` row — `content` holds the released rows
inline (pilot volumes are small enough, same posture as `Sitrep.content`), `checksum` is
a sha256 of that exact content so a citing researcher can verify their copy still
matches what was published, and `doi` stays null until an operator registers a specific
release with an external provider (DataCite/Zenodo) and fills it in by hand — same
pilot-placeholder posture as `cap_sender` in milestone 1.

**API keys (`ApiKey` model) — `create_api_key()`/`verify_api_key()`/`revoke_api_key()`.**
Only a sha256 hash of the raw bearer secret is ever persisted; the raw key
(`op_live_<24 random bytes, url-safe>`) is returned exactly once, at minting time
(`POST /analyst/opendata/api-keys`, analyst-only) — the same "never store the
credential itself" posture a password would get, even though this is a machine
credential. Revocation is a timestamp (`revoked_at`), not a delete, so a revoked key's
audit trail survives; `verify_api_key()` treats an unknown key and a revoked key
identically (both return `None`) so a caller can't distinguish "never existed" from
"existed and was cut off."

**Rate limiting — `check_rate_limit()`.** The exact same real-Redis
`INCR`-plus-`EXPIRE` pattern `ingest/service.py::_check_rate_limits` already uses for
report submission, scoped per API key (`rl:apikey:{key_id}`, 3600s window,
`open_data_rate_limit_per_hour` cap, default 200/hour) instead of per-reporter/cell.
Same fail-open posture on a Redis outage: a briefly-unthrottled public research API is
a far smaller risk than an unusable one during a Redis blip.

**Retention/anonymization job — `anonymize_expired_reports()`.** The DPDP-style half of
this milestone: once a `Report` is older than `open_data_retention_months` (default 12),
its exact `lat`/`lon`/`geom` are permanently overwritten with its own H3 cell's centroid
— the same fuzzing every public read path (`geo/router.py`, `recovery/service.py`)
already applies at *read* time, now made a one-way *write*-time reduction in stored
precision instead. Unlike `recovery/service.py::purge_expired_missing_persons` (its
closest precedent — same real-scheduled-job-not-just-policy posture, same "one audit
entry per batch, no PII in the payload" convention), this job never deletes a `Report`:
hazard type, confidence, incident linkage, and `h3_cell` itself (already ~0.7 km²,
the unit every downstream aggregation already works from) are all untouched — only
location precision degrades, and only once, tracked via the new
`Report.location_anonymized_at` column so a later tick never reprocesses (or re-audits)
the same row. Registered in `core/scheduler.py` as a real 24-hourly job, in the "never
shed" group alongside the missing-person retention purge — not one of the deferrable
analytics jobs.

**Public vs. gated surface (`modules/opendata/router.py`).** The dataset *catalog*
(`GET /opendata/datasets`) is fully public, no key required — a researcher needs to see
what exists before requesting a key, and a catalog entry carries only aggregate
metadata (row/suppressed-group counts, DP parameters, checksum), never raw rows. The
actual data *download* (`GET /opendata/datasets/{id}`) is the one gated, rate-limited
endpoint, since that's the resource worth controlling and metering.

**Verified in two layers**, matching this project's established unit-vs-live split:
- `tests/test_opendata_service.py` (18 tests) — API-key lifecycle (hash-only storage,
  accept/reject/revoke), rate limiting (allows-under-cap, raises-over-cap, fails open
  on a simulated Redis outage), the Laplace-noise helper's statistical shape
  (zero-centered, smaller epsilon = more noise), k-anonymity suppression and DP-noise
  application in isolation (noise monkeypatched to zero to test the grouping/rounding
  logic precisely), dataset-release persistence + checksum, and the retention job's
  lat/lon/geom overwrite + audit entry.
- `scripts/opendata_live_check.py` (rerunnable against the persistent dev stack, same
  convention as `cap_live_check.py`/`hazard_registry_live_check.py`) — a **controlled
  re-identification check**, not just "suppressed_group_count > 0 somewhere": since
  `apply_verification()` is the only code path that ever sets `Report.status="verified"`
  (see `scoring/service.py`'s own docstring) and a release's window is bound to
  `Report.created_at`, submitting-and-verifying exactly 2 reports (below the k=5 floor)
  at one never-before-used location and 6 reports (at the floor) at another, then
  building a release over exactly that just-created window, makes the release's exact
  shape predictable: `suppressed_group_count == 1`, `row_count == 1`, and the one
  released row is the 6-report batch, DP-noised but non-negative. Also verified: the
  public catalog needs no key; download is 401 with none and 403 with an invalid or a
  revoked one; a freshly minted key's raw secret is shown exactly once and never appears
  in the key listing; rate limiting really returns 429 once ~200 requests land on one
  key. Separately, the retention job was live-verified directly against the real
  Postgres database (not the script above, since the default 12-month window can't be
  reached by submitting a report through the API today): a report was submitted, its
  `created_at` backdated to 400 days ago via `psql`, `anonymize_expired_reports()` was
  invoked directly against the real `SessionLocal`, and the row's `lat`/`lon` were
  confirmed overwritten to its `h3_cell`'s exact centroid, `location_anonymized_at` set,
  and a real `opendata.locations_anonymized` audit entry with no PII landed in the real
  chain — then a second invocation confirmed 0 rows processed (idempotent, since
  `location_anonymized_at` is now set).

Backend suite: 519 → 537 tests (18 new, all in `test_opendata_service.py`), all
passing, plus a clean `alembic upgrade head` (migration `0019_opendata`, adding
`api_keys`/`dataset_releases` tables and `reports.location_anonymized_at`) and a clean
`docker compose build` for `backend` (no new dependency — `numpy` was already present
for the classifier/scoring stack and covers the Laplace-noise draw). No frontend
change.

Also added: `docs/opendata-datasheet.md`, a "Datasheets for Datasets"-style document
(Gebru et al. convention) covering motivation, composition, the anonymization
mechanism in detail, collection process, uses/misuses, distribution, and maintenance —
the plan's own "publish a datasheet" instruction.

**Not built, deliberately:** an automatic release cadence — `POST
/analyst/opendata/releases` is analyst-triggered, on purpose, since publishing implies
a review step (a human should see `suppressed_group_count`/`row_count` before anything
goes out) rather than a fully automatic schedule. Real DOI registration and a chosen
license are both external/decision steps left as placeholders (`DatasetRelease.doi`,
the datasheet's licensing section), the same "pilot placeholder swappable the day a
real integration lands" posture as `cap_sender`/`cap_sender_name` in milestone 1.

## Milestones

1. CAP generator + inbound CAP corroboration — ✅ built, see above
2. Hazard registry refactor (config-only new hazards) — ✅ built, see below
3. Open-data pipeline with DP + retention jobs — ✅ built, see below
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
- DP: re-identification test on released aggregates (no cell below k threshold) —
  ✅ done (`tests/test_opendata_service.py` proves suppression happens on the *true*
  count before noise; `scripts/opendata_live_check.py` proves it against the real
  running stack with a controlled below-floor batch that never appears in the output
  and an at-floor batch that does).
- Tenanting: cross-state analyst cannot read another state's exact coordinates (authz test).
- Hazard registry: add a toy hazard purely via config → report→score→alert path works in
  drill with zero code diff outside the registry — ✅ done (`tests/test_hazard_registry.py`
  proves every derived table degrades gracefully for a minimal hazard; `scripts/
  hazard_registry_live_check.py` proves the same against the real running stack with an
  actual added-then-removed YAML file and a rebuild).
