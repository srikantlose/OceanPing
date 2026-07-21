import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geometry
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.core.db import Base

EMBEDDING_DIM = get_settings().embedding_dim

HAZARD_TYPES = [
    "coastal_flooding",
    "storm_surge",
    "high_waves",
    "tsunami",
    "rip_current",
    "oil_spill",
    "algal_bloom",
    "erosion",
    "other",
]

REPORT_STATUSES = ["unverified", "corroborated", "verified", "rejected"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Reporter(Base):
    __tablename__ = "reporters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    source: Mapped[str] = mapped_column(String(16))  # telegram | web | drill
    external_id_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    trust_score: Mapped[float] = mapped_column(Float, default=0.5)
    verified_count: Mapped[int] = mapped_column(Integer, default=0)
    debunked_count: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(16), default="citizen")  # citizen | fisherman | volunteer (phase 2)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    reports: Mapped[list["Report"]] = relationship(back_populates="reporter")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    hazard_type: Mapped[str] = mapped_column(String(32), index=True)
    h3_cells: Mapped[list] = mapped_column(JSONB, default=list)
    centroid_lat: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)
    report_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="unverified", index=True)
    max_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    centroid_embedding: Mapped[list | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    reports: Mapped[list["Report"]] = relationship(back_populates="incident")


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reporters.id"), index=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("incidents.id"), nullable=True, index=True
    )
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326))
    h3_cell: Mapped[str] = mapped_column(String(16), index=True)
    hazard_type: Mapped[str] = mapped_column(String(32), index=True)
    urgency: Mapped[str] = mapped_column(String(8), default="medium")  # low | medium | high
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    lang: Mapped[str] = mapped_column(String(8), default="und")
    source: Mapped[str] = mapped_column(String(16))  # telegram | web | drill
    status: Mapped[str] = mapped_column(String(16), default="unverified", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_components: Mapped[dict] = mapped_column(JSONB, default=dict)
    embedding: Mapped[list | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    # Client-generated idempotency key (phase 3, milestone 5): the mobile app's
    # offline queue retries a submission until it gets an answer, and a reply
    # lost on a flaky link is indistinguishable from a request that never
    # arrived — so without this a bad network turns one sighting into several
    # reports, which is exactly the kind of inflation the coherence signal must
    # never see. Null for reports from channels that don't queue (web form,
    # Telegram, IVR, drill).
    client_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    reporter: Mapped[Reporter] = relationship(back_populates="reports")
    incident: Mapped[Incident | None] = relationship(back_populates="reports")
    media: Mapped[list["MediaAsset"]] = relationship(back_populates="report")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reports.id"), index=True)
    path: Mapped[str] = mapped_column(String(512))
    phash: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    exif: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    report: Mapped[Report] = relationship(back_populates="media")


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    provider: Mapped[str] = mapped_column(String(128))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326))
    variables: Mapped[list] = mapped_column(JSONB, default=list)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SensorReading(Base):
    __tablename__ = "sensor_readings"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    station_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    variable: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float] = mapped_column(Float)


class StationAnomaly(Base):
    __tablename__ = "station_anomalies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"), index=True)
    variable: Mapped[str] = mapped_column(String(64))
    zscore: Mapped[float] = mapped_column(Float)
    value: Mapped[float] = mapped_column(Float)
    baseline_mean: Mapped[float] = mapped_column(Float)
    baseline_std: Mapped[float] = mapped_column(Float)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class Verification(Base):
    __tablename__ = "verifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reports.id"), index=True)
    analyst: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(16))  # verify | reject
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event_type: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


ALERT_TIERS = ["advisory", "watch", "warning"]


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    hazard_type: Mapped[str] = mapped_column(String(32), index=True)
    tier: Mapped[str] = mapped_column(String(16), index=True)  # advisory | watch | warning
    h3_cells: Mapped[list] = mapped_column(JSONB, default=list)
    message: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"en": "...", ...}
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | expired
    issued_by: Mapped[str | None] = mapped_column(String(128), nullable=True)  # analyst username; None = automatic
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Snapshot of the bathtub model at issue/upgrade time for water-level-relevant
    # hazards (see scoring/engine.py's HAZARD_VARIABLES) — a fixed list rather than
    # recomputed on every read, so a later tide change doesn't retroactively change
    # what an already-issued alert claimed. Empty when there's no fresh gauge
    # reading to base a prediction on (see inundation/service.py).
    predicted_flooded_cells: Mapped[list] = mapped_column(JSONB, default=list)
    # Propagation-forecast pre-alert cells (phase 3, milestone 3): the nearest-
    # horizon cell set from the incident's freshest hazard-front forecast (see
    # modules/forecast/), i.e. where the hazard is projected to reach next,
    # ahead of any actual report. Same fixed-snapshot semantics as
    # predicted_flooded_cells. Additive to delivery targeting only — never
    # used for routing exclusion or the confirmed-incident map layer, both of
    # which stay tied to real reports/gauge readings, not a forecast.
    projected_cells: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    channel: Mapped[str] = mapped_column(String(16))  # telegram | web_push | sms
    # Stable identifier per channel: telegram chat id, SMS phone number, or a
    # sha256 hash of the push endpoint (the endpoint itself lives in `meta` —
    # it can exceed this column's length and isn't needed for lookups).
    address: Mapped[str] = mapped_column(String(256))
    h3_cells: Mapped[list] = mapped_column(JSONB, default=list)  # geofence cell set
    min_tier: Mapped[str] = mapped_column(String(16), default="advisory")
    lang: Mapped[str] = mapped_column(String(8), default="en")
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)  # channel-specific extra data
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id"), index=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))  # sent | failed
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrainingExample(Base):
    __tablename__ = "training_examples"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reports.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(8), default="und")
    hazard_type: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(16))  # verify | reject
    # Set only when an analyst rejects a report but flags the *hazard type* (not the
    # report itself) as wrong — makes the row a usable label even though outcome=reject.
    corrected_hazard_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="linear_probe")
    artifact_path: Mapped[str] = mapped_column(String(512))
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    training_examples_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RagDocument(Base):
    __tablename__ = "rag_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), default="hazard_faq")
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(8), default="en")
    embedding: Mapped[list | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ChatLog(Base):
    __tablename__ = "chat_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    channel: Mapped[str] = mapped_column(String(16), default="web")  # web | telegram
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    retrieved_doc_ids: Mapped[list] = mapped_column(JSONB, default=list)
    retrieval_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_evacuation_directive: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SatelliteObservation(Base):
    __tablename__ = "satellite_observations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    recipe: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Float)
    scene_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scene_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PfzAdvisory(Base):
    __tablename__ = "pfz_advisories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    sector: Mapped[str] = mapped_column(String(64), index=True)  # INCOIS's 14-sector naming
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    depth_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    bearing: Mapped[str | None] = mapped_column(String(128), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32), default="stub")  # stub | incois (future)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Shelter(Base):
    __tablename__ = "shelters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(256))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326))
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | full | closed
    address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ElevationCell(Base):
    """Per-H3-cell (res 9) ground elevation, meters above the DEM's vertical
    datum — real data from a Copernicus DEM GLO-30 extract (see
    scripts/inundation/), not illustrative seed data. The bathtub model in
    modules/inundation/engine.py floods a cell once a water level meets or
    exceeds this value. Cells over open water can carry small negative
    values (a known Copernicus DEM artifact — its radar source is unreliable
    over water) rather than a clean 0; harmless here since those cells are
    already permanently "flooded" either way."""
    __tablename__ = "elevation_cells"

    h3_cell: Mapped[str] = mapped_column(String(16), primary_key=True)
    elevation_m: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="copernicus_dem_glo30")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Sitrep(Base):
    """Auto-generated NDMA-style situation report. Every number in `content`
    is pulled straight from verified DB state at generation time (see
    modules/sitrep/service.py::build_snapshot) — never invented or inferred —
    so an analyst reviewing a draft is checking wording, not arithmetic.
    `data_snapshot_hash` is a sha256 of the raw snapshot backing this report,
    also carried in the audit log's sitrep.generated/sitrep.filed entries so
    a filed SITREP is traceable back to the exact numbers it reported."""
    __tablename__ = "sitreps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft | filed
    content: Mapped[dict] = mapped_column(JSONB, default=dict)
    data_snapshot_hash: Mapped[str] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    filed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Forecast(Base):
    """Short-horizon prediction (phase 3, milestone 3) — either a per-
    station/variable sensor forecast (harmonic-trend regression over real
    readings) or a hazard-front propagation forecast (linear front-velocity
    extrapolation of a real incident's time-ordered report cluster). See
    modules/forecast/engine.py for why each is deliberately simple rather
    than e.g. Prophet/ANUGA. `content` holds the kind-specific projected
    points/cells — never invented, always derived from real DB history.
    `validation` stays null until validate_forecasts() finds that reality has
    caught up to what this forecast predicted, then it's filled in once and
    never edited again — the same immutable-after-the-fact discipline as a
    filed SITREP."""
    __tablename__ = "forecasts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # sensor | propagation
    subject_type: Mapped[str] = mapped_column(String(16))  # station | incident
    subject_id: Mapped[str] = mapped_column(String(64), index=True)
    hazard_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # propagation only
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    horizon_hours: Mapped[float] = mapped_column(Float)
    content: Mapped[dict] = mapped_column(JSONB, default=dict)
    validation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Narrative(Base):
    """Rumor tracker (phase 3, milestone 4): a cluster of citizen reports whose
    text repeats the same claim, found by clustering Report.embedding vectors
    directly (see modules/narratives/engine.py) — independent of Incident's
    spatial-adjacency clustering, since the same rumor can spread across
    multiple locations/incidents that would never merge into one incident.
    Only persisted once a cluster also contradicts something real (no active
    instrument anomaly for the claimed hazard nearby, or an analyst has
    already rejected a member report) — an unremarkable cluster of true
    reports isn't a rumor, so it never gets a row here. `message` holds a
    per-language, per-channel-length draft correction (same shape as
    `Alert.message` — see alerts/engine.py::draft_message) that only goes out
    once an analyst approves it (see modules/narratives/service.py); it is
    never sent automatically."""
    __tablename__ = "narratives"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    hazard_type: Mapped[str] = mapped_column(String(32), index=True)
    report_ids: Mapped[list] = mapped_column(JSONB, default=list)
    report_count: Mapped[int] = mapped_column(Integer, default=0)
    h3_cells: Mapped[list] = mapped_column(JSONB, default=list)
    centroid_lat: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)
    representative_text: Mapped[str] = mapped_column(Text)
    instrument_flat: Mapped[bool] = mapped_column(Boolean, default=False)
    rejected_report_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft | approved | dismissed
    message: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"en": {"standard":..., "short":...}, ...}
    draft_method: Mapped[str] = mapped_column(String(16), default="template")  # template | llm
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SafetyCheckin(Base):
    """"Mark Safe" (phase 3, milestone 5): a person telling responders they're
    safe, or that they need help, during or after an event.

    Deliberately not a `Report`: a check-in is a statement about a *person*,
    not an observation of a hazard, and must never feed the confidence engine
    or incident clustering — "I'm safe" is not corroboration that anything is
    happening, and a cluster of them is not a hazard. Keeping it in its own
    table makes that separation structural rather than a rule someone has to
    remember.

    `status` is deliberately coarse (safe | need_help). Anything finer would
    invite triage decisions this app has no way to verify, and `need_help` is
    already the only distinction that changes what a responder does next.
    """
    __tablename__ = "safety_checkins"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reporters.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)  # safe | need_help
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326))
    h3_cell: Mapped[str] = mapped_column(String(16), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Same offline-queue idempotency contract as Report.client_key.
    client_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    # When the person actually marked themselves safe, which can be well
    # before the device got a network to send it on (see created_at).
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    reporter: Mapped[Reporter] = relationship()


class NarrativeDelivery(Base):
    """Delivery log for an approved narrative correction — same shape as
    `AlertDelivery`, kept as its own table rather than reusing `Alert`/
    `AlertDelivery` directly: a correction isn't a hazard-tier proposal, and
    letting one masquerade as an Alert row would risk `sync_incident_alert`'s
    tier-upgrade logic silently overwriting it later."""
    __tablename__ = "narrative_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    narrative_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("narratives.id"), index=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))  # sent | failed | skipped
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_reports_cell_time", Report.h3_cell, Report.created_at)
Index("ix_readings_station_var_time", SensorReading.station_id, SensorReading.variable, SensorReading.time)
Index("ix_subscriptions_channel_address", Subscription.channel, Subscription.address, unique=True)
Index("ix_alerts_incident_status", Alert.incident_id, Alert.status)
Index("ix_training_examples_outcome", TrainingExample.outcome)
Index("ix_forecasts_kind_subject", Forecast.kind, Forecast.subject_id)
Index("ix_satellite_observations_incident_recipe", SatelliteObservation.incident_id, SatelliteObservation.recipe)
Index("ix_pfz_advisories_sector_valid", PfzAdvisory.sector, PfzAdvisory.valid_until)
Index("ix_narratives_status_hazard", Narrative.status, Narrative.hazard_type)
Index("ix_safety_checkins_status_observed", SafetyCheckin.status, SafetyCheckin.observed_at)
# Structural guarantee that the audit chain can never fork: in a valid chain
# every entry's prev_hash is the previous entry's hash, so a repeated
# prev_hash means two entries claim the same predecessor. Making that a
# database constraint turns a fork from silent corruption (found much later
# by verify_chain, unrecoverable by then) into a failed INSERT at write time.
# scoring/audit.py's advisory lock is what stops the race in the first place;
# this is the backstop if that is ever bypassed or wrong.
Index("ix_audit_log_prev_hash", AuditLog.prev_hash, unique=True)
