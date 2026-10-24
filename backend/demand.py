"""Planned journeys and the future travel demand they add, weighted by vehicle type.

Each planned trip is routed (Google Routes when configured, otherwise a clearly flagged
straight-line estimate), matched to the registered roads it passes, and given the time it
should reach each one. Demand is the sum of passenger-car units (PCU) expected on a road in a
time block. Planned trips are intentions, not confirmed vehicles, and are only reported in
aggregate; corridor groups with too few trips are suppressed to protect privacy.
"""
from __future__ import annotations

import hashlib
import math
import os
import secrets
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

import core
import places
import store
from core import iso_now
from security import _decode_bearer, authority_token, citizen_token

router = APIRouter()

VEHICLE_TYPES = ["two_wheeler", "car", "auto_rickshaw", "cab", "bus", "truck", "bicycle"]
# Approximate passenger-car units in line with Indian urban-road guidance (IRC:106). Override
# with PCU_FACTORS in .env, e.g. "two_wheeler=0.5,car=1,auto_rickshaw=1.2,bus=3.0".
DEFAULT_PCU = {"two_wheeler": 0.75, "car": 1.0, "auto_rickshaw": 1.2, "cab": 1.0, "bus": 2.2, "truck": 2.2, "bicycle": 0.5}
GOOGLE_CORRIDOR_M = 300        # a road counts as on the route within this distance
ESTIMATE_CORRIDOR_M = 800      # wider band when only a straight-line estimate exists
ESTIMATE_SPEED_KMH = 20.0      # assumed average speed for straight-line estimates
ROAD_CIRCUITY = 1.3            # road distance is typically ~30% longer than a straight line
MIN_GROUP_TRIPS = int(os.getenv("DEMAND_MIN_GROUP_TRIPS", "3"))
HOTSPOT_PCU_SCALE = float(os.getenv("DEMAND_HOTSPOT_PCU_SCALE", "50"))


def _pcu_factors() -> dict[str, float]:
    factors = dict(DEFAULT_PCU)
    for part in filter(None, (os.getenv("PCU_FACTORS") or "").split(",")):
        name, _, value = part.partition("=")
        if name.strip() in factors:
            factors[name.strip()] = float(value)
    return factors


PCU_FACTORS = _pcu_factors()


class TripInput(BaseModel):
    origin: str = Field(min_length=2, max_length=200)
    destination: str = Field(min_length=2, max_length=200)
    origin_lat: float | None = Field(default=None, ge=-90, le=90)
    origin_lon: float | None = Field(default=None, ge=-180, le=180)
    destination_lat: float | None = Field(default=None, ge=-90, le=90)
    destination_lon: float | None = Field(default=None, ge=-180, le=180)
    departure_at: datetime
    vehicle_type: Literal["two_wheeler", "car", "auto_rickshaw", "cab", "bus", "truck", "bicycle"] = "car"


def _resolve_points(payload: TripInput) -> tuple[float, float, float, float]:
    points = [payload.origin_lat, payload.origin_lon, payload.destination_lat, payload.destination_lon]
    if (payload.origin_lat is None) != (payload.origin_lon is None) or (payload.destination_lat is None) != (payload.destination_lon is None):
        raise HTTPException(422, "Provide both latitude and longitude for each point, or neither.")
    if payload.origin_lat is None or payload.destination_lat is None:
        origin = (payload.origin_lat, payload.origin_lon) if payload.origin_lat is not None else places.first_location(payload.origin)
        destination = (payload.destination_lat, payload.destination_lon) if payload.destination_lat is not None else places.first_location(payload.destination)
        if not origin or not destination:
            raise HTTPException(422, "Place search could not find both places. Choose them on the map instead.")
        points = [origin[0], origin[1], destination[0], destination[1]]
    if not (core.in_bengaluru(points[0], points[1]) and core.in_bengaluru(points[2], points[3])):
        raise HTTPException(422, "Both places must be in the Bengaluru area.")
    return points[0], points[1], points[2], points[3]


def plan_route(origin: tuple[float, float], destination: tuple[float, float], departure: datetime) -> dict:
    """Route geometry, distance, and duration; Google when available, otherwise an estimate."""
    if core.google_configured():
        request = {
            "origin": {"location": {"latLng": {"latitude": origin[0], "longitude": origin[1]}}},
            "destination": {"location": {"latLng": {"latitude": destination[0], "longitude": destination[1]}}},
            "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE", "languageCode": "en-IN", "units": "METRIC",
        }
        if departure > datetime.now(timezone.utc) + timedelta(minutes=1):
            request["departureTime"] = departure.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        try:
            data = core.google_post_json("https://routes.googleapis.com/directions/v2:computeRoutes", request)
            route = (data.get("routes") or [None])[0]
            if route:
                encoded = route.get("polyline", {}).get("encodedPolyline", "")
                return {"source": "google_route", "polyline": encoded, "coordinates": core.decode_google_polyline(encoded),
                        "distance_m": float(route.get("distanceMeters", 0)), "duration_s": float(str(route.get("duration", "0s")).removesuffix("s"))}
        except HTTPException:
            pass  # fall back to the estimate below, which is labelled as such
    straight = core.haversine_m(origin[0], origin[1], destination[0], destination[1])
    distance = straight * ROAD_CIRCUITY
    return {"source": "straight_line_estimate", "polyline": None,
            "coordinates": [[origin[1], origin[0]], [destination[1], destination[0]]],
            "distance_m": distance, "duration_s": distance / (ESTIMATE_SPEED_KMH / 3.6)}


def associate(route: dict) -> list[dict]:
    """Registered roads near the route, with the expected seconds after departure to reach each."""
    corridor = GOOGLE_CORRIDOR_M if route["source"] == "google_route" else ESTIMATE_CORRIDOR_M
    length = core.polyline_length_m(route["coordinates"]) or 1.0
    matches = []
    for segment in store.rows("SELECT segment_id,latitude,longitude FROM road_segments WHERE latitude IS NOT NULL"):
        distance, along = core.locate_on_polyline(segment["latitude"], segment["longitude"], route["coordinates"])
        if distance <= corridor:
            matches.append({"segment_id": segment["segment_id"], "offset_s": round(route["duration_s"] * along / length, 1),
                            "distance_m": round(along, 1)})
    return sorted(matches, key=lambda m: m["offset_s"])


def nearest_zone(latitude: float, longitude: float) -> str | None:
    best, best_distance = None, float("inf")
    for zone in store.rows("SELECT zone_id,latitude,longitude,radius_m FROM zones WHERE latitude IS NOT NULL"):
        distance = core.haversine_m(latitude, longitude, zone["latitude"], zone["longitude"])
        if distance <= (zone["radius_m"] or 2000) * 1.5 and distance < best_distance:
            best, best_distance = zone["zone_id"], distance
    return best


def _store_route(db, trip_id: str, payload: TripInput, points: tuple, departure: datetime) -> dict:
    route = plan_route((points[0], points[1]), (points[2], points[3]), departure)
    matches = associate(route)
    db.execute("DELETE FROM trip_segments WHERE trip_id=?", (trip_id,))
    for match in matches:
        db.execute("INSERT INTO trip_segments(trip_id,segment_id,offset_s,distance_m) VALUES(?,?,?,?)",
                   (trip_id, match["segment_id"], match["offset_s"], match["distance_m"]))
    db.execute("""UPDATE planned_trips SET origin=?,destination=?,departure_at=?,origin_lat=?,origin_lon=?,destination_lat=?,
      destination_lon=?,vehicle_type=?,pcu=?,origin_zone=?,destination_zone=?,route_source=?,route_polyline=?,route_distance_m=?,
      route_duration_s=?,updated_at=?,last_risk=NULL,last_assessment_json=NULL,last_assessed_at=NULL WHERE trip_id=?""",
               (payload.origin, payload.destination, departure.isoformat(), points[0], points[1], points[2], points[3],
                payload.vehicle_type, PCU_FACTORS[payload.vehicle_type], nearest_zone(points[0], points[1]),
                nearest_zone(points[2], points[3]), route["source"], route["polyline"], route["distance_m"], route["duration_s"],
                iso_now(), trip_id))
    return {"route_source": route["source"], "segments": matches, "distance_m": round(route["distance_m"]),
            "duration_s": round(route["duration_s"])}


def _check_departure(departure: datetime) -> datetime:
    if departure.tzinfo is None:
        raise HTTPException(422, "departure_at must include a timezone.")
    if departure <= datetime.now(timezone.utc):
        raise HTTPException(422, "departure_at must be in the future.")
    return departure.astimezone(timezone.utc)


def _trip_state(trip: dict, now: datetime | None = None) -> str:
    if trip["state"] != "scheduled":
        return trip["state"]
    now = now or datetime.now(timezone.utc)
    arrival = core.parse_utc(trip["departure_at"]) + timedelta(seconds=trip.get("route_duration_s") or 0)
    return "completed" if arrival < now else "scheduled"


def _owned_trip(trip_id: str, authorization: str | None) -> dict:
    """The trip, if the caller owns it: by account, or by the one-time owner token of older trips."""
    trip = store.one("SELECT * FROM planned_trips WHERE trip_id=?", (trip_id,))
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not token:
        raise HTTPException(401, "Sign in to manage this trip.")
    owner = False
    if trip:
        try:
            claims = _decode_bearer(authorization)
            owner = trip.get("username") == claims["sub"]
        except HTTPException:
            owner = hashlib.sha256(token.encode()).hexdigest() == trip["owner_token_hash"]
    if not trip or not owner:
        raise HTTPException(404, "Trip not found for this account.")
    return trip


# ---------- travellers heading to the same place ----------
# Other people's scheduled trips that arrive within SAME_DESTINATION_M of a destination within
# SAME_DESTINATION_WINDOW of the same time. People are counted, not trips. As with corridors, a
# count below MIN_GROUP_TRIPS is reported only as "fewer than", so no one can learn that one
# particular other person is heading somewhere.

SAME_DESTINATION_M = 500
SAME_DESTINATION_WINDOW = timedelta(minutes=60)
DESTINATION_CELL_DEG = 0.0045  # about 500 m, for grouping popular destinations


def _arrival(trip: dict) -> datetime:
    return core.parse_utc(trip["departure_at"]) + timedelta(seconds=trip.get("route_duration_s") or 0)


def same_destination(latitude: float, longitude: float, arrival: datetime, username: str) -> dict:
    lat_pad = SAME_DESTINATION_M / 111_000
    lon_pad = lat_pad / max(0.2, math.cos(math.radians(latitude)))
    rows = store.rows("""SELECT username,departure_at,route_duration_s,destination_lat,destination_lon FROM planned_trips
      WHERE state='scheduled' AND username IS NOT NULL AND username<>? AND destination_lat BETWEEN ? AND ?
      AND destination_lon BETWEEN ? AND ?""", (username, latitude - lat_pad, latitude + lat_pad, longitude - lon_pad, longitude + lon_pad))
    people = {row["username"] for row in rows
              if core.haversine_m(latitude, longitude, row["destination_lat"], row["destination_lon"]) <= SAME_DESTINATION_M
              and abs(_arrival(row) - arrival) <= SAME_DESTINATION_WINDOW}
    shown = len(people) >= MIN_GROUP_TRIPS
    return {"travellers": len(people) if shown else None, "fewer_than": None if shown else MIN_GROUP_TRIPS,
            "radius_m": SAME_DESTINATION_M, "window_minutes": int(SAME_DESTINATION_WINDOW.total_seconds() // 60)}


def _place_name(latitude: float, longitude: float) -> str:
    """The nearest registered road within a kilometre, or the nearest zone (never what people typed)."""
    roads = store.rows("SELECT road,area,latitude,longitude FROM road_segments WHERE latitude IS NOT NULL")
    nearest = min(roads, key=lambda r: core.haversine_m(latitude, longitude, r["latitude"], r["longitude"]), default=None)
    if nearest and core.haversine_m(latitude, longitude, nearest["latitude"], nearest["longitude"]) <= 1000:
        return f"{nearest['road']}, {nearest['area']}"
    zone = nearest_zone(latitude, longitude)
    named = store.one("SELECT name FROM zones WHERE zone_id=?", (zone,)) if zone else None
    return f"{named['name']} area" if named else f"{latitude:.3f}, {longitude:.3f}"


def popular_destinations(hours: int = 48, limit: int = 10) -> dict:
    """Places that at least MIN_GROUP_TRIPS people plan to reach within the same hour, busiest first.
    Destinations are grouped into cells of about 500 m; each cell reports its busiest 60-minute window."""
    now = datetime.now(timezone.utc)
    rows = store.rows("""SELECT username,departure_at,route_duration_s,destination_lat,destination_lon FROM planned_trips
      WHERE state='scheduled' AND username IS NOT NULL AND destination_lat IS NOT NULL AND departure_at>=? AND departure_at<?""",
                      (now.isoformat(), (now + timedelta(hours=hours)).isoformat()))
    cells: dict[tuple, list] = defaultdict(list)
    for row in rows:
        cell = (round(row["destination_lat"] / DESTINATION_CELL_DEG), round(row["destination_lon"] / DESTINATION_CELL_DEG))
        cells[cell].append((_arrival(row), row["username"], row["destination_lat"], row["destination_lon"]))
    shown, suppressed = [], 0
    for visits in cells.values():
        visits.sort()
        busiest: list = []
        for index, (first, *_) in enumerate(visits):
            window = [visit for visit in visits[index:] if visit[0] - first <= timedelta(minutes=60)]
            if len({v[1] for v in window}) > len({v[1] for v in busiest}):
                busiest = window
        people = {visit[1] for visit in busiest}
        if len(people) < MIN_GROUP_TRIPS:
            suppressed += len({visit[1] for visit in visits})
            continue
        lat = sum(v[2] for v in busiest) / len(busiest)
        lon = sum(v[3] for v in busiest) / len(busiest)
        shown.append({"near": _place_name(lat, lon), "latitude": round(lat, 3), "longitude": round(lon, 3),
                      "arrivals_from": busiest[0][0].isoformat(), "arrivals_until": busiest[-1][0].isoformat(), "travellers": len(people)})
    shown.sort(key=lambda d: (-d["travellers"], d["arrivals_from"]))
    return {"destinations": shown[:limit], "suppressed_destination_travellers": suppressed}


# ---------- trip API ----------

@router.post("/api/trips", status_code=201)
def create_trip(payload: TripInput, actor: str = Depends(citizen_token)):
    departure = _check_departure(payload.departure_at)
    points = _resolve_points(payload)
    token, trip_id = secrets.token_urlsafe(32), str(uuid.uuid4())
    with store.connect() as db:
        db.execute("""INSERT INTO planned_trips(trip_id,owner_token_hash,origin,destination,departure_at,state,created_at,username)
          VALUES(?,?,?,?,?,?,?,?)""", (trip_id, hashlib.sha256(token.encode()).hexdigest(), payload.origin, payload.destination,
                                       departure.isoformat(), "scheduled", iso_now(), actor))
        route = _store_route(db, trip_id, payload, points, departure)
    arrival = departure + timedelta(seconds=route["duration_s"])
    return {"trip_id": trip_id, "owner_token": token, "departure_at": departure.isoformat(), "vehicle_type": payload.vehicle_type,
            "pcu": PCU_FACTORS[payload.vehicle_type], **route,
            "same_destination": same_destination(points[2], points[3], arrival, actor),
            "privacy_notice": "Only you can see this trip. Authorities see planned demand as totals per road and corridor."}


@router.get("/api/trips")
def my_trips(actor: str = Depends(citizen_token)):
    trips = store.rows("""SELECT trip_id,origin,destination,departure_at,state,vehicle_type,pcu,route_source,route_distance_m,
      route_duration_s,origin_lat,origin_lon,destination_lat,destination_lon,last_risk,last_assessed_at,last_assessment_json,created_at
      FROM planned_trips WHERE username=? ORDER BY departure_at DESC LIMIT 200""", (actor,))
    import json

    now = datetime.now(timezone.utc)
    for trip in trips:
        trip["state"] = _trip_state(trip, now)
        trip["last_assessment"] = json.loads(trip.pop("last_assessment_json") or "null")
        arrival = _arrival(trip)
        trip["same_destination"] = (same_destination(trip["destination_lat"], trip["destination_lon"], arrival, actor)
                                    if trip["state"] == "scheduled" and trip["destination_lat"] is not None and arrival > now else None)
        trip["segments"] = store.rows("""SELECT s.segment_id,s.area,s.road,ts.offset_s FROM trip_segments ts
          JOIN road_segments s ON s.segment_id=ts.segment_id WHERE ts.trip_id=? ORDER BY ts.offset_s""", (trip["trip_id"],))
    return {"trips": trips}


@router.patch("/api/trips/{trip_id}")
def update_trip(trip_id: str, payload: TripInput, authorization: str | None = Header(default=None)):
    trip = _owned_trip(trip_id, authorization)
    if trip["state"] != "scheduled":
        raise HTTPException(404, "Scheduled trip not found for this account.")
    departure = _check_departure(payload.departure_at)
    # Unchanged place names without coordinates keep the stored points; changed names are searched again.
    unchanged = (payload.origin, payload.destination) == (trip["origin"], trip["destination"])
    if payload.origin_lat is None and payload.destination_lat is None and unchanged and trip["origin_lat"] is not None:
        points = (trip["origin_lat"], trip["origin_lon"], trip["destination_lat"], trip["destination_lon"])
    else:
        points = _resolve_points(payload)
    with store.connect() as db:
        route = _store_route(db, trip_id, payload, points, departure)
    return {"trip_id": trip_id, "state": "scheduled", "departure_at": departure.isoformat(), **route}


@router.patch("/api/trips/{trip_id}/cancel")
def cancel_trip(trip_id: str, authorization: str | None = Header(default=None)):
    trip = _owned_trip(trip_id, authorization)
    if trip["state"] != "scheduled":
        raise HTTPException(404, "Scheduled trip not found for this account.")
    with store.connect() as db:
        db.execute("UPDATE planned_trips SET state='cancelled',updated_at=? WHERE trip_id=?", (iso_now(), trip_id))
        db.execute("DELETE FROM trip_segments WHERE trip_id=?", (trip_id,))
    return {"trip_id": trip_id, "state": "cancelled"}


# ---------- demand ----------

def _passages(segment_id: str | None = None, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
    """Expected passages of scheduled trips past registered roads."""
    clauses, params = ["t.state='scheduled'"], []
    if segment_id:
        clauses.append("ts.segment_id=?")
        params.append(segment_id)
    if since:
        clauses.append("t.departure_at>=?")
        params.append((since - timedelta(hours=6)).isoformat())
    if until:
        clauses.append("t.departure_at<?")
        params.append(until.isoformat())
    rows = store.rows(f"""SELECT ts.segment_id,ts.offset_s,t.trip_id,t.departure_at,COALESCE(t.pcu,1.0) AS pcu,t.origin_zone,
      t.destination_zone FROM trip_segments ts JOIN planned_trips t ON t.trip_id=ts.trip_id WHERE {' AND '.join(clauses)}""", tuple(params))
    for row in rows:
        row["passes_at"] = core.parse_utc(row["departure_at"]) + timedelta(seconds=row["offset_s"])
    return rows


def segment_demand(segment_id: str, target: datetime) -> dict:
    """Planned PCU expected on a road in the target's time block, and how that compares with
    the same block on the previous four same-type days (weekday/weekend)."""
    day, block = core.block_for(target)
    start, end = core.block_bounds(day, block)
    windows = [(start, end)]
    probe = day
    while len(windows) < 5 and (day - probe).days < 35:
        probe -= timedelta(days=1)
        if (probe.weekday() >= 5) == (day.weekday() >= 5):
            windows.append(core.block_bounds(probe, block))
    passages = _passages(segment_id, since=windows[-1][0], until=end)
    totals = []
    for low, high in windows:
        selected = [p for p in passages if low <= p["passes_at"] < high]
        totals.append((len(selected), sum(p["pcu"] for p in selected)))
    history = [pcu for _, pcu in totals[1:]]
    typical = sum(history) / len(history) if any(history) else None
    pcu = totals[0][1]
    return {"window": {"date": day.isoformat(), "time_block": block}, "planned_trips": totals[0][0], "pcu": round(pcu, 2),
            "typical_pcu": round(typical, 2) if typical is not None else None,
            "demand_index": round(pcu / typical, 2) if typical else None}


def upcoming_demand(hours: int = 48) -> list[dict]:
    """Planned PCU per road and time block over the coming hours (only non-empty cells)."""
    now = datetime.now(timezone.utc)
    cells: dict[tuple, dict] = {}
    for passage in _passages(since=now, until=now + timedelta(hours=hours)):
        if not now - timedelta(hours=1) <= passage["passes_at"] <= now + timedelta(hours=hours):
            continue
        day, block = core.block_for(passage["passes_at"])
        cell = cells.setdefault((passage["segment_id"], day, block), {"segment_id": passage["segment_id"], "date": day.isoformat(),
                                                                       "time_block": block, "planned_trips": 0, "pcu": 0.0})
        cell["planned_trips"] += 1
        cell["pcu"] = round(cell["pcu"] + passage["pcu"], 2)
    names = {s["segment_id"]: s for s in store.rows("SELECT segment_id,area,road,zone_id,latitude,longitude FROM road_segments")}
    return [{**cell, **{k: names.get(cell["segment_id"], {}).get(k) for k in ("area", "road", "zone_id", "latitude", "longitude")}}
            for cell in sorted(cells.values(), key=lambda c: (c["date"], TIME_BLOCKS_INDEX[c["time_block"]], -c["pcu"]))]


TIME_BLOCKS_INDEX = {name: i for i, name in enumerate(core.TIME_BLOCKS)}


def corridors(hours: int = 72) -> dict:
    """Planned trips by zone-to-zone corridor and departure hour; small groups are suppressed."""
    now = datetime.now(timezone.utc)
    rows = store.rows("""SELECT origin_zone,destination_zone,departure_at,COALESCE(pcu,1.0) AS pcu FROM planned_trips
      WHERE state='scheduled' AND departure_at>=? AND departure_at<?""", (now.isoformat(), (now + timedelta(hours=hours)).isoformat()))
    groups: dict[tuple, dict] = defaultdict(lambda: {"planned_trips": 0, "pcu": 0.0})
    for row in rows:
        hour = core.parse_utc(row["departure_at"]).astimezone(core.LOCAL_TZ).replace(minute=0, second=0, microsecond=0)
        key = (row["origin_zone"] or "outside_zones", row["destination_zone"] or "outside_zones", hour.isoformat())
        groups[key]["planned_trips"] += 1
        groups[key]["pcu"] += row["pcu"]
    shown, suppressed = [], 0
    for (origin, destination, hour), values in sorted(groups.items(), key=lambda item: (item[0][2], -item[1]["pcu"])):
        if values["planned_trips"] >= MIN_GROUP_TRIPS:
            shown.append({"origin_zone": origin, "destination_zone": destination, "departure_hour_ist": hour,
                          "planned_trips": values["planned_trips"], "pcu": round(values["pcu"], 2)})
        else:
            suppressed += values["planned_trips"]
    return {"corridors": shown, "suppressed_trips": suppressed, "minimum_group_size": MIN_GROUP_TRIPS}


def hotspots(hours: int = 48, limit: int = 20) -> list[dict]:
    """Roads and blocks where planned demand meets expected congestion.
    score = 0.6 x forecast expected load + 0.4 x min(1, planned PCU / HOTSPOT_PCU_SCALE)."""
    import forecasting  # imported here: forecasting also reads demand

    result = []
    for cell in upcoming_demand(hours):
        start = core.block_bounds(date.fromisoformat(cell["date"]), cell["time_block"])[0]
        target = max(start, datetime.now(timezone.utc)) + timedelta(minutes=1)
        try:
            outlook = forecasting.forecast(cell["segment_id"], target, record=False)
        except HTTPException:
            continue
        demand_share = min(1.0, cell["pcu"] / HOTSPOT_PCU_SCALE)
        score = 0.6 * outlook["expected_load"] + 0.4 * demand_share
        result.append({**cell, "expected_load": outlook["expected_load"], "predicted_class": outlook["predicted_class"],
                       "forecast_method": outlook["method"], "confidence": outlook["confidence"]["level"],
                       "score": round(score, 3), "level": "High" if score >= 0.66 else "Medium" if score >= 0.4 else "Low"})
    return sorted(result, key=lambda r: -r["score"])[:limit]


@router.get("/api/demand")
def demand_overview(hours: int = Query(default=48, ge=1, le=168), actor: str = Depends(authority_token)):
    return {"pcu_factors": PCU_FACTORS, "segments": upcoming_demand(hours), **corridors(hours), "hotspots": hotspots(hours),
            **popular_destinations(hours),
            "notice": "Planned trips are intentions, not confirmed vehicles. Individual journeys are never shown."}


@router.get("/api/demand/vehicle-types")
def vehicle_types():
    return {"vehicle_types": [{"id": v, "pcu": PCU_FACTORS[v]} for v in VEHICLE_TYPES]}
