"""Route recommendations: candidate routes scored with forecasts, traffic, demand, incidents, and weather.

Route risk (0-100) combines four kinds of evidence with a noisy-OR, so that correlated
signals cannot add up beyond 100 and no single source is counted twice:

    risk = 100 x [1 - (1 - 0.9 C)(1 - 0.5 D)(1 - I)(1 - W)]

C  traffic: forecast expected load (0..1) of the roads on the route at the time each is
   reached, 0.6 x the worst road + 0.4 x the average (one jam dominates a journey); or, if
   higher, Google's traffic delay for the trip (travel time / time without traffic - 1, capped
   at 1). The larger of the two is used because both describe the same traffic.
D  planned demand above the usual level for those roads and times (down-weighted because
   the congestion forecast already reflects typical demand).
I  verified incidents and official notices (accidents, hazards, closures) in force within
   200 m of the route when it is reached, weighted by category and distance; nearby duplicates
   count once.
W  forecast weather at departure: thunderstorm 0.5, heavy rain (above 5 mm) 0.4, rain 0.25,
   fog 0.2, wind of 40 km/h or more 0.15, combined with the same noisy-OR.

A route through an official road closure is marked restricted and ranked after open routes.
Open routes are ranked by risk plus a time penalty: min(30, 50 x (duration / fastest - 1)).
Each route lists the reasons behind its score. Departure times are compared on the forecast
evidence only, because Google's traffic delay is known only for the chosen time.
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
DEPARTURE_OPTIONS_MIN = [-60, -30, 30, 60, 90, 120]
ADVICE_MIN_IMPROVEMENT = 10.0
HIGH_RISK = 60.0
SAFER_MIN_REDUCTION = 10.0
LEAVE_EARLY_MIN_DELAY_S = 5 * 60
SCORING_METHOD = ("Noisy-OR of traffic (congestion forecasts, or Google's traffic delay when higher; 0.9), planned demand "
                  "above usual (0.5), verified incidents and notices within 200 m, and forecast weather (storm, rain, fog, "
                  "wind); closures mark a route restricted. Unvalidated research heuristic.")
CATEGORY_NAMES = {"vip_movement": "VIP movement", "metro_construction": "Metro construction"}


def _category_name(category: str) -> str:
    return CATEGORY_NAMES.get(category, category.replace("_", " ").capitalize())


def _clock(moment: datetime) -> str:
    """Bengaluru time like 9:05 am (strftime's no-padding flags differ between systems)."""
    local = moment.astimezone(core.LOCAL_TZ)
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"


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
                # The time without traffic; with the duration it gives Google's traffic delay.
                static = float(str(route["staticDuration"]).removesuffix("s")) if route.get("staticDuration") else None
            except (TypeError, ValueError):
                raise HTTPException(502, "Google Routes returned an invalid travel time.") from None
            routes.append({"source": "google_route", "coordinates": core.decode_google_polyline(route.get("polyline", {}).get("encodedPolyline", "")),
                           "distance_m": float(route.get("distanceMeters", 0)), "duration_s": duration,
                           "static_duration_s": static if profile == "driving" and static else None})
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


def _weather_component(origin, destination, departure: datetime) -> tuple[float, dict | None, list[str]]:
    """Weather risk, the forecast used, and the reasons (thunderstorm, rain, fog, strong wind)."""
    best = None
    for point in (origin, destination):
        candidate = weather.forecast_for(point[0], point[1], departure)
        if candidate and (best is None or (candidate["time_gap_minutes"], candidate["distance_m"]) < (best["time_gap_minutes"], best["distance_m"])):
            best = candidate
    if not best:
        return 0.0, None, []
    precipitation = best.get("precipitation_mm") or 0.0
    wind = best.get("wind_kph") or 0.0
    condition = weather.label_from_code(best.get("weather_code"))
    parts, reasons = [], []
    if condition == "Storm":
        parts.append(0.5)
        reasons.append("Thunderstorm forecast")
    elif weather.is_wet(condition, best.get("precipitation_mm")):
        parts.append(0.4 if precipitation > 5 else 0.25)
        reasons.append(f"Heavy rain forecast ({precipitation:.1f} mm in the hour)" if precipitation > 5 else "Rain forecast")
    if condition == "Fog":
        parts.append(0.2)
        reasons.append("Fog forecast: lower visibility")
    if wind >= 40:
        parts.append(0.15)
        reasons.append(f"Strong wind forecast ({wind:.0f} km/h)")
    return _noisy_or(parts), best, reasons


def _traffic_delay(route: dict) -> tuple[float | None, float | None]:
    """Google's traffic delay for the trip: (extra seconds, traffic score 0..1), or (None, None)."""
    static = route.get("static_duration_s")
    if not static or not route.get("duration_s"):
        return None, None
    extra = max(0.0, route["duration_s"] - static)
    return extra, min(1.0, extra / static)


def _reasons(roads: list[dict], delay_s: float | None, live: bool, incidents: list[dict], weather_reasons: list[str],
             demand_score: float) -> list[str]:
    """The evidence behind a route's risk, in plain words, strongest first."""
    reasons = []
    if any(i["closure"] for i in incidents):
        reasons.append("An official road closure is on the route when you would reach it")
    for level, word in (("High", "Heavy"), ("Medium", "Moderate")):
        jammed = [r for r in roads if r["predicted_class"] == level]
        if jammed:
            worst = max(jammed, key=lambda r: r["expected_load"])
            others = f" and {len(jammed) - 1} more road{'s' if len(jammed) > 2 else ''}" if len(jammed) > 1 else ""
            reasons.append(f"{word} congestion expected on {worst['road']}{others} around {_clock(core.parse_utc(worst['passes_at']))}")
            break
    if delay_s is not None and delay_s >= 120:
        reasons.append(f"Traffic {'is adding' if live else 'is expected to add'} about {round(delay_s / 60)} minutes to this trip")
    for item in sorted(incidents, key=lambda i: i["distance_m"])[:3]:
        if not item["closure"]:
            kind = "official notice" if item["official"] else "verified report"
            reasons.append(f"{_category_name(item['category'])} {item['distance_m']} m from the route ({kind})")
    reasons.extend(weather_reasons)
    if demand_score >= 0.3:
        reasons.append("More trips than usual are planned on roads along this route")
    return reasons


def _risk_level(score: float) -> str:
    return "high" if score >= HIGH_RISK else "medium" if score >= 30 else "low"


def assess_route(route: dict, departure: datetime, origin, destination, live: bool = False) -> dict:
    """Score one route for one departure time. Google's traffic delay is used only for the time it was
    requested for; `live` says whether that time is now."""
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
    weather_score, weather_context, weather_reasons = _weather_component(origin, destination, departure)
    loads = [r["expected_load"] for r in roads]
    congestion = 0.6 * max(loads) + 0.4 * sum(loads) / len(loads) if loads else None
    delay_s, delay_score = _traffic_delay(route) if route.get("assessed_for") == departure else (None, None)
    traffic = max(congestion or 0.0, delay_score or 0.0) if (congestion is not None or delay_score is not None) else None
    demand_parts = []
    for road in roads:
        if road["demand_index"] is not None:
            demand_parts.append(max(0.0, min(1.0, (road["demand_index"] - 1.0) / 2)))
        elif road["planned_pcu"]:
            demand_parts.append(min(1.0, road["planned_pcu"] / demand.HOTSPOT_PCU_SCALE))
    demand_score = max(demand_parts) if demand_parts else 0.0
    incident_score = _incident_component(incidents_found)
    risk = 100 * _noisy_or([0.9 * (traffic or 0.0), 0.5 * demand_score, incident_score, weather_score])
    forecast_risk = 100 * _noisy_or([0.9 * (congestion or 0.0), 0.5 * demand_score, incident_score, weather_score])
    gaps = []
    if not roads:
        gaps.append("No registered roads with forecasts lie on this route, so congestion is not assessed.")
    elif all(r["method"] == "cold_start" for r in roads):
        gaps.append("Forecasts on this route are limited-data estimates.")
    if weather_context is None:
        gaps.append("No weather forecast is stored for this time; open a route again after weather is collected.")
    if route["source"] != "google_route":
        gaps.append("Road routing is unavailable, so this is a straight-line corridor estimate.")
    return {"risk_score": round(risk, 1), "risk_level": _risk_level(risk), "forecast_risk_score": round(forecast_risk, 1),
            "restricted": any(i["closure"] for i in incidents_found),
            "components": {"congestion": None if congestion is None else round(congestion, 3),
                           "traffic": None if traffic is None else round(traffic, 3),
                           "traffic_delay": None if delay_score is None else round(delay_score, 3),
                           "planned_demand": round(demand_score, 3), "incidents": round(incident_score, 3),
                           "weather": round(weather_score, 3)},
            "traffic_delay_s": None if delay_s is None else round(delay_s),
            "reasons": _reasons(roads, delay_s, live, incidents_found, weather_reasons, demand_score),
            "roads": roads, "incidents": incidents_found, "weather_context": weather_context, "data_gaps": gaps}


def departure_advice(route: dict, departure: datetime, origin, destination, current: dict, leave_now: bool) -> dict:
    """Compare nearby departure times for the best route; only advised when forecasts support it."""
    if not current["roads"] or all(r["method"] == "cold_start" for r in current["roads"]):
        return {"supported": False, "reason": "There are not enough road forecasts on this route to compare departure times."}
    now = datetime.now(timezone.utc)
    # Forecast evidence only: Google's traffic delay is known for the chosen time and not for the others.
    options = [{"departure_at": departure.isoformat(), "offset_minutes": 0, "risk_score": current["forecast_risk_score"]}]
    for minutes in DEPARTURE_OPTIONS_MIN:
        candidate = departure + timedelta(minutes=minutes)
        if candidate < now - timedelta(minutes=1) or (leave_now and minutes < 0):
            continue
        assessed = assess_route(route, candidate, origin, destination)
        if assessed["restricted"]:
            continue
        options.append({"departure_at": candidate.isoformat(), "offset_minutes": minutes, "risk_score": assessed["forecast_risk_score"]})
    best = min(options, key=lambda o: (o["risk_score"], abs(o["offset_minutes"])))
    improvement = current["forecast_risk_score"] - best["risk_score"]
    if best["offset_minutes"] == 0 or improvement < ADVICE_MIN_IMPROVEMENT:
        return {"supported": True, "recommendation": "keep", "options": options,
                "message": "Your chosen departure time is as good as the nearby alternatives."}
    when = "earlier" if best["offset_minutes"] < 0 else "later"
    return {"supported": True, "recommendation": "shift", "best": best, "options": options,
            "message": f"Leaving {abs(best['offset_minutes'])} minutes {when} lowers the expected route risk by {improvement:.0f} points."}


def travel_notes(scored: list[dict], leave_now: bool) -> list[dict]:
    """Advice above the route list: a safer route than the fastest, a high risk on every route, and
    how much earlier to leave because of traffic."""
    notes = []
    best = scored[0]
    fastest = min(scored, key=lambda r: r["baseline_duration_s"])
    if fastest is not best:
        extra = max(0, round((best["baseline_duration_s"] - fastest["baseline_duration_s"]) / 60))
        if fastest["restricted"] and not best["restricted"]:
            notes.append({"kind": "safer_route", "route_index": best["route_index"],
                          "message": f"The fastest route passes an official road closure. The recommended route avoids it "
                                     f"and takes about {extra} more minute{'' if extra == 1 else 's'}."})
        elif fastest["risk_score"] - best["risk_score"] >= SAFER_MIN_REDUCTION:
            notes.append({"kind": "safer_route", "route_index": best["route_index"],
                          "message": f"Safer route suggested: the fastest route has {fastest['risk_level']} risk "
                                     f"({fastest['risk_score']:.0f}/100); the recommended one lowers it to {best['risk_score']:.0f}/100 "
                                     f"for about {extra} more minute{'' if extra == 1 else 's'}."})
    if best["risk_score"] >= HIGH_RISK:
        reasons = "; ".join(best["reasons"][:2]) or "several risk factors"
        notes.append({"kind": "high_risk", "message": f"High travel risk even on the recommended route ({reasons}). "
                                                      "Consider another departure time, or postpone the trip if you can."})
    delay = best.get("traffic_delay_s")
    if delay is not None and delay >= LEAVE_EARLY_MIN_DELAY_S:
        minutes_late = round(delay / 60)
        notes.append({"kind": "leave_early", "delay_minutes": minutes_late,
                      "message": (f"Traffic is adding about {minutes_late} minutes right now. If you need to arrive at a set time, "
                                  f"leave {minutes_late} minutes earlier than usual." if leave_now else
                                  f"Traffic is expected to add about {minutes_late} minutes. To arrive on time, leave about "
                                  f"{minutes_late} minutes earlier than you would on a clear road.")})
    return notes


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
        route["assessed_for"] = departure  # the time Google's travel times are for
        assessed = assess_route(route, departure, origin, destination, live=leave_now)
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
    notes = travel_notes(scored, leave_now)
    route_id = str(uuid.uuid4())
    # Keep the evidence behind each score for later review. Origin and destination are
    # personal travel details, so they are not stored with the assessment.
    record = {"profile": profile, "departure_at": departure.isoformat(), "leave_now": leave_now, "provider": provider,
              "routes": [{k: item[k] for k in ("route_index", "distance_m", "baseline_duration_s", "risk_score", "restricted",
                                              "components", "rank_cost", "data_gaps")} |
                         {"roads": [{k: r[k] for k in ("segment_id", "passes_at", "predicted_class", "method", "planned_pcu")} for r in item["roads"]],
                          "incidents": [i["incident_id"] for i in item["incidents"]],
                          "weather_forecast_for": (item["weather_context"] or {}).get("forecast_for")} for item in scored],
              "departure_advice": best["departure_advice"], "travel_notes": notes}
    with store.connect() as db:
        db.execute("""INSERT INTO route_assessments(route_id,issue_time,provider,source_type,scoring_method,route_results_json)
          VALUES(?,?,?,?,?,?)""", (route_id, iso_now(), provider, scored[0]["source_type"], SCORING_METHOD, json.dumps(record, default=str)))
    return {"route_id": route_id, "provider": provider, "routes": scored, "scoring_method": SCORING_METHOD,
            "departure_at": departure.isoformat(), "leave_now": leave_now, "travel_notes": notes,
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
