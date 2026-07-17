# Phase 1 — Intelligence Core & Tiered Alerting (blueprint weeks 7–14, gap plan)

**Status: 🟡 in progress — Milestones 1–4 built (July 2026).** Prereq: phase 0 (built).
Everything here stays inside the monolith.

## Milestone 1 — as built

The alerts engine, composer, and public layer are live:

- `backend/app/modules/alerts/engine.py` — pure tier-eligibility function. Structurally
  incapable of returning `"warning"` (no `analyst`/`issued_by` parameter exists in its
  signature — enforced by `test_eligible_tier_signature_has_no_analyst_parameter` and a
  brute-force sweep in `test_eligible_tier_can_never_return_warning`).
- `backend/app/modules/alerts/service.py` — `sync_incident_alert()` (auto advisory/watch,
  hooked into `scoring/service.py::rescore_report` and `apply_verification`),
  `issue_warning()` (the only path to warning tier — always analyst-attributed),
  `expire_alert()`, and a synchronous best-effort Telegram `_broadcast()`.
- `backend/app/modules/alerts/router.py` — `POST /analyst/incidents/{id}/warning`,
  `POST /analyst/alerts/{id}/expire`, `GET /analyst/alerts`, public `GET /map/alerts`.
- Telegram bot: `/subscribe` (shares a location → H3 k-ring geofence) and `/unsubscribe`
  (`modules/ingest/bot_runner.py`).
- Frontend: alert polygon layer in `MapView.tsx`, "Issue warning" / "Expire" actions and
  an active-alerts panel in `AnalystDashboard.tsx`, tier colors in `lib/palette.ts`.
- Migration `0002_alerts.py`: `alerts`, `subscriptions`, `alert_deliveries`,
  `reporters.role`.
- Tests: `backend/tests/test_alerts_engine.py` (11 cases). `scripts/drill.py` now issues
  and expires a warning and asserts the public map reflects both, on top of the existing
  end-to-end flow — verified passing against the live stack.

**Known limitation, by design (documented, not a bug):** broadcast is synchronous and
in-process — Milestone 2's delivery worker replaces this once channel count/volume
demands it.

**Observed pre-existing quirk (not introduced by this milestone, not yet root-caused):**
during manual post-drill inspection, a `station_anomalies` row transitioned from
`active=true` to `active=false` without a corresponding `anomaly.resolved` audit entry,
and outside of any traceable `detect_anomalies()` invocation (checked via APScheduler
logs). `detect_anomalies()`'s refresh branch (`sensors/service.py`) doesn't audit-log
in-place refreshes, only creation/resolution, which made this hard to pin down. Doesn't
affect correctness of the escalation gate — reports never reach `verified`/`warning`
without a human regardless of anomaly flapping — but worth instrumenting further (e.g.
audit-log every active-state transition, including refreshes) before phase 2 leans
harder on instrument corroboration.

## Milestone 2 — as built

The delivery fan-out worker replaces Milestone 1's synchronous in-process broadcast:

- `backend/app/modules/delivery/queue.py` — `enqueue_alert()` / `dequeue_alert()`, a
  best-effort Redis list queue (`oceanping:alert_deliveries`, configurable). Enqueue never
  raises — a Redis outage logs a warning and lets report ingestion/scoring proceed.
- `backend/app/modules/delivery/adapters.py` — one `send(alert, subscription) ->
  DeliveryResult` contract: `TelegramAdapter`, `WebPushAdapter` (`pywebpush` + VAPID keys),
  and SMS via `ConsoleAdapter` (local default) / `TwilioAdapter` / `ExotelAdapter`, picked
  by `settings.sms_provider`.
- `backend/app/modules/delivery/worker.py` — standalone process (`python -m
  app.modules.delivery.worker`, its own compose service) draining the queue and fanning out
  to matching subscriptions (geofence + min-tier filter), recording every attempt in
  `alert_deliveries`.
- `backend/app/modules/delivery/router.py` — `POST /subscribe/web-push`,
  `POST /subscribe/sms` (+ unsubscribe counterparts), `GET /subscribe/vapid-public-key`.
- `Subscription.meta` JSONB column (migration `0003_delivery.py`) holds channel-specific
  extras (the web-push `push_subscription` blob) without growing the core columns.
- Frontend: `frontend/public/sw.js` + `frontend/lib/webpush.ts` wire a browser "Enable
  alerts" button to the web-push subscribe endpoint.
- Tests: `test_delivery_adapters.py`, `test_delivery_queue.py`, `test_delivery_worker.py`.

**Race condition found and fixed:** `alerts/service.py::sync_incident_alert()` is called
from inside `scoring/service.py::rescore_recent()`'s per-report loop, which calls
`db.commit()` **once**, after the loop — so `enqueue_alert()` fires (and the worker can
dequeue) before the issuing transaction actually commits. A worker reading the alert row on
its own connection could see nothing yet under a plain `db.get()`. Rather than restructure
the batch-commit pattern (used deliberately for performance), `worker.py` treats the lookup
as eventually consistent: `_load_alert_with_retries()` retries up to 5 times with a 0.2s
backoff, which is safe under Postgres's default READ COMMITTED isolation (`core/db.py` — no
override) since each retry gets a fresh snapshot. Covered by
`test_load_alert_with_retries_rides_out_the_commit_race` and
`..._gives_up_after_max_attempts`; verified live via `scripts/drill.py`, which polls
`/analyst/alerts/{id}/deliveries` after both the auto-proposed alert and the analyst-issued
warning and asserts delivery attempts landed.

## Milestone 3 — as built

Voice notes sent to the Telegram bot now become report text instead of requiring typing:

- `backend/app/modules/ingest/voice.py` — `transcribe(audio_bytes) -> str | None`, lazy-loads
  a `faster-whisper` (CTranslate2, CPU-only) model on first use, mirroring the
  lazy-singleton pattern already used by `nlp/classifier.py::_load_model()`. Best-effort by
  design: a failed load or bad decode returns `None` rather than raising, so a voice note
  degrades to a photo-only report instead of breaking the bot conversation. Model choice
  (`whisper_model_size`, default `"small"`) and device/compute type are config (`core/config.py`),
  no code change needed to size up for accuracy vs. a Raspberry-Pi-class deployment.
- `backend/app/modules/ingest/bot_runner.py` — new `on_voice` handler registered in the
  `DESCRIPTION` conversation state (`filters.VOICE`, alongside the existing text handler);
  downloads the voice OGG, transcribes off the event loop via `asyncio.to_thread`, and feeds
  the transcript into the same `context.user_data["text"]` the typed-description path uses —
  so classification, language detection, and scoring treat a voice report identically to a
  typed one from `create_report()`'s perspective.
- Tests: `test_voice_transcription.py` — covers segment joining, empty-transcript handling,
  decode-error fallback, and that a failed model load is cached (`_model_failed`) rather than
  retried on every voice note.

**Not yet done from the original plan:** Bhashini ASR as a configurable alternative — deferred
until there's a concrete need for a hosted-API fallback (faster-whisper's multilingual
support already covers the drill script's Tamil/Hindi/English mix reasonably well offline).
**Not verified live:** no `TELEGRAM_BOT_TOKEN` is configured in this environment, so the bot
process doesn't run here — the transcription unit is verified, but an actual "send a Tamil
voice note to the bot" pass is still open per this plan's Verification section, to be done by
whoever configures a real bot token.

## Milestone 4 — as built

The active-learning loop's export + retrain + swap seam is live, deliberately scoped down
from a full MuRIL/IndicBERT fine-tune to a linear probe until labeled volume justifies more:

- `backend/app/models.py` — `TrainingExample` (report_id, text, lang, hazard_type, outcome)
  and `ModelVersion` (name, artifact_path, metrics, training_examples_count). Migration
  `0004_training.py` (follows `0002`'s `Base.metadata.create_all` pattern for new tables).
- `backend/app/modules/scoring/service.py::apply_verification()` — one-line hook per the
  plan: every verify/reject inserts a `TrainingExample` row alongside the existing
  `Verification` row.
- `backend/training/train_classifier.py` — `train(examples)`: encodes text with the same
  multilingual sentence-transformer `classifier.py` already uses in embedding mode, fits a
  `LogisticRegression` head over those embeddings, holds out an eval split (falls back to
  reporting training-set fit, clearly flagged `held_out_eval: false`, when there's too
  little data to split — the common case pre-pilot). Drops any hazard class with fewer than
  2 examples rather than erroring. Returns `None` (not an exception) when there aren't at
  least 2 usable classes — "not enough data yet" is an expected, handled state, not a bug.
- `backend/training/retrain.py` — the operational wrapper (`python -m training.retrain
  [--dry-run]`): exports verified `training_examples`, calls `train_classifier.train()`,
  prints metrics, and (unless `--dry-run`) writes `{training_artifacts_dir}/<version>/
  classifier.joblib` plus a `ModelVersion` row. Version name is a timestamp
  (`finetuned-YYYYMMDD-HHMMSS`); nothing is auto-promoted.
- `backend/app/modules/nlp/classifier.py` — new `NLP_MODE=finetuned`: lazy-loads the
  joblib artifact named by `NLP_MODEL_VERSION` (mirrors `_load_model()`'s load-once-cache-
  failure pattern exactly, down to a second lock/globals pair). A missing/unset version or
  a corrupt artifact degrades to embedding mode, then keyword, same as every other failure
  path in this module — `classify()`'s signature and callers are untouched, per the plan.
- `docker-compose.yml` — new `models` named volume mounted at `/srv/data/models` on
  `backend` and `bot` (the two services that call `classify()`); without it, artifacts
  written by `retrain.py` would vanish on the next container recreate. `worker` doesn't
  classify, so it doesn't need the mount. `NLP_MODEL_VERSION` env var threaded through
  alongside the existing `NLP_MODE`.
- `backend/Dockerfile` — `COPY training ./training`, so `python -m training.retrain` runs
  the same way `worker.py` does, inside the existing backend image.
- Tests: `test_train_classifier.py` (class-dropping, embedder-unavailable, separable-fit
  cases, using a fake deterministic embedder so no real model download is needed),
  `test_retrain.py` (export filtering, `--dry-run` vs. real write, using fake DB objects —
  no test in this suite touches a real Postgres connection, this one included),
  `test_classifier_finetuned.py` (finetuned-mode happy path, degrade-to-embedding,
  degrade-to-keyword, and the two `_load_finetuned()` failure-caching cases).

**Verified live, not just under mocks:** ran `python -m training.retrain --dry-run` against
the live stack with zero verified examples (correctly reports "not enough labeled data" and
exits 1); seeded a handful of verified `training_examples` across two hazard classes,
re-ran `retrain.py` for real against the actual sentence-transformer model (not a fake) —
it exported the rows, trained, and wrote a real artifact + `model_versions` row; then
loaded that exact artifact via `NLP_MODE=finetuned` / `NLP_MODEL_VERSION=<version>` in a
fresh Python process and confirmed `classify()` returns `mode="finetuned"` predictions from
it. The seed data was removed afterward — it was verification scaffolding, not real drill
output. Also confirmed the full test suite runs clean at 78 tests and `scripts/drill.py`
still exercises the analyst-verify path (which now doubles as the training-export hook)
without regressions.

**Deliberately not done yet, per the plan's own risk note:** a real MuRIL/IndicBERT
fine-tune. The plan calls out that a transformer fine-tune needs `training_examples` to
clear roughly 500 rows before it's worth the training cost; at real-world verification
volume today, a linear probe over frozen embeddings is the honest scope for "the swappable
seam behind `classify()`" — swapping in an actual fine-tune later is a `train_classifier.py`
change, not a `classify()` or ingest-pipeline change, exactly as designed. Also not done:
milestone 5's correction UI, so rejected reports are recorded (`outcome="reject"`) but
`retrain.py` doesn't train on them yet — a reject doesn't tell you the *correct* hazard
type, just that the report wasn't credible, so using them as training labels needs the
"wrong hazard? which?" correction prompt milestone 5 adds.

## Remaining milestones (unchanged from original plan below)

## Goals

Close the loop from *detection* to *notification*: tiered alerts with hard safety gates and
real delivery channels; upgrade NLP from prototype-matching to trained models; turn analyst
decisions into a self-improving training loop.

## Already built (do not rebuild)

- Confidence scoring + escalation gate — `backend/app/modules/scoring/{engine,service}.py`
- Analyst review queue + verify/reject + trust ladder — `modules/analyst/router.py`, `apply_verification()`
- Hotspots + incidents — `modules/geo/hotspots.py`, `modules/nlp/dedup.py`
- Audit chain — `modules/scoring/audit.py` (**every alert issuance must go through `append_audit`**)
- Telegram bot skeleton with conversation flow — `modules/ingest/bot_runner.py`
- Labeled data already accumulating in the `verifications` table

## Gap work breakdown

### 1. Alerting engine — new module `backend/app/modules/alerts/`

- `engine.py`: tier model `advisory < watch < warning`. Pure tier-eligibility function:
  - advisory: any corroborated incident
  - watch: corroborated incident with instrument component > 0 and ≥ N independent reporters
  - warning: **only** analyst-issued (reuse the no-citizen-only-escalation principle from
    `scoring/service.py::rescore_report`); the API must make auto-warning impossible, not
    just discouraged.
- `service.py`: `propose_alert(incident)` (auto, advisory/watch), `issue_alert(...)`
  (analyst-confirmed; audit-logged with tier, geometry = incident H3 cells + k-ring buffer,
  expiry, issuing analyst).
- `router.py`: analyst alert composer endpoints (`POST /analyst/alerts`, list/expire), and
  public `GET /map/alerts` (GeoJSON, active alerts only).
- Dashboard: alert composer panel in `frontend/components/AnalystDashboard.tsx` + alert
  layer in `MapView.tsx` (status palette, `lib/palette.ts` — warning uses `--critical`).

### 2. Delivery fan-out — new module `backend/app/modules/delivery/`

- `subscriptions` model: (channel, address, h3_cells[] geofence, min_tier, lang).
- Channel adapters behind one interface (`send(alert, subscription) -> DeliveryResult`):
  - **Telegram**: extend bot with `/subscribe` (share location → district cells) and
    `/unsubscribe`; broadcast via bot API. First channel — free, testable today.
  - **Web push**: `pywebpush` + service worker in the frontend.
  - **SMS**: provider adapter interface with `TwilioAdapter` / `ExotelAdapter` and a
    `ConsoleAdapter` stub used locally (no paid account needed until pilot).
- Fan-out runs in a dedicated worker process (same image, new compose service
  `worker: command: python -m app.modules.delivery.worker`) consuming a Redis list queue —
  do NOT block API requests on delivery. Record every attempt in `alert_deliveries`.

### 3. NLP upgrades — inside `backend/app/modules/nlp/`

- **MuRIL/IndicBERT fine-tune** replacing prototype matching: keep the
  `classifier.classify()` signature exactly (callers in `ingest/service.py` untouched).
  Training script `backend/training/train_classifier.py` reads exported labels (below);
  artifacts versioned in `model_versions` table; `NLP_MODE=finetuned` selects it, with the
  existing embedding/keyword fallbacks kept as degradation path.
- **Whisper voice notes**: bot handler for `filters.VOICE` → `faster-whisper` (small,
  CPU) → transcript → same `create_report(text=…)` path. Bhashini ASR as configurable
  alternative (free national API; needs registration).
- **First-person vs hearsay classifier**: same fine-tune run, second head/label. Output
  stored in `confidence_components.detail`; feed as a multiplier on the coherence
  component (hearsay counts half) in `scoring/service.py` — weights table itself unchanged.
- **Transliteration normalization** pre-step (indic-transliteration lib) before language ID.

### 4. Active-learning loop

- Export: every `apply_verification()` call also inserts a `training_examples` row
  (text, lang, final hazard_type, verify/reject outcome) — one-line hook in
  `scoring/service.py`.
- `backend/training/retrain.py`: weekly (documented cron/manual), retrains, evaluates
  against a frozen eval set, writes `model_versions` row with metrics; promotion is a
  manual config flip (canary: `NLP_MODEL_VERSION=`).
- Analyst dashboard: correction UI — when rejecting, ask "wrong hazard type? which?" so
  rejections become labeled data, not just negatives.

## Data model changes (one Alembic migration)

`alerts` (tier, geom cells JSONB, incident_id, issued_by, issued_at, expires_at, message
per-lang JSONB) · `alert_deliveries` (alert_id, subscription_id, status, attempted_at) ·
`subscriptions` · `training_examples` · `model_versions`. Reporters: add `role` column
(default `citizen`) now to avoid a later migration (used by phase 2 fisherman mode).

## New infra

Compose service `worker` (same backend image). Optional: `faster-whisper` model in the
`hfcache` volume. No new datastores.

## External dependencies & risks

- Twilio/Exotel accounts — deferred via ConsoleAdapter; don't block the phase on procurement.
- MuRIL fine-tune needs labeled volume: if `training_examples` < ~500 rows, bootstrap with
  drill-generated + hand-labeled synthetic corpus first (document provenance).
- Alert fatigue: per-district daily alert budget enforced in `alerts/service.py`, tracked
  metric from day one.

## Milestones

1. ✅ Alerts module + composer UI + public alert layer (Telegram broadcast only)
2. ✅ Delivery worker + subscriptions + web push + SMS stub
3. ✅ Whisper voice reports through the bot
4. ✅ Training export + retrain script + linear-probe swap behind `classify()` (full
   MuRIL/IndicBERT fine-tune deferred until labeled volume clears the ~500-row threshold)
5. Hearsay signal into scoring + correction UI

## Verification

- Unit: tier-eligibility function (auto-warning impossible), delivery adapter contract,
  hearsay multiplier math.
- E2E: extend `scripts/drill.py` — after corroboration, issue a watch alert as analyst,
  assert a subscribed drill Telegram chat (or ConsoleAdapter log) received it and
  `alert_deliveries` + audit chain record it.
- Voice: send a Tamil voice note to the bot → report appears with transcript + correct class.
- ✅ Retrain dry-run: `python -m training.retrain --dry-run` (inside the backend container)
  produces a metrics report — verified both the "not enough data" and real-metrics cases
  against the live stack.
