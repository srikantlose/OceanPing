# Phase 1 — Intelligence Core & Tiered Alerting (blueprint weeks 7–14, gap plan)

**Status: 🟡 in progress — Milestone 1 built (July 2026).** Prereq: phase 0 (built).
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
2. Delivery worker + subscriptions + web push + SMS stub
3. Whisper voice reports through the bot
4. Training export + retrain script + MuRIL swap behind `classify()`
5. Hearsay signal into scoring + correction UI

## Verification

- Unit: tier-eligibility function (auto-warning impossible), delivery adapter contract,
  hearsay multiplier math.
- E2E: extend `scripts/drill.py` — after corroboration, issue a watch alert as analyst,
  assert a subscribed drill Telegram chat (or ConsoleAdapter log) received it and
  `alert_deliveries` + audit chain record it.
- Voice: send a Tamil voice note to the bot → report appears with transcript + correct class.
- Retrain dry-run: `python backend/training/retrain.py --dry-run` produces metrics report.
