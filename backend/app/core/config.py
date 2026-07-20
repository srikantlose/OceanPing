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

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
