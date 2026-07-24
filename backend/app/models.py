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
from app.modules.hazards.registry import HAZARD_TYPES

EMBEDDING_DIM = get_settings().embedding_dim

REPORT_STATUSES = ["unverified", "corroborated", "verified", "rejected"]

RELIEF_CATEGORIES = ["water", "food", "medical", "shelter", "other"]
MISSING_PERSON_TYPES = ["missing", "found"]


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
    # Bus pipeline mode only (phase 3, milestone 8): which stage of the
    # nlp -> dedup -> scoring consumer chain has completed for this report.
    # Always "scored" in inline mode (the default) and for every row created
    # before this milestone — inline mode does all three steps synchronously
    # before the row is ever visible, so there's nothing to observe mid-flight.
    processing_stage: Mapped[str] = mapped_column(String(16), default="scored", index=True)
    # Bus mode only: True once hazard_type is final and must never be
    # overwritten by the nlp consumer — either the reporter's own explicit
    # pick (mirrors create_report()'s longstanding "user's explicit pick
    # wins"), or the nlp consumer has already classified it. False means
    # hazard_type is still the "other" placeholder the gateway wrote pending
    # real classification. Always True in inline mode (nothing ever reads it).
    hazard_locked: Mapped[bool] = mapped_column(Boolean, default=True)
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
    # Open-data retention (phase 4, milestone 3): set once
    # modules/opendata/service.py::anonymize_expired_reports has overwritten
    # lat/lon/geom with this report's H3 cell centroid, so the job never
    # reprocesses (or re-audits) the same row on a later tick. Null means the
    # exact GPS location is still stored as submitted.
    location_anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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


class OfficialAdvisory(Base):
    __tablename__ = "official_advisories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    # CAP <identifier>, scoped to <sender> — a re-delivered copy of the same
    # message (a retried webhook) is deliberately not deduped on this alone
    # (see cap_service.py::ingest_cap_document), since a real agency can also
    # reuse an identifier space across senders.
    cap_identifier: Mapped[str] = mapped_column(String(256), index=True)
    sender: Mapped[str] = mapped_column(String(256))
    event: Mapped[str] = mapped_column(String(256))  # raw CAP <event> text, kept verbatim
    # None when the raw <event> text doesn't map to a hazard this app models
    # (see cap_ingest.py::map_event_to_hazard) — such rows are never created
    # in the first place, but the column stays nullable for that honesty.
    hazard_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    urgency: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    certainty: Mapped[str] = mapped_column(String(16))
    msg_type: Mapped[str] = mapped_column(String(16))  # Alert | Update | Cancel
    headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # One row per CAP <area> block (a message covering several disjoint areas
    # becomes several rows sharing cap_identifier). Exactly one of these two
    # is set, matching CAP's own polygon-or-circle area shapes.
    area_polygon: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # [[lat, lon], ...] ring
    area_circle: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # {"lat", "lon", "radius_km"}
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_xml: Mapped[str] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


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


class DamageAssessment(Base):
    """Post-disaster damage assessment (phase 3, milestone 7): a photo taken
    after an event, run through a coarse CV triage (see
    modules/recovery/cv.py) that derives real signal from the actual pixels —
    classical brightness/hue/edge-density heuristics, not a trained damage
    classifier. `cv_mode` records which path produced the result (`heuristic`
    always in this environment; `heuristic+yolo` if a real object detector is
    ever installed — see cv.py's lazy-load). Deliberately not tied to a
    `Report`: a damage assessment is a statement about a *place* after the
    fact, not a hazard observation feeding the confidence/incident pipeline."""
    __tablename__ = "damage_assessments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reporters.id"), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326))
    h3_cell: Mapped[str] = mapped_column(String(16), index=True)
    photo_path: Mapped[str] = mapped_column(String(512))
    phash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    damage_class: Mapped[str] = mapped_column(String(32))  # flooding | structural_or_debris | minor_or_none
    severity: Mapped[str] = mapped_column(String(16))  # minor | moderate | severe | destroyed
    cv_confidence: Mapped[float] = mapped_column(Float)
    cv_mode: Mapped[str] = mapped_column(String(16))  # heuristic | heuristic+yolo
    cv_detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="submitted", index=True)  # submitted | reviewed
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    reporter: Mapped[Reporter] = relationship()


class ReliefRequest(Base):
    """A request for aid after an event — its own table (not a Report) since
    "I need water" is a needs statement, not a hazard observation. Matched
    against open AidOffers by category + proximity (see
    modules/recovery/engine.py::match_aid); a match is only ever a suggestion
    surfaced to a human, never an automatic fulfillment."""
    __tablename__ = "relief_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reporters.id"), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326))
    h3_cell: Mapped[str] = mapped_column(String(16), index=True)
    category: Mapped[str] = mapped_column(String(16), index=True)  # water | food | medical | shelter | other
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    people_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | fulfilled | cancelled
    fulfilled_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    reporter: Mapped[Reporter] = relationship()


class AidOffer(Base):
    """The other half of the mutual-aid board: what a volunteer/org can give,
    matched against open ReliefRequests the same way (see engine.py::match_aid)."""
    __tablename__ = "aid_offers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reporters.id"), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="POINT", srid=4326))
    h3_cell: Mapped[str] = mapped_column(String(16), index=True)
    category: Mapped[str] = mapped_column(String(16), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    reporter: Mapped[Reporter] = relationship()


class MissingPerson(Base):
    """Missing/found-person registry (phase 3, milestone 7). Strict privacy by
    construction: submission is public (a family member reporting someone
    missing must never sit behind a login, same reasoning as Report/
    SafetyCheckin), but every read path is analyst-only — see
    modules/recovery/router.py. `matched_person_id` cross-links a resolved
    missing/found pair once an analyst confirms a fuzzy-name candidate match
    (see engine.py::fuzzy_name_score) — matching is always analyst-confirmed,
    never automatic, since misidentifying a person is a much worse failure
    mode than a missed match. Retention is enforced by
    modules/recovery/service.py::purge_expired_missing_persons (a scheduled
    job, not just a policy on paper) — see recovery_missing_person_retention_days."""
    __tablename__ = "missing_persons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reporters.id"), index=True)
    report_type: Mapped[str] = mapped_column(String(8), index=True)  # missing | found
    name: Mapped[str] = mapped_column(String(256))
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    h3_cell: Mapped[str | None] = mapped_column(String(16), nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | resolved
    matched_person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("missing_persons.id"), nullable=True
    )
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    reporter: Mapped[Reporter] = relationship()


class ApiKey(Base):
    """A researcher/consumer credential for the open-data API (phase 4,
    milestone 3). Only a hash of the raw key is ever stored — see
    modules/opendata/service.py::create_api_key/verify_api_key — the same
    "never persist the credential itself" posture a password would get,
    even though this is a machine credential rather than a login. Revocation
    is a timestamp, not a delete, so a revoked key's audit trail (who minted
    it, when, its last use) survives."""
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    label: Mapped[str] = mapped_column(String(128))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16))  # display-only, never enough to reconstruct the key
    created_by: Mapped[str] = mapped_column(String(128))  # analyst username who minted it
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DatasetRelease(Base):
    """A frozen, published snapshot from the open-data pipeline (phase 4,
    milestone 3) — see modules/opendata/service.py::build_dataset_release.
    `content` is the already-k-anonymized, already-DP-noised row set itself
    (pilot volumes are small enough to store the release inline, same
    posture as Sitrep.content); `checksum` is a sha256 of that exact content
    so a citing researcher can verify they still have the data as released.
    `doi` stays null until an operator registers the release with an
    external DOI provider (e.g. DataCite/Zenodo) and fills it in by hand —
    that registration step is outside this app, same as cap_sender being a
    pilot placeholder until a real partnership exists."""
    __tablename__ = "dataset_releases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    h3_resolution: Mapped[int] = mapped_column(Integer)
    k_anonymity_min: Mapped[int] = mapped_column(Integer)
    dp_epsilon: Mapped[float] = mapped_column(Float)
    row_count: Mapped[int] = mapped_column(Integer)
    suppressed_group_count: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[list] = mapped_column(JSONB, default=list)
    checksum: Mapped[str] = mapped_column(String(64))
    doi: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


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
Index("ix_damage_assessments_cell_created", DamageAssessment.h3_cell, DamageAssessment.created_at)
Index("ix_relief_requests_status_category", ReliefRequest.status, ReliefRequest.category)
Index("ix_aid_offers_status_category", AidOffer.status, AidOffer.category)
Index("ix_missing_persons_status_type", MissingPerson.status, MissingPerson.report_type)
