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
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
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
import places
import routing
import scheduler
import segments
import store
import weather
from core import DATA_DIR, ROOT, iso_now
from security import (AUTHORITY_PASSWORD_MIN, PASSWORDS, _decode_bearer, authority_token, check_sign_in_allowed,
                      create_access_token, record_sign_in, sign_in_limits, signed_in_user, verify_password)

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
    if not existing and len(password) < AUTHORITY_PASSWORD_MIN:
        raise RuntimeError(f"AUTHORITY_PASSWORD in .env must be at least {AUTHORITY_PASSWORD_MIN} characters long.")
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
    # The portal being signed in to: the authority sign-in never gives a token to a commuter account.
    portal: Literal["citizen", "authority"] | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


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
    token = create_access_token(account["username"], account["role"], account.get("token_version") or 0)
    return {"access_token": token, "token_type": "bearer", "expires_in": 43200,
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


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.post("/api/auth/login")
def login_account(payload: LoginInput, request: Request):
    identifier = (payload.email or payload.username or "").strip().casefold()
    if not identifier:
        raise HTTPException(422, "Enter your email address.")
    column = "email" if "@" in identifier else "username"
    account = store.one(f"SELECT username,email,password_hash,role,token_version FROM user_accounts WHERE {column}=?", (identifier,))
    # Wrong passwords count against the account, whether its email or its username was typed.
    key, client = (account["username"] if account else identifier), _client_address(request)
    try:
        check_sign_in_allowed(key, client)
    except HTTPException:
        record_sign_in(key, client, "paused")
        raise
    if not verify_password(payload.password, account):
        record_sign_in(key, client, "wrong_password")
        raise HTTPException(401, "Email or password is incorrect.")
    if payload.portal and payload.portal != account["role"]:
        record_sign_in(key, client, "wrong_portal")
        raise HTTPException(403, "This account is not an authority account." if payload.portal == "authority"
                            else "This is an authority account. Use the authority sign-in.")
    record_sign_in(key, client, "signed_in")
    return _signed_in(account)


@app.post("/api/auth/password")
def change_password(payload: PasswordChange, request: Request, authorization: str | None = Header(default=None)):
    """Change the signed-in account's password. Its other sessions are signed out; this one gets a new token."""
    claims = _decode_bearer(authorization)
    account = store.one("SELECT username,email,password_hash,role FROM user_accounts WHERE username=?", (claims["sub"],))
    client = _client_address(request)
    check_sign_in_allowed(account["username"], client)
    if not verify_password(payload.current_password, account):
        record_sign_in(account["username"], client, "wrong_password")
        raise HTTPException(403, "The current password is incorrect.")
    if account["role"] == "authority" and len(payload.new_password) < AUTHORITY_PASSWORD_MIN:
        raise HTTPException(422, f"Authority passwords need at least {AUTHORITY_PASSWORD_MIN} characters.")
    if payload.new_password == payload.current_password:
        raise HTTPException(422, "Choose a new password that is different from the current one.")
    with store.connect() as db:
        db.execute("""UPDATE user_accounts SET password_hash=?,password_changed_at=?,token_version=COALESCE(token_version,0)+1
          WHERE username=?""", (PASSWORDS.hash(payload.new_password), iso_now(), account["username"]))
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
                   ("password_changed", "user_account", account["username"], account["username"], iso_now(), "{}"))
    return _signed_in(store.one("SELECT username,email,role,token_version FROM user_accounts WHERE username=?", (account["username"],)))


@app.get("/api/auth/sign-ins")
def authority_sign_ins(limit: int = Query(default=50, ge=1, le=200), actor: str = Depends(authority_token)):
    """Recent sign-in attempts on authority accounts, and the limits that pause sign-in."""
    failures, window = sign_in_limits()
    rows = store.rows("""SELECT a.account_key AS username,a.client,a.outcome,a.attempted_at FROM login_attempts a
      JOIN user_accounts u ON u.username=a.account_key WHERE u.role='authority' ORDER BY a.attempted_at DESC LIMIT ?""", (limit,))
    return {"sign_ins": rows, "max_failures": failures, "pause_minutes": int(window.total_seconds() // 60)}


@app.get("/api/auth/me")
def current_account(authorization: str | None = Header(default=None)):
    claims = _decode_bearer(authorization)
    account = store.one("SELECT email,password_changed_at FROM user_accounts WHERE username=?", (claims["sub"],))
    return {"username": claims["sub"], "email": account["email"], "role": claims["role"],
            "password_changed_at": account["password_changed_at"]}


# ---------- maps ----------

@app.get("/api/map/config")
def map_config():
    return {"center": [12.9716, 77.5946], "zoom": 11, "google_routing_configured": core.google_configured(),
            "google_maps_browser_key": core.google_browser_key(), "place_search": places.provider_setting()}


@app.get("/api/geocode")
def geocode(q: str = Query(min_length=3, max_length=120), actor: str = Depends(signed_in_user)):
    """Places in Bengaluru matching a typed name or address (searched only when submitted)."""
    return places.search(q)


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

