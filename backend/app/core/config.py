from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://oceanping:oceanping@localhost:5433/oceanping"
    redis_url: str = "redis://localhost:6380/0"
    media_dir: str = "./data/media"

    secret_key: str = "dev-secret-change-me"
    analyst_username: str = "analyst"
    analyst_password: str = "oceanping-dev"
    session_max_age_seconds: int = 12 * 3600

    telegram_bot_token: str = ""

    # Voice-note transcription (phase 1, milestone 3): faster-whisper, CPU-only.
    # "small" is multilingual and runs acceptably on CPU; bump for accuracy.
    whisper_model_size: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    # "embedding" uses sentence-transformers; "keyword" is a light fallback;
    # "finetuned" tries a retrain.py artifact first, degrading to embedding/keyword
    # if it can't load (see nlp_model_version below).
    nlp_mode: str = "embedding"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    classify_threshold: float = 0.35

    # Active-learning loop (phase 1, milestone 4): retrain.py writes classifier
    # artifacts under training_artifacts_dir/<version>/classifier.joblib.
    # nlp_model_version is the canary flag — promotion is a manual config flip,
    # never automatic, so a bad retrain can't silently degrade production.
    training_artifacts_dir: str = "./data/models"
    nlp_model_version: str = ""

    erddap_poll_minutes: int = 10
    rescore_minutes: int = 2
    anomaly_zscore_threshold: float = 3.0
    anomaly_active_hours: float = 2.0
    instrument_radius_km: float = 25.0

    # Spatiotemporal coherence window
    coherence_minutes: int = 30
    # Semantic dedup / incident merge window
    incident_window_hours: int = 6

    hotspot_window_hours: int = 6
    hotspot_min_cluster_size: int = 5

    rate_limit_reports_per_reporter: int = 5   # per 10 minutes
    rate_limit_reports_per_cell: int = 60      # per 10 minutes

    corroborated_threshold: float = 0.6

    # Alerts (phase 1): watch tier needs an instrument-consistent anomaly AND
    # this many independent reporters on the incident; warning is analyst-only.
    alert_min_watch_reporters: int = 3
    alert_default_expiry_hours: float = 6.0
    # H3 k-ring radius (res 8) used to build a subscriber's geofence from one point.
    subscription_radius_rings: int = 10

    # Delivery worker (phase 1, milestone 2): alert issuance enqueues here rather
    # than blocking on channel I/O; the worker process drains it.
    delivery_queue_key: str = "oceanping:alert_deliveries"
    delivery_queue_timeout_seconds: int = 5

    # Web push (VAPID). Empty public key means the frontend won't offer browser
    # alerts and the worker skips web_push subscriptions.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_admin_email: str = "admin@oceanping.example"

    # SMS: "console" (log only, default/local), "twilio", or "exotel". Both
    # provider adapters speak plain HTTPS so no extra SDK dependency is needed.
    sms_provider: str = "console"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    exotel_sid: str = ""
    exotel_token: str = ""
    exotel_from_number: str = ""
    exotel_subdomain: str = "api.exotel.com"

    # Satellite corroboration (phase 2, milestone 2): "stub" is the only
    # provider exercised without real credentials — deterministic, local-dev
    # only. sentinel_hub/earth_engine are real adapter shells gated on the
    # credentials below; the actual scene-scoring recipe is deferred (see the
    # phase-2 plan) since there's no way to verify it without an account.
    satellite_provider: str = "stub"
    satellite_poll_minutes: int = 60
    satellite_active_incident_hours: float = 24.0
    sentinel_hub_client_id: str = ""
    sentinel_hub_client_secret: str = ""
    earth_engine_service_account_json: str = ""

    # RAG chatbot (phase 2, milestone 3): retrieval is always on (it's just
    # sentence-transformer cosine similarity over rag_documents, no API key
    # needed); an empty anthropic_api_key means /chat always returns
    # chat_helpline_message instead of a generated answer — same credential-
    # gated-degrade pattern as every other real adapter in this app. The
    # retrieval threshold is enforced in code (chat/service.py), not just in
    # the system prompt, so a low-relevance question never reaches the LLM.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    # Calibrated against the live corpus, not guessed: differently-phrased but
    # genuinely on-topic questions ("who do I contact in an emergency?" against
    # faq-helpline) scored ~0.34-0.40 cosine similarity with this multilingual
    # sentence-transformer model, closely-phrased ones scored 0.75+, and clearly
    # off-topic questions ("what's the capital of France?") scored ~0.05-0.10.
    # 0.45 (a round-number guess) would have rejected real, correctly-matched
    # questions - 0.28 sits with margin above the off-topic cluster and below
    # the loosely-phrased-but-relevant one.
    chat_retrieval_threshold: float = 0.28
    chat_helpline_message: str = (
        "I can't confidently answer that from official sources. Please check "
        "current alerts on the map, or contact your local disaster helpline "
        "(India: dial 112, or the NDMA control room at 1078)."
    )

    # WhatsApp Business Cloud API (phase 2, milestone 4): conversational report
    # submission through the same channel-agnostic flow Telegram uses
    # (ingest/report_conversation.py). An empty access token means the
    # inbound webhook still parses messages and create_report() still runs,
    # but every outbound reply is skipped - same credential-gated-degrade
    # pattern as Twilio/Exotel/Sentinel Hub elsewhere in this app. There is no
    # Meta Business/WhatsApp account in this environment, so outbound sends
    # and the Meta-side webhook subscription are unverified live; inbound
    # payload parsing is verified against Meta's documented webhook JSON shape
    # under mocked tests.
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_api_version: str = "v20.0"

    # IVR (phase 2, milestone 4): a Twilio Voice webhook returning TwiML.
    # Exotel's classic Exoml call-control markup is Twilio-compatible for the
    # Gather/Say/Record verbs this flow uses, so the same endpoint serves
    # either provider without a code fork. Reuses twilio_account_sid/
    # twilio_auth_token above to authenticate the recording download - no new
    # credential needed. The "location" step is a short menu of named pilot
    # coastal locations (modules/ivr/locations.py) rather than the caller's
    # real registered village or cell-tower area, since this environment has
    # no telco integration to look either of those up against.
    ivr_recording_max_seconds: int = 60

    # Fisherman mode (phase 2, milestone 5): PFZ advisories are a deterministic
    # local stub (see modules/fisherman/pfz.py for why — INCOIS's real PFZ
    # product has no machine-readable feed, confirmed live). Real INCOIS PFZ
    # bulletins are reissued roughly every 2-3 days; this refresh interval
    # mirrors that cadence rather than the much-faster ERDDAP poll.
    pfz_refresh_hours: float = 24.0
    pfz_validity_hours: float = 60.0

    # Evacuation routing (phase 2, milestone 6): unlike every other external
    # integration in this app, Valhalla is real and running, not a stub — see
    # docker/valhalla/ + scripts/routing/fetch_osm_extract.sh. It's gated on
    # reachability rather than a credential: a self-hosted routing engine has
    # no API key, but its tiles take a one-time build after the OSM extract
    # script runs, so "not up yet" is a real, expected degrade mode. Hazard
    # geometry is only excluded from a route once it's cleared the same
    # escalation gate as everywhere else in this app (corroborated+ incidents,
    # analyst-issued warning alerts) — never from raw citizen report volume.
    valhalla_url: str = "http://valhalla:8002"
    routing_default_costing: str = "pedestrian"
    routing_active_incident_hours: float = 24.0

    # Inundation model (phase 3, milestone 1): a bathtub model over a real
    # Copernicus DEM GLO-30 extract for the pilot area (see
    # scripts/inundation/) — the per-cell elevation table is real ingested
    # data, not a stub. Live alert/routing wiring is gated on a fresh reading
    # of this sensor variable, same credential/data-gated-degrade pattern as
    # every other real integration here: stations.json's real INCOIS tide
    # gauge is disabled (no dataset id available), so in an untouched
    # environment this wiring stays a no-op until drill.py injects one or a
    # real gauge is configured.
    inundation_reference_variable: str = "water_level"
    inundation_wire_hours: float = 2.0

    # Auto-SITREPs (phase 3, milestone 2): hourly draft in NDMA format built
    # only from verified DB state (report/incident counts, alerts issued,
    # hotspot movement, shelter resources) — see modules/sitrep/. This is
    # both the scheduler cadence and the reporting window length when there's
    # no prior SITREP to anchor to; the normal case just starts the window at
    # the previous SITREP's period_end, so windows tile without gaps or
    # overlap regardless of this setting.
    sitrep_period_hours: float = 1.0

    # Forecasting (phase 3, milestone 3): harmonic-trend sensor forecasts and
    # hazard-front propagation forecasts, both regenerated on this cadence —
    # see modules/forecast/. Deliberately much slower than erddap_poll_minutes
    # or rescore_minutes: a 1-3h-horizon forecast doesn't need to be refit
    # every couple of minutes, and doing so would just relearn the same trend
    # from near-identical data.
    forecast_interval_minutes: int = 30
    # How much trailing history a sensor forecast fits its harmonic-trend
    # regression against — same window as the anomaly baseline.
    forecast_sensor_baseline_days: float = 7.0
    forecast_sensor_horizon_hours: float = 3.0
    forecast_sensor_step_minutes: int = 30
    # An incident still receives new propagation forecasts for this long after
    # its last report — mirrors incident_window_hours.
    forecast_propagation_incident_hours: float = 6.0

    # Rumor tracker (phase 3, milestone 4): clusters citizen reports by text-
    # embedding similarity (not spatial adjacency — unlike incident merge, the
    # same rumor can spread across locations that would never merge into one
    # incident) and persists a Narrative only for a cluster that also
    # contradicts something real — see modules/narratives/. Same cadence
    # class as forecasting: a rumor doesn't need re-detection every couple of
    # minutes either.
    narrative_interval_minutes: int = 30
    narrative_window_hours: float = 12.0
    narrative_sim_threshold: float = 0.55

    # Mobile offline queue (phase 3, milestone 5): how far back a client-
    # supplied observation time may reach before it's clamped (see
    # ingest/service.py::clamp_observed_at). This bounds how far into the past
    # a public caller can place a report, since both the coherence window and
    # incident merge key off that timestamp — long enough for a genuinely
    # out-of-coverage phone (a fishing trip, a night without signal), short
    # enough that backdating can't reach an unrelated event.
    offline_max_report_age_hours: float = 24.0

    # LoRaWAN / IoT pilot (phase 3, milestone 6): a real EMQX MQTT broker feeds
    # the same sensor_readings hypertable and anomaly path as ERDDAP — a node
    # is just a Station with provider "iot" (see modules/iot/). The bridge
    # process (python -m app.modules.iot.bridge) subscribes to
    # `mqtt_topic` and writes what it receives. Anonymous access is fine for a
    # single-node pilot broker on a private network; a real deployment would
    # put per-node credentials + TLS in front (the paho client already takes
    # username/password, wired from these settings when set).
    mqtt_host: str = "emqx"
    mqtt_port: int = 1883
    mqtt_topic: str = "oceanping/iot/+/telemetry"
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_client_id: str = "oceanping-iot-bridge"
    # A reading timestamped further ahead than this (clock skew on a cheap
    # node) is pinned to now, so a bad clock can't park a future reading where
    # anomaly detection would treat it as the freshest sample forever.
    iot_max_future_skew_minutes: float = 5.0

    # Recovery module (phase 3, milestone 7): post-disaster damage assessment,
    # mutual-aid board, and the missing/found-person registry (see
    # modules/recovery/). Mutual-aid matching is a live proximity+category
    # scan over currently-open requests/offers (pilot volumes are small enough
    # this needs no background job — see engine.py::match_aid).
    recovery_mutual_aid_max_km: float = 5.0
    # Fuzzy name-similarity floor (difflib.SequenceMatcher ratio, stdlib —
    # see engine.py::fuzzy_name_score) before a missing/found pair is even
    # surfaced as a *candidate* to an analyst; resolution is always a human
    # decision, this only controls what's worth showing them.
    recovery_missing_match_threshold: float = 0.72
    # If both entries carry a location, a candidate beyond this radius is
    # dropped even with a strong name match — "Ramesh" found 400km away from
    # where "Ramesh" went missing is almost certainly a different person.
    # Entries missing a location (phone-only reports) skip this gate.
    recovery_missing_match_max_km: float = 25.0
    # Privacy retention (phase 3, milestone 7's named "strict privacy...
    # retention limit"): missing/found-person rows carry a name, description,
    # and often a photo of an identifiable, often vulnerable person, and this
    # registry has no purpose once a case is old — see
    # recovery/service.py::purge_expired_missing_persons, a real scheduled
    # job, not just a documented policy.
    recovery_missing_person_retention_days: float = 180.0

    # The architecture split (phase 3, milestone 8): "inline" is every prior
    # milestone's behavior, unchanged — create_report() runs NLP, dedup, and
    # scoring synchronously in one transaction, no Redpanda involved at all.
    # "bus" splits that into a gateway (validate + rate-limit + produce) plus
    # three independent consumer deployments (nlp/dedup/scoring, see
    # modules/ingest/consumers/) reading a real Redpanda topic chain — see
    # modules/ingest/bus.py. Default stays "inline" so the existing stack,
    # every existing drill assertion, and every prior milestone's live
    # verification keep working unchanged; "bus" is an alternate deployment
    # mode (docker-compose.yml's "split" profile), not a hard cutover, per
    # this milestone's own risk note about freezing behavior during a split.
    pipeline_mode: str = "inline"
    kafka_bootstrap_servers: str = "redpanda:9092"
    # Consumer-group lag on reports.raw beyond which core/scheduler.py's
    # analytics jobs (SITREPs, forecasts, narratives, satellite polling — the
    # plan's own "analytics consumers") defer themselves for one tick, so a
    # 50x report-ingestion surge can't starve the DB the critical ingest ->
    # dedup -> score -> alert path also needs. Only checked when
    # pipeline_mode is "bus"; inline mode has no lag concept and never sheds.
    load_shed_lag_threshold: int = 500

    # CAP interop (phase 4, milestone 1): every issued alert also renders as a
    # real CAP 1.2 document (see modules/alerts/cap.py), validated in tests
    # against the actual OASIS-published CAP-v1.2.xsd — so agency integration
    # (NDMA/SACHET) becomes a config/partnership step whenever it lands, not
    # an engineering project (the plan's own framing: "build the generator
    # before the partnership exists"). No real partner sender identity exists
    # yet, so cap_sender/cap_sender_name are pilot placeholders, swappable via
    # env the day one does. Inbound ingestion (cap_ingest.py + cap_service.py)
    # treats an *active* official CAP advisory covering a report's location as
    # a corroboration signal in scoring/service.py — cap_ingest_api_key gates
    # the ingestion webhook the same credential-checked-if-set,
    # skipped-if-empty way whatsapp_app_secret gates that webhook's signature.
    cap_sender: str = "pilot@oceanping.example"
    cap_sender_name: str = "OceanPing Pilot"
    public_base_url: str = "http://localhost:3000"
    cap_ingest_api_key: str = ""

    # Open data & research API (phase 4, milestone 3): anonymized, H3-
    # aggregated event datasets for researchers, gated by per-consumer API
    # keys (see modules/opendata/). The k-anonymity floor is enforced on the
    # *true* count before any DP noise is added — noise can't retroactively
    # hide that a raw count of 1 was ever computed, so a group under the
    # floor is dropped outright rather than noised. open_data_h3_resolution
    # is deliberately coarser than the internal res-8 report grid (see
    # geo/h3utils.py) so aggregation groups start out bigger before k-anon
    # even has to suppress anything.
    open_data_h3_resolution: int = 6
    open_data_k_anonymity_min: int = 5
    open_data_dp_epsilon: float = 1.0
    open_data_rate_limit_per_hour: int = 200
    # DPDP-style retention: once a report is this many months old, its exact
    # lat/lon/geom are permanently overwritten with its H3 cell's centroid —
    # see modules/opendata/service.py::anonymize_expired_reports, a real
    # scheduled job (core/scheduler.py), the same "policy enforced by code,
    # not just written down" posture as recovery_missing_person_retention_days.
    open_data_retention_months: float = 12.0

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
