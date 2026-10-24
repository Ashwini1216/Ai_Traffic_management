"""Trip reassessment, departure reminders, notifications, and active-journey monitoring.

Each scheduled trip gets one advance notice about TRIP_REMINDER_MINUTES before departure:
expected traffic and its reasons, the delay traffic adds, and a better route or departure
time when there is one. Editing the departure time brings a new notice.

Scheduled trips are reassessed as departure approaches (and whenever the scheduler runs).
A notification is sent only for a material change: risk up by at least MATERIAL_INCREASE
points, a move into a higher risk band, or a new closure on the route. Each notification
names the affected roads and incidents, how fresh the data is, and a suggestion. The same
situation is never notified twice (a fingerprint of band, closures, and incidents).

During a journey the app may send the device position (with the user's permission).
Disruptions ahead on the route are reported once each; inaccurate positions are ignored. A
closure or a serious hazard ahead (an accident, blockage, waterlogging or VIP movement), or
leaving the route, prompts a check for a better route from the current position; the journey
can then switch to the new route.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

import core
import demand
import forecasting
import routing
import store
from core import iso_now
from security import citizen_token, signed_in_user

router = APIRouter()

REASSESS_HOURS = float(os.getenv("TRIP_REASSESS_HOURS", "12"))
REMINDER_MINUTES = float(os.getenv("TRIP_REMINDER_MINUTES", "60"))
MATERIAL_INCREASE = 15.0
GPS_MAX_ACCURACY_M = 150
OFF_ROUTE_M = 500
ARRIVED_M = 150
AHEAD_MARGIN_M = 50
# Incidents at least this serious (see forecasting.INCIDENT_WEIGHTS) ahead on the route prompt a route check.
HAZARD_REROUTE_WEIGHT = 0.5
# Route checks call the paid routing service, so each journey may check at most every two minutes.
ROUTE_CHECK_MIN_INTERVAL_S = 120
BETTER_ROUTE_MIN_RISK = 10.0
BETTER_ROUTE_MIN_SAVING_S = 300
SAME_ROUTE_M = 120
_route_checks: dict[str, datetime] = {}


def band(risk: float) -> str:
    return "high" if risk >= 60 else "medium" if risk >= 30 else "low"


BAND_ORDER = {"low": 0, "medium": 1, "high": 2}


def notify(username: str, kind: str, title: str, message: str, evidence: dict, fingerprint: str, trip_id: str | None = None) -> str | None:
    """Store a notification unless the same one (same fingerprint) was already sent."""
    if store.one("SELECT notification_id FROM notifications WHERE username=? AND fingerprint=?", (username, fingerprint)):
        return None
    notification_id = str(uuid.uuid4())
    with store.connect() as db:
        db.execute("""INSERT INTO notifications(notification_id,username,trip_id,kind,title,message,evidence_json,fingerprint,created_at)
          VALUES(?,?,?,?,?,?,?,?,?)""", (notification_id, username, trip_id, kind, title, message,
                                         json.dumps(evidence, default=str), fingerprint, iso_now()))
    return notification_id


def _trip_route(trip: dict) -> dict:
    if trip.get("route_source") == "google_route" and trip.get("route_polyline"):
        coordinates = core.decode_google_polyline(trip["route_polyline"])
    else:
        coordinates = [[trip["origin_lon"], trip["origin_lat"]], [trip["destination_lon"], trip["destination_lat"]]]
    return {"source": trip.get("route_source") or "straight_line_estimate", "coordinates": coordinates,
            "distance_m": trip.get("route_distance_m") or 0.0, "duration_s": trip.get("route_duration_s") or 0.0}


def reassess_trip(trip: dict, now: datetime | None = None) -> dict:
    """Assess one scheduled trip and notify its owner of a material change."""
    now = now or datetime.now(timezone.utc)
    departure = core.parse_utc(trip["departure_at"])
    origin, destination = (trip["origin_lat"], trip["origin_lon"]), (trip["destination_lat"], trip["destination_lon"])
    route = _trip_route(trip)
    assessed = routing.assess_route(route, max(departure, now), origin, destination)
    risk = assessed["risk_score"]
    closures = sorted(i["incident_id"] for i in assessed["incidents"] if i["closure"])
    incident_ids = sorted(i["incident_id"] for i in assessed["incidents"])
    fingerprint = f"trip:{trip['trip_id']}|band:{band(risk)}|closures:{','.join(closures)}|incidents:{','.join(incident_ids)}"
    previous = json.loads(trip.get("last_assessment_json") or "null")
    previous_risk = trip.get("last_risk")
    reasons = []
    if previous_risk is None:
        if band(risk) == "high":
            reasons.append("first check found high risk")
        if closures:
            reasons.append("a closure is on your route")
    else:
        if risk - previous_risk >= MATERIAL_INCREASE:
            reasons.append(f"risk rose from {previous_risk:.0f} to {risk:.0f}")
        if BAND_ORDER[band(risk)] > BAND_ORDER[band(previous_risk)]:
            reasons.append(f"risk moved from {band(previous_risk)} to {band(risk)}")
        if set(closures) - set((previous or {}).get("closures", [])):
            reasons.append("a new closure is on your route")
    notification_id = None
    if reasons:
        roads = [f"{r['road']} ({r['predicted_class']})" for r in assessed["roads"] if r["predicted_class"] != "Low"][:4]
        incidents_text = [f"{i['category'].replace('_', ' ')} near {i['road'] or 'the route'}" for i in assessed["incidents"]][:3]
        advice = routing.departure_advice(route, max(departure, now), origin, destination, assessed, False)
        suggestion = advice.get("message") if advice.get("recommendation") == "shift" else (
            "A closure is on this route: compare alternative routes before you leave." if closures else
            "Compare routes before you leave; the current plan may be slower than usual.")
        freshest = max((r["passes_at"] for r in assessed["roads"]), default=None)
        evidence = {"reasons": reasons, "risk_score": risk, "previous_risk": previous_risk, "components": assessed["components"],
                    "roads": assessed["roads"], "incidents": assessed["incidents"], "data_gaps": assessed["data_gaps"],
                    "weather": assessed["weather_context"], "assessed_at": now.isoformat(), "latest_forecast_window": freshest,
                    "departure_advice": advice}
        message = (f"Your trip from {trip['origin']} to {trip['destination']}: " + "; ".join(reasons) + ". "
                   + (f"Watch: {', '.join(incidents_text + roads)}. " if incidents_text or roads else "")
                   + suggestion + f" (assessed {now.astimezone(core.LOCAL_TZ).strftime('%H:%M')} IST)")
        notification_id = notify(trip["username"], "closure_on_route" if closures else "risk_increase",
                                 "Your planned trip needs attention", message, evidence, fingerprint, trip["trip_id"])
    summary = {"risk_score": risk, "band": band(risk), "closures": closures, "components": assessed["components"],
               "roads": [{k: r[k] for k in ("road", "predicted_class", "method", "passes_at")} for r in assessed["roads"]],
               "data_gaps": assessed["data_gaps"], "fingerprint": fingerprint}
    with store.connect() as db:
        db.execute("UPDATE planned_trips SET last_risk=?,last_assessment_json=?,last_assessed_at=? WHERE trip_id=?",
                   (risk, json.dumps(summary, default=str), now.isoformat(), trip["trip_id"]))
    return {**summary, "notification_id": notification_id, "reasons": reasons}


def reassess_due_trips(now: datetime | None = None) -> dict:
    """Reassess every scheduled trip that departs within the next REASSESS_HOURS."""
    now = now or datetime.now(timezone.utc)
    trips = store.rows("""SELECT * FROM planned_trips WHERE state='scheduled' AND username IS NOT NULL AND origin_lat IS NOT NULL
      AND departure_at>? AND departure_at<=?""", (now.isoformat(), (now + timedelta(hours=REASSESS_HOURS)).isoformat()))
    sent = 0
    for trip in trips:
        try:
            sent += reassess_trip(trip, now)["notification_id"] is not None
        except HTTPException:
            continue
    return {"trips_checked": len(trips), "notifications_sent": sent}


def remind_trip(trip: dict, now: datetime | None = None) -> str | None:
    """The advance notice for one trip. It asks the routing service once, for the trip's own departure time."""
    now = now or datetime.now(timezone.utc)
    departure = max(core.parse_utc(trip["departure_at"]), now)
    origin, destination = (trip["origin_lat"], trip["origin_lon"]), (trip["destination_lat"], trip["destination_lon"])
    result = routing.recommend(origin, destination, departure, advice=True)
    best = result["routes"][0]
    planned = _trip_route(trip)["coordinates"]
    same_route = len(planned) <= 2 or _follows(best["geometry"]["coordinates"], planned) >= 0.8
    parts = [f"Expected traffic: {best['risk_level']} risk ({best['risk_score']:.0f}/100)"
             + (f": {'; '.join(best['reasons'][:3])}." if best["reasons"] else ".")]
    parts += [note["message"] for note in result["travel_notes"] if note["kind"] in ("leave_early", "safer_route")]
    if not same_route and not any(note["kind"] == "safer_route" for note in result["travel_notes"]):
        parts.append("A different route from the one you planned is recommended now; open Start a journey to compare routes.")
    advice = best.get("departure_advice") or {}
    if advice.get("recommendation") == "shift" and best["risk_level"] != "low":  # not worth moving a low-risk trip
        parts.append(advice["message"])
    message = f"Your trip from {trip['origin']} to {trip['destination']} leaves at {routing._clock(departure)}. " + " ".join(parts)
    evidence = {"risk_score": best["risk_score"], "risk_level": best["risk_level"], "reasons": best["reasons"],
                "traffic_delay_s": best.get("traffic_delay_s"), "travel_notes": result["travel_notes"],
                "recommended_route_differs": not same_route, "departure_advice": advice, "route_id": result["route_id"]}
    return notify(trip["username"], "departure_reminder", "Before you leave: traffic on your trip", message, evidence,
                  _reminder_fingerprint(trip), trip["trip_id"])


def _reminder_fingerprint(trip: dict) -> str:
    return f"trip:{trip['trip_id']}|reminder|{trip['departure_at']}"


def send_departure_reminders(now: datetime | None = None) -> dict:
    """Send the advance notice for every scheduled trip that leaves within REMINDER_MINUTES and has none yet."""
    now = now or datetime.now(timezone.utc)
    trips = store.rows("""SELECT * FROM planned_trips WHERE state='scheduled' AND username IS NOT NULL AND origin_lat IS NOT NULL
      AND departure_at>? AND departure_at<=?""", (now.isoformat(), (now + timedelta(minutes=REMINDER_MINUTES)).isoformat()))
    sent = 0
    for trip in trips:
        # Checked before routing, because each notice asks the (paid) routing service once.
        if store.one("SELECT 1 AS sent FROM notifications WHERE username=? AND fingerprint=?", (trip["username"], _reminder_fingerprint(trip))):
            continue
        try:
            sent += remind_trip(trip, now) is not None
        except HTTPException:
            continue
    return {"trips_due": len(trips), "reminders_sent": sent}


# ---------- API: trips and notifications ----------

@router.post("/api/trips/{trip_id}/reassess")
def reassess_endpoint(trip_id: str, authorization: str | None = Header(default=None)):
    trip = demand._owned_trip(trip_id, authorization)
    if trip["state"] != "scheduled" or trip["origin_lat"] is None:
        raise HTTPException(404, "Scheduled trip with a known route not found for this account.")
    return reassess_trip(trip)


@router.get("/api/notifications")
def my_notifications(actor: str = Depends(signed_in_user)):
    rows = store.rows("""SELECT notification_id,trip_id,kind,title,message,evidence_json,created_at,read_at FROM notifications
      WHERE username=? ORDER BY created_at DESC LIMIT 100""", (actor,))
    for row in rows:
        row["evidence"] = json.loads(row.pop("evidence_json"))
    return {"notifications": rows, "unread": sum(r["read_at"] is None for r in rows)}


@router.post("/api/notifications/{notification_id}/read")
def mark_read(notification_id: str, actor: str = Depends(signed_in_user)):
    with store.connect() as db:
        cur = db.execute("UPDATE notifications SET read_at=? WHERE notification_id=? AND username=? AND read_at IS NULL",
                         (iso_now(), notification_id, actor))
    if not cur.rowcount and not store.one("SELECT 1 AS ok FROM notifications WHERE notification_id=? AND username=?", (notification_id, actor)):
        raise HTTPException(404, "Notification not found.")
    return {"notification_id": notification_id, "read": True}


@router.post("/api/notifications/read-all")
def mark_all_read(actor: str = Depends(signed_in_user)):
    with store.connect() as db:
        cur = db.execute("UPDATE notifications SET read_at=? WHERE username=? AND read_at IS NULL", (iso_now(), actor))
    return {"marked": cur.rowcount or 0}


# ---------- API: active journeys ----------

class JourneyInput(BaseModel):
    coordinates: list[list[float]] = Field(min_length=2, max_length=20000)  # [lon, lat] route points
    destination_lat: float = Field(ge=-90, le=90)
    destination_lon: float = Field(ge=-180, le=180)
    trip_id: str | None = None


class PositionInput(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=100000)
    recorded_at: datetime | None = None


def _journey(journey_id: str, actor: str) -> dict:
    journey = store.one("SELECT * FROM active_journeys WHERE journey_id=? AND username=?", (journey_id, actor))
    if not journey:
        raise HTTPException(404, "Journey not found for this account.")
    return journey


@router.post("/api/journeys", status_code=201)
def start_journey(payload: JourneyInput, actor: str = Depends(citizen_token)):
    if not core.in_bengaluru(payload.destination_lat, payload.destination_lon):
        raise HTTPException(422, "The destination is outside the Bengaluru area.")
    journey_id = str(uuid.uuid4())
    with store.connect() as db:
        db.execute("""INSERT INTO active_journeys(journey_id,username,trip_id,route_json,destination_lat,destination_lon,status,
          started_at,alerted_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                   (journey_id, actor, payload.trip_id, json.dumps(payload.coordinates), payload.destination_lat,
                    payload.destination_lon, "active", iso_now(), "[]"))
    return {"journey_id": journey_id, "status": "active",
            "privacy_notice": "Positions are used only to check the road ahead and are not kept after the journey ends."}


@router.post("/api/journeys/{journey_id}/position")
def journey_position(journey_id: str, payload: PositionInput, actor: str = Depends(citizen_token)):
    journey = _journey(journey_id, actor)
    if journey["status"] != "active":
        raise HTTPException(409, "This journey has ended.")
    if payload.accuracy_m is not None and payload.accuracy_m > GPS_MAX_ACCURACY_M:
        return {"status": "low_accuracy", "alerts": [],
                "message": f"Location accuracy is {payload.accuracy_m:.0f} m, so this update was not used. Checks resume with a better fix."}
    if not core.in_bengaluru(payload.latitude, payload.longitude):
        raise HTTPException(422, "The position is outside the Bengaluru area.")
    coordinates = json.loads(journey["route_json"])
    length = core.polyline_length_m(coordinates) or 1.0
    off_route, along = core.locate_on_polyline(payload.latitude, payload.longitude, coordinates)
    to_destination = core.haversine_m(payload.latitude, payload.longitude, journey["destination_lat"], journey["destination_lon"])
    previous = json.loads(journey["last_position_json"] or "null")
    resumed = bool(previous) and (datetime.now(timezone.utc) - core.parse_utc(previous["received_at"])) > timedelta(minutes=10)
    position = {"latitude": payload.latitude, "longitude": payload.longitude, "accuracy_m": payload.accuracy_m, "received_at": iso_now()}
    if to_destination <= ARRIVED_M:
        with store.connect() as db:
            db.execute("UPDATE active_journeys SET status='arrived',ended_at=?,last_position_json=NULL WHERE journey_id=?", (iso_now(), journey_id))
        return {"status": "arrived", "alerts": [], "message": "You have arrived. Monitoring has stopped."}
    alerted = set(json.loads(journey["alerted_json"]))
    alerts = []
    now = datetime.now(timezone.utc)
    for incident in routing._incidents_on_route(coordinates, now, 3600, length):
        incident_along = core.locate_on_polyline(incident["latitude"], incident["longitude"], coordinates)[1]
        if incident_along <= along + AHEAD_MARGIN_M or incident["incident_id"] in alerted:
            continue
        alerted.add(incident["incident_id"])
        alerts.append({**incident, "distance_ahead_m": round(incident_along - along)})
    status = "off_route" if off_route > OFF_ROUTE_M else "on_route"
    suggestion = None
    serious = [a for a in alerts if a["closure"] or forecasting.INCIDENT_WEIGHTS.get(a["category"], 0.25) >= HAZARD_REROUTE_WEIGHT]
    if serious or status == "off_route":
        first = min(serious, key=lambda a: a["distance_ahead_m"]) if serious else None
        what = None if first is None else "A road closure" if first["closure"] else routing._category_name(first["category"])
        suggestion = {"action": "reroute", "from": {"latitude": payload.latitude, "longitude": payload.longitude},
                      "to": {"latitude": journey["destination_lat"], "longitude": journey["destination_lon"]},
                      "message": f"{what} is about {first['distance_ahead_m']} m ahead on your route; check for a better route "
                                 "from where you are." if first else
                                 "You seem to have left the planned route; check for a better route from where you are."}
    for alert in alerts:
        notify(actor, "disruption_ahead", "Disruption ahead on your journey",
               f"{alert['category'].replace('_', ' ').capitalize()} about {alert['distance_ahead_m']} m ahead"
               + (" (road closed)" if alert["closure"] else "") + ".", alert, f"journey:{journey_id}|incident:{alert['incident_id']}")
    with store.connect() as db:
        db.execute("UPDATE active_journeys SET last_position_json=?,alerted_json=? WHERE journey_id=?",
                   (json.dumps(position), json.dumps(sorted(alerted)), journey_id))
    return {"status": status, "progress_pct": round(100 * min(1.0, along / length), 1), "distance_from_route_m": round(off_route),
            "distance_remaining_m": round(max(0.0, length - along)), "alerts": alerts, "suggestion": suggestion,
            "resumed_after_gap": resumed}


def _follows(candidate: list[list[float]], route: list[list[float]]) -> float:
    """The share of a candidate route's points that lie on another route ([lon, lat] points)."""
    points = candidate[:: max(1, len(candidate) // 40)]
    return sum(core.locate_on_polyline(lat, lon, route)[0] <= SAME_ROUTE_M for lon, lat in points) / len(points) if points else 0.0


@router.post("/api/journeys/{journey_id}/better-route")
def better_route(journey_id: str, payload: PositionInput, actor: str = Depends(citizen_token)):
    """From where the traveller is now: is a route clearly better than the one being followed (faster by
    five minutes, lower risk by ten points, or avoiding a closure)?"""
    journey = _journey(journey_id, actor)
    if journey["status"] != "active":
        raise HTTPException(409, "This journey has ended.")
    if not core.in_bengaluru(payload.latitude, payload.longitude):
        raise HTTPException(422, "The position is outside the Bengaluru area.")
    now = datetime.now(timezone.utc)
    last = _route_checks.get(journey_id)
    if last and (now - last).total_seconds() < ROUTE_CHECK_MIN_INTERVAL_S:
        raise HTTPException(429, "Routes were checked moments ago. The next check is possible in two minutes.")
    _route_checks[journey_id] = now
    current = json.loads(journey["route_json"])
    routes = routing.recommend((payload.latitude, payload.longitude), (journey["destination_lat"], journey["destination_lon"]),
                               None, advice=False)["routes"]
    best = routes[0]
    yours = max(routes, key=lambda r: _follows(r["geometry"]["coordinates"], current))
    if _follows(yours["geometry"]["coordinates"], current) < 0.8:
        yours = None  # no candidate follows the planned route (for example, after leaving it)
    checked = {"checked_at": now.isoformat(), "route": best}
    if yours is best:
        return {**checked, "better": False, "message": "Your route is still the best one from here."}
    if yours is None:
        return {**checked, "better": True, "message": "Your planned route no longer fits where you are. This is the best route from here."}
    saving = yours["baseline_duration_s"] - best["baseline_duration_s"]
    risk_drop = yours["risk_score"] - best["risk_score"]
    avoids_closure = yours["restricted"] and not best["restricted"]
    if not avoids_closure and risk_drop < BETTER_ROUTE_MIN_RISK and saving < BETTER_ROUTE_MIN_SAVING_S:
        return {**checked, "better": False, "message": "Your route is still about as good as the alternatives."}
    gains = ["avoids the road closure on your route"] if avoids_closure else []
    if saving >= 60:
        gains.append(f"saves about {round(saving / 60)} minutes")
    if risk_drop >= 1:
        gains.append(f"lowers the route risk from {yours['risk_score']:.0f} to {best['risk_score']:.0f}")
    elif risk_drop <= -1:
        gains.append(f"has a slightly higher risk ({best['risk_score']:.0f} instead of {yours['risk_score']:.0f})")
    return {**checked, "better": True, "message": "A better route is available: it " + ", ".join(gains) + ".",
            "current": {"risk_score": yours["risk_score"], "duration_s": yours["baseline_duration_s"]}}


class JourneyRouteInput(BaseModel):
    coordinates: list[list[float]] = Field(min_length=2, max_length=20000)  # [lon, lat] route points


@router.post("/api/journeys/{journey_id}/route")
def change_journey_route(journey_id: str, payload: JourneyRouteInput, actor: str = Depends(citizen_token)):
    """Follow a new route from here on; disruptions already reported are not reported again."""
    journey = _journey(journey_id, actor)
    if journey["status"] != "active":
        raise HTTPException(409, "This journey has ended.")
    if any(len(point) != 2 for point in payload.coordinates):
        raise HTTPException(422, "Route points must be [longitude, latitude] pairs.")
    with store.connect() as db:
        db.execute("UPDATE active_journeys SET route_json=? WHERE journey_id=?", (json.dumps(payload.coordinates), journey_id))
    return {"journey_id": journey_id, "status": "active", "route_points": len(payload.coordinates)}


@router.post("/api/journeys/{journey_id}/end")
def end_journey(journey_id: str, actor: str = Depends(citizen_token)):
    _journey(journey_id, actor)
    with store.connect() as db:
        db.execute("UPDATE active_journeys SET status='ended',ended_at=?,last_position_json=NULL WHERE journey_id=? AND status='active'",
                   (iso_now(), journey_id))
    return {"journey_id": journey_id, "status": "ended"}
