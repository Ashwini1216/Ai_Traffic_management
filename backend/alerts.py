"""Operational alerts for authorities: high-risk zones, closures, demand hotspots, data quality.

Each alert carries its evidence and a deployment recommendation. An unchanged situation
produces one alert (fingerprint); alerts lapse when the time window they describe passes.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import core
import demand
import forecasting
import incidents
import store
from core import iso_now
from security import authority_token

router = APIRouter()


def raise_alert(kind: str, level: str, title: str, message: str, recommendation: str | None, evidence: dict,
                fingerprint: str, zone_id: str | None = None, segment_id: str | None = None, valid_until: str | None = None) -> str | None:
    if store.one("SELECT alert_id FROM alerts WHERE fingerprint=?", (fingerprint,)):
        return None
    alert_id = str(uuid.uuid4())
    with store.connect() as db:
        db.execute("""INSERT INTO alerts(alert_id,kind,level,zone_id,segment_id,title,message,recommendation,evidence_json,
          fingerprint,status,created_at,valid_until) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (alert_id, kind, level, zone_id, segment_id, title, message, recommendation, json.dumps(evidence, default=str),
                    fingerprint, "open", iso_now(), valid_until))
    return alert_id


def expire_old_alerts() -> int:
    with store.connect() as db:
        cur = db.execute("UPDATE alerts SET status='expired' WHERE status IN ('open','acknowledged') AND valid_until IS NOT NULL AND valid_until<?",
                         (iso_now(),))
        return cur.rowcount or 0


def evaluate_zones(now: datetime | None = None) -> dict:
    """Zone risk for the next time block, active closures, and planned-demand hotspots."""
    now = now or datetime.now(timezone.utc)
    day, block = core.next_block(*core.block_for(now))
    start, end = core.block_bounds(day, block)
    created = 0
    for zone in forecasting.zone_outlook(start + timedelta(minutes=1)):
        if zone["level"] != "High":
            continue
        busy = [r for r in zone["roads"] if r["predicted_class"] == "High"]
        roads = ", ".join(r["road"] for r in busy[:3]) or "the zone's main roads"
        created += bool(raise_alert(
            "high_risk_zone", "high", f"Heavy congestion expected in {zone['name']}",
            f"{zone['name']} is expected to be heavily congested during {block} on {day.isoformat()} "
            f"(average expected load {zone['expected_load']:.2f}; methods: {', '.join(m.replace('_', ' ') for m in zone['methods'])}).",
            f"Deploy traffic personnel at {roads} before {start.astimezone(core.LOCAL_TZ).strftime('%H:%M')} IST, review signal "
            "timings, and prepare a diversion advisory.",
            zone, f"zone:{zone['zone_id']}|{day}|{block}|High", zone_id=zone["zone_id"], valid_until=end.isoformat()))
    zones = store.rows("SELECT zone_id,name,latitude,longitude,radius_m FROM zones WHERE latitude IS NOT NULL")
    for closure in [i for i in incidents.active(now) if i["closure"]]:
        zone = min(zones, key=lambda z: core.haversine_m(closure["latitude"], closure["longitude"], z["latitude"], z["longitude"]), default=None)
        created += bool(raise_alert(
            "closure_in_zone", "high", f"Road closure in {zone['name'] if zone else 'the city'}",
            f"{closure['category'].replace('_', ' ').capitalize()} near {closure['road'] or 'the marked location'} is in force until "
            f"{(core.parse_utc(closure['expires_at']) or now).astimezone(core.LOCAL_TZ).strftime('%d %b %H:%M')} IST.",
            "Station personnel at the closure points and publish a diversion broadcast if one is not already active.",
            closure, f"closure:{closure['incident_id']}", zone_id=zone["zone_id"] if zone else None, valid_until=closure["expires_at"]))
    for spot in demand.hotspots(24):
        if spot["level"] != "High":
            continue
        spot_end = core.block_bounds(date.fromisoformat(spot["date"]), spot["time_block"])[1]
        created += bool(raise_alert(
            "demand_hotspot", "medium", f"Planned-demand hotspot on {spot['road']}",
            f"{spot['planned_trips']} planned trips ({spot['pcu']} PCU) are expected on {spot['road']} during {spot['time_block']} "
            f"on {spot['date']}, where {spot['predicted_class']} congestion is forecast.",
            "Consider advance commuter advisories and staff the junction during this block.",
            spot, f"hotspot:{spot['segment_id']}|{spot['date']}|{spot['time_block']}", zone_id=spot.get("zone_id"),
            segment_id=spot["segment_id"], valid_until=spot_end.isoformat()))
    return {"alerts_created": created, "alerts_expired": expire_old_alerts(), "evaluated_block": f"{day} {block}"}


def data_quality(now: datetime | None = None) -> dict:
    """Stale or offline cameras, roads whose camera stopped reporting, and quarantine spikes."""
    now = now or datetime.now(timezone.utc)
    created = 0
    today = now.date().isoformat()
    for camera in store.rows("SELECT * FROM cameras WHERE stream_url IS NOT NULL AND authorization_status='approved'"):
        if camera["operational_status"] == "offline" and (camera["consecutive_failures"] or 0) >= 3:
            created += bool(raise_alert("camera_offline", "medium", f"Camera {camera['display_name']} is offline",
                                        f"The stream failed {camera['consecutive_failures']} times in a row: {camera['last_error'] or 'no details'}.",
                                        "Check the camera and its network, then set it back to online in the camera registry.",
                                        {"camera_id": camera["camera_id"]}, f"camera_offline:{camera['camera_id']}|{today}"))
            continue
        interval = camera["collection_interval_seconds"] or 300
        last = core.parse_utc(camera["last_success_at"])
        if camera["operational_status"] == "online" and last and (now - last).total_seconds() > 3 * interval:
            created += bool(raise_alert("camera_stale", "low", f"No recent data from {camera['display_name']}",
                                        f"The last successful measurement was at {last.astimezone(core.LOCAL_TZ).strftime('%d %b %H:%M')} IST.",
                                        "Check whether the stream is reachable; the road is uncovered meanwhile.",
                                        {"camera_id": camera["camera_id"]}, f"camera_stale:{camera['camera_id']}|{today}"))
    recent = store.one("SELECT COUNT(*) AS n FROM quarantined_observations WHERE ingested_at>?", ((now - timedelta(hours=24)).isoformat(),))["n"]
    if recent > 20:
        created += bool(raise_alert("quarantine_spike", "low", "Many observations were quarantined",
                                    f"{recent} incoming observations were quarantined in the last 24 hours.",
                                    "Review the quarantine list for a misconfigured source.", {"count": recent}, f"quarantine:{today}"))
    return {"alerts_created": created}


class AlertUpdate(BaseModel):
    status: Literal["acknowledged", "resolved"]


@router.get("/api/alerts")
def list_alerts(status: str | None = Query(default="active"), limit: int = Query(default=100, ge=1, le=500),
                actor: str = Depends(authority_token)):
    expire_old_alerts()
    if status == "active":
        rows = store.rows("SELECT * FROM alerts WHERE status IN ('open','acknowledged') ORDER BY created_at DESC LIMIT ?", (limit,))
    elif status:
        rows = store.rows("SELECT * FROM alerts WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
    else:
        rows = store.rows("SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,))
    for row in rows:
        row["evidence"] = json.loads(row.pop("evidence_json"))
    return {"alerts": rows}


@router.patch("/api/alerts/{alert_id}")
def update_alert(alert_id: str, payload: AlertUpdate, actor: str = Depends(authority_token)):
    with store.connect() as db:
        cur = db.execute("UPDATE alerts SET status=?,handled_by=?,handled_at=? WHERE alert_id=?", (payload.status, actor, iso_now(), alert_id))
    if not cur.rowcount:
        raise HTTPException(404, "Alert not found.")
    return {"alert_id": alert_id, "status": payload.status}
