"""Road-segment and zone registry, and the data-coverage registry.

Map coverage (a road can be shown and matched to routes) is kept separate from traffic-data
coverage (enough trustworthy observations exist). The coverage registry records, per road
segment, how much history exists and which prediction method that history supports.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import core
import places
import store
from core import TIME_BLOCKS, iso_now
from security import authority_token

router = APIRouter()

SEQUENCE_LENGTH = 6           # consecutive blocks the sequence model reads
MAX_HORIZON_BLOCKS = 3        # furthest block ahead the sequence model forecasts
MIN_PATTERN_RECORDS = 30      # labelled records for a location-specific pattern estimate
MIN_BLOCK_RECORDS = 3         # labelled records per time block for it to count as covered
MIN_COVERED_BLOCKS = 4        # time blocks that must be covered for a pattern estimate

# Approximate public reference points for the named Bengaluru roads and junctions in the
# project dataset. They only place each road on the map for zone views and route matching;
# they are not traffic measurements. Authorities can correct them or geocode them with Google.
REFERENCE_POINTS = {
    ("indiranagar", "100 feet road"): (12.9716, 77.6412),
    ("indiranagar", "cmh road"): (12.9784, 77.6440),
    ("whitefield", "marathahalli bridge"): (12.9569, 77.7011),
    ("whitefield", "itpl main road"): (12.9850, 77.7300),
    ("koramangala", "sony world junction"): (12.9363, 77.6265),
    ("koramangala", "sarjapur road"): (12.9238, 77.6501),
    ("mg road", "trinity circle"): (12.9727, 77.6196),
    ("mg road", "anil kumble circle"): (12.9749, 77.6008),
    ("jayanagar", "jayanagar 4th block"): (12.9297, 77.5831),
    ("jayanagar", "south end circle"): (12.9381, 77.5802),
    ("hebbal", "hebbal flyover"): (13.0356, 77.5971),
    ("hebbal", "ballari road"): (13.0250, 77.5893),
    ("yeshwanthpur", "yeshwanthpur circle"): (13.0236, 77.5497),
    ("yeshwanthpur", "tumkur road"): (13.0300, 77.5389),
    ("electronic city", "silk board junction"): (12.9176, 77.6233),
    ("electronic city", "hosur road"): (12.8458, 77.6604),
}
NAME_ALIASES = {"bellary road": "ballari road", "m g road": "mg road", "itpl road": "itpl main road"}
# Checked in order; the first match names the road type (authorities can override it).
ROAD_TYPE_PATTERNS = [
    ("elevated", r"flyover|bridge|elevated"),
    ("junction", r"junction|circle|signal|\bcross\b"),
    ("highway", r"hosur road|tumkur road|ballari road|bellary road|old madras road|mysore road|\bnh\s?\d+|highway"),
    ("arterial", r"main road|feet road|ring road|\broad\b"),
    ("local", r"block|layout|stage|street"),
]
ROAD_TYPES = ["junction", "arterial", "highway", "elevated", "local", "unknown"]


def _normalize(name: str) -> str:
    text = re.sub(r"[.\-_/]", " ", name.casefold())
    text = re.sub(r"\s+", " ", text).strip()
    return NAME_ALIASES.get(text, text)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _normalize(name)).strip("-") or "segment"


def segment_id_for(area: str, road: str) -> str:
    digest = hashlib.sha1(f"{area}\x1f{road}".encode("utf-8")).hexdigest()[:6]
    return f"{_slug(area)}--{_slug(road)}-{digest}"


def zone_id_for(area: str) -> str:
    return _slug(area)


def infer_road_type(road: str) -> str:
    name = _normalize(road)
    for road_type, pattern in ROAD_TYPE_PATTERNS:
        if re.search(pattern, name):
            return road_type
    return "unknown"


def reference_point(area: str, road: str) -> tuple[float, float] | None:
    return REFERENCE_POINTS.get((_normalize(area), _normalize(road)))


# ---------- registry maintenance ----------

def ensure_segment(area: str, road: str) -> str:
    """Return the segment id for an area/road, registering it if it is new.

    Call it outside any open transaction: it writes through its own connections.
    """
    segment_id = segment_id_for(area, road)
    if store.one("SELECT segment_id FROM road_segments WHERE segment_id=?", (segment_id,)):
        return segment_id
    now = iso_now()
    point = reference_point(area, road)
    with store.connect() as db:
        db.execute("""INSERT INTO road_segments(segment_id,area,road,zone_id,latitude,longitude,location_source,road_type,
          road_type_source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(segment_id) DO NOTHING""",
                   (segment_id, area, road, zone_id_for(area), point[0] if point else None, point[1] if point else None,
                    "reference_point" if point else "unmapped", infer_road_type(road), "name_inferred", now, now))
    _ensure_zone(area)
    refresh_zone_geometry()
    invalidate_coverage()
    return segment_id


def _ensure_zone(area: str) -> None:
    with store.connect() as db:
        db.execute("""INSERT INTO zones(zone_id,name,latitude,longitude,radius_m,location_source,updated_at)
          VALUES(?,?,?,?,?,?,?) ON CONFLICT(zone_id) DO NOTHING""", (zone_id_for(area), area, None, None, None, "derived", iso_now()))


def sync_segments() -> int:
    """Register every area/road that appears in observations or the camera registry."""
    known = {(row["area"], row["road"]) for row in store.rows("SELECT area,road FROM road_segments")}
    pairs = store.rows("""SELECT DISTINCT area,road FROM traffic_observations
      UNION SELECT DISTINCT area,road FROM cameras WHERE area IS NOT NULL AND road IS NOT NULL""")
    added = 0
    for pair in pairs:
        if pair["area"] and pair["road"] and (pair["area"], pair["road"]) not in known:
            ensure_segment(pair["area"], pair["road"])
            added += 1
    for area in {row["area"] for row in store.rows("SELECT DISTINCT area FROM road_segments")}:
        _ensure_zone(area)
    refresh_zone_geometry()
    invalidate_coverage()
    return added


def refresh_zone_geometry() -> None:
    """Derive each zone's centre and radius from its mapped segments (unless an authority set them)."""
    segments = store.rows("SELECT zone_id,latitude,longitude FROM road_segments WHERE latitude IS NOT NULL")
    by_zone: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in segments:
        by_zone[row["zone_id"]].append((row["latitude"], row["longitude"]))
    with store.connect() as db:
        for zone in db.execute("SELECT zone_id FROM zones WHERE location_source='derived'").fetchall():
            points = by_zone.get(zone["zone_id"])
            if not points:
                db.execute("UPDATE zones SET latitude=NULL,longitude=NULL,radius_m=NULL WHERE zone_id=?", (zone["zone_id"],))
                continue
            lat = sum(p[0] for p in points) / len(points)
            lon = sum(p[1] for p in points) / len(points)
            reach = max(core.haversine_m(lat, lon, p[0], p[1]) for p in points)
            db.execute("UPDATE zones SET latitude=?,longitude=?,radius_m=?,updated_at=? WHERE zone_id=?",
                       (lat, lon, round(max(1500.0, reach + 800.0)), iso_now(), zone["zone_id"]))


# ---------- coverage registry ----------

_COVERAGE_CACHE: dict = {"key": None, "at": 0.0, "value": None}
_COVERAGE_LOCK = threading.Lock()


def invalidate_coverage() -> None:
    with _COVERAGE_LOCK:
        _COVERAGE_CACHE["key"] = None


def _state_key() -> tuple:
    """Changes whenever observations are added or reviewed, or segments are edited."""
    row = store.one("SELECT COUNT(*) AS n, MAX(id) AS last_id, MAX(verified_at) AS reviewed FROM traffic_observations")
    segments = store.one("SELECT COUNT(*) AS n, MAX(updated_at) AS changed FROM road_segments")
    return (row["n"], row["last_id"], row["reviewed"], segments["n"], segments["changed"])


def coverage_registry(now: datetime | None = None) -> dict[str, dict]:
    """Coverage statistics for every registered segment, keyed by segment id."""
    now = now or datetime.now(timezone.utc)
    key = _state_key()
    with _COVERAGE_LOCK:
        cached = _COVERAGE_CACHE
        if cached["key"] == key and time.monotonic() - cached["at"] < 300:
            base = cached["value"]
        else:
            base = None
    if base is None:
        base = _compute_coverage()
        with _COVERAGE_LOCK:
            _COVERAGE_CACHE.update(key=key, at=time.monotonic(), value=base)
    return {segment_id: _with_eligibility(stats, now) for segment_id, stats in base.items()}


def _compute_coverage() -> dict[str, dict]:
    segments = store.rows("SELECT * FROM road_segments ORDER BY area,road")
    by_pair = {(s["area"], s["road"]): s for s in segments}
    rows = store.rows("""SELECT area,road,observed_date,time_block,traffic_volume,average_speed,travel_time_index,
      congestion_level,weather,quality_status,source_type FROM traffic_observations""")
    grouped: dict[tuple, list] = defaultdict(list)
    for row in rows:
        if row["time_block"] in TIME_BLOCKS:
            grouped[(row["area"], row["road"])].append(row)
    result = {}
    for pair, segment in by_pair.items():
        records = grouped.get(pair, [])
        result[segment["segment_id"]] = _segment_stats(segment, records)
    return result


def _segment_stats(segment: dict, records: list[dict]) -> dict:
    stats = {
        "segment_id": segment["segment_id"], "area": segment["area"], "road": segment["road"],
        "zone_id": segment["zone_id"], "latitude": segment["latitude"], "longitude": segment["longitude"],
        "location_source": segment["location_source"], "road_type": segment["road_type"],
        "has_map_location": segment["latitude"] is not None,
        "record_count": len(records), "has_traffic_data": bool(records),
    }
    if not records:
        return {**stats, "labelled_count": 0, "verified_count": 0, "sources": {}, "earliest_observation": None,
                "latest_observation": None, "missing_slot_pct": None, "records_per_active_day": None,
                "variables": {}, "weather_coverage_pct": None, "longest_run_blocks": 0,
                "last_run_end": None, "last_run_blocks": 0, "labelled_by_block": {}}
    slots = sorted({(date.fromisoformat(r["observed_date"]), r["time_block"]) for r in records},
                   key=lambda s: (s[0], TIME_BLOCKS.index(s[1])))
    earliest, latest = slots[0][0], slots[-1][0]
    expected = ((latest - earliest).days + 1) * len(TIME_BLOCKS)
    longest = run = 1
    for previous, current in zip(slots, slots[1:]):
        run = run + 1 if core.blocks_between(previous, current) == 1 else 1
        longest = max(longest, run)
    total = len(records)
    availability = {
        "traffic_volume": sum(r["traffic_volume"] is not None for r in records),
        "average_speed": sum(r["average_speed"] is not None for r in records),
        "travel_time_index": sum(r["travel_time_index"] is not None for r in records),
        "congestion_level": sum(r["congestion_level"] is not None for r in records),
        "weather": sum(bool(r["weather"]) for r in records),
    }
    sources: dict[str, int] = defaultdict(int)
    labelled_by_block: dict[str, int] = defaultdict(int)
    for r in records:
        sources[r["source_type"]] += 1
        if r["congestion_level"]:
            labelled_by_block[r["time_block"]] += 1
    return {**stats,
            "labelled_count": availability["congestion_level"],
            "verified_count": sum(r["quality_status"] == "verified" for r in records),
            "sources": dict(sources),
            "earliest_observation": earliest.isoformat(), "latest_observation": latest.isoformat(),
            "observed_slots": len(slots), "expected_slots": expected,
            "missing_slot_pct": round(100 * (1 - len(slots) / expected), 1),
            "records_per_active_day": round(total / len({s[0] for s in slots}), 2),
            "variables": {name: round(100 * count / total, 1) for name, count in availability.items()},
            "weather_coverage_pct": round(100 * availability["weather"] / total, 1),
            "longest_run_blocks": longest,
            "last_run_end": {"date": slots[-1][0].isoformat(), "time_block": slots[-1][1]},
            "last_run_blocks": run,
            "labelled_by_block": {block: labelled_by_block.get(block, 0) for block in TIME_BLOCKS}}


def _with_eligibility(stats: dict, now: datetime) -> dict:
    """Which prediction methods the segment's data supports at this moment."""
    now_slot = core.block_for(now)
    last = stats.get("last_run_end")
    blocks_since = core.blocks_between((date.fromisoformat(last["date"]), last["time_block"]), now_slot) if last else None
    recent_sequence = bool(last) and stats["last_run_blocks"] >= SEQUENCE_LENGTH and 0 <= blocks_since <= MAX_HORIZON_BLOCKS
    covered_blocks = sum(n >= MIN_BLOCK_RECORDS for n in stats.get("labelled_by_block", {}).values())
    pattern_ready = stats["labelled_count"] >= MIN_PATTERN_RECORDS and covered_blocks >= MIN_COVERED_BLOCKS
    if recent_sequence:
        data_method = "sequence_model"
    elif pattern_ready:
        data_method = "historical_pattern"
    elif stats["has_traffic_data"]:
        data_method = "limited_history"
    else:
        data_method = "no_history"
    notes = []
    if not stats["has_map_location"]:
        notes.append("Not placed on the map yet; set its location or geocode it.")
    if stats["has_traffic_data"] and not recent_sequence:
        notes.append("No recent consecutive observations, so live sequence forecasts are unavailable.")
    if stats["has_traffic_data"] and stats["verified_count"] == 0:
        notes.append("No record is independently verified.")
    if stats["has_traffic_data"] and stats.get("weather_coverage_pct", 0) < 50:
        notes.append("Weather is missing for most records.")
    if data_method in ("limited_history", "no_history"):
        notes.append("Too little history for a location-specific estimate; transfer or cold-start methods apply.")
    return {**stats, "blocks_since_last_observation": blocks_since, "recent_sequence_available": recent_sequence,
            "covered_time_blocks": covered_blocks, "pattern_ready": pattern_ready, "data_method": data_method,
            "notes": notes}


def coverage_list(now: datetime | None = None) -> list[dict]:
    registry = coverage_registry(now)
    return sorted(registry.values(), key=lambda item: (item["area"], item["road"]))


# ---------- API ----------

class SegmentUpdate(BaseModel):
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    road_type: str | None = None


class ZoneUpdate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_m: float = Field(ge=200, le=20000)


@router.get("/api/segments")
def list_segments():
    return {"segments": coverage_list(), "road_types": ROAD_TYPES,
            "notice": "Map location is not traffic-data coverage: check record counts and data_method."}


@router.get("/api/segments/{segment_id}")
def segment_detail(segment_id: str):
    registry = coverage_registry()
    if segment_id not in registry:
        raise HTTPException(404, "Road segment not found.")
    return registry[segment_id]


@router.patch("/api/segments/{segment_id}")
def update_segment(segment_id: str, payload: SegmentUpdate, actor: str = Depends(authority_token)):
    segment = store.one("SELECT * FROM road_segments WHERE segment_id=?", (segment_id,))
    if not segment:
        raise HTTPException(404, "Road segment not found.")
    if (payload.latitude is None) != (payload.longitude is None):
        raise HTTPException(422, "Provide both latitude and longitude, or neither.")
    if payload.latitude is not None and not core.in_bengaluru(payload.latitude, payload.longitude):
        raise HTTPException(422, "The location is outside the Bengaluru area.")
    if payload.road_type is not None and payload.road_type not in ROAD_TYPES:
        raise HTTPException(422, "road_type must be one of: " + ", ".join(ROAD_TYPES))
    with store.connect() as db:
        if payload.latitude is not None:
            db.execute("UPDATE road_segments SET latitude=?,longitude=?,location_source='authority',updated_at=? WHERE segment_id=?",
                       (payload.latitude, payload.longitude, iso_now(), segment_id))
        if payload.road_type is not None:
            db.execute("UPDATE road_segments SET road_type=?,road_type_source='authority',updated_at=? WHERE segment_id=?",
                       (payload.road_type, iso_now(), segment_id))
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
                   ("segment_updated", "road_segment", segment_id, actor, iso_now(), payload.model_dump_json()))
    refresh_zone_geometry()
    invalidate_coverage()
    return segment_detail(segment_id)


@router.post("/api/segments/{segment_id}/geocode")
def geocode_segment(segment_id: str, actor: str = Depends(authority_token)):
    segment = store.one("SELECT * FROM road_segments WHERE segment_id=?", (segment_id,))
    if not segment:
        raise HTTPException(404, "Road segment not found.")
    found = places.first_location(f"{segment['road']}, {segment['area']}, Bengaluru")
    if not found:
        raise HTTPException(404, "Place search could not locate this road. Set its location manually.")
    location = {"lat": found[0], "lng": found[1]}
    with store.connect() as db:
        db.execute("UPDATE road_segments SET latitude=?,longitude=?,location_source='geocoded',updated_at=? WHERE segment_id=?",
                   (location["lat"], location["lng"], iso_now(), segment_id))
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
                   ("segment_geocoded", "road_segment", segment_id, actor, iso_now(), "{}"))
    refresh_zone_geometry()
    invalidate_coverage()
    return segment_detail(segment_id)


@router.get("/api/zones")
def list_zones():
    zones = store.rows("SELECT * FROM zones ORDER BY name")
    counts = defaultdict(lambda: {"segments": 0, "mapped": 0, "with_data": 0})
    for stats in coverage_registry().values():
        bucket = counts[stats["zone_id"]]
        bucket["segments"] += 1
        bucket["mapped"] += stats["has_map_location"]
        bucket["with_data"] += stats["has_traffic_data"]
    return {"zones": [{**zone, **counts[zone["zone_id"]]} for zone in zones]}


@router.patch("/api/zones/{zone_id}")
def update_zone(zone_id: str, payload: ZoneUpdate, actor: str = Depends(authority_token)):
    if not store.one("SELECT zone_id FROM zones WHERE zone_id=?", (zone_id,)):
        raise HTTPException(404, "Zone not found.")
    if not core.in_bengaluru(payload.latitude, payload.longitude):
        raise HTTPException(422, "The zone centre is outside the Bengaluru area.")
    with store.connect() as db:
        db.execute("UPDATE zones SET latitude=?,longitude=?,radius_m=?,location_source='authority',updated_at=? WHERE zone_id=?",
                   (payload.latitude, payload.longitude, payload.radius_m, iso_now(), zone_id))
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
                   ("zone_updated", "zone", zone_id, actor, iso_now(), payload.model_dump_json()))
    return next(z for z in list_zones()["zones"] if z["zone_id"] == zone_id)
