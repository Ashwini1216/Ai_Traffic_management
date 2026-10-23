"""Shared settings and helpers: paths, Bengaluru time blocks, geography, and HTTP."""
from __future__ import annotations

import json
import math
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import certifi
from dotenv import load_dotenv
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("FLOWSTATE_DATA_DIR") or ROOT / "data")
MODELS_DIR = Path(os.getenv("FLOWSTATE_MODELS_DIR") or ROOT / "models")

# Bengaluru observes IST all year (no daylight saving), so a fixed offset is exact
# and avoids depending on the tzdata package on Windows.
LOCAL_TZ = timezone(timedelta(hours=5, minutes=30))
TIME_BLOCKS = ["Midnight", "Morning", "Daytime", "Noon", "Evening", "Night"]
# The historical dataset names six blocks but does not define their hours. These start
# hours (IST) are an assumption used to place timestamped observations, forecasts and
# trips into blocks; set TIME_BLOCK_STARTS in .env if the dataset defines them differently.
DEFAULT_BLOCK_STARTS = "Midnight=0,Morning=5,Daytime=10,Noon=12,Evening=16,Night=21"
# Requests outside this box are not treated as Bengaluru locations.
BENGALURU_BOUNDS = {"south": 12.70, "north": 13.25, "west": 77.30, "east": 77.90}
EARTH_RADIUS_M = 6371000


def _parse_block_starts(text: str) -> dict[str, int]:
    starts: dict[str, int] = {}
    for part in text.split(","):
        name, _, hour = part.partition("=")
        starts[name.strip()] = int(hour.strip())
    hours = list(starts.values())
    if list(starts) != TIME_BLOCKS or hours[0] != 0 or hours != sorted(set(hours)) or hours[-1] > 23:
        raise ValueError("TIME_BLOCK_STARTS must list " + ", ".join(TIME_BLOCKS)
                         + " in order with increasing start hours from 0 to 23, e.g. " + DEFAULT_BLOCK_STARTS)
    return starts


BLOCK_START_HOURS = _parse_block_starts(os.getenv("TIME_BLOCK_STARTS") or DEFAULT_BLOCK_STARTS)


# ---------- time ----------

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_iso(value: datetime | None) -> str | None:
    """Timestamps are compared as text in SQL, so they must all be stored in UTC."""
    return value.astimezone(timezone.utc).isoformat() if value else None


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def local_today() -> date:
    return datetime.now(LOCAL_TZ).date()


def block_for(moment: datetime) -> tuple[date, str]:
    """The IST date and time block containing a moment."""
    local = moment.astimezone(LOCAL_TZ)
    block = TIME_BLOCKS[0]
    for name in TIME_BLOCKS:
        if local.hour >= BLOCK_START_HOURS[name]:
            block = name
    return local.date(), block


def block_bounds(day: date, block: str) -> tuple[datetime, datetime]:
    """UTC start and end of a time block on an IST date."""
    index = TIME_BLOCKS.index(block)
    start = datetime.combine(day, time(BLOCK_START_HOURS[block]), LOCAL_TZ)
    if index + 1 < len(TIME_BLOCKS):
        end = datetime.combine(day, time(BLOCK_START_HOURS[TIME_BLOCKS[index + 1]]), LOCAL_TZ)
    else:
        end = datetime.combine(day + timedelta(days=1), time(0), LOCAL_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def next_block(day: date, block: str, steps: int = 1) -> tuple[date, str]:
    index = TIME_BLOCKS.index(block) + steps
    return day + timedelta(days=index // len(TIME_BLOCKS)), TIME_BLOCKS[index % len(TIME_BLOCKS)]


def blocks_between(start: tuple[date, str], end: tuple[date, str]) -> int:
    """Number of block steps from start to end (negative if end is earlier)."""
    return ((end[0] - start[0]).days * len(TIME_BLOCKS)
            + TIME_BLOCKS.index(end[1]) - TIME_BLOCKS.index(start[1]))


# ---------- geography ----------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p1 - p2) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon1 - lon2) / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def distance_to_polyline_m(latitude: float, longitude: float, coordinates: list[list[float]]) -> float:
    """Distance in metres from a point to a [lon, lat] polyline, measured to its segments
    (not only its vertices, which can be hundreds of metres apart on straight roads)."""
    return locate_on_polyline(latitude, longitude, coordinates)[0]


def locate_on_polyline(latitude: float, longitude: float, coordinates: list[list[float]]) -> tuple[float, float]:
    """(distance in metres to the polyline, distance in metres along it to the nearest point)."""
    if not coordinates:
        return float("inf"), 0.0
    if len(coordinates) == 1:
        lon, lat = coordinates[0]
        return haversine_m(latitude, longitude, lat, lon), 0.0
    # Flat projection centred on the point; accurate for the short distances checked here.
    metres_per_degree = EARTH_RADIUS_M * math.pi / 180
    scale_x = metres_per_degree * math.cos(math.radians(latitude))
    points = [((lon - longitude) * scale_x, (lat - latitude) * metres_per_degree) for lon, lat in coordinates]
    best, best_along, travelled = float("inf"), 0.0, 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        dx, dy = x2 - x1, y2 - y1
        length_sq = dx * dx + dy * dy
        t = 0.0 if length_sq == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length_sq))
        distance = math.hypot(x1 + t * dx, y1 + t * dy)
        if distance < best:
            best, best_along = distance, travelled + t * math.sqrt(length_sq)
        travelled += math.sqrt(length_sq)
    return best, best_along


def polyline_length_m(coordinates: list[list[float]]) -> float:
    return sum(haversine_m(a[1], a[0], b[1], b[0]) for a, b in zip(coordinates, coordinates[1:]))


def in_bengaluru(latitude: float, longitude: float) -> bool:
    bounds = BENGALURU_BOUNDS
    return bounds["south"] <= latitude <= bounds["north"] and bounds["west"] <= longitude <= bounds["east"]


def decode_google_polyline(encoded: str) -> list[list[float]]:
    coordinates: list[list[float]] = []
    index = latitude = longitude = 0
    while index < len(encoded):
        values = []
        for _ in range(2):
            result = shift = 0
            while True:
                if index >= len(encoded):
                    raise HTTPException(502, "Google Routes returned an invalid route shape.")
                value = ord(encoded[index]) - 63
                index += 1
                result |= (value & 0x1f) << shift
                shift += 5
                if value < 0x20:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        latitude += values[0]
        longitude += values[1]
        coordinates.append([longitude / 1e5, latitude / 1e5])
    return coordinates


# ---------- HTTP ----------

# Trust the operating system's certificates plus certifi's bundle: python.org builds for
# macOS ship without root certificates, and Windows networks may add their own authorities.
HTTPS_CONTEXT = ssl.create_default_context()
HTTPS_CONTEXT.load_verify_locations(cafile=certifi.where())


def google_key() -> str:
    return os.getenv("GOOGLE_MAPS_API_KEY", "").strip()


def google_configured() -> bool:
    key = google_key()
    return bool(key) and not key.startswith("REPLACE_")


def external_json(url: str, user_agent: str = "BengaluruTrafficMonitor/0.2"):
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20, context=HTTPS_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "The configured external data provider could not be reached.") from exc


def google_post_json(url: str, payload: dict):
    key = google_key()
    if not key:
        raise HTTPException(503, "Google Maps services are not configured. Add GOOGLE_MAPS_API_KEY to the project .env file.")
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline",
    })
    try:
        with urllib.request.urlopen(request, timeout=25, context=HTTPS_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Google explains a refusal (invalid key, API not enabled, billing, bad request) in the body.
        try:
            reason = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
        except (ValueError, OSError):
            reason = ""
        raise HTTPException(502, f"Google Routes refused the request: {reason}" if reason else
                            "Google Routes refused the request. Check the server key, billing, and Routes API access.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "Google Routes could not be reached. Check the internet connection and try again.") from exc


GEOCODE_BOUNDS = "12.75,77.30|13.20,77.85"   # biases address search towards Bengaluru
GEOCODE_ERRORS = {
    "OVER_DAILY_LIMIT": (429, "The Google key has reached its address-search limit, or billing is not set up for it."),
    "OVER_QUERY_LIMIT": (429, "The Google key has reached its address-search limit; try again later."),
    "INVALID_REQUEST": (422, "Google could not read this address; try a more specific one."),
}


def google_geocode(address: str) -> list[dict]:
    """Google Geocoding results for an address near Bengaluru; [] when nothing matches."""
    params = urllib.parse.urlencode({"address": address, "region": "in", "bounds": GEOCODE_BOUNDS, "key": google_key()})
    data = external_json("https://maps.googleapis.com/maps/api/geocode/json?" + params, "FlowStateTrafficMonitor/1.0")
    status = data.get("status")
    if status == "OK":
        return data.get("results", [])
    if status == "ZERO_RESULTS":
        return []
    if status == "REQUEST_DENIED":
        # Google names the cause: billing not enabled, Geocoding API not enabled, or a key restriction.
        raise HTTPException(502, "Google refused the address search: " + (
            data.get("error_message") or "check that the Geocoding API is enabled for the server key and billing is set up."))
    code, message = GEOCODE_ERRORS.get(status, (502, "Google's address search failed; try again shortly."))
    raise HTTPException(code, message)
