# Phase 4 — Scale, Openness & Sustainability (months 11+, gap plan)

**Status: 🔲 planned.** Prereqs: phase 3 (service split, mobile app, verified-event corpus).
This phase is thematic rather than strictly sequential — items are largely independent
tracks; pick by pilot/partner pull.

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

## Milestones

1. CAP generator + inbound CAP corroboration
2. Hazard registry refactor (config-only new hazards)
3. Open-data pipeline with DP + retention jobs
4. Multi-state tenanting + per-district metrics
5. Vulnerability-aware alerting (with DPO review)
6. AR mode; federated learning spike → go/no-go
7. Insurance trigger API pilot

## Verification

- CAP: generated documents validate against the CAP 1.2 XSD; round-trip inbound test.
- DP: re-identification test on released aggregates (no cell below k threshold).
- Tenanting: cross-state analyst cannot read another state's exact coordinates (authz test).
- Hazard registry: add a toy hazard purely via config → report→score→alert path works in
  drill with zero code diff outside the registry.
