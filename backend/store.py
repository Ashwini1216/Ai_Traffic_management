from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Float, Index, Integer, String, Text, URL, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, Result
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import core
import dataset
from core import DATA_DIR

DB_PATH = DATA_DIR / "traffic_monitor.db"
DATASET = DATA_DIR / "bangalore_traffic_timely_dataset.zip"
# Version 2 validates every archive row and quarantines invalid ones. Databases
# imported by version 1 (no validation) are rebuilt from the archive on startup.
HISTORICAL_IMPORT_VERSION = "2"


class Base(DeclarativeBase):
    """SQLAlchemy 2 ORM metadata for the traffic monitor database."""


class AppMeta(Base):
    __tablename__ = "app_meta"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class UserAccount(Base):
    __tablename__ = "user_accounts"
    username: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str | None] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="citizen")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    password_changed_at: Mapped[str | None] = mapped_column(String)
    # Sign-in tokens carry this number; changing the password raises it, which signs out other sessions.
    token_version: Mapped[int | None] = mapped_column(Integer)


class LoginAttempt(Base):
    """Sign-in attempts, for pausing sign-in after repeated wrong passwords and for the authority's
    sign-in history. account_key is the account's username, or the name typed if no account matches."""
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_account", "account_key", "attempted_at"),
                      Index("ix_login_attempts_client", "client", "attempted_at"))
    attempt_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_key: Mapped[str] = mapped_column(String, nullable=False)
    client: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)  # signed_in, wrong_password, paused, wrong_portal
    attempted_at: Mapped[str] = mapped_column(String, nullable=False)


class IncidentBroadcast(Base):
    __tablename__ = "incident_broadcasts"
    broadcast_id: Mapped[str] = mapped_column(String, primary_key=True)
    incident_id: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    area: Mapped[str | None] = mapped_column(String)
    road: Mapped[str | None] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(String)


class CameraFootage(Base):
    __tablename__ = "camera_footage"
    footage_id: Mapped[str] = mapped_column(String, primary_key=True)
    camera_id: Mapped[str] = mapped_column(String, nullable=False)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_at: Mapped[str] = mapped_column(String, nullable=False)
    analysis_json: Mapped[str] = mapped_column(Text, nullable=False)
    file_deleted_at: Mapped[str | None] = mapped_column(String)  # set when retention removes the video


class TrafficObservation(Base):
    __tablename__ = "traffic_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String)
    observed_date: Mapped[str] = mapped_column(String, nullable=False)
    time_block: Mapped[str] = mapped_column(String, nullable=False)
    area: Mapped[str] = mapped_column(String, nullable=False)
    road: Mapped[str] = mapped_column(String, nullable=False)
    traffic_volume: Mapped[float | None] = mapped_column(Float)
    average_speed: Mapped[float | None] = mapped_column(Float)
    travel_time_index: Mapped[float | None] = mapped_column(Float)
    congestion_level: Mapped[str | None] = mapped_column(String)
    weather: Mapped[str | None] = mapped_column(String)
    roadwork: Mapped[str | None] = mapped_column(String)
    incident_reports: Mapped[int | None] = mapped_column(Integer)
    quality_status: Mapped[str] = mapped_column(String, nullable=False)
    quality_flags_json: Mapped[str] = mapped_column(Text, nullable=False)
    camera_id: Mapped[str | None] = mapped_column(String)
    ingested_at: Mapped[str] = mapped_column(String, nullable=False)
    # Added in schema version 2: timestamps, interval measurements, aligned weather, review.
    segment_id: Mapped[str | None] = mapped_column(String)
    observed_start: Mapped[str | None] = mapped_column(String)
    observed_end: Mapped[str | None] = mapped_column(String)
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    vehicle_count: Mapped[int | None] = mapped_column(Integer)
    flow_per_hour: Mapped[float | None] = mapped_column(Float)
    occupancy: Mapped[float | None] = mapped_column(Float)
    queue_length: Mapped[float | None] = mapped_column(Float)
    precipitation_mm: Mapped[float | None] = mapped_column(Float)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    weather_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    verified_by: Mapped[str | None] = mapped_column(String)
    verified_at: Mapped[str | None] = mapped_column(String)
    review_note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_observations_road_date", "area", "road", "observed_date"),
                      Index("ix_observations_quality", "quality_status"),
                      Index("ix_observations_segment_time", "segment_id", "observed_start"))


class QuarantinedObservation(Base):
    __tablename__ = "quarantined_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    ingested_at: Mapped[str] = mapped_column(String, nullable=False)


class Camera(Base):
    __tablename__ = "cameras"
    camera_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    area: Mapped[str | None] = mapped_column(String)
    road: Mapped[str | None] = mapped_column(String)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    authorization_status: Mapped[str] = mapped_column(String, nullable=False)
    operational_status: Mapped[str] = mapped_column(String, nullable=False)
    last_success_at: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    # Added in schema version 2: live stream collection settings and health.
    stream_url: Mapped[str | None] = mapped_column(Text)
    collection_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    sample_seconds: Mapped[int | None] = mapped_column(Integer)
    counting_line_json: Mapped[str | None] = mapped_column(Text)
    roi_json: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[str | None] = mapped_column(String)
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int | None] = mapped_column(Integer)
    segment_id: Mapped[str | None] = mapped_column(String)


class Incident(Base):
    __tablename__ = "incidents"
    incident_id: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    area: Mapped[str | None] = mapped_column(String)
    road: Mapped[str | None] = mapped_column(String)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String, nullable=False)
    report_source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(String)
    official_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String)
    reviewed_at: Mapped[str | None] = mapped_column(String)
    # Added in schema version 2: official notices, closures, validity windows, duplicates.
    starts_at: Mapped[str | None] = mapped_column(String)
    official: Mapped[int | None] = mapped_column(Integer)
    closure: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[str | None] = mapped_column(String)
    created_by: Mapped[str | None] = mapped_column(String)
    corroboration_count: Mapped[int | None] = mapped_column(Integer)
    duplicate_of: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str | None] = mapped_column(String)
    __table_args__ = (Index("ix_incidents_status", "status", "expires_at"),)


class PlannedTrip(Base):
    __tablename__ = "planned_trips"
    trip_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_token_hash: Mapped[str] = mapped_column(String, nullable=False)
    origin: Mapped[str] = mapped_column(String, nullable=False)
    destination: Mapped[str] = mapped_column(String, nullable=False)
    departure_at: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    # Added in schema version 2: owner account, coordinates, vehicle weighting, route, reassessment.
    username: Mapped[str | None] = mapped_column(String)
    origin_lat: Mapped[float | None] = mapped_column(Float)
    origin_lon: Mapped[float | None] = mapped_column(Float)
    destination_lat: Mapped[float | None] = mapped_column(Float)
    destination_lon: Mapped[float | None] = mapped_column(Float)
    vehicle_type: Mapped[str | None] = mapped_column(String)
    pcu: Mapped[float | None] = mapped_column(Float)
    origin_zone: Mapped[str | None] = mapped_column(String)
    destination_zone: Mapped[str | None] = mapped_column(String)
    route_source: Mapped[str | None] = mapped_column(String)
    route_polyline: Mapped[str | None] = mapped_column(Text)
    route_distance_m: Mapped[float | None] = mapped_column(Float)
    route_duration_s: Mapped[float | None] = mapped_column(Float)
    last_risk: Mapped[float | None] = mapped_column(Float)
    last_assessment_json: Mapped[str | None] = mapped_column(Text)
    last_assessed_at: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str | None] = mapped_column(String)
    __table_args__ = (Index("ix_trips_demand", "state", "departure_at", "origin", "destination"),)


class TripSegment(Base):
    """Which registered roads a planned trip passes, and when it should reach them."""
    __tablename__ = "trip_segments"
    trip_id: Mapped[str] = mapped_column(String, primary_key=True)
    segment_id: Mapped[str] = mapped_column(String, primary_key=True)
    offset_s: Mapped[float] = mapped_column(Float, nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (Index("ix_trip_segments_segment", "segment_id"),)


class WeatherSnapshot(Base):
    __tablename__ = "weather_snapshots"
    weather_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    captured_at: Mapped[str] = mapped_column(String, nullable=False)
    forecast_for: Mapped[str | None] = mapped_column(String)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    precipitation_mm: Mapped[float | None] = mapped_column(Float)
    wind_kph: Mapped[float | None] = mapped_column(Float)
    weather_code: Mapped[str | None] = mapped_column(String)
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)


class ModelPrediction(Base):
    __tablename__ = "model_predictions"
    prediction_id: Mapped[str] = mapped_column(String, primary_key=True)
    issue_time: Mapped[str] = mapped_column(String, nullable=False)
    target_date: Mapped[str] = mapped_column(String, nullable=False)
    target_area: Mapped[str | None] = mapped_column(String)
    target_road: Mapped[str | None] = mapped_column(String)
    target_time_block: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    predicted_class: Mapped[str] = mapped_column(String, nullable=False)
    probabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    observed_outcome: Mapped[str | None] = mapped_column(String)
    evaluated_at: Mapped[str | None] = mapped_column(String)
    # Added in schema version 2: which method produced the forecast and how it scored later.
    segment_id: Mapped[str | None] = mapped_column(String)
    target_start: Mapped[str | None] = mapped_column(String)
    method: Mapped[str | None] = mapped_column(String)
    horizon_blocks: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    predicted_values_json: Mapped[str | None] = mapped_column(Text)
    observed_values_json: Mapped[str | None] = mapped_column(Text)
    errors_json: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_predictions_target", "target_date", "target_time_block"),)


class ModelVersion(Base):
    """Every trained forecasting model, its evaluation, and whether it is in use."""
    __tablename__ = "model_versions"
    version: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # candidate | active | retired | rejected
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    trained_by: Mapped[str] = mapped_column(String, nullable=False)
    artifact_dir: Mapped[str] = mapped_column(String, nullable=False)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_json: Mapped[str] = mapped_column(Text, nullable=False)
    data_json: Mapped[str] = mapped_column(Text, nullable=False)
    activated_at: Mapped[str | None] = mapped_column(String)
    activated_by: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)


class GeocodeCache(Base):
    __tablename__ = "geocode_cache"
    query_key: Mapped[str] = mapped_column(String, primary_key=True)
    results_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[str] = mapped_column(String, nullable=False)


class RouteAssessment(Base):
    __tablename__ = "route_assessments"
    route_id: Mapped[str] = mapped_column(String, primary_key=True)
    issue_time: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    scoring_method: Mapped[str] = mapped_column(Text, nullable=False)
    route_results_json: Mapped[str] = mapped_column(Text, nullable=False)


class AuthorityRequest(Base):
    """A commuter's request for authority access, reviewed by an existing authority."""
    __tablename__ = "authority_requests"
    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    organization: Mapped[str] = mapped_column(String, nullable=False)
    designation: Mapped[str] = mapped_column(String, nullable=False)
    official_email: Mapped[str | None] = mapped_column(String)
    employee_id: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # pending | approved | rejected
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String)
    reviewed_at: Mapped[str | None] = mapped_column(String)
    review_note: Mapped[str | None] = mapped_column(Text)


class Alert(Base):
    """An operational alert for authorities (high-risk zone, closure, hotspot, data quality)."""
    __tablename__ = "alerts"
    alert_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[str] = mapped_column(String, nullable=False)
    zone_id: Mapped[str | None] = mapped_column(String)
    segment_id: Mapped[str | None] = mapped_column(String)
    title: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # open | acknowledged | resolved | expired
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    valid_until: Mapped[str | None] = mapped_column(String)
    handled_by: Mapped[str | None] = mapped_column(String)
    handled_at: Mapped[str | None] = mapped_column(String)
    __table_args__ = (Index("ix_alerts_status", "status", "created_at"),)


class JobRun(Base):
    """One run of a background job, for monitoring."""
    __tablename__ = "job_runs"
    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)
    details_json: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_job_runs_job", "job", "started_at"),)


class ObservationDailySummary(Base):
    """Long-term daily summary per road, kept after detailed interval records age out."""
    __tablename__ = "observation_daily_summaries"
    segment_id: Mapped[str] = mapped_column(String, primary_key=True)
    observed_date: Mapped[str] = mapped_column(String, primary_key=True)
    records: Mapped[int] = mapped_column(Integer, nullable=False)
    mean_volume: Mapped[float | None] = mapped_column(Float)
    mean_speed: Mapped[float | None] = mapped_column(Float)
    mean_travel_time_index: Mapped[float | None] = mapped_column(Float)
    camera_vehicles: Mapped[int | None] = mapped_column(Integer)
    labels_json: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class Notification(Base):
    """A message to one account, e.g. a material risk increase on a planned trip."""
    __tablename__ = "notifications"
    notification_id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    trip_id: Mapped[str | None] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    read_at: Mapped[str | None] = mapped_column(String)
    __table_args__ = (Index("ix_notifications_user", "username", "read_at"),)


class ActiveJourney(Base):
    """A journey in progress that the commuter chose to have monitored."""
    __tablename__ = "active_journeys"
    journey_id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    trip_id: Mapped[str | None] = mapped_column(String)
    route_json: Mapped[str] = mapped_column(Text, nullable=False)
    destination_lat: Mapped[float] = mapped_column(Float, nullable=False)
    destination_lon: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    last_position_json: Mapped[str | None] = mapped_column(Text)
    alerted_json: Mapped[str] = mapped_column(Text, nullable=False)
    ended_at: Mapped[str | None] = mapped_column(String)


class RoadSegment(Base):
    """A named road or junction from the traffic data, with where it sits on the map."""
    __tablename__ = "road_segments"
    segment_id: Mapped[str] = mapped_column(String, primary_key=True)
    area: Mapped[str] = mapped_column(String, nullable=False)
    road: Mapped[str] = mapped_column(String, nullable=False)
    zone_id: Mapped[str] = mapped_column(String, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    location_source: Mapped[str] = mapped_column(String, nullable=False)
    road_type: Mapped[str] = mapped_column(String, nullable=False)
    road_type_source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (UniqueConstraint("area", "road", name="uq_road_segments_area_road"),)


class Zone(Base):
    """A geographic zone (one per area) used for zone-wise monitoring."""
    __tablename__ = "zones"
    zone_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    radius_m: Mapped[float | None] = mapped_column(Float)
    location_source: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[str] = mapped_column(String, nullable=False)
    details_json: Mapped[str] = mapped_column(Text, nullable=False)


def _database_engine() -> Engine:
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        if configured.startswith("postgresql://"):
            configured = configured.replace("postgresql://", "postgresql+psycopg2://", 1)
        return create_engine(configured, pool_pre_ping=True, future=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(URL.create("sqlite", database=str(DB_PATH)), connect_args={"check_same_thread": False, "timeout": 30}, future=True)


engine = _database_engine()
DATABASE_KIND = engine.dialect.name
# Tables whose single integer primary key is generated by the database. PostgreSQL inserts
# into these get "RETURNING <key>" so callers can read lastrowid as they do with SQLite.
AUTOINCREMENT_KEYS = {
    table.name: next(iter(table.primary_key.columns)).name
    for table in Base.metadata.sorted_tables
    if len(table.primary_key.columns) == 1 and isinstance(next(iter(table.primary_key.columns)).type, Integer)
}


class CompatibleResult:
    def __init__(self, result: Result, lastrowid: int | None = None):
        self._result = result
        self.lastrowid = lastrowid if lastrowid is not None else getattr(result, "lastrowid", None)
        self.rowcount = result.rowcount

    @staticmethod
    def _dict(row):
        return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)

    def fetchone(self):
        row = self._result.fetchone()
        return self._dict(row) if row is not None else None

    def fetchall(self):
        return [self._dict(row) for row in self._result.fetchall()]


class CompatibleConnection:
    """Small compatibility bridge for existing SQL while storage uses SQLAlchemy."""
    def __init__(self, connection: Connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()

    def _prepare(self, query: str, params: Any):
        query = query.strip()
        if query.upper().startswith("PRAGMA"):
            return None, None
        if self.connection.dialect.name == "postgresql":
            query = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\b", "SERIAL PRIMARY KEY", query, flags=re.I)
            match = re.match(r"INSERT\s+INTO\s+(\w+)", query, flags=re.I)
            if match and match.group(1).lower() in AUTOINCREMENT_KEYS and " RETURNING " not in query.upper():
                query += f" RETURNING {AUTOINCREMENT_KEYS[match.group(1).lower()]}"
        if isinstance(params, (tuple, list)):
            values = {f"p{i}": value for i, value in enumerate(params)}
            iterator = iter(values)
            query = re.sub(r"\?", lambda _: f":{next(iterator)}", query)
        elif params is None:
            values = {}
        else:
            values = params
        return text(query), values

    def execute(self, query: str, params: Any = ()) -> CompatibleResult:
        statement, values = self._prepare(query, params)
        if statement is None:
            return CompatibleResult(self.connection.execute(text("SELECT 1 WHERE 1=0")))
        result = self.connection.execute(statement, values)
        inserted_id = None
        if self.connection.dialect.name == "postgresql" and " RETURNING " in str(statement).upper():
            row = result.fetchone()
            inserted_id = row[0] if row is not None else None
        return CompatibleResult(result, inserted_id)

    def executemany(self, query: str, parameters: list[tuple]) -> CompatibleResult:
        statement, _ = self._prepare(query, tuple(range(query.count("?"))))
        if not parameters:
            return CompatibleResult(self.connection.execute(text("SELECT 1 WHERE 1=0")))
        names = re.findall(r":(p\d+)", str(statement))
        values = [{name: row[i] for i, name in enumerate(names)} for row in parameters]
        result = self.connection.execute(statement, values)
        return CompatibleResult(result)

    def columns(self, table: str) -> set[str]:
        return {column["name"] for column in inspect(self.connection).get_columns(table)}


def connect() -> CompatibleConnection:
    connection = engine.connect()
    connection.begin()
    return CompatibleConnection(connection)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize() -> None:
    Base.metadata.create_all(engine)
    with connect() as db:
        prediction_columns = db.columns("model_predictions")
        if "target_area" not in prediction_columns:
            db.execute("ALTER TABLE model_predictions ADD COLUMN target_area TEXT")
        if "target_road" not in prediction_columns:
            db.execute("ALTER TABLE model_predictions ADD COLUMN target_road TEXT")
        _add_missing_columns(db, "incidents", INCIDENT_V2_COLUMNS)
        _add_missing_columns(db, "traffic_observations", OBSERVATION_V2_COLUMNS)
        _add_missing_columns(db, "model_predictions", PREDICTION_V2_COLUMNS)
        _add_missing_columns(db, "planned_trips", TRIP_V2_COLUMNS)
        _add_missing_columns(db, "cameras", CAMERA_V2_COLUMNS)
        _add_missing_columns(db, "camera_footage", {"file_deleted_at": "TEXT"})
        _add_missing_columns(db, "user_accounts", {"password_changed_at": "TEXT", "token_version": "INTEGER"})
        db.execute("CREATE INDEX IF NOT EXISTS ix_observations_segment_time ON traffic_observations(segment_id,observed_start)")
        _backfill_observation_keys(db)
        _normalize_expiry_timestamps(db)
        meta = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM app_meta").fetchall()}
        imported = "historical_dataset_imported" in meta
        current = meta.get("historical_dataset_import_version") == HISTORICAL_IMPORT_VERSION
        if DATASET.exists() and not (imported and current):
            if imported:
                # Rows from the older, unvalidated import are a copy of the archive; rebuild them.
                db.execute("DELETE FROM traffic_observations WHERE source_type='historical_dataset'")
                db.execute("DELETE FROM quarantined_observations WHERE source_type='historical_dataset'")
            _import_historical_dataset(db)
            for key, value in (("historical_dataset_imported", now_iso()),
                               ("historical_dataset_import_version", HISTORICAL_IMPORT_VERSION)):
                db.execute("""INSERT INTO app_meta(key,value) VALUES(?,?)
                  ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, value))


OBSERVATION_V2_COLUMNS = {
    "segment_id": "TEXT", "observed_start": "TEXT", "observed_end": "TEXT", "interval_seconds": "INTEGER",
    "vehicle_count": "INTEGER", "flow_per_hour": "FLOAT", "occupancy": "FLOAT", "queue_length": "FLOAT",
    "precipitation_mm": "FLOAT", "temperature_c": "FLOAT", "weather_snapshot_id": "INTEGER",
    "verified_by": "TEXT", "verified_at": "TEXT", "review_note": "TEXT",
}


INCIDENT_V2_COLUMNS = {
    "official_note": "TEXT", "reviewed_by": "TEXT", "reviewed_at": "TEXT", "starts_at": "TEXT", "official": "INTEGER",
    "closure": "INTEGER", "severity": "TEXT", "created_by": "TEXT", "corroboration_count": "INTEGER",
    "duplicate_of": "TEXT", "updated_at": "TEXT",
}
CAMERA_V2_COLUMNS = {
    "stream_url": "TEXT", "collection_interval_seconds": "INTEGER", "sample_seconds": "INTEGER", "counting_line_json": "TEXT",
    "roi_json": "TEXT", "last_attempt_at": "TEXT", "last_error": "TEXT", "consecutive_failures": "INTEGER", "segment_id": "TEXT",
}
TRIP_V2_COLUMNS = {
    "username": "TEXT", "origin_lat": "FLOAT", "origin_lon": "FLOAT", "destination_lat": "FLOAT", "destination_lon": "FLOAT",
    "vehicle_type": "TEXT", "pcu": "FLOAT", "origin_zone": "TEXT", "destination_zone": "TEXT", "route_source": "TEXT",
    "route_polyline": "TEXT", "route_distance_m": "FLOAT", "route_duration_s": "FLOAT", "last_risk": "FLOAT",
    "last_assessment_json": "TEXT", "last_assessed_at": "TEXT", "updated_at": "TEXT",
}
PREDICTION_V2_COLUMNS = {
    "segment_id": "TEXT", "target_start": "TEXT", "method": "TEXT", "horizon_blocks": "INTEGER", "confidence": "FLOAT",
    "predicted_values_json": "TEXT", "observed_values_json": "TEXT", "errors_json": "TEXT",
}


def _add_missing_columns(db: CompatibleConnection, table: str, columns: dict[str, str]) -> None:
    existing = db.columns(table)
    for name, sql_type in columns.items():
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def _backfill_observation_keys(db: CompatibleConnection) -> None:
    """Give rows from older versions their road segment and block start/end times."""
    import segments  # imported here: segments itself depends on this module

    for pair in db.execute("SELECT DISTINCT area,road FROM traffic_observations WHERE segment_id IS NULL").fetchall():
        db.execute("UPDATE traffic_observations SET segment_id=? WHERE area=? AND road=? AND segment_id IS NULL",
                   (segments.segment_id_for(pair["area"], pair["road"]), pair["area"], pair["road"]))
    slots = db.execute("""SELECT DISTINCT observed_date,time_block FROM traffic_observations
      WHERE observed_start IS NULL""").fetchall()
    for slot in slots:
        day = dataset.parse_observed_date(slot["observed_date"])
        if day is None or slot["time_block"] not in dataset.TIME_BLOCKS:
            continue
        start, end = core.block_bounds(day, slot["time_block"])
        db.execute("""UPDATE traffic_observations SET observed_start=?,observed_end=?,interval_seconds=?
          WHERE observed_date=? AND time_block=? AND observed_start IS NULL""",
                   (start.isoformat(), end.isoformat(), int((end - start).total_seconds()), slot["observed_date"], slot["time_block"]))


def _normalize_expiry_timestamps(db: CompatibleConnection) -> None:
    """Rewrite expiry times saved with a non-UTC offset (older versions kept the client's
    offset), because active-record checks compare these values as text."""
    for table, key in (("incidents", "incident_id"), ("incident_broadcasts", "broadcast_id")):
        stale = db.execute(f"SELECT {key},expires_at FROM {table} WHERE expires_at IS NOT NULL AND expires_at NOT LIKE '%+00:00'").fetchall()
        for row in stale:
            try:
                value = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            db.execute(f"UPDATE {table} SET expires_at=? WHERE {key}=?", (value.astimezone(timezone.utc).isoformat(), row[key]))


def _import_historical_dataset(db: CompatibleConnection) -> None:
    import segments  # imported here: segments itself depends on this module

    batch, quarantine, seen_keys = [], [], set()
    ingested = now_iso()
    bounds: dict[tuple[str, str], tuple[str, str, int]] = {}
    for line_number, row, clean, problems in dataset.validated_rows(DATASET):
        if clean is None:
            quarantine.append(("historical_dataset", json.dumps({"archive": DATASET.name, "line": line_number, "row": row}),
                               "; ".join(problems), ingested))
            continue
        flags = ["Imported from project dataset; not independently verified."]
        flags += [f"{field}_not_provided" for field in ("traffic_volume", "average_speed", "travel_time_index",
                                                         "incident_reports", "congestion_level") if clean[field] is None]
        key = (clean["observed_date"], clean["time_block"], clean["area"], clean["road"])
        if key in seen_keys:
            flags.append("conflicting_duplicate_for_road_and_time_block")
        seen_keys.add(key)
        slot = (clean["observed_date"], clean["time_block"])
        if slot not in bounds:
            start, end = core.block_bounds(dataset.parse_observed_date(slot[0]), slot[1])
            bounds[slot] = (start.isoformat(), end.isoformat(), int((end - start).total_seconds()))
        batch.append(("historical_dataset", DATASET.name, f"line:{line_number}", clean["observed_date"], clean["time_block"],
            clean["area"], clean["road"], clean["traffic_volume"], clean["average_speed"], clean["travel_time_index"],
            clean["congestion_level"], clean["weather"], clean["roadwork"], clean["incident_reports"],
            "unverified", json.dumps(flags), None, ingested,
            segments.segment_id_for(clean["area"], clean["road"]), *bounds[slot]))
        if len(batch) == 1000:
            _insert_observations(db, batch)
            batch.clear()
    if batch:
        _insert_observations(db, batch)
    if quarantine:
        db.executemany("INSERT INTO quarantined_observations(source_type,raw_json,reason,ingested_at) VALUES(?,?,?,?)", quarantine)


def _insert_observations(db: CompatibleConnection, rows: list[tuple]) -> None:
    db.executemany("""INSERT INTO traffic_observations
        (source_type,source_name,source_record_id,observed_date,time_block,area,road,traffic_volume,average_speed,
         travel_time_index,congestion_level,weather,roadwork,incident_reports,quality_status,quality_flags_json,camera_id,ingested_at,
         segment_id,observed_start,observed_end,interval_seconds)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)


def rows(query: str, params: tuple = ()) -> list[dict]:
    with connect() as db:
        return db.execute(query, params).fetchall()


def one(query: str, params: tuple = ()) -> dict | None:
    with connect() as db:
        return db.execute(query, params).fetchone()


def execute(query: str, params: tuple = ()) -> int | None:
    with connect() as db:
        return db.execute(query, params).lastrowid
