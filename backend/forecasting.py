"""Congestion forecasts for a road and time, using the best method its data supports.

Method order (the response always names the method and its confidence):
1. sequence_model     - the active hybrid LSTM -> Random Forest model, when the road has six
                        consecutive recent observations ending at most three blocks before the target;
2. historical_pattern - the road's own history for that time block and day type;
3. transfer           - similar roads' patterns, only if transfer passed validation;
4. cold_start         - a limited-data estimate from current signals and a generic daily profile.
A historical sequence is never invented: without recent observations the sequence model is not used.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import core
import hybrid_model as hm
import model_registry
import patterns
import segments
import store
import weather
from core import DATA_DIR, ROOT, TIME_BLOCKS, iso_now
from security import authority_token, signed_in_user

router = APIRouter()

DEFAULT_CLASSES = ["Low", "Medium", "High"]
METHOD_LABELS = {
    "sequence_model": "Hybrid LSTM -> Random Forest model on recent observations",
    "historical_pattern": "Historical pattern for this road, time block, and day type",
    "transfer": "Transferred from similar roads (validated)",
    "cold_start": "Limited-data estimate from current signals",
}
# Generic share of congestion by time block, used only by the cold-start estimate.
GENERIC_BLOCK_LOAD = {"Midnight": 0.15, "Morning": 0.55, "Daytime": 0.5, "Noon": 0.45, "Evening": 0.65, "Night": 0.35}
INCIDENT_WEIGHTS = {"road_closure": 0.9, "road_blockage": 0.7, "accident": 0.6, "vip_movement": 0.6, "diversion": 0.5,
                    "waterlogging": 0.55, "construction": 0.45, "metro_construction": 0.45, "signal_failure": 0.4,
                    "heavy_traffic": 0.45, "breakdown": 0.35, "public_event": 0.45, "other": 0.25}
SIGNAL_RADIUS_M = 500
CLASS_LOAD = {"Low": 0.0, "Medium": 0.5, "High": 1.0}


# ---------- cached history ----------

_CACHE: dict = {"key": None, "frame": None, "counts": None}
_CACHE_LOCK = threading.Lock()


def history():
    """(history frame, per-road label counts), recomputed only when observations change."""
    key = segments._state_key()
    with _CACHE_LOCK:
        if _CACHE["key"] == key:
            return _CACHE["frame"], _CACHE["counts"]
    frame = hm.load_history()
    counts = patterns.build_counts(frame)
    with _CACHE_LOCK:
        _CACHE.update(key=key, frame=frame, counts=counts)
    return frame, counts


def _classes(model=None, frame=None) -> list[str]:
    if model is not None:
        return list(model.classes)
    if frame is not None and not frame.empty and frame["label"].notna().any():
        return hm.ordered_classes(frame["label"].dropna())
    return DEFAULT_CLASSES


def latest_window(frame, segment_id: str, target_slot: int) -> list[dict] | None:
    """The latest SEQ_LEN consecutive records ending 1..MAX_HORIZON blocks before the target."""
    if frame is None or frame.empty:
        return None
    rows = frame[(frame["segment_id"] == segment_id) & (frame["slot"] < target_slot)].tail(hm.SEQ_LEN).to_dict("records")
    if len(rows) < hm.SEQ_LEN or rows[-1]["slot"] - rows[0]["slot"] != hm.SEQ_LEN - 1:
        return None
    return rows if 1 <= target_slot - rows[-1]["slot"] <= hm.MAX_HORIZON else None


# ---------- current signals ----------

def signals(segment: dict, target: datetime) -> dict:
    """Evidence available now around the road: incidents and notices, weather forecast,
    recent camera indicators, and planned demand. It is reported with every forecast."""
    result: dict = {"incidents": [], "closure": False, "weather": None, "camera": None, "planned_demand": None}
    if segment.get("latitude") is None:
        result["note"] = "The road has no map location, so nearby incidents and weather cannot be matched."
        return result
    lat, lon = segment["latitude"], segment["longitude"]
    stamp = target.isoformat()
    for incident in store.rows("""SELECT incident_id,category,latitude,longitude,status,expires_at,starts_at,official,closure
      FROM incidents WHERE status='verified' AND latitude IS NOT NULL AND (expires_at IS NULL OR expires_at>?)
      AND (starts_at IS NULL OR starts_at<=?)""", (stamp, stamp)):
        distance = core.haversine_m(lat, lon, incident["latitude"], incident["longitude"])
        if distance <= SIGNAL_RADIUS_M:
            result["incidents"].append({"incident_id": incident["incident_id"], "category": incident["category"],
                                        "distance_m": round(distance), "official": bool(incident["official"]),
                                        "closure": bool(incident["closure"])})
            result["closure"] = result["closure"] or bool(incident["closure"])
    forecast = weather.forecast_for(lat, lon, target)
    if forecast:
        result["weather"] = {"forecast_for": forecast["forecast_for"], "captured_at": forecast["captured_at"],
                             "precipitation_mm": forecast["precipitation_mm"], "weather_code": forecast["weather_code"],
                             "wet": bool(weather.is_wet(weather.label_from_code(forecast["weather_code"]), forecast["precipitation_mm"]))}
    if target - datetime.now(timezone.utc) <= timedelta(hours=2):
        recent = store.one("""SELECT observed_end,vehicle_count,flow_per_hour,occupancy,queue_length,camera_id FROM traffic_observations
          WHERE segment_id=? AND source_type='camera' AND observed_end>=? AND quality_status<>'rejected'
          ORDER BY observed_end DESC LIMIT 1""", (segment["segment_id"], (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()))
        if recent:
            result["camera"] = recent
    try:
        import demand

        result["planned_demand"] = demand.segment_demand(segment["segment_id"], target)
    except ImportError:
        pass
    return result


def _noisy_or(values) -> float:
    remaining = 1.0
    for value in values:
        remaining *= 1 - max(0.0, min(1.0, value))
    return 1 - remaining


def cold_start(segment: dict, target: datetime, evidence: dict, classes: list[str]) -> tuple[dict, dict]:
    """A documented heuristic for roads without usable history: generic time-of-day load
    combined (noisy-OR, so correlated signals cannot add up past 1) with current signals."""
    day, block = core.block_for(target)
    components = {"time_of_day": GENERIC_BLOCK_LOAD[block] * (0.8 if day.weekday() >= 5 else 1.0)}
    if evidence["incidents"]:
        components["incidents"] = _noisy_or(INCIDENT_WEIGHTS.get(i["category"], 0.25) for i in evidence["incidents"])
    camera = evidence.get("camera")
    if camera:
        parts = []
        if camera.get("occupancy") is not None:
            parts.append(min(1.0, camera["occupancy"] * 1.4))
        if camera.get("queue_length") is not None:
            parts.append(min(1.0, camera["queue_length"] / 15))
        if parts:
            components["camera"] = max(parts)
    if evidence.get("weather", {}) and evidence["weather"].get("wet"):
        components["rain"] = 0.3
    demand_info = evidence.get("planned_demand") or {}
    if demand_info.get("demand_index") is not None:
        components["planned_demand"] = max(0.0, min(1.0, (demand_info["demand_index"] - 1.0) / 2))
    load = _noisy_or(components.values())
    ordered = [c for c in DEFAULT_CLASSES if c in classes] or classes
    if len(ordered) == 3:
        centre = {ordered[0]: 0.15, ordered[1]: 0.5, ordered[2]: 0.85}
        raw = {c: max(0.02, 1 - abs(load - centre[c]) * 2) for c in ordered}
    else:
        raw = {c: 1.0 for c in ordered}
    total = sum(raw.values())
    return {c: v / total for c, v in raw.items()}, {"load": round(load, 3), "components": {k: round(v, 3) for k, v in components.items()}}


def _level(score: float) -> str:
    return "high" if score >= 0.65 else "medium" if score >= 0.4 else "low"


# ---------- forecast ----------

def forecast(segment_id: str, target: datetime, record: bool = True, reason: str = "api") -> dict:
    registry = segments.coverage_registry()
    if segment_id not in registry:
        raise HTTPException(404, "Road segment not found.")
    segment = registry[segment_id]
    now = datetime.now(timezone.utc)
    target = target.astimezone(timezone.utc)
    target_day, target_block = core.block_for(target)
    now_slot = core.block_for(now)
    if core.blocks_between(now_slot, (target_day, target_block)) < 0:
        raise HTTPException(422, "The target time is in the past; forecasts are for the current or a future block.")
    frame, counts = history()
    model, model_row = None, None
    try:
        model, model_row = model_registry.load_active()
    except (OSError, ValueError):
        model = None
    classes = _classes(model, frame)
    target_slot = hm.slot_of(target_day, target_block)
    evidence = signals(segment, target)
    result: dict = {"segment_id": segment_id, "area": segment["area"], "road": segment["road"],
                    "target_date": target_day.isoformat(), "target_time_block": target_block,
                    "target_start": core.block_bounds(target_day, target_block)[0].isoformat(),
                    "latest_observation": segment.get("latest_observation"), "signals": evidence,
                    "data_method": segment["data_method"]}
    window = latest_window(frame, segment_id, target_slot) if model is not None else None
    if window:
        horizon = target_slot - window[-1]["slot"]
        path = model.predict(window, window[-1]["slot"], window[-1].get("road_type") or segment["road_type"], horizon,
                             counts.get(segment_id))
        step = path[-1]
        confidence = max(step["probabilities"].values()) * (1 - 0.1 * (horizon - 1))
        result.update(method="sequence_model", probabilities=step["probabilities"], predicted_values=step["predicted_values"],
                      horizon_blocks=horizon, model_version=model_row["version"],
                      evidence={"window": [{"date": r["date"].isoformat(), "time_block": r["time_block"]} for r in window]})
    elif segment["pattern_ready"] and segment_id in counts:
        probabilities, records, cell = patterns.pattern_distribution(counts[segment_id], target_block, patterns.day_type(target_day), classes)
        latest = date.fromisoformat(segment["latest_observation"])
        age_days = (now.date() - latest).days
        recency = 1.0 if age_days <= 365 else 0.75 if age_days <= 730 else 0.6
        confidence = min(0.85, records / (records + 10)) * recency
        result.update(method="historical_pattern", probabilities=probabilities, model_version="pattern",
                      evidence={"records": records, "cell": cell, "history_age_days": age_days})
    else:
        validation = model_registry.transfer_validation() or {}
        donors = [s for s in registry.values() if s["pattern_ready"] and s["segment_id"] != segment_id]
        probabilities, used = (None, [])
        if validation.get("enabled") and donors:
            probabilities, used = patterns.transfer_distribution(segment, donors, counts, target_block, patterns.day_type(target_day),
                                                                 classes, counts.get(segment_id))
        if probabilities:
            similarity = sum(u["similarity"] for u in used) / len(used)
            confidence = min(0.6, similarity * 0.6)
            result.update(method="transfer", probabilities=probabilities, model_version="transfer",
                          evidence={"donors": used, "transfer_validation_macro_f1": validation.get("macro_f1")})
        else:
            probabilities, details = cold_start(segment, target, evidence, classes)
            confidence = 0.25
            result.update(method="cold_start", probabilities=probabilities, model_version="cold-start-v1",
                          evidence={**details, "reason": "No usable history and no validated transfer for this road."})
    result["probabilities"] = {c: round(p, 4) for c, p in result["probabilities"].items()}
    result["predicted_class"] = max(result["probabilities"], key=result["probabilities"].get)
    result["expected_load"] = round(sum(CLASS_LOAD.get(c, 0.5) * p for c, p in result["probabilities"].items()), 3)
    result["confidence"] = {"score": round(confidence, 3), "level": _level(confidence)}
    result["method_label"] = METHOD_LABELS[result["method"]]
    result.setdefault("horizon_blocks", core.blocks_between(now_slot, (target_day, target_block)))
    result["notice"] = ("Research estimate. " + {
        "sequence_model": "Made from the road's recent observations by the active hybrid model.",
        "historical_pattern": "No recent observations: this is how the road usually behaved at this time in past records.",
        "transfer": "This road has too little history; the estimate is borrowed from similar roads.",
        "cold_start": "This road has no usable history; treat this limited-data estimate with caution.",
    }[result["method"]])
    if record:
        result["prediction_id"] = _record(result, reason)
    return result


def _record(result: dict, reason: str) -> str:
    prediction_id = str(uuid.uuid4())
    with store.connect() as db:
        db.execute("""INSERT INTO model_predictions(prediction_id,issue_time,target_date,target_area,target_road,target_time_block,
          source_type,model_version,predicted_class,probabilities_json,input_json,segment_id,target_start,method,horizon_blocks,
          confidence,predicted_values_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (prediction_id, iso_now(), result["target_date"], result["area"], result["road"], result["target_time_block"],
                    "model_prediction", result["model_version"], result["predicted_class"], json.dumps(result["probabilities"]),
                    json.dumps({"reason": reason, "evidence": result.get("evidence")}, default=str), result["segment_id"],
                    result["target_start"], result["method"], result["horizon_blocks"], result["confidence"]["score"],
                    json.dumps(result.get("predicted_values")) if result.get("predicted_values") else None))
    return prediction_id


def record_outcome(db, segment_id: str, observed_date: str, time_block: str, observation: dict) -> None:
    """Link a later observation to earlier forecasts for the same road and block."""
    label = observation.get("congestion_level")
    if label is not None:
        db.execute("""UPDATE model_predictions SET observed_outcome=?,evaluated_at=? WHERE target_date=? AND target_time_block=?
          AND (segment_id=? OR (segment_id IS NULL AND target_area=? AND target_road=?)) AND observed_outcome IS NULL""",
                   (label, iso_now(), observed_date, time_block, segment_id, observation.get("area"), observation.get("road")))
    observed = {c: observation.get(c) for c in hm.NUMERIC if observation.get(c) is not None}
    if not observed:
        return
    for row in db.execute("""SELECT prediction_id,predicted_values_json FROM model_predictions WHERE segment_id=? AND target_date=?
      AND target_time_block=? AND predicted_values_json IS NOT NULL AND errors_json IS NULL""",
                          (segment_id, observed_date, time_block)).fetchall():
        predicted = json.loads(row["predicted_values_json"])
        errors = {c: round(abs(predicted[c] - observed[c]), 4) for c in observed if c in predicted}
        db.execute("UPDATE model_predictions SET observed_values_json=?,errors_json=? WHERE prediction_id=?",
                   (json.dumps(observed), json.dumps(errors), row["prediction_id"]))


def zone_outlook(target: datetime) -> list[dict]:
    """Expected congestion per zone at a time: average expected load of its roads."""
    registry = segments.coverage_registry()
    zones = {z["zone_id"]: z for z in store.rows("SELECT * FROM zones")}
    by_zone: dict[str, list[dict]] = {}
    for segment_id, stats in registry.items():
        if stats["has_traffic_data"] or stats["has_map_location"]:
            try:
                by_zone.setdefault(stats["zone_id"], []).append(forecast(segment_id, target, record=False))
            except HTTPException:
                continue
    result = []
    for zone_id, items in by_zone.items():
        zone = zones.get(zone_id, {})
        load = sum(i["expected_load"] for i in items) / len(items)
        level = "High" if load >= 0.66 else "Medium" if load >= 0.33 else "Low"
        result.append({"zone_id": zone_id, "name": zone.get("name", zone_id), "latitude": zone.get("latitude"),
                       "longitude": zone.get("longitude"), "radius_m": zone.get("radius_m"), "expected_load": round(load, 3),
                       "level": level, "methods": sorted({i["method"] for i in items}),
                       "roads": [{"segment_id": i["segment_id"], "road": i["road"], "predicted_class": i["predicted_class"],
                                  "method": i["method"], "confidence": i["confidence"]["level"]} for i in items]})
    return sorted(result, key=lambda z: -z["expected_load"])


# ---------- API ----------

def _resolve_segment(segment_id: str | None, area: str | None, road: str | None) -> str:
    if segment_id:
        return segment_id
    if area and road:
        return segments.segment_id_for(area, road)
    raise HTTPException(422, "Provide segment_id, or both area and road.")


def _parse_target(target: str | None) -> datetime:
    if not target:
        return datetime.now(timezone.utc)
    try:
        moment = datetime.fromisoformat(target.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "target must be an ISO date-time with a timezone.") from None
    if moment.tzinfo is None:
        raise HTTPException(422, "target must include a timezone.")
    return moment


@router.get("/api/forecast")
def forecast_endpoint(segment_id: str | None = None, area: str | None = None, road: str | None = None,
                      target: str | None = None, actor: str = Depends(signed_in_user)):
    return forecast(_resolve_segment(segment_id, area, road), _parse_target(target))


@router.get("/api/forecast/zones")
def zones_endpoint(target: str | None = None, actor: str = Depends(signed_in_user)):
    moment = _parse_target(target)
    return {"target": moment.isoformat(), "zones": zone_outlook(moment),
            "notice": "Zone levels average each road's forecast; methods and confidence vary by road."}


class ReplayInput(BaseModel):
    segment_id: str
    as_of_date: date
    as_of_time_block: str
    horizons: int = 3
    version: str | None = None


@router.post("/api/forecast/replay")
def replay(payload: ReplayInput, actor: str = Depends(authority_token)):
    """Run a model as if it were a past moment, then compare with what was observed."""
    if payload.as_of_time_block not in TIME_BLOCKS or not 1 <= payload.horizons <= hm.MAX_HORIZON:
        raise HTTPException(422, f"Use a valid time block and 1-{hm.MAX_HORIZON} horizons.")
    if payload.version:
        row = model_registry.get(payload.version)
        if not row:
            raise HTTPException(404, "Model version not found.")
    else:
        row = model_registry.active() or (model_registry.versions() or [None])[0]
        if not row:
            raise HTTPException(404, "No model has been trained yet.")
    try:
        model = hm.HybridModel.load(core.MODELS_DIR / row["artifact_dir"], row["version"])
    except (OSError, ValueError) as exc:
        raise HTTPException(409, f"The model files could not be loaded: {exc}") from exc
    frame, _ = history()
    as_of_slot = hm.slot_of(payload.as_of_date, payload.as_of_time_block)
    window = latest_window(frame, payload.segment_id, as_of_slot + 1)
    if not window or window[-1]["slot"] != as_of_slot:
        raise HTTPException(422, "There are no six consecutive observations ending at that block for this road.")
    before = frame[frame["date"] < payload.as_of_date]
    path = model.predict(window, as_of_slot, window[-1].get("road_type") or "unknown", payload.horizons,
                         patterns.build_counts(before[before["segment_id"] == payload.segment_id]).get(payload.segment_id))
    actual = frame[frame["segment_id"] == payload.segment_id].set_index("slot")
    steps = []
    for step in path:
        day, block = hm.slot_date_block(step["slot"])
        observed = actual.loc[step["slot"]] if step["slot"] in actual.index else None
        steps.append({"date": day.isoformat(), "time_block": block, "predicted_class": step["predicted_class"],
                      "probabilities": {c: round(p, 4) for c, p in step["probabilities"].items()},
                      "predicted_values": step["predicted_values"],
                      "observed_class": None if observed is None else observed["label"],
                      "observed_values": None if observed is None else {c: (None if hm._is_missing(observed[c]) else float(observed[c])) for c in hm.NUMERIC}})
    return {"segment_id": payload.segment_id, "model_version": row["version"], "model_status": row["status"],
            "window": [{"date": r["date"].isoformat(), "time_block": r["time_block"], "label": r["label"],
                        **{c: (None if hm._is_missing(r[c]) else float(r[c])) for c in hm.NUMERIC}} for r in window],
            "steps": steps,
            "notice": "Replay on past records for evaluation and demonstration; it is not a live forecast."
                      + ("" if row["status"] == "active" else " This version is not active for live forecasts.")}


@router.get("/api/models")
def list_models(actor: str = Depends(authority_token)):
    items = []
    for row in model_registry.versions():
        metrics = row["metrics"]
        items.append({key: row[key] for key in ("version", "kind", "status", "created_at", "trained_by", "activated_at",
                                                 "activated_by", "notes", "acceptance", "data")} |
                     {"test": {k: metrics.get("test", {}).get(k) for k in ("accuracy", "macro_f1", "count")},
                      "baselines_test": {n: {"accuracy": b.get("accuracy"), "macro_f1": b.get("macro_f1")}
                                         for n, b in metrics.get("baselines_test", {}).items()},
                      "lstm_contribution": {k: v for k, v in metrics.get("lstm_contribution", {}).items() if k != "rf_without_lstm_test"},
                      "transfer_enabled": metrics.get("transfer_validation", {}).get("enabled")})
    return {"models": items, "training": training_status()}


@router.get("/api/models/{version}")
def model_detail(version: str, actor: str = Depends(authority_token)):
    row = model_registry.get(version)
    if not row:
        raise HTTPException(404, "Model version not found.")
    return row


@router.post("/api/models/{version}/activate")
def activate_model(version: str, actor: str = Depends(authority_token)):
    try:
        return model_registry.activate(version, actor)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


_TRAINING: dict = {"process": None, "started_at": None, "log": None, "actor": None, "activate": False}
_TRAINING_LOCK = threading.Lock()


def training_status() -> dict:
    with _TRAINING_LOCK:
        process = _TRAINING["process"]
        if process is None:
            return {"running": False}
        code = process.poll()
        tail = ""
        if _TRAINING["log"] and os.path.exists(_TRAINING["log"]):
            with open(_TRAINING["log"], encoding="utf-8", errors="replace") as handle:
                tail = "".join(handle.readlines()[-25:])
        return {"running": code is None, "return_code": code, "started_at": _TRAINING["started_at"],
                "started_by": _TRAINING["actor"], "activate_if_accepted": _TRAINING["activate"], "log_tail": tail}


@router.post("/api/models/train", status_code=202)
def start_training(activate: bool = False, actor: str = Depends(authority_token)):
    """Retrain in a separate process (controlled and versioned, never after every record)."""
    with _TRAINING_LOCK:
        if _TRAINING["process"] is not None and _TRAINING["process"].poll() is None:
            raise HTTPException(409, "A training run is already in progress.")
        logs = DATA_DIR / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / f"training-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.log"
        command = [sys.executable, str(ROOT / "backend" / "train_hybrid.py"), "--trained-by", actor]
        if activate:
            command.append("--activate")
        handle = open(log_path, "w", encoding="utf-8")
        _TRAINING.update(process=subprocess.Popen(command, cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT,
                                                  env={**os.environ, "PYTHONIOENCODING": "utf-8"}),
                         started_at=iso_now(), log=str(log_path), actor=actor, activate=activate)
    with store.connect() as db:
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
                   ("model_training_started", "model_version", None, actor, iso_now(), json.dumps({"activate": activate})))
    return training_status()


@router.get("/api/models/train/status")
def training_status_endpoint(actor: str = Depends(authority_token)):
    return training_status()


@router.get("/api/evaluation")
def evaluation():
    """How earlier forecasts compared with what was later observed, by method."""
    rows = store.rows("""SELECT method,model_version,predicted_class,observed_outcome,errors_json FROM model_predictions
      WHERE observed_outcome IS NOT NULL OR errors_json IS NOT NULL""")
    summary: dict[str, dict] = {}
    for row in rows:
        key = row["method"] or "legacy"
        bucket = summary.setdefault(key, {"evaluated": 0, "correct": 0, "errors": {}})
        if row["observed_outcome"] is not None:
            bucket["evaluated"] += 1
            bucket["correct"] += row["predicted_class"] == row["observed_outcome"]
        for column, value in json.loads(row["errors_json"] or "{}").items():
            bucket["errors"].setdefault(column, []).append(value)
    for bucket in summary.values():
        bucket["accuracy"] = round(bucket["correct"] / bucket["evaluated"], 4) if bucket["evaluated"] else None
        bucket["mae"] = {c: round(sum(v) / len(v), 4) for c, v in bucket.pop("errors").items()}
    counts = store.one("""SELECT COUNT(*) AS prediction_count,
      SUM(CASE WHEN observed_outcome IS NOT NULL THEN 1 ELSE 0 END) AS evaluated_count FROM model_predictions""")
    return {**counts, "by_method": summary,
            "message": "Forecasts are linked when a later observation arrives for the same road, date, and time block."}
