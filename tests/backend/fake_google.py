"""Offline stand-ins for Google Maps Platform, OpenStreetMap search and Open-Meteo, so tests
never use a real key or a public service.

install(core) replaces the two functions every module calls for external data. Routes are built
from the requested points (a direct route and a curved alternative), with the traffic delay set
in TRAFFIC; place searches answer from a small table; weather is a fixed 48-hour forecast with
rain two to four hours ahead.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

CALLS: list[tuple[str, dict]] = []
# Travel time with traffic divided by the time without traffic, for each fake route.
TRAFFIC = {"direct": 1.15, "alternative": 1.05}
DEFAULT_TRAFFIC = dict(TRAFFIC)
# The name carries markup so the browser tests can check popups show it as plain text.
INDIRANAGAR = {"displayName": {"text": "<img src=x onerror=\"window.__xss=1\">Indiranagar"},
               "formattedAddress": "<img src=x onerror=\"window.__xss=1\">Indiranagar, Bengaluru",
               "location": {"latitude": 12.9719, "longitude": 77.6412}, "types": ["sublocality"]}
AVALAHALLI = {"displayName": {"text": "Avalahalli"}, "formattedAddress": "Avalahalli, Banashankari, Bengaluru, Karnataka",
              "location": {"latitude": 12.9457, "longitude": 77.5440}, "types": ["sublocality"]}
DELHI = {"displayName": {"text": "Connaught Place"}, "formattedAddress": "Connaught Place, New Delhi",
         "location": {"latitude": 28.6315, "longitude": 77.2167}, "types": ["sublocality"]}


def encode_polyline(points: list[tuple[float, float]]) -> str:
    out, prev_lat, prev_lng = [], 0, 0
    for lat, lng in points:
        lat_e5, lng_e5 = round(lat * 1e5), round(lng * 1e5)
        for value, prev in ((lat_e5, prev_lat), (lng_e5, prev_lng)):
            delta = value - prev
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 0x20:
                out.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            out.append(chr(delta + 63))
        prev_lat, prev_lng = lat_e5, lng_e5
    return "".join(out)


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def routes(payload: dict) -> dict:
    CALLS.append(("routes", payload))
    o, d = payload["origin"]["location"]["latLng"], payload["destination"]["location"]["latLng"]
    start, end = (o["latitude"], o["longitude"]), (d["latitude"], d["longitude"])
    found = []
    for name, bend, free_flow_s in (("direct", 0.0, 780), ("alternative", 0.012, 1080)):
        points = [(start[0] + (end[0] - start[0]) * t + bend * math.sin(math.pi * t), start[1] + (end[1] - start[1]) * t)
                  for t in (i / 20 for i in range(21))]
        length = sum(_distance_m(a, b) for a, b in zip(points, points[1:]))
        found.append({"duration": f"{round(free_flow_s * TRAFFIC[name])}s", "staticDuration": f"{free_flow_s}s",
                      "distanceMeters": round(length), "polyline": {"encodedPolyline": encode_polyline(points)}})
    return {"routes": found if payload.get("computeAlternativeRoutes") else found[:1]}


def places(payload: dict) -> dict:
    text = payload["textQuery"].lower()
    CALLS.append(("places", {"query": payload["textQuery"]}))
    if "denied" in text:
        raise HTTPException(502, "Google place search refused the request: Places API (New) has not been used in this project.")
    if "nowhere" in text:
        return {}
    if "delhi" in text:
        return {"places": [DELHI]}
    return {"places": [AVALAHALLI if "avalahalli" in text else INDIRANAGAR]}


def post_json(url: str, payload: dict, field_mask: str | None = None, service: str = "Google Routes") -> dict:
    if "places.googleapis.com" in url:
        return places(payload)
    return routes(payload)


def _openstreetmap(query: dict) -> list:
    text = query.get("q", [""])[0].lower()
    CALLS.append(("openstreetmap", {"query": text}))
    if "nowhere" in text:
        return []
    if "delhi" in text:
        return [{"name": "Connaught Place", "display_name": "Connaught Place, New Delhi, India", "lat": "28.6315", "lon": "77.2167"}]
    return [{"name": "Avalahalli", "display_name": "Avalahalli, Bengaluru South, Karnataka, India", "lat": "12.9460",
             "lon": "77.5450", "type": "suburb"}]


def _weather(query: dict) -> dict:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hours = [now + timedelta(hours=h) for h in range(48)]
    rain = [2.5 if 2 <= h <= 4 else 0.0 for h in range(48)]
    return {
        "latitude": float(query["latitude"][0]), "longitude": float(query["longitude"][0]),
        "current": {"time": now.strftime("%Y-%m-%dT%H:%M"), "temperature_2m": 27.0, "precipitation": 0.0,
                    "wind_speed_10m": 9.0, "weather_code": 1},
        "hourly": {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in hours], "temperature_2m": [26.0] * 48,
                   "precipitation": rain, "wind_speed_10m": [10.0] * 48,
                   "weather_code": [61 if r else 1 for r in rain]},
    }


def external_json(url: str, user_agent: str | None = None):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "nominatim" in parsed.netloc:
        assert user_agent and "FlowState" in user_agent, "OpenStreetMap requests must identify the app"
        return _openstreetmap(query)
    if parsed.netloc == "api.open-meteo.com":
        CALLS.append(("weather", {"latitude": query["latitude"][0], "longitude": query["longitude"][0]}))
        return _weather(query)
    raise AssertionError(f"Unexpected external request during tests: {parsed.netloc}{parsed.path}")


ORIGINALS: dict = {}


def install(core) -> None:
    """Swap in the fakes; the real functions stay available in ORIGINALS for error-handling tests."""
    ORIGINALS.setdefault("google_post_json", core.google_post_json)
    ORIGINALS.setdefault("external_json", core.external_json)
    core.google_post_json = post_json
    core.external_json = external_json
