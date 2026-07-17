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


Index("ix_reports_cell_time", Report.h3_cell, Report.created_at)
Index("ix_readings_station_var_time", SensorReading.station_id, SensorReading.variable, SensorReading.time)
Index("ix_subscriptions_channel_address", Subscription.channel, Subscription.address, unique=True)
Index("ix_alerts_incident_status", Alert.incident_id, Alert.status)
Index("ix_training_examples_outcome", TrainingExample.outcome)
