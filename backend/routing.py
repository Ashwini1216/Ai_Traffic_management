"""Route recommendations: candidate routes scored with forecasts, demand, incidents, and weather.

Route risk (0-100) combines four kinds of evidence with a noisy-OR, so that correlated
signals cannot add up beyond 100 and no single source is counted twice:

    risk = 100 x [1 - (1 - 0.9 C)(1 - 0.5 D)(1 - I)(1 - W)]

C  congestion: forecast expected load (0..1) of the roads on the route at the time each is
   reached; 0.6 x the worst road + 0.4 x the average (one jam dominates a journey).
D  planned demand above the usual level for those roads and times (down-weighted because
   the congestion forecast already reflects typical demand).
I  verified incidents and official notices in force within 200 m of the route when it is
   reached, weighted by category and distance; nearby duplicates count once.
W  forecast rain at departure (0.25, or 0.4 above 5 mm).

A route through an official road closure is marked restricted and ranked after open routes.
Open routes are ranked by risk plus a time penalty: min(30, 50 x (duration / fastest - 1)).
This is an unvalidated research heuristic; the response lists its evidence and gaps.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

import core
import demand
import forecasting
import store
import weather
from core import iso_now
from security import signed_in_user

router = APIRouter()

INCIDENT_RADIUS_M = 200
INCIDENT_MERGE_M = 100
DEPARTURE_OPTIONS_MIN = [-30, 30, 60, 90, 120]
ADVICE_MIN_IMPROVEMENT = 10.0
SCORING_METHOD = ("Noisy-OR of congestion forecasts (0.9), planned demand above usual (0.5), verified incidents and "
                  "notices within 200 m, and forecast rain; closures mark a route restricted. Unvalidated research heuristic.")


def _noisy_or(values) -> float:
    remaining = 1.0
    for value in values:
        remaining *= 1 - max(0.0, min(1.0, value))
    return 1 - remaining


def _candidate_routes(origin: tuple[float, float], destination: tuple[float, float], departure: datetime,
                      leave_now: bool, profile: str) -> tuple[list[dict], str]:
    """Google alternatives when configured; otherwise one straight-line corridor estimate."""
    if core.google_configured():
        mode = {"driving": "DRIVE", "cycling": "BICYCLE", "walking": "WALK"}[profile]
        request = {
            "origin": {"location": {"latLng": {"latitude": origin[0], "longitude": origin[1]}}},
            "destination": {"location": {"latLng": {"latitude": destination[0], "longitude": destination[1]}}},
            "travelMode": mode, "computeAlternativeRoutes": profile == "driving", "languageCode": "en-IN", "units": "METRIC",
        }
        if profile == "driving":
            request["routingPreference"] = "TRAFFIC_AWARE"
            # Google rejects departure times that have passed; omitting the field means "now".
            if not leave_now:
                request["departureTime"] = departure.isoformat(timespec="seconds").replace("+00:00", "Z")
        data = core.google_post_json("https://routes.googleapis.com/directions/v2:computeRoutes", request)
        if not data.get("routes"):
            raise HTTPException(404, "Google Maps could not find a route for those locations.")
        routes = []
        for route in data["routes"]:
            try:
                duration = float(str(route.get("duration", "0s")).removesuffix("s"))
            except (TypeError, ValueError):
                raise HTTPException(502, "Google Routes returned an invalid travel time.") from None
            routes.append({"source": "google_route", "coordinates": core.decode_google_polyline(route.get("polyline", {}).get("encodedPolyline", "")),
                           "distance_m": float(route.get("distanceMeters", 0)), "duration_s": duration})
        return routes, "Google Routes API"
    estimate = demand.plan_route(origin, destination, departure)
    return [estimate], "Straight-line corridor estimate (Google Routes is not configured)"


def _incidents_on_route(coordinates: list[list[float]], departure: datetime, duration_s: float, length_m: float) -> list[dict]:
    end = departure + timedelta(seconds=duration_s)
    rows = store.rows("""SELECT incident_id,category,area,road,latitude,longitude,created_at,starts_at,expires_at,official,closure,
      severity FROM incidents WHERE status='verified' AND latitude IS NOT NULL AND (expires_at IS NULL OR expires_at>?)
      AND (starts_at IS NULL OR starts_at<?)""", (departure.isoformat(), end.isoformat()))
    found = []
    for row in rows:
        distance, along = core.locate_on_polyline(row["latitude"], row["longitude"], coordinates)
        if distance > INCIDENT_RADIUS_M:
            continue
        reached = departure + timedelta(seconds=duration_s * along / (length_m or 1.0))
        starts, expires = core.parse_utc(row["starts_at"]), core.parse_utc(row["expires_at"])
        if (starts and reached < starts) or (expires and reached >= expires):
            continue  # not in force when the traveller gets there
        found.append({"incident_id": row["incident_id"], "category": row["category"], "road": row["road"],
                      "distance_m": round(distance), "created_at": row["created_at"], "official": bool(row["official"]),
                      "closure": bool(row["closure"]), "severity": row["severity"], "reached_at": reached.isoformat(),
                      "latitude": row["latitude"], "longitude": row["longitude"]})
    return found


def _incident_component(items: list[dict]) -> float:
    """Noisy-OR over incident locations; reports within 100 m of each other count once (the strongest)."""
    locations: list[tuple[dict, float]] = []
    for item in sorted(items, key=lambda i: -forecasting.INCIDENT_WEIGHTS.get(i["category"], 0.25)):
        weight = forecasting.INCIDENT_WEIGHTS.get(item["category"], 0.25) * (1.0 if item["distance_m"] <= 100 else 0.75)
        if any(core.haversine_m(item["latitude"], item["longitude"], other["latitude"], other["longitude"]) <= INCIDENT_MERGE_M
               for other, _ in locations):
            continue
        locations.append((item, weight))
    return _noisy_or(weight for _, weight in locations)


def _weather_component(origin, destination, departure: datetime) -> tuple[float, dict | None]:
    best = None
    for point in (origin, destination):
        candidate = weather.forecast_for(point[0], point[1], departure)
        if candidate and (best is None or (candidate["time_gap_minutes"], candidate["distance_m"]) < (best["time_gap_minutes"], best["distance_m"])):
            best = candidate
    if not best:
        return 0.0, None
    precipitation = best.get("precipitation_mm") or 0.0
    wet = weather.is_wet(weather.label_from_code(best.get("weather_code")), best.get("precipitation_mm"))
    return (0.4 if precipitation > 5 else 0.25) if wet else 0.0, best


def assess_route(route: dict, departure: datetime, origin, destination) -> dict:
    """Score one route for one departure time."""
    length = core.polyline_length_m(route["coordinates"]) or route["distance_m"] or 1.0
    roads = []
    for match in demand.associate(route):
        passes_at = departure + timedelta(seconds=match["offset_s"])
        try:
            outlook = forecasting.forecast(match["segment_id"], max(passes_at, datetime.now(timezone.utc)), record=False)
        except HTTPException:
            continue
        planned = (outlook["signals"] or {}).get("planned_demand") or demand.segment_demand(match["segment_id"], passes_at)
        roads.append({"segment_id": match["segment_id"], "area": outlook["area"], "road": outlook["road"],
                      "passes_at": passes_at.isoformat(), "predicted_class": outlook["predicted_class"],
                      "expected_load": outlook["expected_load"], "method": outlook["method"],
                      "confidence": outlook["confidence"]["level"], "planned_pcu": planned["pcu"], "demand_index": planned["demand_index"]})
    incidents_found = _incidents_on_route(route["coordinates"], departure, route["duration_s"], length)
    weather_score, weather_context = _weather_component(origin, destination, departure)
    loads = [r["expected_load"] for r in roads]
    congestion = 0.6 * max(loads) + 0.4 * sum(loads) / len(loads) if loads else None
    demand_parts = []
    for road in roads:
        if road["demand_index"] is not None:
            demand_parts.append(max(0.0, min(1.0, (road["demand_index"] - 1.0) / 2)))
        elif road["planned_pcu"]:
            demand_parts.append(min(1.0, road["planned_pcu"] / demand.HOTSPOT_PCU_SCALE))
    demand_score = max(demand_parts) if demand_parts else 0.0
    incident_score = _incident_component(incidents_found)
    risk = 100 * _noisy_or([0.9 * (congestion or 0.0), 0.5 * demand_score, incident_score, weather_score])
    gaps = []
    if not roads:
        gaps.append("No registered roads with forecasts lie on this route, so congestion is not assessed.")
    elif all(r["method"] == "cold_start" for r in roads):
        gaps.append("Forecasts on this route are limited-data estimates.")
    if weather_context is None:
        gaps.append("No weather forecast is stored for this time; open a route again after weather is collected.")
    if route["source"] != "google_route":
        gaps.append("Road routing is unavailable, so this is a straight-line corridor estimate.")
    return {"risk_score": round(risk, 1), "restricted": any(i["closure"] for i in incidents_found),
            "components": {"congestion": None if congestion is None else round(congestion, 3), "planned_demand": round(demand_score, 3),
                           "incidents": round(incident_score, 3), "weather": round(weather_score, 3)},
            "roads": roads, "incidents": incidents_found, "weather_context": weather_context, "data_gaps": gaps}


def departure_advice(route: dict, departure: datetime, origin, destination, current: dict, leave_now: bool) -> dict:
    """Compare nearby departure times for the best route; only advised when forecasts support it."""
    if not current["roads"] or all(r["method"] == "cold_start" for r in current["roads"]):
        return {"supported": False, "reason": "There are not enough road forecasts on this route to compare departure times."}
    now = datetime.now(timezone.utc)
    options = [{"departure_at": departure.isoformat(), "offset_minutes": 0, "risk_score": current["risk_score"]}]
    for minutes in DEPARTURE_OPTIONS_MIN:
        candidate = departure + timedelta(minutes=minutes)
        if candidate < now - timedelta(minutes=1) or (leave_now and minutes < 0):
            continue
        assessed = assess_route(route, candidate, origin, destination)
        if assessed["restricted"]:
            continue
        options.append({"departure_at": candidate.isoformat(), "offset_minutes": minutes, "risk_score": assessed["risk_score"]})
    best = min(options, key=lambda o: (o["risk_score"], abs(o["offset_minutes"])))
    improvement = current["risk_score"] - best["risk_score"]
    if best["offset_minutes"] == 0 or improvement < ADVICE_MIN_IMPROVEMENT:
        return {"supported": True, "recommendation": "keep", "options": options,
                "message": "Your chosen departure time is as good as the nearby alternatives."}
    when = "earlier" if best["offset_minutes"] < 0 else "later"
    return {"supported": True, "recommendation": "shift", "best": best, "options": options,
            "message": f"Leaving {abs(best['offset_minutes'])} minutes {when} lowers the expected route risk by {improvement:.0f} points."}


def recommend(origin: tuple[float, float], destination: tuple[float, float], departure_at: datetime | None,
              profile: str = "driving", advice: bool = True) -> dict:
    now = datetime.now(timezone.utc)
    departure = departure_at or now
    if departure.tzinfo is None:
        raise HTTPException(422, "departure_at must include a timezone.")
    departure = departure.astimezone(timezone.utc)
    if departure < now - timedelta(minutes=5):
        raise HTTPException(422, "departure_at cannot be in the past.")
    leave_now = departure <= now + timedelta(minutes=1)
    if leave_now:
        departure = now
    routes, provider = _candidate_routes(origin, destination, departure, leave_now, profile)
    fastest = min((r["duration_s"] for r in routes), default=1.0) or 1.0
    scored = []
    for index, route in enumerate(routes):
        assessed = assess_route(route, departure, origin, destination)
        time_penalty = min(30.0, 50.0 * (route["duration_s"] / fastest - 1))
        scored.append({"route_index": index, "distance_m": route["distance_m"], "baseline_duration_s": route["duration_s"],
                       "geometry": {"type": "LineString", "coordinates": route["coordinates"]}, "legs": [],
                       "source_type": route["source"], "rank_cost": round(assessed["risk_score"] + time_penalty, 1),
                       "time_penalty": round(time_penalty, 1), "nearby_verified_incidents": assessed["incidents"],
                       "weather_penalty": round(assessed["components"]["weather"] * 100, 1), **assessed, "_route": route})
    scored.sort(key=lambda item: (item["restricted"], item["rank_cost"], item["baseline_duration_s"]))
    best = scored[0]
    best["departure_advice"] = departure_advice(best["_route"], departure, origin, destination, best, leave_now) if advice else None
    for item in scored:
        item.pop("_route")
    route_id = str(uuid.uuid4())
    # Keep the evidence behind each score for later review. Origin and destination are
    # personal travel details, so they are not stored with the assessment.
    record = {"profile": profile, "departure_at": departure.isoformat(), "leave_now": leave_now, "provider": provider,
              "routes": [{k: item[k] for k in ("route_index", "distance_m", "baseline_duration_s", "risk_score", "restricted",
                                              "components", "rank_cost", "data_gaps")} |
                         {"roads": [{k: r[k] for k in ("segment_id", "passes_at", "predicted_class", "method", "planned_pcu")} for r in item["roads"]],
                          "incidents": [i["incident_id"] for i in item["incidents"]],
                          "weather_forecast_for": (item["weather_context"] or {}).get("forecast_for")} for item in scored],
              "departure_advice": best["departure_advice"]}
    with store.connect() as db:
        db.execute("""INSERT INTO route_assessments(route_id,issue_time,provider,source_type,scoring_method,route_results_json)
          VALUES(?,?,?,?,?,?)""", (route_id, iso_now(), provider, scored[0]["source_type"], SCORING_METHOD, json.dumps(record, default=str)))
    return {"route_id": route_id, "provider": provider, "routes": scored, "scoring_method": SCORING_METHOD,
            "departure_at": departure.isoformat(), "leave_now": leave_now,
            "traffic_data_coverage": "Forecasts for registered roads on each route; see data_gaps for missing evidence.",
            "weather_status": "provider_forecast_used" if best["weather_context"] else "unavailable",
            "notice": "Travel times come from the routing provider; the risk score is an unvalidated research heuristic."}


@router.get("/api/routes")
def route_candidates(
    origin_lat: float = Query(ge=-90, le=90), origin_lon: float = Query(ge=-180, le=180),
    destination_lat: float = Query(ge=-90, le=90), destination_lon: float = Query(ge=-180, le=180),
    profile: Literal["driving", "cycling", "walking"] = "driving",
    departure_at: datetime | None = None,
    advice: bool = True,
    actor: str = Depends(signed_in_user),
):
    return recommend((origin_lat, origin_lon), (destination_lat, destination_lon), departure_at, profile, advice)
