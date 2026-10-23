"""Traffic observation ingestion: validation, quarantine, weather alignment, and review.

Observations arrive either for a whole time block (date + block, like the historical
dataset) or for a timestamped interval (camera counts every one or five minutes). Invalid,
duplicate, or stale records are quarantined for review rather than entering the history.
Authority-verified observations join the training history used by the forecasting model.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

import core
import forecasting
import segments
import store
import weather
from core import TIME_BLOCKS, iso_now
from security import authority_token

router = APIRouter()

LIVE_SOURCE_MAX_AGE = timedelta(hours=24)  # camera measurements older than this are stale
MAX_INTERVAL = timedelta(hours=6)
CLOCK_SKEW = timedelta(minutes=5)


class ObservationInput(BaseModel):
    observed_date: str | None = None
    time_block: str | None = None
    observed_start: datetime | None = None
    observed_end: datetime | None = None
    area: str = Field(min_length=1, max_length=100)
    road: str = Field(min_length=1, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    traffic_volume: float | None = Field(default=None, ge=0, le=1_000_000)
    average_speed: float | None = Field(default=None, ge=0, le=250)          # km/h
    travel_time_index: float | None = Field(default=None, gt=0, le=20)
    vehicle_count: int | None = Field(default=None, ge=0, le=100_000)        # vehicles in the interval
    flow_per_hour: float | None = Field(default=None, ge=0, le=20_000)       # vehicles per hour
    occupancy: float | None = Field(default=None, ge=0, le=1)                # share of the view covered
    queue_length: float | None = Field(default=None, ge=0, le=1000)          # queued vehicles
    congestion_level: Literal["Low", "Medium", "High"] | None = None
    weather: str | None = Field(default=None, max_length=80)
    roadwork: str | None = Field(default=None, max_length=80)
    incident_reports: int | None = Field(default=None, ge=0, le=10000)
    source_type: Literal["manual", "camera"] = "manual"
    source_name: str = Field(default="user_entry", max_length=100)
    source_record_id: str | None = Field(default=None, max_length=200)
    camera_id: str | None = None

    @field_validator("observed_date")
    @classmethod
    def _valid_observed_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            observed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("observed_date must use YYYY-MM-DD format") from None
        if observed > core.local_today():
            raise ValueError("observed_date cannot be in the future")
        return observed.isoformat()

    @field_validator("time_block")
    @classmethod
    def _valid_time_block(cls, value: str | None) -> str | None:
        if value is not None and value not in TIME_BLOCKS:
            raise ValueError("time_block must be one of: " + ", ".join(TIME_BLOCKS))
        return value

    @model_validator(mode="after")
    def _consistent(self):
        if self.observed_start is not None:
            if self.observed_start.tzinfo is None or (self.observed_end and self.observed_end.tzinfo is None):
                raise ValueError("observed_start and observed_end must include a timezone")
            if self.observed_end is not None:
                if self.observed_end <= self.observed_start:
                    raise ValueError("observed_end must be after observed_start")
                if self.observed_end - self.observed_start > MAX_INTERVAL:
                    raise ValueError("an observation interval cannot exceed 6 hours")
            if (self.observed_end or self.observed_start) > datetime.now(timezone.utc) + CLOCK_SKEW:
                raise ValueError("the observation cannot be in the future")
            day, block = core.block_for(self.observed_start)
            if (self.observed_date and self.observed_date != day.isoformat()) or (self.time_block and self.time_block != block):
                raise ValueError("observed_date and time_block do not match observed_start (IST)")
            self.observed_date, self.time_block = day.isoformat(), block
        elif not (self.observed_date and self.time_block):
            raise ValueError("Provide observed_start, or both observed_date and time_block")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude, or neither")
        if self.latitude is not None and not core.in_bengaluru(self.latitude, self.longitude):
            raise ValueError("the location is outside the Bengaluru area")
        if self.vehicle_count is not None and self.flow_per_hour is not None and self.observed_end is not None:
            seconds = (self.observed_end - self.observed_start).total_seconds()
            expected = self.vehicle_count / seconds * 3600
            if abs(expected - self.flow_per_hour) > max(10.0, 0.1 * expected):
                raise ValueError("flow_per_hour is inconsistent with vehicle_count over the interval (check units)")
        return self


def _quarantine(raw: dict, reason) -> int:
    with store.connect() as db:
        cur = db.execute("INSERT INTO quarantined_observations(source_type,raw_json,reason,ingested_at) VALUES(?,?,?,?)",
                         (str(raw.get("source_type", "unknown")), json.dumps(raw, default=str), json.dumps(reason, default=str), iso_now()))
        return cur.lastrowid


def _location(payload: ObservationInput, camera: dict | None, segment_id: str) -> tuple[float, float] | None:
    if camera and camera.get("latitude") is not None:
        return camera["latitude"], camera["longitude"]
    if payload.latitude is not None:
        return payload.latitude, payload.longitude
    segment = store.one("SELECT latitude,longitude FROM road_segments WHERE segment_id=?", (segment_id,))
    if segment and segment["latitude"] is not None:
        return segment["latitude"], segment["longitude"]
    return None


def store_observation(raw: dict) -> dict:
    """Validate and store one observation; invalid input is quarantined for review."""
    try:
        payload = ObservationInput.model_validate(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False)
        quarantine_id = _quarantine(raw, errors)
        raise HTTPException(422, detail={"message": "Invalid observation quarantined for review.", "quarantine_id": quarantine_id, "errors": errors})
    camera = None
    problems = []
    if payload.source_type == "camera":
        if not payload.camera_id:
            raise HTTPException(422, "camera_id is required for camera observations.")
        camera = store.one("SELECT * FROM cameras WHERE camera_id=?", (payload.camera_id,))
        if not camera or camera["authorization_status"] != "approved" or camera["operational_status"] != "online":
            raise HTTPException(403, "This camera is not approved and online in the camera registry.")
        if camera["area"] and (camera["area"], camera["road"]) != (payload.area, payload.road):
            problems.append("The observation's area and road do not match the camera's registered road.")
        if payload.observed_start is not None:
            newest = payload.observed_end or payload.observed_start
            if datetime.now(timezone.utc) - newest > LIVE_SOURCE_MAX_AGE:
                problems.append("Stale camera observation: it ended more than 24 hours ago.")
    if problems:
        quarantine_id = _quarantine(raw, problems)
        raise HTTPException(422, detail={"message": "Invalid observation quarantined for review.", "quarantine_id": quarantine_id, "errors": problems})

    segment_id = segments.ensure_segment(payload.area, payload.road)
    if payload.observed_start is not None:
        start = payload.observed_start.astimezone(timezone.utc)
        end = payload.observed_end.astimezone(timezone.utc) if payload.observed_end else None
    else:
        start, end = core.block_bounds(date.fromisoformat(payload.observed_date), payload.time_block)
    interval = int((end - start).total_seconds()) if end else None
    duplicate = store.one("""SELECT id FROM traffic_observations WHERE source_type=? AND source_name=?
      AND COALESCE(camera_id,'')=? AND segment_id=? AND observed_start=? AND COALESCE(observed_end,'')=? LIMIT 1""",
                          (payload.source_type, payload.source_name, payload.camera_id or "", segment_id,
                           start.isoformat(), end.isoformat() if end else ""))
    if not duplicate and payload.source_record_id:
        duplicate = store.one("SELECT id FROM traffic_observations WHERE source_name=? AND source_record_id=? LIMIT 1",
                              (payload.source_name, payload.source_record_id))
    if duplicate:
        quarantine_id = _quarantine(raw, [f"Duplicate of observation {duplicate['id']} (same source, road, and time)."])
        raise HTTPException(409, detail={"message": "Duplicate observation quarantined for review.", "quarantine_id": quarantine_id,
                                         "duplicate_of": duplicate["id"]})

    flags = []
    for field in ("traffic_volume", "average_speed", "travel_time_index", "congestion_level"):
        if getattr(payload, field) is None:
            flags.append(f"{field}_not_provided")
    if payload.source_type == "manual":
        flags.append("manual_source_not_independently_verified")
    if payload.source_type == "camera":
        flags.append("camera_source_requires_site_validation")
        if payload.source_name == "yolov8n_video_count":
            flags.append("clip_vehicle_count_is_not_calibrated_block_volume")
    # Attach the weather observed at that time and place, kept separate from forecasts.
    weather_label, precipitation, temperature, snapshot_id = payload.weather, None, None, None
    location = _location(payload, camera, segment_id)
    if location:
        midpoint = start + ((end - start) / 2 if end else timedelta())
        window = max(weather.ALIGN_WINDOW, (end - start) / 2 if end else weather.ALIGN_WINDOW)
        snapshot = weather.observed_at(location[0], location[1], midpoint, window)
        if snapshot:
            precipitation, temperature, snapshot_id = snapshot["precipitation_mm"], snapshot["temperature_c"], snapshot["weather_id"]
            if not weather_label:
                weather_label = weather.label_from_code(snapshot["weather_code"])
                if weather_label:
                    flags.append("weather_label_from_provider_observation")
    else:
        flags.append("location_unknown_weather_not_aligned")
    if snapshot_id is None and not payload.weather:
        flags.append("weather_not_available")

    with store.connect() as db:
        cur = db.execute("""INSERT INTO traffic_observations
          (source_type,source_name,source_record_id,observed_date,time_block,area,road,
           traffic_volume,average_speed,travel_time_index,congestion_level,weather,roadwork,
           incident_reports,quality_status,quality_flags_json,camera_id,ingested_at,segment_id,observed_start,
           observed_end,interval_seconds,vehicle_count,flow_per_hour,occupancy,queue_length,precipitation_mm,
           temperature_c,weather_snapshot_id)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (payload.source_type, payload.source_name, payload.source_record_id, payload.observed_date,
           payload.time_block, payload.area, payload.road, payload.traffic_volume, payload.average_speed,
           payload.travel_time_index, payload.congestion_level, weather_label, payload.roadwork,
           payload.incident_reports, "unverified", json.dumps(flags), payload.camera_id, iso_now(), segment_id,
           start.isoformat(), end.isoformat() if end else None, interval, payload.vehicle_count, payload.flow_per_hour,
           payload.occupancy, payload.queue_length, precipitation, temperature, snapshot_id))
        record_id = cur.lastrowid
        # Only block-level records are comparable with block forecasts; a labelled record
        # evaluates the forecast's class, and numeric values give its error.
        if (start, end) == core.block_bounds(date.fromisoformat(payload.observed_date), payload.time_block):
            forecasting.record_outcome(db, segment_id, payload.observed_date, payload.time_block, payload.model_dump())
        if payload.camera_id:
            db.execute("UPDATE cameras SET last_success_at=? WHERE camera_id=?", (iso_now(), payload.camera_id))
    segments.invalidate_coverage()
    return {"id": record_id, "source_type": payload.source_type, "quality_status": "unverified", "quality_flags": flags,
            "segment_id": segment_id, "observed_start": start.isoformat(), "observed_end": end.isoformat() if end else None,
            "time_block": payload.time_block, "weather": weather_label, "weather_snapshot_id": snapshot_id}


class ObservationReview(BaseModel):
    quality_status: Literal["verified", "rejected"]
    note: str = Field(default="", max_length=500)


def _decode(rows: list[dict]) -> list[dict]:
    for item in rows:
        item["quality_flags"] = json.loads(item.pop("quality_flags_json") or "[]")
    return rows


@router.post("/api/observations")
def ingest_observation(raw: dict = Body(...), actor: str = Depends(authority_token)):
    return store_observation(raw)


@router.get("/api/observations")
def list_observations(quality_status: str | None = None, source_type: str | None = None, segment_id: str | None = None,
                      limit: int = Query(default=100, ge=1, le=1000), actor: str = Depends(authority_token)):
    clauses, params = [], []
    for column, value in (("quality_status", quality_status), ("source_type", source_type), ("segment_id", segment_id)):
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = store.rows(f"SELECT * FROM traffic_observations{where} ORDER BY COALESCE(observed_start,observed_date) DESC,id DESC LIMIT ?",
                      (*params, limit))
    return {"observations": _decode(rows), "count": len(rows)}


@router.patch("/api/observations/{observation_id}")
def review_observation(observation_id: int, payload: ObservationReview, actor: str = Depends(authority_token)):
    if not store.one("SELECT id FROM traffic_observations WHERE id=?", (observation_id,)):
        raise HTTPException(404, "Observation not found.")
    with store.connect() as db:
        db.execute("UPDATE traffic_observations SET quality_status=?,verified_by=?,verified_at=?,review_note=? WHERE id=?",
                   (payload.quality_status, actor, iso_now(), payload.note.strip() or None, observation_id))
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
                   ("observation_reviewed", "traffic_observation", str(observation_id), actor, iso_now(), payload.model_dump_json()))
    segments.invalidate_coverage()
    return _decode(store.rows("SELECT * FROM traffic_observations WHERE id=?", (observation_id,)))[0]


@router.get("/api/records")
def records(limit: int = Query(default=100, ge=1, le=1000), road: str | None = None, source_type: str | None = None):
    clauses, params = [], []
    if road:
        clauses.append("road=?")
        params.append(road)
    if source_type:
        clauses.append("source_type=?")
        params.append(source_type)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    result = store.rows(f"SELECT * FROM traffic_observations{where} ORDER BY observed_date DESC,id DESC LIMIT ?", (*params, limit))
    return {"records": _decode(result), "count": len(result)}


@router.get("/api/quarantine")
def quarantine(limit: int = Query(default=100, ge=1, le=500), actor: str = Depends(authority_token)):
    return {"records": store.rows("SELECT * FROM quarantined_observations ORDER BY id DESC LIMIT ?", (limit,))}
