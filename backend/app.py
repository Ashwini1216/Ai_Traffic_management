"""FlowState API: wires the feature modules together and serves accounts and the website.

Feature modules (each with its own router):
  segments      road segment, zone, and data-coverage registry
  observations  traffic observation ingestion, validation, quarantine, and review
  weather       weather collection and alignment
  forecasting   congestion forecasts (hybrid model, patterns, transfer, cold start) and models
  incidents     citizen reports, official notices, verification, and public alerts
  demand        planned trips, vehicle-weighted demand, corridors, and hotspots
  routing       route recommendations and departure-time advice
  notifications trip reassessment, notifications, and journey monitoring
  cameras       camera registry, clip review, and stream collection
  alerts        authority alerts with deployment recommendations
  scheduler     background jobs
  access        authority access requests
"""
from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import access
import alerts
import cameras
import core
import demand
import forecasting
import incidents
import notifications
import observations
import routing
import scheduler
import segments
import store
import weather
from core import DATA_DIR, ROOT, iso_now
from security import PASSWORDS, _decode_bearer, authority_token, create_access_token, signed_in_user

SYNTHETIC_MANIFEST_FILE = DATA_DIR / "synthetic_traffic_dataset_manifest.json"
# FLOWSTATE_FRONTEND_DIST lets the browser tests serve their own build without touching frontend/dist.
FRONTEND_DIST = Path(os.getenv("FLOWSTATE_FRONTEND_DIST") or ROOT / "frontend" / "dist")


def startup() -> None:
    store.initialize()
    segments.sync_segments()
    username, password = os.getenv("AUTHORITY_USERNAME"), os.getenv("AUTHORITY_PASSWORD")
    email = (os.getenv("AUTHORITY_EMAIL") or "").strip().casefold() or None
    if email and not EMAIL_PATTERN.match(email):
        raise RuntimeError("AUTHORITY_EMAIL in .env is not a valid email address.")
    if not (username and password) or password.startswith("CHANGE_ME"):
        return
    username = username.casefold()
    existing = store.one("SELECT username,email FROM user_accounts WHERE username=?", (username,))
    taken = bool(email and store.one("SELECT username FROM user_accounts WHERE email=? AND username<>?", (email, username)))
    with store.connect() as db:
        if not existing:
            db.execute("INSERT INTO user_accounts(username,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                       (username, None if taken else email, PASSWORDS.hash(password), "authority", iso_now()))
        elif email and not existing["email"] and not taken:
            # An AUTHORITY_EMAIL added to .env later lets the existing account sign in with it too.
            db.execute("UPDATE user_accounts SET email=? WHERE username=?", (email, username))


@asynccontextmanager
async def lifespan(_: FastAPI):
    startup()
    scheduler.SCHEDULER.start()
    yield
    scheduler.SCHEDULER.stop()


app = FastAPI(
    title="Bengaluru Collaborative Traffic Monitor",
    description="Data-quality-aware traffic monitoring and congestion prediction. Historical records, model estimates, "
                "and live observations are kept clearly separate.",
    version="0.3.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Content-Type", "Authorization"],
)
for module in (segments, weather, observations, forecasting, incidents, demand, routing, notifications, cameras, alerts,
               scheduler, access):
    app.include_router(module.router)
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


@app.get("/", include_in_schema=False)
@app.get("/user", include_in_schema=False)
@app.get("/authority", include_in_schema=False)
def dashboard():
    # The React app routes /user and /authority in the browser; serve it for a reload there too.
    built = FRONTEND_DIST / "index.html"
    if built.is_file():
        return FileResponse(built)
    raise HTTPException(503, "React frontend is not built yet. Start the Vite dev server in frontend/ or run npm run build.")


# ---------- accounts ----------

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)


class LoginInput(BaseModel):
    """People sign in with their email; the authority account created from .env may also use its username."""
    email: str | None = Field(default=None, max_length=254)
    username: str | None = Field(default=None, max_length=40)
    password: str = Field(min_length=1, max_length=128)


def _username_from_email(email: str) -> str:
    """Accounts are keyed by an internal username; new accounts derive one from their email."""
    base = re.sub(r"[^a-z0-9_]", "_", email.split("@")[0])[:30].strip("_") or "user"
    base = base if len(base) >= 3 else f"{base}_user"
    candidate, number = base, 1
    while store.one("SELECT username FROM user_accounts WHERE username=?", (candidate,)):
        number += 1
        candidate = f"{base}_{number}"
    return candidate


def _signed_in(account: dict) -> dict:
    return {"access_token": create_access_token(account["username"], account["role"]), "token_type": "bearer", "expires_in": 43200,
            "user": {"username": account["username"], "email": account["email"], "role": account["role"]}}


@app.post("/api/auth/register", status_code=201)
def register_account(payload: RegisterInput):
    email = payload.email.strip().casefold()
    if not EMAIL_PATTERN.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    if store.one("SELECT username FROM user_accounts WHERE email=?", (email,)):
        raise HTTPException(409, "That email is already registered. Sign in instead.")
    account = {"username": _username_from_email(email), "email": email, "role": "citizen"}
    with store.connect() as db:
        db.execute("INSERT INTO user_accounts(username,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                   (account["username"], email, PASSWORDS.hash(payload.password), "citizen", iso_now()))
    return _signed_in(account)


@app.post("/api/auth/login")
def login_account(payload: LoginInput):
    identifier = (payload.email or payload.username or "").strip().casefold()
    if not identifier:
        raise HTTPException(422, "Enter your email address.")
    column = "email" if "@" in identifier else "username"
    account = store.one(f"SELECT username,email,password_hash,role FROM user_accounts WHERE {column}=?", (identifier,))
    if not account or not PASSWORDS.verify(payload.password, account["password_hash"]):
        raise HTTPException(401, "Email or password is incorrect.")
    return _signed_in(account)


@app.get("/api/auth/me")
def current_account(authorization: str | None = Header(default=None)):
    claims = _decode_bearer(authorization)
    account = store.one("SELECT email FROM user_accounts WHERE username=?", (claims["sub"],))
    return {"username": claims["sub"], "email": account["email"] if account else None, "role": claims["role"]}


# ---------- maps ----------

@app.get("/api/map/config")
def map_config():
    return {"center": [12.9716, 77.5946], "zoom": 11, "map_provider": "Google Maps",
            "google_routing_configured": core.google_configured()}


@app.get("/api/geocode")
def geocode(q: str = Query(min_length=3, max_length=120), actor: str = Depends(signed_in_user)):
    if not core.google_configured():
        raise HTTPException(503, "Google Maps services are not configured. Add GOOGLE_MAPS_API_KEY to the project .env file.")
    results = [{
        "display_name": item.get("formatted_address"),
        "latitude": float(item["geometry"]["location"]["lat"]),
        "longitude": float(item["geometry"]["location"]["lng"]),
        "place_type": (item.get("types") or [None])[0],
        "source_type": "google_geocoding",
    } for item in core.google_geocode(q + ", Bengaluru, India")[:5]]
    return {"query": q, "results": results, "source_type": "google_geocoding"}


# ---------- status and data summaries ----------

@app.get("/api/health")
def health():
    result = store.one("SELECT COUNT(*) AS records FROM traffic_observations")
    return {"status": "ok", "service": "traffic-monitor", "database": "connected", "observation_records": result["records"]}


@app.get("/api/summary")
def summary():
    totals = store.one("""SELECT COUNT(*) AS record_count, COUNT(DISTINCT road) AS road_count,
        COUNT(DISTINCT area) AS area_count, MIN(observed_date) AS date_start, MAX(observed_date) AS date_end
        FROM traffic_observations WHERE source_type='historical_dataset'""")
    counts = store.rows("""SELECT COALESCE(congestion_level,'Unknown') AS label, COUNT(*) AS count
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


@app.get("/api/coverage")
def coverage():
    """Roads that have traffic observations, with their coverage registry statistics."""
    result = [item for item in segments.coverage_list() if item["has_traffic_data"]]
    for item in result:
        item["unverified_count"] = item["record_count"] - item["verified_count"]
        item["verified_coverage"] = item["verified_count"] > 0
        item["coverage_note"] = "Historical records exist; none are independently verified." if item["verified_count"] == 0 else "At least one verified record exists."
    return {"segments": result, "segment_count": len(result), "map_coverage_is_not_traffic_coverage": True}


@app.get("/api/authority/overview")
def authority_overview(actor: str = Depends(authority_token)):
    return {"actor": actor, "health": health(), "coverage": coverage(), "incidents": incidents.list_incidents(limit=200),
            "cameras": cameras.cameras(), "footage_count": store.one("SELECT COUNT(*) AS count FROM camera_footage")["count"],
            "broadcasts": store.rows("SELECT * FROM incident_broadcasts WHERE status='active' AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC", (iso_now(),)),
            "alerts": alerts.list_alerts(status="active", limit=50)["alerts"]}


@app.get("/api/predictions")
def prediction_history(limit: int = Query(default=100, ge=1, le=500), actor: str = Depends(authority_token)):
    result = store.rows("SELECT * FROM model_predictions ORDER BY issue_time DESC LIMIT ?", (limit,))
    for item in result:
        item["probabilities"] = json.loads(item.pop("probabilities_json"))
        item["inputs"] = json.loads(item.pop("input_json"))
        for field in ("predicted_values_json", "observed_values_json", "errors_json"):
            value = item.pop(field, None)
            item[field.removesuffix("_json")] = json.loads(value) if value else None
    return {"predictions": result}

