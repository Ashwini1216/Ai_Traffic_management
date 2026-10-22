from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Float, Index, Integer, String, Text, URL, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, Result
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "traffic_monitor.db"
DATASET = ROOT / "data" / "bangalore_traffic_timely_dataset.zip"


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
    __table_args__ = (Index("ix_observations_road_date", "area", "road", "observed_date"),
                      Index("ix_observations_quality", "quality_status"))


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
    __table_args__ = (Index("ix_trips_demand", "state", "departure_at", "origin", "destination"),)


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
    __table_args__ = (Index("ix_predictions_target", "target_date", "target_time_block"),)


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
            primary_keys = {"traffic_observations": "id", "quarantined_observations": "id",
                            "weather_snapshots": "weather_id", "audit_log": "audit_id"}
            if match and match.group(1).lower() in primary_keys and " RETURNING " not in query.upper():
                query += f" RETURNING {primary_keys[match.group(1).lower()]}"
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
        done = one("SELECT value FROM app_meta WHERE key=?", ("historical_dataset_imported",))
        if not done and DATASET.exists():
            _import_historical_dataset(db)
            db.execute("INSERT INTO app_meta(key,value) VALUES(?,?)", ("historical_dataset_imported", now_iso()))


def _import_historical_dataset(db: CompatibleConnection) -> None:
    with zipfile.ZipFile(DATASET) as archive:
        name = next((entry for entry in archive.namelist() if entry.lower().endswith(".csv")), None)
        if name is None:
            return
        with archive.open(name) as raw:
            rows = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            batch = []
            ingested = now_iso()
            for row in rows:
                batch.append(("historical_dataset", DATASET.name, None, row.get("Date", ""), row.get("TimeBlock", ""),
                    row.get("Area Name", ""), row.get("Road/Intersection Name", ""), _number(row.get("Traffic Volume")),
                    _number(row.get("Average Speed")), _number(row.get("Travel Time Index")), row.get("TrafficSituation"),
                    row.get("Weather Conditions"), row.get("Roadwork and Construction Activity"), _integer(row.get("Incident Reports")),
                    "unverified", json.dumps(["Imported from project dataset; not independently verified."]), None, ingested))
                if len(batch) == 1000:
                    _insert_observations(db, batch)
                    batch.clear()
            if batch:
                _insert_observations(db, batch)


def _insert_observations(db: CompatibleConnection, rows: list[tuple]) -> None:
    db.executemany("""INSERT INTO traffic_observations
        (source_type,source_name,source_record_id,observed_date,time_block,area,road,traffic_volume,average_speed,
         travel_time_index,congestion_level,weather,roadwork,incident_reports,quality_status,quality_flags_json,camera_id,ingested_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)


def _number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _integer(value):
    try:
        return int(float(value)) if value not in (None, "") else None
    except (ValueError, TypeError):
        return None


def rows(query: str, params: tuple = ()) -> list[dict]:
    with connect() as db:
        return db.execute(query, params).fetchall()


def one(query: str, params: tuple = ()) -> dict | None:
    with connect() as db:
        return db.execute(query, params).fetchone()


def execute(query: str, params: tuple = ()) -> int | None:
    with connect() as db:
        return db.execute(query, params).lastrowid
