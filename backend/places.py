"""Search for any place or address in Bengaluru by name.

Google Places (Text Search) is used when a Google key is configured. When there is no key,
when Google refuses the request, or when it finds nothing, OpenStreetMap's Nominatim service is
used instead. Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/)
allows at most one request per second, requires an identifying User-Agent and visible
attribution, and does not allow search-as-you-type. So searches are only sent when a person
submits one, all of them go through this server, at most one per second, and results are
cached. Google results are not cached, as Google's terms do not allow it.

PLACE_SEARCH in .env chooses the service: auto (default), google, openstreetmap, or off.
NOMINATIM_URL can point at another Nominatim server.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.parse

from fastapi import HTTPException

import core

USER_AGENT = "FlowStateTrafficMonitor/1.0 (+https://github.com/Ashwini1216/Ai_Traffic_management)"
GOOGLE_FIELDS = "places.displayName,places.formattedAddress,places.location,places.types"
GOOGLE_ATTRIBUTION = "Search results from Google"
OSM_ATTRIBUTION = "Search results © OpenStreetMap contributors"
OSM_MIN_INTERVAL_S = 1.1
CACHE_SECONDS = 24 * 3600
CACHE_MAX_ENTRIES = 500

_osm_lock = threading.Lock()
_osm_last_request = [0.0]
_osm_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}


def provider_setting() -> str:
    return (os.getenv("PLACE_SEARCH") or "auto").strip().lower()


def _google(query: str, limit: int) -> list[dict]:
    box = core.BENGALURU_BOUNDS
    payload = {"textQuery": query, "maxResultCount": limit, "languageCode": "en", "regionCode": "IN",
               "locationRestriction": {"rectangle": {"low": {"latitude": box["south"], "longitude": box["west"]},
                                                     "high": {"latitude": box["north"], "longitude": box["east"]}}}}
    data = core.google_post_json("https://places.googleapis.com/v1/places:searchText", payload,
                                 field_mask=GOOGLE_FIELDS, service="Google place search")
    return [{"name": place.get("displayName", {}).get("text"), "display_name": place.get("formattedAddress"),
             "latitude": float(place["location"]["latitude"]), "longitude": float(place["location"]["longitude"]),
             "place_type": (place.get("types") or [None])[0]}
            for place in data.get("places", []) if place.get("location")]


def _openstreetmap(query: str, limit: int) -> list[dict]:
    key = (query.casefold(), limit)
    cached = _osm_cache.get(key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    box = core.BENGALURU_BOUNDS
    params = urllib.parse.urlencode({
        "q": query, "format": "jsonv2", "limit": limit, "countrycodes": "in", "bounded": 1, "accept-language": "en",
        "viewbox": f"{box['west']},{box['north']},{box['east']},{box['south']}",
    })
    url = (os.getenv("NOMINATIM_URL") or "https://nominatim.openstreetmap.org/search") + "?" + params
    with _osm_lock:  # one request at a time, at most one per second
        wait = OSM_MIN_INTERVAL_S - (time.monotonic() - _osm_last_request[0])
        if wait > 0:
            time.sleep(wait)
        try:
            data = core.external_json(url, USER_AGENT)
        finally:
            _osm_last_request[0] = time.monotonic()
    results = [{"name": item.get("name") or str(item.get("display_name", "")).split(",")[0],
                "display_name": item.get("display_name"), "latitude": float(item["lat"]), "longitude": float(item["lon"]),
                "place_type": item.get("type")}
               for item in (data if isinstance(data, list) else []) if item.get("lat") and item.get("lon")]
    if len(_osm_cache) >= CACHE_MAX_ENTRIES:
        _osm_cache.pop(min(_osm_cache, key=lambda k: _osm_cache[k][0]))
    _osm_cache[key] = (time.time(), results)
    return results


def _answer(query: str, results: list[dict], provider: str, attribution: str) -> dict:
    inside = [r for r in results if core.in_bengaluru(r["latitude"], r["longitude"])]
    for result in inside:
        result["source_type"] = provider
    return {"query": query, "results": inside, "provider": provider, "attribution": attribution}


def search(query: str, limit: int = 5) -> dict:
    """Places in Bengaluru matching `query`, with the service used and the attribution to show."""
    mode, text = provider_setting(), query.strip()
    if mode == "off":
        raise HTTPException(503, "Place search is switched off on this server (PLACE_SEARCH=off in .env).")
    if mode not in ("auto", "google", "openstreetmap"):
        raise HTTPException(503, "PLACE_SEARCH in .env must be auto, google, openstreetmap, or off.")
    if mode == "google" and not core.google_configured():
        raise HTTPException(503, "Google place search needs GOOGLE_MAPS_API_KEY in the project .env file.")
    if mode in ("auto", "google") and core.google_configured():
        try:
            answer = _answer(text, _google(text, limit), "google_places", GOOGLE_ATTRIBUTION)
            if answer["results"] or mode == "google":
                return answer
        except HTTPException:
            if mode == "google":
                raise
    return _answer(text, _openstreetmap(text, limit), "openstreetmap", OSM_ATTRIBUTION)


def first_location(query: str) -> tuple[float, float] | None:
    results = search(query, limit=1)["results"]
    return (results[0]["latitude"], results[0]["longitude"]) if results else None
