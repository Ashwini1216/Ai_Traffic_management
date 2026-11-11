"""OpenStreetMap road geometry via a configurable OSRM-compatible service.

The default public service is for a small research demo. Configure OSRM_BASE_URL
with a dedicated/self-hosted instance for larger use. Durations are road-profile
estimates, not live traffic. Failed road routing never becomes a straight line.
"""
from __future__ import annotations

import math
import os
import threading
import time
from urllib.parse import urlencode

from fastapi import HTTPException

import core

_LOCK = threading.Lock()
_CACHE: dict = {}
CACHE_SECONDS = 300
USER_AGENT = "FlowStateTrafficMonitor/1.0 (+https://github.com/Ashwini1216/Ai_Traffic_management)"


def provider() -> str:
    choice = (os.getenv("ROUTING_PROVIDER") or "auto").strip().lower()
    if choice not in {"auto", "google", "openstreetmap", "local"}:
        raise HTTPException(503, "ROUTING_PROVIDER must be auto, google, openstreetmap or local.")
    return ("google" if core.google_configured() else "openstreetmap") if choice == "auto" else choice


def _point(point):
    if len(point) != 2 or not all(math.isfinite(float(v)) for v in point) or not core.in_bengaluru(*point):
        raise HTTPException(422, "Both places must be within the supported Bengaluru area.")


def candidates(origin, destination, profile="driving", via=None, departure=None) -> list[dict]:
    _point(origin)
    _point(destination)
    if profile=='driving' and not via:
        import local_routing
        local=local_routing.routes(origin,destination,departure)
        if local:
            return local
        if provider()=='local':
            message = 'No open local road route is available for these points. Local graph coverage is limited to the installed pilot.'
            raise HTTPException(409 if local==[] else 422, {'message':message,'code':'no_open_route'} if local==[] else message)
    points = [origin, *(via or []), destination]
    for point in points:
        _point(point)
    configured_profile = os.getenv("OSRM_PROFILE_" + profile.upper(), "driving" if profile == "driving" else "")
    if not configured_profile or not configured_profile.replace("_", "").isalnum():
        raise HTTPException(422, f"An OpenStreetMap {profile} routing profile is not configured.")
    base = (os.getenv("OSRM_BASE_URL") or "https://router.project-osrm.org").rstrip("/")
    if not base.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise HTTPException(503, "OSRM_BASE_URL must use HTTPS or a local routing server.")
    coordinates = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    url = f"{base}/route/v1/{configured_profile}/{coordinates}?" + urlencode({
        "alternatives": "3" if len(points) == 2 else "false", "geometries": "geojson",
        "overview": "full", "steps": "false", "radiuses": ";".join(["500"] * len(points)),
    })
    with _LOCK:
        cached = _CACHE.get(url)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return [dict(r) for r in cached[1]]
    data = core.external_json(url, USER_AGENT)
    if not isinstance(data, dict) or data.get("code") != "Ok":
        if isinstance(data, dict) and data.get("code") in {"NoRoute", "NoSegment"}:
            raise HTTPException(404, "No drivable road route was found for these places. Choose a nearby road point.")
        raise HTTPException(502, "The OpenStreetMap routing service returned an invalid response.")
    routes, seen = [], set()
    for item in data.get("routes", []):
        try:
            geometry = item["geometry"]["coordinates"]
            distance, duration = float(item["distance"]), float(item["duration"])
            if len(geometry) < 2 or not all(len(p) == 2 and all(math.isfinite(float(v)) for v in p) for p in geometry):
                continue
            if not math.isfinite(distance + duration) or distance <= 0 or duration <= 0:
                continue
            fingerprint = tuple((round(p[0], 5), round(p[1], 5)) for p in geometry)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            routes.append({"source": "osm_route", "coordinates": geometry, "distance_m": distance,
                           "duration_s": duration, "static_duration_s": None,
                           "algorithm": "OSRM road-network routing", "attribution": "© OpenStreetMap contributors"})
        except (KeyError, TypeError, ValueError):
            continue
    if not routes:
        raise HTTPException(502, "The routing service returned no valid road geometry.")
    with _LOCK:
        if len(_CACHE) >= 128:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[url] = (time.monotonic(), routes)
    return [dict(r) for r in routes]


def encode_polyline(coordinates) -> str:
    """Store road geometry in the existing portable encoded-polyline column."""
    result, previous = [], [0, 0]
    for lon, lat in coordinates:
        for i, value in enumerate((lat, lon)):
            value = round(value * 1e5)
            delta = value - previous[i]
            previous[i] = value
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 32:
                result.append(chr((32 | (delta & 31)) + 63))
                delta >>= 5
            result.append(chr(delta + 63))
    return "".join(result)
