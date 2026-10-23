"""Crowd-sourced incident reports, official notices, verification, and public alerts.

Citizen reports start unverified; a nearby report of the same kind within a short window is
merged as a corroboration instead of a new item, and reports left unreviewed for too long
become stale. Authorities verify reports and publish official notices (VIP movements, road
closures, metro work, diversions, events, restrictions) with a validity window. Only verified,
currently valid items affect forecasts and route scores.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import core
import store
from core import iso_now, utc_iso
from security import authority_token, citizen_token

router = APIRouter()

CITIZEN_CATEGORIES = {"accident", "breakdown", "road_blockage", "heavy_traffic", "construction",
                      "waterlogging", "signal_failure", "diversion", "other"}
OFFICIAL_CATEGORIES = CITIZEN_CATEGORIES | {"vip_movement", "road_closure", "metro_construction", "public_event", "restriction"}
# Applied when an item is verified or published without an expiry, so stale information
# cannot affect route scores indefinitely. Authorities can set any other window.
DEFAULT_VALIDITY_HOURS = {
    "accident": 3, "breakdown": 2, "heavy_traffic": 2, "signal_failure": 6, "road_blockage": 6, "waterlogging": 6,
    "diversion": 24, "construction": 24 * 7, "other": 3, "vip_movement": 3, "road_closure": 12,
    "metro_construction": 24 * 30, "public_event": 6, "restriction": 24,
}
DEFAULT_SEVERITY = {"road_closure": "critical", "accident": "warning", "road_blockage": "warning", "vip_movement": "warning",
                    "waterlogging": "warning", "diversion": "warning", "metro_construction": "warning", "public_event": "warning"}
DUPLICATE_RADIUS_M = 150
DUPLICATE_WINDOW = timedelta(hours=2)
REPORT_STALE_AFTER = timedelta(hours=6)
ACTIVE_FILTER = "status='verified' AND (expires_at IS NULL OR expires_at>?) AND (starts_at IS NULL OR starts_at<=?)"


def _audit(db, action: str, entity_id: str, actor: str, details: str = "{}") -> None:
    db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
               (action, "incident", entity_id, actor, iso_now(), details))


def default_expiry(category: str, start: datetime | None = None) -> str:
    return utc_iso((start or datetime.now(timezone.utc)) + timedelta(hours=DEFAULT_VALIDITY_HOURS.get(category, 3)))


def active(at: datetime | None = None, located_only: bool = True) -> list[dict]:
    """Verified incidents and notices in force at a moment."""
    stamp = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    located = " AND latitude IS NOT NULL AND longitude IS NOT NULL" if located_only else ""
    return store.rows(f"""SELECT incident_id,category,description,area,road,latitude,longitude,official,closure,severity,
      starts_at,expires_at,official_note,corroboration_count,report_source FROM incidents WHERE {ACTIVE_FILTER}{located}""", (stamp, stamp))


def expire_stale_reports() -> int:
    """Unreviewed reports older than REPORT_STALE_AFTER no longer wait in the review queue."""
    cutoff = (datetime.now(timezone.utc) - REPORT_STALE_AFTER).isoformat()
    with store.connect() as db:
        cur = db.execute("UPDATE incidents SET status='stale',updated_at=? WHERE status='reported' AND created_at<?", (iso_now(), cutoff))
        return cur.rowcount or 0


def _check_location(latitude: float | None, longitude: float | None, required: bool = False) -> None:
    if (latitude is None) != (longitude is None):
        raise HTTPException(422, "Provide both latitude and longitude, or neither.")
    if latitude is None and required:
        raise HTTPException(422, "A map location (latitude and longitude) is required for official notices.")
    if latitude is not None and not core.in_bengaluru(latitude, longitude):
        raise HTTPException(422, "The location is outside the Bengaluru area.")


class IncidentInput(BaseModel):
    category: str
    description: str = Field(min_length=5, max_length=1000)
    area: str | None = Field(default=None, max_length=100)
    road: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    expires_at: datetime | None = None


class OfficialNoticeInput(BaseModel):
    category: str
    description: str = Field(min_length=5, max_length=1000)
    area: str | None = Field(default=None, max_length=100)
    road: str | None = Field(default=None, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    severity: Literal["info", "warning", "critical"] | None = None
    closure: bool | None = None
    broadcast_message: str | None = Field(default=None, min_length=10, max_length=500)


class IncidentUpdate(BaseModel):
    status: Literal["verified", "rejected", "resolved"]
    official_note: str = Field(default="", max_length=500)
    expires_at: datetime | None = None
    starts_at: datetime | None = None
    closure: bool | None = None
    severity: Literal["info", "warning", "critical"] | None = None


class BroadcastInput(BaseModel):
    message: str = Field(min_length=10, max_length=500)
    severity: Literal["info", "warning", "critical"] = "warning"
    expires_at: datetime | None = None


def _require_timezones(*values: datetime | None) -> None:
    if any(value is not None and value.tzinfo is None for value in values):
        raise HTTPException(422, "Times must include a timezone.")


@router.post("/api/incidents", status_code=201)
def report_incident(payload: IncidentInput, actor: str = Depends(citizen_token)):
    if payload.category not in CITIZEN_CATEGORIES:
        raise HTTPException(422, "Unsupported incident category.")
    _check_location(payload.latitude, payload.longitude)
    _require_timezones(payload.expires_at)
    incident_id, now = str(uuid.uuid4()), iso_now()
    original = None
    if payload.latitude is not None:
        since = (datetime.now(timezone.utc) - DUPLICATE_WINDOW).isoformat()
        for candidate in store.rows("""SELECT incident_id,latitude,longitude FROM incidents WHERE category=? AND status IN ('reported','verified')
          AND latitude IS NOT NULL AND created_at>=? ORDER BY created_at""", (payload.category, since)):
            if core.haversine_m(payload.latitude, payload.longitude, candidate["latitude"], candidate["longitude"]) <= DUPLICATE_RADIUS_M:
                original = candidate["incident_id"]
                break
    with store.connect() as db:
        db.execute("""INSERT INTO incidents(incident_id,category,description,area,road,latitude,longitude,status,report_source,
          created_at,expires_at,official,closure,severity,created_by,corroboration_count,duplicate_of,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (incident_id, payload.category, payload.description, payload.area, payload.road, payload.latitude, payload.longitude,
                    "duplicate" if original else "reported", "citizen_report", now, utc_iso(payload.expires_at), 0, 0,
                    DEFAULT_SEVERITY.get(payload.category, "info"), actor, 0, original, now))
        if original:
            db.execute("UPDATE incidents SET corroboration_count=COALESCE(corroboration_count,0)+1,updated_at=? WHERE incident_id=?",
                       (now, original))
    if original:
        count = store.one("SELECT corroboration_count FROM incidents WHERE incident_id=?", (original,))["corroboration_count"]
        return {"incident_id": incident_id, "status": "duplicate", "duplicate_of": original, "corroborations": count,
                "message": "A matching report already exists nearby; yours was added as a confirmation."}
    return {"incident_id": incident_id, "status": "reported", "verification_required": True}


@router.post("/api/incidents/official", status_code=201)
def publish_notice(payload: OfficialNoticeInput, actor: str = Depends(authority_token)):
    if payload.category not in OFFICIAL_CATEGORIES:
        raise HTTPException(422, "Unsupported notice category.")
    _check_location(payload.latitude, payload.longitude, required=True)
    _require_timezones(payload.starts_at, payload.expires_at)
    start = payload.starts_at or datetime.now(timezone.utc)
    expires = utc_iso(payload.expires_at) or default_expiry(payload.category, start)
    if expires <= utc_iso(start):
        raise HTTPException(422, "The notice must end after it starts.")
    closure = payload.closure if payload.closure is not None else payload.category == "road_closure"
    severity = payload.severity or DEFAULT_SEVERITY.get(payload.category, "info")
    incident_id, now = str(uuid.uuid4()), iso_now()
    with store.connect() as db:
        db.execute("""INSERT INTO incidents(incident_id,category,description,area,road,latitude,longitude,status,report_source,
          created_at,expires_at,starts_at,official,closure,severity,created_by,reviewed_by,reviewed_at,corroboration_count,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (incident_id, payload.category, payload.description, payload.area, payload.road, payload.latitude, payload.longitude,
                    "verified", "authority_notice", now, expires, utc_iso(start), 1, int(closure), severity, actor, actor, now, 0, now))
        _audit(db, "official_notice_published", incident_id, actor, payload.model_dump_json())
    broadcast = None
    if payload.broadcast_message:
        broadcast = broadcast_incident(incident_id, BroadcastInput(message=payload.broadcast_message, severity=severity), actor)
    return {"incident_id": incident_id, "status": "verified", "official": True, "closure": closure, "severity": severity,
            "starts_at": utc_iso(start), "expires_at": expires, "broadcast": broadcast}


@router.get("/api/incidents")
def list_incidents(status: str | None = None, official: bool | None = None, limit: int = Query(default=100, ge=1, le=500),
                   actor: str = Depends(authority_token)):
    expire_stale_reports()
    clauses, params = [], []
    if status == "open":
        clauses.append("status IN ('reported','verified')")
    elif status:
        clauses.append("status=?")
        params.append(status)
    if official is not None:
        clauses.append("COALESCE(official,0)=?")
        params.append(int(official))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    result = store.rows(f"""SELECT incident_id,category,description,area,road,latitude,longitude,status,report_source,created_at,
      expires_at,starts_at,official,closure,severity,official_note,reviewed_by,reviewed_at,corroboration_count,duplicate_of,created_by
      FROM incidents{where} ORDER BY created_at DESC LIMIT ?""", (*params, limit))
    return {"incidents": result}


@router.get("/api/incidents/public")
def public_incidents():
    """Verified incidents and official notices currently in force, for the commuter map.
    Citizen descriptions are not published; the authority's note is shown instead."""
    items = []
    for row in active():
        items.append({"incident_id": row["incident_id"], "category": row["category"], "area": row["area"], "road": row["road"],
                      "latitude": row["latitude"], "longitude": row["longitude"], "official": bool(row["official"]),
                      "closure": bool(row["closure"]), "severity": row["severity"], "starts_at": row["starts_at"],
                      "expires_at": row["expires_at"], "corroborations": row["corroboration_count"] or 0,
                      "details": row["description"] if row["official"] else row["official_note"]})
    return {"incidents": items, "notice": "Only verified items currently in force are shown."}


@router.patch("/api/incidents/{incident_id}")
def update_incident(incident_id: str, payload: IncidentUpdate, actor: str = Depends(authority_token)):
    incident = store.one("SELECT incident_id,category,expires_at,starts_at FROM incidents WHERE incident_id=?", (incident_id,))
    if not incident:
        raise HTTPException(404, "Incident not found.")
    _require_timezones(payload.expires_at, payload.starts_at)
    now = iso_now()
    expires = utc_iso(payload.expires_at)
    if payload.status == "verified" and not expires and (not incident["expires_at"] or incident["expires_at"] <= now):
        expires = default_expiry(incident["category"])
    with store.connect() as db:
        db.execute("""UPDATE incidents SET status=?,expires_at=COALESCE(?,expires_at),starts_at=COALESCE(?,starts_at),
          closure=COALESCE(?,closure),severity=COALESCE(?,severity),official_note=COALESCE(NULLIF(?,''),official_note),
          reviewed_by=?,reviewed_at=?,updated_at=? WHERE incident_id=?""",
                   (payload.status, expires, utc_iso(payload.starts_at), None if payload.closure is None else int(payload.closure),
                    payload.severity, payload.official_note.strip(), actor, now, now, incident_id))
        if payload.status in ("resolved", "rejected"):
            # Public alerts must not outlive the incident they describe.
            db.execute("UPDATE incident_broadcasts SET status='ended' WHERE incident_id=? AND status='active'", (incident_id,))
        _audit(db, "incident_status_changed", incident_id, actor, payload.model_dump_json())
    return {"incident_id": incident_id, "status": payload.status, "official_note": payload.official_note,
            "expires_at": expires or incident["expires_at"]}


@router.post("/api/incidents/{incident_id}/broadcast", status_code=201)
def broadcast_incident(incident_id: str, payload: BroadcastInput, actor: str = Depends(authority_token)):
    incident = store.one("SELECT * FROM incidents WHERE incident_id=?", (incident_id,))
    if not incident:
        raise HTTPException(404, "Incident not found.")
    if incident["status"] != "verified":
        raise HTTPException(409, "Verify the incident before broadcasting it.")
    _require_timezones(payload.expires_at)
    broadcast_id = str(uuid.uuid4())
    # Without its own expiry, an alert lasts as long as the verified incident is valid.
    expires = utc_iso(payload.expires_at) or incident["expires_at"]
    with store.connect() as db:
        db.execute("""INSERT INTO incident_broadcasts(broadcast_id,incident_id,message,area,road,severity,status,
          created_by,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (broadcast_id, incident_id, payload.message, incident["area"], incident["road"], payload.severity, "active",
                    actor, iso_now(), expires))
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
                   ("incident_broadcast_created", "incident_broadcast", broadcast_id, actor, iso_now(), payload.model_dump_json()))
    return {"broadcast_id": broadcast_id, "incident_id": incident_id, "status": "active", "severity": payload.severity, "expires_at": expires}


@router.get("/api/broadcasts")
def public_broadcasts():
    return {"broadcasts": store.rows("""SELECT broadcast_id,incident_id,message,area,road,severity,created_at,expires_at
      FROM incident_broadcasts WHERE status='active' AND (expires_at IS NULL OR expires_at>?)
      ORDER BY created_at DESC LIMIT 100""", (iso_now(),))}
