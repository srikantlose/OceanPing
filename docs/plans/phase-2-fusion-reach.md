# Phase 2 — Fusion & Reach (blueprint weeks 15–24, gap plan)

**Status: 🟡 in progress (July 2026).** Milestones 2 (satellite + six-signal rebalance),
3 (RAG chatbot), and 4 (channel-agnostic conversation core + WhatsApp + IVR) built.
Milestone 1 (INCOIS real datasets) investigated and found blocked on external data
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

## Milestone 3 — as built

Retrieval-augmented chat is live, grounded on a hand-authored hazard-safety FAQ corpus:

- `backend/app/models.py` — `RagDocument` (id is a human-readable slug like
  `faq-alert-tiers`, `String(64)` primary key, same idempotent-upsert-by-id pattern as
  `Station`, not a UUID) and `ChatLog` (question, answer, retrieved_doc_ids, retrieval_
  score, is_evacuation_directive, is_fallback). Migration `0007_chat.py` (follows `0004`/
  `0006`'s `create_all` pattern for new tables).
- `backend/app/modules/chat/corpus.py` — 12 hand-written hazard-safety FAQ entries
  (hazard types, alert tiers, report statuses, tsunami/rip-current/oil-spill/algal-bloom
  safety info, how reporting and trust scores work, an emergency-contact entry). This is
  educational reference content, written so it would never itself read as a real-time
  evacuation directive - `seed_corpus(db)` upserts it into `rag_documents` at startup
  (`main.py`'s `lifespan()`, right alongside the existing `sync_stations()` call),
  embedding each entry with the same multilingual sentence-transformer `classify()` uses.
  Real INCOIS advisories / PFZ bulletins / a shelter list were named as corpus sources in
  the original plan too; scraping a live feed or building the shelters table (milestone 6)
  is out of scope here - shipped with real, useful, honestly-sourced content instead of
  faking external sources this session has no way to verify.
- `backend/app/modules/chat/llm.py` — `AnthropicAdapter.complete()`, a plain `httpx` call
  to the Messages API (no new SDK dependency - `httpx` is already used this way by every
  other adapter in `delivery/adapters.py`), gated on `settings.anthropic_api_key` exactly
  like Twilio/Exotel are gated on their own credentials. Unconfigured means the endpoint
  always returns the helpline fallback, never a crash.
- `backend/app/modules/chat/service.py::answer()` — two safety rules enforced **in code**,
  not just in the system prompt, per the plan:
  1. `is_evacuation_directive()` (a keyword heuristic, same honest scoping as
     `classifier.py::detect_hearsay()` - no labeled data exists to train a real classifier)
     hard-bypasses retrieval and the LLM entirely for questions like "should I evacuate?",
     returning the helpline message plus a live lookup of active alerts near the asker's
     location (by lat/lon for the web client, or by a Telegram subscriber's already-stored
     geofence cells directly - no lossy cells→lat/lon→cells round trip).
  2. Any other question's best retrieval match below `chat_retrieval_threshold` also never
     reaches the LLM - same fallback. `retrieve()` fetches the (small, code-seeded) corpus
     and ranks it by cosine similarity in Python, the same pattern `nlp/dedup.py` already
     uses for incident-merge similarity (no pgvector SQL distance operator is used
     anywhere in this codebase yet, so this doesn't introduce a new one without reason).
  Every question is logged to `chat_logs` regardless of which path it took.
- `backend/app/modules/chat/router.py` — public `POST /chat` (no analyst auth, same trust
  boundary as `/reports` and `/map/*`).
- `backend/app/modules/ingest/bot_runner.py` — new `/ask <question>` command, passing the
  subscriber's stored geofence cells (if subscribed) straight through as `alert_cells`.
- `docker-compose.yml` — `ANTHROPIC_API_KEY` threaded through `backend` and `bot` (both
  call `chat/service.py::answer()` directly).
- Tests: `test_chat_corpus.py` (id uniqueness, non-empty content, the corpus never itself
  reads as an evacuation directive, seed/upsert with and without an embedder available),
  `test_chat_llm.py` (credential gate, response parsing, HTTP-error and empty-content
  handling - real `httpx.Response` objects, no live API call), `test_chat_service.py`
  (evacuation-directive detection, cosine ranking, and - the important one - that the
  evacuation bypass and the low-score fallback each *provably* never call the embedder or
  LLM adapter, by monkeypatching them to raise if invoked).

**Verified live, not just under mocks:** rebuilt the image and confirmed `seed_corpus()`
embedded all 12 corpus entries at real startup (`psql` showed every row with a non-null
embedding). Exercised `/chat` live with six real questions spanning clearly on-topic,
loosely-phrased-but-relevant, and clearly off-topic - `chat_logs.retrieval_score` showed
closely-phrased questions ("what are the warning signs of a tsunami?") scoring 0.75+
against their correct document, loosely-phrased-but-relevant ones ("who do I contact in an
emergency?") scoring ~0.34-0.39, and off-topic ones ("what's the capital of France?" / "tell
me a joke about pizza") scoring 0.05-0.10 - a clear, real separation. **Found and fixed a
real calibration bug from this data**: the initial `chat_retrieval_threshold` guess (0.45)
sat *above* the loosely-phrased-relevant cluster, meaning real, correctly-matched questions
like "who do I contact in an emergency?" would have been wrongly rejected; rebalanced to
0.28 (documented with the data in `config.py`) and reverified live that the same question
now clears the gate. Also exercised the evacuation-directive path live with a real
location near the Chennai Marina drill data and confirmed it returned the actual active
alerts near that point (8 real alerts from prior drill runs), never reaching retrieval or
the LLM. No `ANTHROPIC_API_KEY` is configured in this environment, so the real-answer path
(LLM call succeeding) is unverified live - covered by mocked tests only, same status as
Twilio/Exotel/Sentinel Hub elsewhere in this project.

**Not built:** a frontend chat widget. The plan's milestone-3 deliverable is the retrieval
+ generation + safety-gate seam and the endpoints (`POST /chat` + bot `/ask`); no chat UI
component was in the gap-work breakdown's action items, so none was added - `/chat` is
reachable from any web client already.

## Milestone 4 — as built

The location -> hazard -> description -> photo report flow is now shared across
channels, with WhatsApp and phone-call (IVR) adapters built on top of it:

- `backend/app/modules/ingest/report_conversation.py` (new) — the flow itself, extracted
  out of `bot_runner.py`: `ConvState` enum, the canonical hazard menu (`HAZARD_LABELS` /
  `HAZARD_SPEECH_LABELS` / `hazard_menu_items()`, in `HAZARD_TYPES` order so every channel
  numbers/lists hazards identically), pure transition functions (`start`, `on_location`,
  `on_hazard`, `on_description`, `skip_description`, `mark_done`) operating on a
  `ReportSession` dataclass, and `build_report_kwargs()` to assemble `create_report()`'s
  flow-owned arguments. Fully pure/testable — no I/O. A small Redis-backed session store
  (`save_session`/`load_session`/`clear_session`, same client and key-style convention as
  `ingest/service.py`'s rate limiter) is exported alongside it for channels whose webhook
  calls are stateless HTTP requests with no in-process home for a session object between
  messages (WhatsApp) — Telegram doesn't need it, since python-telegram-bot's
  `context.user_data` already holds the `ReportSession` for the life of the conversation.
- `backend/app/modules/ingest/bot_runner.py` — refactored into a thin adapter: every
  handler now calls into `report_conversation` for the prompt/next-state and just
  translates it to/from Telegram's Update/context objects. `ConvState` enum members are
  used directly as python-telegram-bot's `ConversationHandler` state keys (PTB only
  requires hashable state keys, not ints) — no behavior change versus the pre-refactor
  bot, confirmed by a full fake-Update walkthrough of the flow (new `test_bot_runner.py`,
  since this file previously had zero test coverage) and a live rebuild/run of the bot
  container.
- `backend/app/modules/whatsapp/` (new) — the WhatsApp Business Cloud API adapter:
  - `client.py` — plain `httpx` calls to the Graph API (`send_text`, `send_hazard_menu` as
    an interactive list message, `download_media`, `verify_signature` for the
    `X-Hub-Signature-256` header), gated on `whatsapp_access_token`/
    `whatsapp_phone_number_id` exactly like every other adapter in this app — unconfigured
    means outbound sends silently no-op and signature verification is skipped (logged),
    never a crash.
  - `service.py::handle_payload()` — parses Meta's webhook message shape and drives the
    same `report_conversation` state machine Telegram uses, keyed by the sender's phone
    number in the Redis session store. Trigger words start a session; `cancel` clears one;
    a wrong message type for the current state re-prompts instead of erroring.
  - `router.py` — `GET /webhooks/whatsapp` (verify-token challenge) and
    `POST /webhooks/whatsapp` (signature check, then dispatch). Public, no analyst auth —
    the signature check is the real gate, same trust model as `/chat` and the Telegram bot.
  - `delivery/adapters.py` gained `WhatsAppAdapter` (alert fan-out, not the conversational
    flow — a separate concern, same as Telegram's `TelegramAdapter` vs. `bot_runner.py`)
    and `get_adapter("whatsapp")` dispatch.
- `backend/app/modules/ivr/` (new) — a Twilio Voice webhook (TwiML in/out); Exotel's
  classic Exoml call-control markup is Twilio-compatible for the Gather/Say/Record verbs
  used here, so one implementation serves either provider without a fork:
  - `locations.py` — a short list of named pilot coastal locations (Marina Beach, Besant
    Nagar/Elliot's Beach, Kasimedu, Injambakkam, Ennore) selectable by a single DTMF digit —
    an honest stand-in for a real registered-village/cell-tower lookup, which this
    environment has no telco integration to reach or verify, same role `StubProvider`
    plays for satellite imagery.
  - `service.py` — hazard digit (1–9, from `report_conversation`'s canonical menu) ->
    location digit -> a recorded voice description -> `create_report(source="ivr")`. The
    description reuses the exact same Whisper transcription (`ingest/voice.py`) the
    Telegram bot already uses for voice notes; the recording download reuses the existing
    `twilio_account_sid`/`twilio_auth_token` credentials (no new credential needed).
    Call state between webhook steps lives in Redis keyed by `CallSid` — a plain dict, not
    `ReportSession`, since IVR's digit-menu shape doesn't match the free-text-location/photo
    state machine WhatsApp and Telegram share.
  - `router.py` — `POST /webhooks/ivr/voice`, one endpoint for all steps (`?step=` query
    param), form-encoded per Twilio's convention.
  - **Deferred, not half-built:** the phase-2 plan's "language → hazard digit → location"
    wording implies a language-selection step, but that's meaningless without translated
    prompt strings to switch to — Tamil/Telugu localization is already explicitly scoped
    to milestone 5 (fisherman mode), so the language step ships there together with real
    translated strings rather than as an empty menu here.
- `docker-compose.yml` — `WHATSAPP_*` threaded through `backend` (inbound webhook) and
  `worker` (outbound `WhatsAppAdapter`); `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN` added to
  `backend` too (previously only `worker` had them), since the IVR recording download now
  runs there.
- Tests: `test_report_conversation.py` (pure transitions, invalid-state/invalid-hazard
  errors, session-store roundtrip and Redis-down degradation), `test_bot_runner.py` (fake
  Telegram Update/context objects driving the full refactored flow end to end, plus
  rate-limit and cancel paths), `test_whatsapp_client.py` (credential gates, payload
  shapes, signature verification with real HMAC), `test_whatsapp_service.py` (full webhook
  payload fixtures matching Meta's documented shape, covering trigger/help/cancel/
  wrong-state/happy-path/skip/rate-limited branches), `test_ivr_service.py` (TwiML output
  per step, digit validation, the full hazard→location→recording→create_report path, the
  Twilio-recording-download credential gate), and `WhatsAppAdapter` additions to
  `test_delivery_adapters.py`.

**Verified live, not just under mocks:** rebuilt and restarted `backend`, `worker`, and
`bot`. Posted a full, realistic Meta webhook message sequence (trigger word → location →
interactive list-reply hazard pick → free-text description → skip) directly at the running
`backend`'s `/webhooks/whatsapp` and confirmed via `psql` a real `reports` row landed
(`source=whatsapp`, `hazard_type=oil_spill`, correct text) and that the Redis conversation
session (`report_conv:whatsapp:...`) was cleared afterward. Also verified live: an
unconfigured verify-token correctly 403s the `GET` challenge, and an unrecognized message
with no active session gets the help-text fallback rather than crashing. Separately posted a
full Twilio-style form-encoded call sequence (start → hazard digit 6 → location digit 1 →
recording callback with no `RecordingUrl`, since Twilio isn't configured here) at
`/webhooks/ivr/voice` and confirmed a second real `reports` row (`source=ivr`,
`hazard_type=oil_spill`, Marina Beach's coordinates, `text=NULL` since there was no
recording to transcribe) plus correct TwiML at every step. **A real regression this testing
caught and fixed**: `handle_hazard`'s digit-to-hazard lookup used `items[int(digit) - 1]`,
so digit `"0"` silently wrapped via Python's negative indexing to `items[-1]` ("other")
instead of being rejected — caught by `test_ivr_service.py`, fixed with an explicit
`1 <= idx <= len(items)` range check, and reconfirmed live (`Digits=0` now returns "Invalid
selection"). Checked backend/worker logs for both runs: no exceptions. Rebuilt and ran the
`bot` container: it still starts cleanly and degrades exactly as before
(`TELEGRAM_BOT_TOKEN not set — bot disabled`) — there is no real Telegram bot token in this
environment (same gap as every other channel credential this project has hit), so the
refactored `bot_runner.py` can't be driven through actual Telegram polling; its handler
logic is instead covered end-to-end by `test_bot_runner.py`'s fake-Update walkthrough, and
the shared `report_conversation` core it now depends on was exercised for real by the live
WhatsApp run above. No Meta Business/WhatsApp account or Twilio/Exotel account exists in
this environment, so outbound sends (`WhatsAppAdapter`, `client.send_text`/
`send_hazard_menu`) and the recording download are unverified live — covered by mocked
tests only, same status as Sentinel Hub/Earth Engine/Anthropic elsewhere in this project.

**Not built:** IVR language selection (see above — deferred to milestone 5 alongside
Tamil/Telugu localization); a WhatsApp "subscribe" or `/ask`-equivalent chat entry point
(milestone 4's scope is the report-submission flow; WhatsApp subscribe/chat parity with
Telegram wasn't in the gap-work breakdown's action items for this milestone).

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
3. ✅ RAG chatbot (web + Telegram) — real Anthropic-answer path unverified live, no key
   configured in this environment; retrieval + safety gates fully verified live
4. ✅ Channel-agnostic conversation core + WhatsApp adapter + IVR webhook — real WhatsApp
   webhook and IVR call flow verified live end-to-end against the running stack (real
   `reports` rows created); outbound WhatsApp sends and Twilio recording download
   unverified live, no real Meta/Twilio account in this environment; IVR language
   selection deferred to milestone 5
5. Fisherman mode (roles, PFZ surfaces)
6. Valhalla routing + shelters + public map routing UI

## Verification

- ✅ Drill additions: synthetic oil-spill reports + StubProvider satellite observation →
  satellite component appears in `confidence_components`, instrument stays exactly 0,
  and the assertion is live in `scripts/drill.py` (not just a mocked unit test); six-weight
  tests updated in `test_scoring_engine.py`.
- ✅ Chat: adversarial ("should I evacuate?") verified live to bypass retrieval/LLM and
  return real nearby alerts; retrieval-threshold enforcement verified both live (six real
  questions, see milestone 3) and under mocked unit tests
  (`test_chat_service.py`). A full ~30-question eval set incl. more adversarial phrasings
  is still open for whoever configures a real `ANTHROPIC_API_KEY` and wants to validate
  actual generated answer quality, not just the safety-gate plumbing.
- ✅ IVR: simulated Twilio-style webhook payload sequence → real report with
  `source="ivr"` and the correct pilot-location coordinates (village/cell-tower geocoding
  isn't available in this environment — see milestone 4's "as built" section for why a
  named-location menu stands in for it); verified live against the running stack, not just
  under mocks.
- ✅ WhatsApp: full webhook conversation (trigger → location → hazard → description →
  skip) verified live to create a real report with `source="whatsapp"`; verify-token
  rejection and the no-session help-text fallback also verified live.
- Routing: request from a point inside a drill flood zone → route avoids closed cells
  (assert no polyline vertex inside excluded polygons).
