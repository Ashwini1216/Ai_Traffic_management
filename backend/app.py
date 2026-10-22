from __future__ import annotations

import hashlib
import hmac
import json
import os
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import secrets
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import joblib
import pandas as pd
from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from jose import JWTError, jwt
from passlib.context import CryptContext
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
import store

MODEL_FILE = ROOT / "models" / "next_block_rf.joblib"
METRICS_FILE = ROOT / "models" / "next_block_rf_metrics.json"
LSTM_MODEL_FILE = ROOT / "models" / "next_block_lstm.keras"
LSTM_PREPROCESS_FILE = ROOT / "models" / "next_block_lstm_preprocess.joblib"
LSTM_METRICS_FILE = ROOT / "models" / "next_block_lstm_metrics.json"
SYNTHETIC_MANIFEST_FILE = ROOT / "data" / "synthetic_traffic_dataset_manifest.json"
TIME_BLOCKS = ["Midnight", "Morning", "Daytime", "Noon", "Evening", "Night"]
INCIDENT_CATEGORIES = {
    "accident", "breakdown", "road_blockage", "heavy_traffic", "construction",
    "waterlogging", "signal_failure", "diversion", "other",
}
JWT_ALGORITHM = "HS256"
_configured_jwt_secret = os.getenv("JWT_SECRET_KEY", "")
JWT_SECRET = _configured_jwt_secret if _configured_jwt_secret and not _configured_jwt_secret.startswith("REPLACE_") else secrets.token_urlsafe(48)
PASSWORDS = CryptContext(schemes=["bcrypt_sha256"], deprecated="auto")
app = FastAPI(
    title="Bengaluru Collaborative Traffic Monitor",
    description="Data-quality-aware local project API. Historical records and model estimates are clearly separated from live observations.",
    version="0.2.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Content-Type", "Authorization"],
)
FRONTEND_DIST = ROOT / "frontend" / "dist"
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")



def startup() -> None:
    store.initialize()
    username, password = os.getenv("AUTHORITY_USERNAME"), os.getenv("AUTHORITY_PASSWORD")
    if username and password and not password.startswith("CHANGE_ME") and not store.one("SELECT username FROM user_accounts WHERE username=?", (username.casefold(),)):
        with store.connect() as db:
            db.execute("INSERT INTO user_accounts(username,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                (username.casefold(), None, PASSWORDS.hash(password), "authority", iso_now()))


app.router.on_startup.append(startup)


class RegisterInput(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=10, max_length=128)
    email: str | None = Field(default=None, max_length=254)


class LoginInput(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=128)


def _decode_bearer(authorization: str | None) -> dict:
    provided = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not provided:
        raise HTTPException(401, "Sign in is required.")
    try:
        claims = jwt.decode(provided, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if not claims.get("sub") or not claims.get("role"):
            raise HTTPException(401, "Invalid account token.")
        return claims
    except JWTError as exc:
        raise HTTPException(401, "Invalid or expired account token.") from exc


def authority_token(authorization: str | None = Header(default=None)) -> str:
    expected = os.getenv("AUTHORITY_TOKEN")
    provided = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if expected and provided and hmac.compare_digest(provided, expected):
        return "legacy_authority_token"
    if not provided:
        raise HTTPException(401, "Sign in with an authority account to perform this action.")
    claims = _decode_bearer(authorization)
    if claims.get("role") != "authority":
        raise HTTPException(403, "Authority role is required for this action.")
    return str(claims["sub"])


def citizen_token(authorization: str | None = Header(default=None)) -> str:
    claims = _decode_bearer(authorization)
    if claims.get("role") != "citizen":
        raise HTTPException(403, "A commuter account is required for this action.")
    return str(claims["sub"])


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, "Date must use YYYY-MM-DD format.")


class ObservationInput(BaseModel):
    observed_date: str
    time_block: str
    area: str = Field(min_length=1, max_length=100)
    road: str = Field(min_length=1, max_length=200)
    traffic_volume: float | None = Field(default=None, ge=0, le=1_000_000)
    average_speed: float | None = Field(default=None, ge=0, le=250)
    travel_time_index: float | None = Field(default=None, gt=0, le=20)
    congestion_level: Literal["Low", "Medium", "High"] | None = None
    weather: str | None = Field(default=None, max_length=80)
    roadwork: str | None = Field(default=None, max_length=80)
    incident_reports: int | None = Field(default=None, ge=0, le=10000)
    source_type: Literal["manual", "camera"] = "manual"
    source_name: str = Field(default="user_entry", max_length=100)
    source_record_id: str | None = Field(default=None, max_length=200)
    camera_id: str | None = None


class IncidentInput(BaseModel):
    category: str
    description: str = Field(min_length=5, max_length=1000)
    area: str | None = Field(default=None, max_length=100)
    road: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    expires_at: datetime | None = None


class IncidentUpdate(BaseModel):
    status: Literal["verified", "rejected", "resolved"]
    official_note: str = Field(default="", max_length=500)
    expires_at: datetime | None = None


class BroadcastInput(BaseModel):
    message: str = Field(min_length=10, max_length=500)
    severity: Literal["info", "warning", "critical"] = "warning"
    expires_at: datetime | None = None


class CameraInput(BaseModel):
    camera_id: str = Field(min_length=2, max_length=80)
    display_name: str = Field(min_length=2, max_length=160)
    area: str | None = None
    road: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    source_type: Literal["authorized_cctv", "approved_ip_camera", "permitted_integration"]
    authorization_status: Literal["approved", "pending", "revoked"] = "pending"
    operational_status: Literal["online", "offline", "unknown"] = "unknown"


class TripInput(BaseModel):
    origin: str = Field(min_length=2, max_length=200)
    destination: str = Field(min_length=2, max_length=200)
    departure_at: datetime


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


class PredictionInput(BaseModel):
    Date: str | None = None
    Area_Name: str = Field(alias="Area Name")
    Road_Name: str = Field(alias="Road/Intersection Name")
    Weather_Conditions: str = Field(alias="Weather Conditions")
    Roadwork: str = Field(alias="Roadwork and Construction Activity")
    TimeBlock: str
    Traffic_Volume: float | None = Field(default=None, alias="Traffic Volume", ge=0)
    Average_Speed: float | None = Field(default=None, alias="Average Speed", ge=0, le=250)
    Travel_Time_Index: float | None = Field(default=None, alias="Travel Time Index", gt=0)
    Incident_Reports: int | None = Field(default=None, alias="Incident Reports", ge=0)


class SequenceStep(BaseModel):
    Date: str
    Area_Name: str = Field(alias="Area Name")
    Road_Name: str = Field(alias="Road/Intersection Name")
    Weather: str | None = Field(default=None, alias="Weather Conditions")
    Roadwork: str | None = Field(default=None, alias="Roadwork and Construction Activity")
    TimeBlock: str
    Traffic_Volume: float | None = Field(default=None, alias="Traffic Volume", ge=0)
    Average_Speed: float | None = Field(default=None, alias="Average Speed", ge=0, le=250)
    Travel_Time_Index: float | None = Field(default=None, alias="Travel Time Index", gt=0)
    Incident_Reports: int | None = Field(default=None, alias="Incident Reports", ge=0)


class SequencePredictionInput(BaseModel):
    sequence: list[SequenceStep] = Field(min_length=6, max_length=6)


@app.get("/", include_in_schema=False)
def dashboard():
    built = FRONTEND_DIST / "index.html"
    if built.is_file():
        return FileResponse(built)
    raise HTTPException(503, "React frontend is not built yet. Start the Vite dev server in frontend/ or run npm run build.")


@app.get("/styles.css", include_in_schema=False)
def styles():
    return FileResponse(ROOT / "frontend" / "styles.css", media_type="text/css")


def create_access_token(username: str, role: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    return jwt.encode({"sub": username, "role": role, "exp": expires}, JWT_SECRET, algorithm=JWT_ALGORITHM)


@app.post("/api/auth/register", status_code=201)
def register_account(payload: RegisterInput):
    username = payload.username.strip().casefold()
    if not username.replace("_", "").isalnum():
        raise HTTPException(422, "Username may contain letters, numbers, and underscores only.")
    if store.one("SELECT username FROM user_accounts WHERE username=?", (username,)):
        raise HTTPException(409, "That username is already registered.")
    if payload.email and store.one("SELECT username FROM user_accounts WHERE lower(email)=lower(?)", (payload.email.strip(),)):
        raise HTTPException(409, "That email is already registered.")
    with store.connect() as db:
        db.execute("INSERT INTO user_accounts(username,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
          (username, payload.email.strip().casefold() if payload.email else None,
           PASSWORDS.hash(payload.password), "citizen", iso_now()))
    return {"access_token": create_access_token(username, "citizen"), "token_type": "bearer",
      "expires_in": 43200, "user": {"username": username, "role": "citizen"}}


@app.post("/api/auth/login")
def login_account(payload: LoginInput):
    username = payload.username.strip().casefold()
    account = store.one("SELECT username,password_hash,role FROM user_accounts WHERE username=?", (username,))
    if not account or not PASSWORDS.verify(payload.password, account["password_hash"]):
        raise HTTPException(401, "Username or password is incorrect.")
    return {"access_token": create_access_token(account["username"], account["role"]), "token_type": "bearer",
      "expires_in": 43200, "user": {"username": account["username"], "role": account["role"]}}


@app.get("/api/auth/me")
def current_account(authorization: str | None = Header(default=None)):
    claims = _decode_bearer(authorization)
    return {"username": claims["sub"], "role": claims["role"]}


def external_json(url: str, user_agent: str = "BengaluruTrafficMonitor/0.2"):
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "The configured external data provider could not be reached.") from exc


def google_post_json(url: str, payload: dict):
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "Google Maps services are not configured. Add GOOGLE_MAPS_API_KEY to the project .env file.")
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline",
    })
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "Google Routes could not calculate this route. Check the server key, billing, and Routes API access.") from exc


def decode_google_polyline(encoded: str) -> list[list[float]]:
    coordinates: list[list[float]] = []
    index = latitude = longitude = 0
    while index < len(encoded):
        values = []
        for _ in range(2):
            result = shift = 0
            while True:
                if index >= len(encoded):
                    raise HTTPException(502, "Google Routes returned an invalid route shape.")
                value = ord(encoded[index]) - 63
                index += 1
                result |= (value & 0x1f) << shift
                shift += 5
                if value < 0x20:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        latitude += values[0]
        longitude += values[1]
        coordinates.append([longitude / 1e5, latitude / 1e5])
    return coordinates


@app.get("/api/map/config")
def map_config():
    return {"center": [12.9716, 77.5946], "zoom": 11, "map_provider": "Google Maps"}


@app.get("/api/geocode")
def geocode(q: str = Query(min_length=3, max_length=120)):
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "Google Maps services are not configured. Add GOOGLE_MAPS_API_KEY to the project .env file.")
    params = urllib.parse.urlencode({
        "address": q + ", Bengaluru, India", "region": "in",
        "bounds": "12.75,77.30|13.20,77.85", "key": key,
    })
    data = external_json("https://maps.googleapis.com/maps/api/geocode/json?" + params, "FlowStateTrafficMonitor/1.0")
    if data.get("status") == "ZERO_RESULTS":
        return {"query": q, "results": [], "source_type": "google_geocoding"}
    if data.get("status") != "OK":
        raise HTTPException(502, "Google Maps could not find this address. Check the Geocoding API setup and try a more specific address.")
    results = [{
        "display_name": item.get("formatted_address"),
        "latitude": float(item["geometry"]["location"]["lat"]),
        "longitude": float(item["geometry"]["location"]["lng"]),
        "place_type": (item.get("types") or [None])[0],
        "source_type": "google_geocoding",
    } for item in data.get("results", [])[:5]]
    return {"query": q, "results": results, "source_type": "google_geocoding"}


@app.post("/api/weather/fetch")
def fetch_weather(latitude: float = Query(ge=-90, le=90), longitude: float = Query(ge=-180, le=180)):
    now = datetime.now(timezone.utc)
    threshold = (now-timedelta(minutes=15)).isoformat()
    cached = store.one("""SELECT captured_at FROM weather_snapshots
      WHERE source_name='Open-Meteo' AND source_type='provider_observation'
      AND ABS(latitude-?)<0.005 AND ABS(longitude-?)<0.005 AND captured_at>?
      ORDER BY captured_at DESC LIMIT 1""", (latitude,longitude,threshold))
    if cached:
        cached_records = store.rows("SELECT * FROM weather_snapshots WHERE source_name='Open-Meteo' AND captured_at=?", (cached["captured_at"],))
        return {"provider":"Open-Meteo","captured_at":cached["captured_at"],"records_saved":0,
          "cached":True,"current":next((x for x in cached_records if x["source_type"]=="provider_observation"),None)}
    params = urllib.parse.urlencode({
        "latitude": latitude, "longitude": longitude, "timezone": "UTC", "forecast_days": 2,
        "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
        "hourly": "temperature_2m,precipitation,wind_speed_10m,weather_code",
    })
    data = external_json("https://api.open-meteo.com/v1/forecast?" + params)
    captured = iso_now()
    current = data.get("current", {})
    hourly = data.get("hourly", {})
    records = []
    if current.get("time"):
        records.append((latitude,longitude,captured,None,current.get("temperature_2m"),
          current.get("precipitation"),current.get("wind_speed_10m"),str(current.get("weather_code")),
          "Open-Meteo","provider_observation"))
    times = hourly.get("time", [])
    for index, forecast_time in enumerate(times):
        records.append((latitude,longitude,captured,forecast_time,
          (hourly.get("temperature_2m") or [None]*len(times))[index],
          (hourly.get("precipitation") or [None]*len(times))[index],
          (hourly.get("wind_speed_10m") or [None]*len(times))[index],
          str((hourly.get("weather_code") or [None]*len(times))[index]),
          "Open-Meteo","provider_forecast"))
    with store.connect() as db:
        db.executemany("""INSERT INTO weather_snapshots(latitude,longitude,captured_at,forecast_for,
          temperature_c,precipitation_mm,wind_kph,weather_code,source_name,source_type)
          VALUES(?,?,?,?,?,?,?,?,?,?)""", records)
    return {"provider": "Open-Meteo", "captured_at": captured, "records_saved": len(records),
      "current": current, "forecast_source_type": "provider_forecast"}


def distance_to_route_m(latitude: float, longitude: float, coordinates: list[list[float]]) -> float:
    if not coordinates:
        return float("inf")
    best = float("inf")
    for lon, lat in coordinates:
        lat1, lat2 = math.radians(latitude), math.radians(lat)
        dlat = lat1-lat2
        dlon = math.radians(longitude-lon)
        a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
        best = min(best, 6371000*2*math.atan2(math.sqrt(a), math.sqrt(max(0,1-a))))
    return best


@app.get("/api/routes")
def route_candidates(
    origin_lat: float = Query(ge=-90, le=90), origin_lon: float = Query(ge=-180, le=180),
    destination_lat: float = Query(ge=-90, le=90), destination_lon: float = Query(ge=-180, le=180),
    profile: Literal["driving", "cycling", "walking"] = "driving",
    departure_at: datetime | None = None,
):
    target_departure = departure_at or datetime.now(timezone.utc)
    if target_departure.tzinfo is None:
        raise HTTPException(422, "departure_at must include a timezone.")
    google_mode = {"driving": "DRIVE", "cycling": "BICYCLE", "walking": "WALK"}[profile]
    route_request = {
        "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lon}}},
        "destination": {"location": {"latLng": {"latitude": destination_lat, "longitude": destination_lon}}},
        "travelMode": google_mode,
        "computeAlternativeRoutes": profile == "driving",
        "languageCode": "en-IN", "units": "METRIC",
    }
    if profile == "driving":
        route_request["routingPreference"] = "TRAFFIC_AWARE"
        route_request["departureTime"] = target_departure.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    data = google_post_json("https://routes.googleapis.com/directions/v2:computeRoutes", route_request)
    if not data.get("routes"):
        raise HTTPException(404, "Google Maps could not find a route for those locations.")
    weather_rows = store.rows("""SELECT * FROM weather_snapshots WHERE source_type='provider_forecast'
      AND forecast_for IS NOT NULL ORDER BY captured_at DESC LIMIT 2000""")
    closest_weather = None
    closest_weather_distance = float("inf")
    for weather_item in weather_rows:
        try:
            forecast_time = datetime.fromisoformat(weather_item["forecast_for"].replace("Z","+00:00"))
            if forecast_time.tzinfo is None:
                forecast_time = forecast_time.replace(tzinfo=timezone.utc)
            if abs((forecast_time-target_departure.astimezone(timezone.utc)).total_seconds()) > 7200:
                continue
            distance = min(
              distance_to_route_m(weather_item["latitude"],weather_item["longitude"],[[origin_lon,origin_lat]]),
              distance_to_route_m(weather_item["latitude"],weather_item["longitude"],[[destination_lon,destination_lat]])
            )
            if distance <= 25000 and distance < closest_weather_distance:
                closest_weather,closest_weather_distance = weather_item,distance
        except (ValueError,TypeError):
            continue
    weather_penalty = 0
    if closest_weather:
        precipitation = closest_weather.get("precipitation_mm") or 0
        wind = closest_weather.get("wind_kph") or 0
        weather_penalty = (10 if precipitation >= 0.1 else 0) + (15 if precipitation > 2 else 0) + (5 if wind > 40 else 0)
    active = store.rows("""SELECT category,area,road,latitude,longitude,created_at,expires_at FROM incidents
      WHERE status='verified' AND latitude IS NOT NULL AND longitude IS NOT NULL
      AND (expires_at IS NULL OR expires_at>?)""", (iso_now(),))
    routes = []
    for route in data.get("routes", []):
        polyline = route.get("polyline", {}).get("encodedPolyline", "")
        coordinates = decode_google_polyline(polyline)
        duration_text = route.get("duration", "0s")
        try:
            duration_seconds = float(duration_text.removesuffix("s"))
        except (TypeError, ValueError):
            raise HTTPException(502, "Google Routes returned an invalid travel time.")
        routes.append({
            "duration": duration_seconds,
            "distance": route.get("distanceMeters", 0),
            "geometry": {"type": "LineString", "coordinates": coordinates},
            "legs": [],
        })
    shortest = min((route.get("duration", 0) for route in routes), default=1) or 1
    category_weight = {"accident":20,"road_blockage":25,"waterlogging":18,"construction":14,
      "diversion":18,"signal_failure":12,"breakdown":12,"heavy_traffic":14,"other":8}
    scored = []
    for index, route in enumerate(routes):
        geometry = route.get("geometry", {}).get("coordinates", [])
        nearby = []
        for incident in active:
            distance = distance_to_route_m(incident["latitude"],incident["longitude"],geometry)
            if distance <= 200:
                nearby.append({"category":incident["category"],"road":incident["road"],
                  "distance_m":round(distance),"created_at":incident["created_at"]})
        detour = max(0.0, (route.get("duration",shortest)/shortest-1)*25)
        incident_penalty = sum(category_weight.get(item["category"],8) for item in nearby)
        scored.append({
          "route_index":index,"distance_m":route.get("distance"),"baseline_duration_s":route.get("duration"),
          "geometry":route.get("geometry"),"legs":route.get("legs"),
          "nearby_verified_incidents":nearby,"weather_context":closest_weather,"weather_penalty":weather_penalty,"risk_score":round(min(100,detour+incident_penalty+weather_penalty),1),
          "source_type":"google_maps_route",
        })
    scored.sort(key=lambda item:(item["risk_score"],item["baseline_duration_s"] or float("inf")))
    route_id = str(uuid.uuid4())
    scoring = "Unvalidated heuristic: Google route alternatives, category-weighted verified incidents within 200 m, and available weather context."
    return {"route_id":route_id,"provider":"Google Routes API","routes":scored,"scoring_method":scoring,
      "traffic_data_coverage":"Google traffic-aware routing when available","weather_status":"provider_forecast_used" if closest_weather else "unavailable","notice":"Driving time uses Google's traffic-aware route estimate when available. The project risk score is an unvalidated research heuristic."}


@app.get("/api/health")
def health():
    result = store.one("SELECT COUNT(*) AS records FROM traffic_observations")
    return {"status": "ok", "service": "traffic-monitor", "database": "connected", "observation_records": result["records"]}


@app.get("/api/summary")
def summary():
    totals = store.one("""SELECT COUNT(*) record_count, COUNT(DISTINCT road) road_count,
        COUNT(DISTINCT area) area_count, MIN(observed_date) date_start, MAX(observed_date) date_end
        FROM traffic_observations WHERE source_type='historical_dataset'""")
    counts = store.rows("""SELECT COALESCE(congestion_level,'Unknown') label, COUNT(*) count
        FROM traffic_observations WHERE source_type='historical_dataset' GROUP BY congestion_level""")
    return {
        "project": "Bengaluru Traffic Monitor", "dataset": "bangalore_traffic_timely_dataset.zip",
        "source_type": "historical_dataset", **totals,
        "traffic_situation_counts": {row["label"]: row["count"] for row in counts},
        "notice": "Historical dataset records are not live traffic observations.",
    }


@app.get("/api/synthetic/summary")
def synthetic_summary():
    if not SYNTHETIC_MANIFEST_FILE.exists():
        return {"available": False, "source_type": "synthetic", "message": "Run backend/generate_synthetic_dataset.py to create the separate simulation dataset."}
    return {"available": True, **json.loads(SYNTHETIC_MANIFEST_FILE.read_text(encoding="utf-8"))}


@app.get("/api/records")
def records(limit: int = Query(default=100, ge=1, le=1000), road: str | None = None, source_type: str | None = None):
    clauses, params = [], []
    if road:
        clauses.append("road=?"); params.append(road)
    if source_type:
        clauses.append("source_type=?"); params.append(source_type)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    result = store.rows(f"SELECT * FROM traffic_observations{where} ORDER BY observed_date DESC,id DESC LIMIT ?", (*params, limit))
    for item in result:
        item["quality_flags"] = json.loads(item.pop("quality_flags_json"))
    return {"records": result, "count": len(result)}


@app.get("/api/coverage")
def coverage():
    result = store.rows("""SELECT area,road,MIN(observed_date) earliest_observation,
        MAX(observed_date) latest_observation,COUNT(*) record_count,
        SUM(CASE WHEN quality_status='verified' THEN 1 ELSE 0 END) verified_count,
        SUM(CASE WHEN quality_status='unverified' THEN 1 ELSE 0 END) unverified_count
        FROM traffic_observations GROUP BY area,road ORDER BY area,road""")
    for item in result:
        item["verified_coverage"] = item["verified_count"] > 0
        item["coverage_note"] = "Historical records exist; none are independently verified." if item["verified_count"] == 0 else "At least one verified record exists."
    return {"segments": result, "segment_count": len(result), "map_coverage_is_not_traffic_coverage": True}


@app.post("/api/observations")
def ingest_observation(raw: dict = Body(...)):
    try:
        payload = ObservationInput.model_validate(raw)
    except ValidationError as exc:
        reason = json.dumps(exc.errors(), default=str)
        with store.connect() as db:
            cur = db.execute("INSERT INTO quarantined_observations(source_type,raw_json,reason,ingested_at) VALUES(?,?,?,?)", (str(raw.get("source_type", "unknown")), json.dumps(raw, default=str), reason, iso_now()))
            quarantine_id = cur.lastrowid
        raise HTTPException(422, detail={"message": "Invalid observation quarantined for review.", "quarantine_id": quarantine_id, "errors": exc.errors()})
    parse_date(payload.observed_date)
    if payload.time_block not in TIME_BLOCKS:
        raise HTTPException(422, "time_block must be one of: " + ", ".join(TIME_BLOCKS))
    camera_id = payload.camera_id
    if payload.source_type == "camera":
        if not camera_id:
            raise HTTPException(422, "camera_id is required for camera observations.")
        camera = store.one("SELECT * FROM cameras WHERE camera_id=?", (camera_id,))
        if not camera or camera["authorization_status"] != "approved" or camera["operational_status"] != "online":
            raise HTTPException(403, "This camera is not approved and online in the camera registry.")
    values = payload.model_dump()
    values.pop("source_type")
    values.pop("source_name")
    values.pop("camera_id")
    flags = []
    if payload.traffic_volume is None: flags.append("traffic_volume_not_provided")
    if payload.average_speed is None: flags.append("average_speed_not_provided")
    if payload.travel_time_index is None: flags.append("travel_time_index_not_provided")
    if payload.congestion_level is None: flags.append("congestion_label_not_provided")
    if payload.source_type == "manual": flags.append("manual_source_not_independently_verified")
    if payload.source_type == "camera":
        flags.append("camera_source_requires_site_validation")
        if payload.source_name == "yolov8n_video_count":
            flags.append("clip_vehicle_count_is_not_calibrated_block_volume")
    quality = "unverified"
    with store.connect() as db:
        cur = db.execute("""INSERT INTO traffic_observations
          (source_type,source_name,source_record_id,observed_date,time_block,area,road,
           traffic_volume,average_speed,travel_time_index,congestion_level,weather,roadwork,
           incident_reports,quality_status,quality_flags_json,camera_id,ingested_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (payload.source_type, payload.source_name, payload.source_record_id, payload.observed_date,
           payload.time_block, payload.area, payload.road, payload.traffic_volume, payload.average_speed,
           payload.travel_time_index, payload.congestion_level, payload.weather, payload.roadwork,
           payload.incident_reports, quality, json.dumps(flags), camera_id, iso_now()))
        record_id = cur.lastrowid
        db.execute("UPDATE model_predictions SET observed_outcome=?,evaluated_at=? WHERE target_date=? AND target_time_block=? AND target_area=? AND target_road=? AND observed_outcome IS NULL", (payload.congestion_level,iso_now(),payload.observed_date,payload.time_block,payload.area,payload.road))
        if camera_id:
            db.execute("UPDATE cameras SET last_success_at=? WHERE camera_id=?", (iso_now(), camera_id))
    return {"id": record_id, "source_type": payload.source_type, "quality_status": quality, "quality_flags": flags}


@app.get("/api/quarantine")
def quarantine(limit: int = Query(default=100, ge=1, le=500), actor: str = Depends(authority_token)):
    return {"records": store.rows("SELECT * FROM quarantined_observations ORDER BY id DESC LIMIT ?", (limit,))}


@app.get("/api/cameras")
def cameras(actor: str = Depends(authority_token)):
    return {"cameras": store.rows("SELECT camera_id,display_name,area,road,latitude,longitude,source_type,authorization_status,operational_status,last_success_at FROM cameras ORDER BY display_name")}


@app.post("/api/cameras")
def register_camera(payload: CameraInput, actor: str = Depends(authority_token)):
    with store.connect() as db:
        db.execute("""INSERT INTO cameras(camera_id,display_name,area,road,latitude,longitude,source_type,
          authorization_status,operational_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(camera_id) DO UPDATE SET display_name=excluded.display_name,area=excluded.area,
          road=excluded.road,latitude=excluded.latitude,longitude=excluded.longitude,source_type=excluded.source_type,
          authorization_status=excluded.authorization_status,operational_status=excluded.operational_status""",
          (payload.camera_id,payload.display_name,payload.area,payload.road,payload.latitude,payload.longitude,
           payload.source_type,payload.authorization_status,payload.operational_status,iso_now()))
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
           ("camera_registry_update","camera",payload.camera_id,actor,iso_now(),payload.model_dump_json()))
    return {"camera_id": payload.camera_id, "authorization_status": payload.authorization_status, "operational_status": payload.operational_status}


@app.post("/api/cameras/{camera_id}/analyze-video")
async def analyze_camera_video(camera_id: str, video: UploadFile = File(...),
                               observed_date: str = Form(...), time_block: str = Form(...),
                               actor: str = Depends(authority_token)):
    camera = store.one("SELECT * FROM cameras WHERE camera_id=?", (camera_id,))
    if not camera or camera["authorization_status"] != "approved" or camera["operational_status"] != "online":
        raise HTTPException(403, "Video analysis requires a camera marked approved and online by an authority.")
    if not camera["area"] or not camera["road"]:
        raise HTTPException(422, "Add the camera area and road to its registry before analyzing video.")
    parse_date(observed_date)
    if time_block not in TIME_BLOCKS:
        raise HTTPException(422, "time_block must be one of: " + ", ".join(TIME_BLOCKS))
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".avi", ".mov", ".mkv"}:
        raise HTTPException(415, "Upload a supported MP4, AVI, MOV, or MKV video.")
    contents = await video.read(100 * 1024 * 1024 + 1)
    await video.close()
    if len(contents) > 100 * 1024 * 1024:
        raise HTTPException(413, "Video clips must be 100 MB or smaller.")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_file.write(contents)
            temp_path = Path(temp_file.name)
        from vision import analyze_video
        analysis = analyze_video(temp_path)
    except ImportError as exc:
        raise HTTPException(503, "Install the project dependencies from the root requirements.txt before using video analysis.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise
    footage_id = str(uuid.uuid4())
    footage_dir = ROOT / "data" / "cctv_footage"
    footage_dir.mkdir(parents=True, exist_ok=True)
    stored_path = footage_dir / f"{footage_id}{suffix}"
    if temp_path:
        temp_path.replace(stored_path)
        temp_path = None
    try:
        observation = ingest_observation({
            "observed_date": observed_date, "time_block": time_block, "area": camera["area"], "road": camera["road"],
            "traffic_volume": analysis["clip_vehicle_count_estimate"], "average_speed": None,
            "travel_time_index": None, "congestion_level": None, "weather": None, "roadwork": None,
            "incident_reports": None, "source_type": "camera", "source_name": "yolov8n_video_count",
            "camera_id": camera_id,
        })
        with store.connect() as db:
            db.execute("""INSERT INTO camera_footage(footage_id,camera_id,original_filename,stored_path,content_type,
              uploaded_by,uploaded_at,analysis_json) VALUES(?,?,?,?,?,?,?,?)""",
              (footage_id,camera_id,Path(video.filename or "camera-footage" + suffix).name,str(stored_path),
               video.content_type or "application/octet-stream",actor,iso_now(),json.dumps(analysis)))
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise
    return {"camera_id": camera_id, "observation_id": observation["id"], "observation_quality": observation["quality_status"],
      "footage_id": footage_id, "playback_url": f"/api/camera-footage/{footage_id}/video", "analysis": analysis, "actor": actor}


@app.get("/api/camera-footage")
def list_camera_footage(actor: str = Depends(authority_token)):
    return {"footage": store.rows("""SELECT f.footage_id,f.camera_id,c.display_name,f.original_filename,
      f.content_type,f.uploaded_by,f.uploaded_at,f.analysis_json FROM camera_footage f
      JOIN cameras c ON c.camera_id=f.camera_id ORDER BY f.uploaded_at DESC LIMIT 200""")}


@app.get("/api/camera-footage/{footage_id}/video")
def play_camera_footage(footage_id: str, actor: str = Depends(authority_token)):
    record = store.one("SELECT stored_path,content_type,original_filename FROM camera_footage WHERE footage_id=?", (footage_id,))
    if not record:
        raise HTTPException(404, "Camera footage not found.")
    path = Path(record["stored_path"]).resolve()
    if not path.is_relative_to((ROOT / "data" / "cctv_footage").resolve()) or not path.is_file():
        raise HTTPException(404, "Camera footage file is unavailable.")
    return FileResponse(path, media_type=record["content_type"], filename=record["original_filename"], content_disposition_type="inline")


@app.post("/api/incidents/{incident_id}/broadcast", status_code=201)
def broadcast_incident(incident_id: str, payload: BroadcastInput, actor: str = Depends(authority_token)):
    incident = store.one("SELECT * FROM incidents WHERE incident_id=?", (incident_id,))
    if not incident:
        raise HTTPException(404, "Incident not found.")
    if incident["status"] != "verified":
        raise HTTPException(409, "Verify the incident before broadcasting it.")
    if payload.expires_at and payload.expires_at.tzinfo is None:
        raise HTTPException(422, "expires_at must include a timezone.")
    broadcast_id = str(uuid.uuid4())
    with store.connect() as db:
        db.execute("""INSERT INTO incident_broadcasts(broadcast_id,incident_id,message,area,road,severity,status,
          created_by,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (broadcast_id,incident_id,payload.message,incident["area"],incident["road"],payload.severity,"active",
           actor,iso_now(),payload.expires_at.isoformat() if payload.expires_at else None))
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
          ("incident_broadcast_created","incident_broadcast",broadcast_id,actor,iso_now(),payload.model_dump_json()))
    return {"broadcast_id":broadcast_id,"incident_id":incident_id,"status":"active","severity":payload.severity}


@app.get("/api/broadcasts")
def public_broadcasts():
    return {"broadcasts":store.rows("""SELECT broadcast_id,incident_id,message,area,road,severity,created_at,expires_at
      FROM incident_broadcasts WHERE status='active' AND (expires_at IS NULL OR expires_at>?)
      ORDER BY created_at DESC LIMIT 100""",(iso_now(),))}


@app.get("/api/authority/overview")
def authority_overview(actor: str = Depends(authority_token)):
    return {"actor":actor,"health":health(),"coverage":coverage(),"incidents":incidents(limit=200),
      "cameras":cameras(),"footage_count":store.one("SELECT COUNT(*) AS count FROM camera_footage")["count"],
      "broadcasts":store.rows("SELECT * FROM incident_broadcasts WHERE status='active' AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC",(iso_now(),))}


@app.post("/api/incidents", status_code=201)
def report_incident(payload: IncidentInput, actor: str = Depends(citizen_token)):
    if payload.category not in INCIDENT_CATEGORIES:
        raise HTTPException(422, "Unsupported incident category.")
    if (payload.latitude is None) != (payload.longitude is None):
        raise HTTPException(422, "Provide both latitude and longitude, or neither.")
    incident_id = str(uuid.uuid4())
    expires = payload.expires_at
    if expires and expires.tzinfo is None:
        raise HTTPException(422, "expires_at must include a timezone.")
    with store.connect() as db:
        db.execute("""INSERT INTO incidents(incident_id,category,description,area,road,latitude,longitude,
          status,report_source,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          (incident_id,payload.category,payload.description,payload.area,payload.road,payload.latitude,
           payload.longitude,"reported","citizen_report",iso_now(),expires.isoformat() if expires else None))
    return {"incident_id": incident_id, "status": "reported", "verification_required": True}


@app.get("/api/incidents")
def incidents(status: str | None = None, limit: int = Query(default=100, ge=1, le=500),
              actor: str = Depends(authority_token)):
    params: list = []
    where = ""
    if status:
        where = " WHERE status=?"
        params.append(status)
    params.append(limit)
    result = store.rows(f"""SELECT incident_id,category,description,area,road,latitude,longitude,status,
      report_source,created_at,expires_at FROM incidents{where} ORDER BY created_at DESC LIMIT ?""", tuple(params))
    return {"incidents": result}


@app.patch("/api/incidents/{incident_id}")
def update_incident(incident_id: str, payload: IncidentUpdate, actor: str = Depends(authority_token)):
    if not store.one("SELECT incident_id FROM incidents WHERE incident_id=?", (incident_id,)):
        raise HTTPException(404, "Incident not found.")
    if payload.expires_at and payload.expires_at.tzinfo is None:
        raise HTTPException(422, "expires_at must include a timezone.")
    with store.connect() as db:
        db.execute("UPDATE incidents SET status=?,expires_at=COALESCE(?,expires_at) WHERE incident_id=?",
          (payload.status,payload.expires_at.isoformat() if payload.expires_at else None,incident_id))
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
          ("incident_status_changed","incident",incident_id,actor,iso_now(),payload.model_dump_json()))
    return {"incident_id": incident_id, "status": payload.status, "official_note": payload.official_note}


@app.post("/api/trips", status_code=201)
def create_trip(payload: TripInput, actor: str = Depends(citizen_token)):
    if payload.departure_at.tzinfo is None:
        raise HTTPException(422, "departure_at must include a timezone.")
    if payload.departure_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "departure_at must be in the future.")
    token = secrets.token_urlsafe(32)
    trip_id = str(uuid.uuid4())
    with store.connect() as db:
        db.execute("INSERT INTO planned_trips VALUES(?,?,?,?,?,?,?)",
          (trip_id,hashlib.sha256(token.encode()).hexdigest(),payload.origin,payload.destination,
           payload.departure_at.astimezone(timezone.utc).isoformat(),"scheduled",iso_now()))
    return {"trip_id": trip_id, "owner_token": token, "departure_at": payload.departure_at.isoformat(),
      "privacy_notice": "Save the owner token to cancel the trip. Trip-level details are not exposed in demand summaries."}


@app.patch("/api/trips/{trip_id}/cancel")
def cancel_trip(trip_id: str, authorization: str | None = Header(default=None)):
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not token:
        raise HTTPException(401, "The trip owner token is required.")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with store.connect() as db:
        cur = db.execute("UPDATE planned_trips SET state='cancelled' WHERE trip_id=? AND owner_token_hash=? AND state='scheduled'",
          (trip_id,digest))
        if cur.rowcount != 1:
            raise HTTPException(404, "Scheduled trip not found for this owner token.")
    return {"trip_id": trip_id, "state": "cancelled"}


@app.patch("/api/trips/{trip_id}")
def update_trip(trip_id: str, payload: TripInput, authorization: str | None = Header(default=None)):
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not token:
        raise HTTPException(401, "The trip owner token is required.")
    if payload.departure_at.tzinfo is None or payload.departure_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "departure_at must include a timezone and be in the future.")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with store.connect() as db:
        cur = db.execute("""UPDATE planned_trips SET origin=?,destination=?,departure_at=?
          WHERE trip_id=? AND owner_token_hash=? AND state='scheduled'""",
          (payload.origin,payload.destination,payload.departure_at.astimezone(timezone.utc).isoformat(),trip_id,digest))
        if cur.rowcount != 1:
            raise HTTPException(404, "Scheduled trip not found for this owner token.")
    return {"trip_id": trip_id, "state": "scheduled", "departure_at": payload.departure_at.isoformat()}


@app.get("/api/demand")
def demand():
    result = store.rows("""SELECT origin,destination,substr(departure_at,1,13) || ':00:00Z' departure_hour,
      COUNT(*) planned_trip_count FROM planned_trips WHERE state='scheduled'
      GROUP BY origin,destination,departure_hour ORDER BY departure_hour,planned_trip_count DESC""")
    return {"aggregation": "corridor and departure hour; individual journeys are not returned", "corridors": result}


@app.post("/api/weather")
def add_weather(payload: WeatherInput):
    if payload.captured_at.tzinfo is None or (payload.forecast_for and payload.forecast_for.tzinfo is None):
        raise HTTPException(422, "Weather timestamps must include a timezone.")
    with store.connect() as db:
        cur = db.execute("""INSERT INTO weather_snapshots(latitude,longitude,captured_at,forecast_for,
          temperature_c,precipitation_mm,wind_kph,weather_code,source_name,source_type)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (payload.latitude,payload.longitude,payload.captured_at.isoformat(),
           payload.forecast_for.isoformat() if payload.forecast_for else None,payload.temperature_c,
           payload.precipitation_mm,payload.wind_kph,payload.weather_code,payload.source_name,payload.source_type))
    return {"weather_id": cur.lastrowid, "source_type": payload.source_type, "status": "stored_with_provenance"}


@app.get("/api/weather")
def weather(latitude: float | None = None, longitude: float | None = None, limit: int = Query(default=100, ge=1, le=500)):
    if (latitude is None) != (longitude is None):
        raise HTTPException(422, "Provide both latitude and longitude.")
    if latitude is not None:
        items = store.rows("""SELECT * FROM weather_snapshots ORDER BY
          ((latitude-?)*(latitude-?)+(longitude-?)*(longitude-?)),captured_at DESC LIMIT ?""",
          (latitude,latitude,longitude,longitude,limit))
    else:
        items = store.rows("SELECT * FROM weather_snapshots ORDER BY captured_at DESC LIMIT ?", (limit,))
    return {"weather": items}


@app.get("/api/model")
def model_report():
    if not METRICS_FILE.exists():
        raise HTTPException(404, "Model is not trained yet. Run py backend\\train_model.py")
    return json.loads(METRICS_FILE.read_text(encoding="utf-8"))


@app.post("/api/predict")
def predict(payload: PredictionInput):
    if not MODEL_FILE.exists():
        raise HTTPException(404, "Model is not trained yet. Run py backend\\train_model.py")
    if payload.TimeBlock not in TIME_BLOCKS:
        raise HTTPException(422, "TimeBlock must be one of: " + ", ".join(TIME_BLOCKS))
    current_date = parse_date(payload.Date) if payload.Date else datetime.now().date()
    next_index = (TIME_BLOCKS.index(payload.TimeBlock) + 1) % len(TIME_BLOCKS)
    target_date = current_date + timedelta(days=1) if payload.TimeBlock == "Night" else current_date
    target_block = TIME_BLOCKS[next_index]
    model = joblib.load(MODEL_FILE)
    values = {
        "Area Name": payload.Area_Name, "Road/Intersection Name": payload.Road_Name,
        "Weather Conditions": payload.Weather_Conditions, "Roadwork and Construction Activity": payload.Roadwork,
        "TimeBlock": payload.TimeBlock, "Traffic Volume": payload.Traffic_Volume,
        "Average Speed": payload.Average_Speed, "Travel Time Index": payload.Travel_Time_Index,
        "Incident Reports": payload.Incident_Reports, "month": current_date.month, "day_of_week": current_date.weekday(),
    }
    features = pd.DataFrame([values], columns=model["feature_columns"])
    pipeline = model["pipeline"]
    predicted = str(pipeline.predict(features)[0])
    probabilities = {str(label): float(prob) for label, prob in zip(pipeline.named_steps["classifier"].classes_, pipeline.predict_proba(features)[0])}
    prediction_id = str(uuid.uuid4())
    with store.connect() as db:
        db.execute("""INSERT INTO model_predictions(prediction_id,issue_time,target_date,target_area,target_road,target_time_block,
          source_type,model_version,predicted_class,probabilities_json,input_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          (prediction_id,iso_now(),target_date.isoformat(),payload.Area_Name,payload.Road_Name,target_block,"model_prediction",
           "next_block_rf_v1",predicted,json.dumps(probabilities),payload.model_dump_json(by_alias=True)))
    return {"prediction_id": prediction_id, "source_type": "model_prediction",
      "prediction_task": "next available time block for this road",
      "input_time_block": payload.TimeBlock, "target_date": target_date.isoformat(),
      "target_time_block": target_block, "predicted_traffic_situation": predicted,
      "probabilities": probabilities, "model_version": "next_block_rf_v1",
      "notice": "Research estimate from historical data; not a live traffic observation."}


def _predict_lstm_sequence(steps: list[SequenceStep], history_source_type: str) -> dict:
    if not LSTM_MODEL_FILE.exists() or not LSTM_PREPROCESS_FILE.exists():
        raise HTTPException(404, "LSTM model is not trained yet. Run py backend\\train_lstm.py")
    if len(steps) != 6:
        raise HTTPException(422, "Exactly six consecutive observations are required.")
    area, road = steps[0].Area_Name, steps[0].Road_Name
    for index, step in enumerate(steps):
        if step.Area_Name != area or step.Road_Name != road:
            raise HTTPException(422, "All six observations must belong to the same area and road.")
        if step.TimeBlock not in TIME_BLOCKS:
            raise HTTPException(422, "TimeBlock must be one of: " + ", ".join(TIME_BLOCKS))
        parse_date(step.Date)
        if index:
            previous = steps[index - 1]
            previous_date, current_date = date.fromisoformat(previous.Date), date.fromisoformat(step.Date)
            previous_index, current_index = TIME_BLOCKS.index(previous.TimeBlock), TIME_BLOCKS.index(step.TimeBlock)
            consecutive = (previous_date == current_date and current_index == previous_index + 1) or (
                previous_index == len(TIME_BLOCKS) - 1 and current_index == 0 and current_date == previous_date + timedelta(days=1)
            )
            if not consecutive:
                raise HTTPException(422, "The six observations must be chronologically consecutive time blocks.")

    artifact = joblib.load(LSTM_PREPROCESS_FILE)
    feature_rows = []
    for step in steps:
        observed = date.fromisoformat(step.Date)
        feature_rows.append({
            "Area Name": step.Area_Name,
            "Road/Intersection Name": step.Road_Name,
            "Weather Conditions": step.Weather,
            "Roadwork and Construction Activity": step.Roadwork,
            "TimeBlock": step.TimeBlock,
            "Traffic Volume": step.Traffic_Volume,
            "Average Speed": step.Average_Speed,
            "Travel Time Index": step.Travel_Time_Index,
            "Incident Reports": step.Incident_Reports,
            "month": observed.month,
            "day_of_week": observed.weekday(),
        })
    matrix = artifact["preprocessor"].transform(pd.DataFrame(feature_rows, columns=artifact["features"]))
    if hasattr(matrix, "toarray"):
        matrix = matrix.toarray()
    import tensorflow as tf
    model = tf.keras.models.load_model(LSTM_MODEL_FILE)
    probabilities_array = model.predict(matrix.astype("float32").reshape(1, 6, -1), verbose=0)[0]
    encoder = artifact["label_encoder"]
    predicted = str(encoder.inverse_transform([int(probabilities_array.argmax())])[0])
    probabilities = {str(label): float(prob) for label, prob in zip(encoder.classes_, probabilities_array)}
    last = steps[-1]
    next_index = (TIME_BLOCKS.index(last.TimeBlock) + 1) % len(TIME_BLOCKS)
    target_date = date.fromisoformat(last.Date) + (timedelta(days=1) if last.TimeBlock == "Night" else timedelta())
    target_block = TIME_BLOCKS[next_index]
    prediction_id = str(uuid.uuid4())
    input_json = json.dumps([step.model_dump(by_alias=True) for step in steps])
    with store.connect() as db:
        db.execute("""INSERT INTO model_predictions(prediction_id,issue_time,target_date,target_area,target_road,target_time_block,
          source_type,model_version,predicted_class,probabilities_json,input_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          (prediction_id, iso_now(), target_date.isoformat(), area, road, target_block, "model_prediction",
           "next_block_lstm_v1", predicted, json.dumps(probabilities), input_json))
    return {"prediction_id": prediction_id, "source_type": "model_prediction", "history_source_type": history_source_type,
      "history_quality_status": "unverified" if history_source_type == "historical_dataset" else "user_supplied_unverified",
      "target_date": target_date.isoformat(), "target_time_block": target_block,
      "predicted_traffic_situation": predicted, "probabilities": probabilities,
      "model_version": "next_block_lstm_v1", "model_validation_status": "not_accepted_for_deployment",
      "notice": "Experimental LSTM research estimate from unverified historical data. Its held-out accuracy did not beat the majority-class baseline; do not use for live traffic decisions."}


@app.get("/api/model/lstm")
def lstm_model_report():
    if not LSTM_METRICS_FILE.exists():
        raise HTTPException(404, "LSTM model metrics are not available yet.")
    return json.loads(LSTM_METRICS_FILE.read_text(encoding="utf-8"))


@app.post("/api/predict/sequence")
def predict_sequence(payload: SequencePredictionInput):
    return _predict_lstm_sequence(payload.sequence, "user_supplied_sequence")


@app.post("/api/predict/history")
def predict_from_history(area: str = Query(min_length=1, max_length=100),
                         road: str = Query(min_length=1, max_length=200),
                         through_date: str | None = None,
                         through_time_block: str | None = None):
    params: list = [area, road]
    where = "source_type='historical_dataset' AND area=? AND road=? AND congestion_level IS NOT NULL"
    if through_date:
        parse_date(through_date)
        where += " AND observed_date<=?"
        params.append(through_date)
    rows = store.rows(f"SELECT observed_date,time_block,area,road,weather,roadwork,traffic_volume,average_speed,travel_time_index,incident_reports FROM traffic_observations WHERE {where}", tuple(params))
    rows = [row for row in rows if row["time_block"] in TIME_BLOCKS]
    rows.sort(key=lambda row: (row["observed_date"], TIME_BLOCKS.index(row["time_block"])))
    if through_date and through_time_block:
        if through_time_block not in TIME_BLOCKS:
            raise HTTPException(422, "through_time_block must be one of: " + ", ".join(TIME_BLOCKS))
        rows = [row for row in rows if (row["observed_date"], TIME_BLOCKS.index(row["time_block"])) <=
                (through_date, TIME_BLOCKS.index(through_time_block))]
    if len(rows) < 6:
        raise HTTPException(422, "Fewer than six historical observations are available for this road.")
    # Select the latest contiguous six-row window, never silently bridge data gaps.
    chosen = None
    for end in range(len(rows), 5, -1):
        candidate = rows[end - 6:end]
        is_contiguous = True
        for previous, current in zip(candidate, candidate[1:]):
            previous_date, current_date = date.fromisoformat(previous["observed_date"]), date.fromisoformat(current["observed_date"])
            pi, ci = TIME_BLOCKS.index(previous["time_block"]), TIME_BLOCKS.index(current["time_block"])
            if not ((previous_date == current_date and ci == pi + 1) or
                    (pi == len(TIME_BLOCKS) - 1 and ci == 0 and current_date == previous_date + timedelta(days=1))):
                is_contiguous = False
                break
        if is_contiguous:
            chosen = candidate
            break
    if chosen is None:
        raise HTTPException(422, "No six consecutive historical time blocks were found for this road.")
    steps = [SequenceStep.model_validate({
        "Date": row["observed_date"], "Area Name": row["area"], "Road/Intersection Name": row["road"],
        "Weather Conditions": row["weather"], "Roadwork and Construction Activity": row["roadwork"],
        "TimeBlock": row["time_block"], "Traffic Volume": row["traffic_volume"],
        "Average Speed": row["average_speed"], "Travel Time Index": row["travel_time_index"],
        "Incident Reports": row["incident_reports"],
    }) for row in chosen]
    result = _predict_lstm_sequence(steps, "historical_dataset")
    result["history_window"] = {"start_date": chosen[0]["observed_date"], "start_time_block": chosen[0]["time_block"],
      "end_date": chosen[-1]["observed_date"], "end_time_block": chosen[-1]["time_block"], "records": 6}
    return result


@app.get("/api/predictions")
def prediction_history(limit: int = Query(default=100, ge=1, le=500)):
    result = store.rows("SELECT * FROM model_predictions ORDER BY issue_time DESC LIMIT ?", (limit,))
    for item in result:
        item["probabilities"] = json.loads(item.pop("probabilities_json"))
        item["inputs"] = json.loads(item.pop("input_json"))
    return {"predictions": result}


@app.get("/api/evaluation")
def evaluation():
    counts = store.one("""SELECT COUNT(*) prediction_count,
      SUM(CASE WHEN observed_outcome IS NOT NULL THEN 1 ELSE 0 END) evaluated_count
      FROM model_predictions""")
    return {**counts, "message": "Forecast outcomes are linked only when a later observation arrives for the same target date and time block."}
