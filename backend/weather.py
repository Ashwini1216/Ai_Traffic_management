"""Weather collection (Open-Meteo), storage with provenance, and alignment with traffic data.

Observed weather (what happened) and forecasts (what was expected when a prediction was
made) are stored as separate source types so later observations never leak into the
inputs of an earlier forecast.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import core
import store
from core import iso_now, parse_utc
from security import authority_token, signed_in_user

router = APIRouter()

ALIGN_WINDOW = timedelta(minutes=60)      # observed weather must be this close in time
FORECAST_WINDOW = timedelta(minutes=90)   # forecast hour must be this close to the target
MAX_DISTANCE_M = 25000                    # and the weather point this close in space
CACHE_MINUTES = 15
WET_WORDS = ("rain", "drizzle", "shower", "storm", "thunder")


def label_from_code(code: str | int | None) -> str | None:
    """A short weather label from a WMO weather code (as used by Open-Meteo)."""
    if code is None or str(code) in ("", "None"):
        return None
    value = int(float(code))
    if value == 0:
        return "Clear"
    if value in (1, 2, 3):
        return "Cloudy"
    if value in (45, 48):
        return "Fog"
    if value >= 95:
        return "Storm"
    if 51 <= value <= 67 or 80 <= value <= 86 or 71 <= value <= 77:
        return "Rain"
    return None


def is_wet(label: str | None, precipitation_mm: float | None = None) -> bool | None:
    """Rain or similar conditions, from a text label or measured precipitation (None if unknown)."""
    if precipitation_mm is not None and precipitation_mm >= 0.1:
        return True
    if label:
        return any(word in label.casefold() for word in WET_WORDS)
    return False if precipitation_mm is not None else None


def collect(latitude: float, longitude: float) -> dict:
    """Fetch current conditions and the hourly forecast for a point and store both."""
    now = datetime.now(timezone.utc)
    threshold = (now - timedelta(minutes=CACHE_MINUTES)).isoformat()
    cached = store.one("""SELECT captured_at FROM weather_snapshots
      WHERE source_name='Open-Meteo' AND source_type='provider_observation'
      AND ABS(latitude-?)<0.005 AND ABS(longitude-?)<0.005 AND captured_at>?
      ORDER BY captured_at DESC LIMIT 1""", (latitude, longitude, threshold))
    if cached:
        current = store.one("""SELECT * FROM weather_snapshots WHERE source_name='Open-Meteo'
          AND source_type='provider_observation' AND captured_at=? AND ABS(latitude-?)<0.005 AND ABS(longitude-?)<0.005""",
                            (cached["captured_at"], latitude, longitude))
        return {"provider": "Open-Meteo", "captured_at": cached["captured_at"], "records_saved": 0, "cached": True, "current": current}
    params = urllib.parse.urlencode({
        "latitude": latitude, "longitude": longitude, "timezone": "UTC", "forecast_days": 2,
        "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
        "hourly": "temperature_2m,precipitation,wind_speed_10m,weather_code",
    })
    data = core.external_json("https://api.open-meteo.com/v1/forecast?" + params)
    captured = iso_now()
    current = data.get("current", {})
    hourly = data.get("hourly", {})
    code = lambda value: None if value is None else str(value)  # keep missing codes missing
    records = []
    if current.get("time"):
        records.append((latitude, longitude, captured, None, current.get("temperature_2m"),
                        current.get("precipitation"), current.get("wind_speed_10m"), code(current.get("weather_code")),
                        "Open-Meteo", "provider_observation"))
    times = hourly.get("time", [])
    for index, forecast_time in enumerate(times):
        records.append((latitude, longitude, captured, forecast_time,
                        (hourly.get("temperature_2m") or [None] * len(times))[index],
                        (hourly.get("precipitation") or [None] * len(times))[index],
                        (hourly.get("wind_speed_10m") or [None] * len(times))[index],
                        code((hourly.get("weather_code") or [None] * len(times))[index]),
                        "Open-Meteo", "provider_forecast"))
    with store.connect() as db:
        db.executemany("""INSERT INTO weather_snapshots(latitude,longitude,captured_at,forecast_for,
          temperature_c,precipitation_mm,wind_kph,weather_code,source_name,source_type)
          VALUES(?,?,?,?,?,?,?,?,?,?)""", records)
    return {"provider": "Open-Meteo", "captured_at": captured, "records_saved": len(records),
            "current": current, "forecast_source_type": "provider_forecast"}


def _nearest(rows: list[dict], latitude: float, longitude: float, moment: datetime, field: str, window: timedelta) -> dict | None:
    best, best_key = None, None
    for row in rows:
        stamp = parse_utc(row[field])
        if stamp is None:
            continue
        gap = abs((stamp - moment).total_seconds())
        if gap > window.total_seconds():
            continue
        distance = core.haversine_m(latitude, longitude, row["latitude"], row["longitude"])
        if distance > MAX_DISTANCE_M:
            continue
        key = (gap, distance)  # rows arrive newest first, so ties keep the latest capture
        if best_key is None or key < best_key:
            best, best_key = row, key
    if best is not None:
        best = {**best, "distance_m": round(best_key[1]), "time_gap_minutes": round(best_key[0] / 60)}
    return best


def observed_at(latitude: float, longitude: float, moment: datetime, window: timedelta = ALIGN_WINDOW) -> dict | None:
    """Observed weather closest to a place and time (by default within an hour and 25 km)."""
    low, high = (moment - window).isoformat(), (moment + window).isoformat()
    rows = store.rows("""SELECT * FROM weather_snapshots WHERE source_type IN ('provider_observation','manual_test')
      AND forecast_for IS NULL AND captured_at>=? AND captured_at<=? ORDER BY captured_at DESC LIMIT 500""", (low, high))
    return _nearest(rows, latitude, longitude, moment, "captured_at", window)


def forecast_for(latitude: float, longitude: float, moment: datetime, issued_before: datetime | None = None) -> dict | None:
    """Forecast for a place and time, using only forecasts captured before `issued_before`."""
    issued_before = issued_before or datetime.now(timezone.utc)
    rows = store.rows("""SELECT * FROM weather_snapshots WHERE source_type='provider_forecast' AND forecast_for IS NOT NULL
      AND captured_at<=? ORDER BY captured_at DESC LIMIT 3000""", (issued_before.isoformat(),))
    return _nearest(rows, latitude, longitude, moment, "forecast_for", FORECAST_WINDOW)


# ---------- API ----------

class WeatherInput(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    captured_at: datetime
    forecast_for: datetime | None = None
    temperature_c: float | None = Field(default=None, ge=-80, le=70)
    precipitation_mm: float | None = Field(default=None, ge=0, le=2000)
    wind_kph: float | None = Field(default=None, ge=0, le=500)
    weather_code: str | None = Field(default=None, max_length=40)
    source_name: str = Field(min_length=2, max_length=100)
    source_type: Literal["provider_observation", "provider_forecast", "manual_test"]


@router.post("/api/weather/fetch")
def fetch_weather(latitude: float = Query(ge=-90, le=90), longitude: float = Query(ge=-180, le=180),
                  actor: str = Depends(signed_in_user)):
    return collect(latitude, longitude)


@router.post("/api/weather")
def add_weather(payload: WeatherInput, actor: str = Depends(authority_token)):
    if payload.captured_at.tzinfo is None or (payload.forecast_for and payload.forecast_for.tzinfo is None):
        raise HTTPException(422, "Weather timestamps must include a timezone.")
    with store.connect() as db:
        cur = db.execute("""INSERT INTO weather_snapshots(latitude,longitude,captured_at,forecast_for,
          temperature_c,precipitation_mm,wind_kph,weather_code,source_name,source_type)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (payload.latitude, payload.longitude, core.utc_iso(payload.captured_at), core.utc_iso(payload.forecast_for),
           payload.temperature_c, payload.precipitation_mm, payload.wind_kph, payload.weather_code,
           payload.source_name, payload.source_type))
    return {"weather_id": cur.lastrowid, "source_type": payload.source_type, "status": "stored_with_provenance"}


@router.get("/api/weather")
def list_weather(latitude: float | None = None, longitude: float | None = None, limit: int = Query(default=100, ge=1, le=500)):
    if (latitude is None) != (longitude is None):
        raise HTTPException(422, "Provide both latitude and longitude.")
    if latitude is not None:
        items = store.rows("""SELECT * FROM weather_snapshots ORDER BY
          ((latitude-?)*(latitude-?)+(longitude-?)*(longitude-?)),captured_at DESC LIMIT ?""",
                           (latitude, latitude, longitude, longitude, limit))
    else:
        items = store.rows("SELECT * FROM weather_snapshots ORDER BY captured_at DESC LIMIT ?", (limit,))
    return {"weather": items}
