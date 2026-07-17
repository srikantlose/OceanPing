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

    # "embedding" uses sentence-transformers; "keyword" is a light fallback.
    nlp_mode: str = "embedding"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    classify_threshold: float = 0.35

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

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
