"""Forecasting model versions: registration, activation, rollback, and loading.

Status values: "candidate" (passed acceptance, not in use), "rejected" (failed acceptance,
kept for review), "active" (used for forecasts), and "retired" (previously active; it can
be re-activated to roll back).
"""
from __future__ import annotations

import json
import threading

import store
from core import MODELS_DIR, iso_now

KIND = "hybrid_lstm_rf"
_LOCK = threading.Lock()
_LOADED: dict = {"version": None, "model": None}


def _decode(row: dict) -> dict:
    for field in ("metrics_json", "acceptance_json", "data_json"):
        row[field.removesuffix("_json")] = json.loads(row.pop(field) or "{}")
    return row


def versions() -> list[dict]:
    return [_decode(row) for row in store.rows("SELECT * FROM model_versions ORDER BY created_at DESC")]


def get(version: str) -> dict | None:
    row = store.one("SELECT * FROM model_versions WHERE version=?", (version,))
    return _decode(row) if row else None


def active() -> dict | None:
    row = store.one("SELECT * FROM model_versions WHERE status='active' ORDER BY activated_at DESC LIMIT 1")
    return _decode(row) if row else None


def register(version: str, metrics: dict, acceptance: dict, data: dict, trained_by: str, notes: str | None = None) -> str:
    status = "candidate" if acceptance.get("passed") else "rejected"
    with store.connect() as db:
        db.execute("""INSERT INTO model_versions(version,kind,status,created_at,trained_by,artifact_dir,metrics_json,
          acceptance_json,data_json,notes) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (version, KIND, status, iso_now(), trained_by, version, json.dumps(metrics, default=str),
                    json.dumps(acceptance, default=str), json.dumps(data, default=str), notes))
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
                   ("model_registered", "model_version", version, trained_by, iso_now(), json.dumps({"status": status})))
    return status


def activate(version: str, actor: str) -> dict:
    """Put a version into use. Rejected versions cannot be activated; retired ones can (rollback)."""
    row = get(version)
    if not row:
        raise LookupError("Model version not found.")
    if row["status"] == "rejected":
        raise ValueError("This version did not pass the acceptance checks and cannot be activated.")
    if not (MODELS_DIR / row["artifact_dir"]).is_dir():
        raise ValueError("The model files for this version are missing from the models folder.")
    if row["status"] != "active":
        with store.connect() as db:
            db.execute("UPDATE model_versions SET status='retired' WHERE status='active'")
            db.execute("UPDATE model_versions SET status='active',activated_at=?,activated_by=? WHERE version=?",
                       (iso_now(), actor, version))
            db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
                       ("model_activated", "model_version", version, actor, iso_now(), json.dumps({"previous_status": row["status"]})))
    with _LOCK:
        _LOADED.update(version=None, model=None)
    return get(version)


def load_active():
    """(model, registry row) for the active version, or (None, None) when no model is in use."""
    row = active()
    if not row:
        return None, None
    with _LOCK:
        if _LOADED["version"] == row["version"]:
            return _LOADED["model"], row
    import hybrid_model

    model = hybrid_model.HybridModel.load(MODELS_DIR / row["artifact_dir"], row["version"])
    with _LOCK:
        _LOADED.update(version=row["version"], model=model)
    return model, row


def transfer_validation() -> dict | None:
    row = active()
    return (row or {}).get("metrics", {}).get("transfer_validation")


def new_version_name() -> str:
    from datetime import datetime, timezone

    return "hybrid-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
