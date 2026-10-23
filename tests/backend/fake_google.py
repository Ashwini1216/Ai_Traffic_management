"""Offline stand-ins for Google Maps Platform and Open-Meteo, so tests never use a real key.

install(core) replaces the two functions every module calls for external data. Routes are
built from the requested points (a direct route and a curved alternative); geocoding answers
from a small table; weather is a fixed 48-hour forecast with rain two to four hours ahead.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

CALLS: list[tuple[str, dict]] = []
# The address carries markup so the browser tests can check popups show it as plain text.
INDIRANAGAR = {"formatted_address": "<img src=x onerror=\"window.__xss=1\">Indiranagar, Bengaluru",
               "geometry": {"location": {"lat": 12.9719, "lng": 77.6412}}, "types": ["sublocality"]}


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


def routes(url: str, payload: dict) -> dict:
    CALLS.append(("routes", payload))
    o, d = payload["origin"]["location"]["latLng"], payload["destination"]["location"]["latLng"]
    start, end = (o["latitude"], o["longitude"]), (d["latitude"], d["longitude"])
    found = []
    for bend, seconds in ((0.0, 900), (0.012, 1140)):
        points = [(start[0] + (end[0] - start[0]) * t + bend * math.sin(math.pi * t), start[1] + (end[1] - start[1]) * t)
                  for t in (i / 20 for i in range(21))]
        length = sum(_distance_m(a, b) for a, b in zip(points, points[1:]))
        found.append({"duration": f"{seconds}s", "distanceMeters": round(length), "polyline": {"encodedPolyline": encode_polyline(points)}})
    return {"routes": found if payload.get("computeAlternativeRoutes") else found[:1]}


def _geocode(address: str) -> dict:
    text = address.lower()
    if "nowhere" in text:
        return {"status": "ZERO_RESULTS", "results": []}
    if "denied" in text:
        return {"status": "REQUEST_DENIED", "error_message": "You must enable Billing on the Google Cloud Project."}
    return {"status": "OK", "results": [INDIRANAGAR]}


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


def external_json(url: str, user_agent: str | None = None) -> dict:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if parsed.netloc == "maps.googleapis.com" and parsed.path.endswith("/geocode/json"):
        CALLS.append(("geocode", {"address": query.get("address", [""])[0]}))
        return _geocode(query.get("address", [""])[0])
    if parsed.netloc == "api.open-meteo.com":
        CALLS.append(("weather", {"latitude": query["latitude"][0], "longitude": query["longitude"][0]}))
        return _weather(query)
    raise AssertionError(f"Unexpected external request during tests: {parsed.netloc}{parsed.path}")


ORIGINALS: dict = {}


def install(core) -> None:
    """Swap in the fakes; the real functions stay available in ORIGINALS for error-handling tests."""
    ORIGINALS.setdefault("google_post_json", core.google_post_json)
    ORIGINALS.setdefault("external_json", core.external_json)
    core.google_post_json = routes
    core.external_json = external_json
