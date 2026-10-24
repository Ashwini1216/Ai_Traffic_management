"""Background jobs that keep the platform's data current, run inside the API process.

Run the API as a single process (the default uvicorn command) so each job runs once.
Set SCHEDULER_ENABLED=0 in .env to switch the jobs off; authorities can still run any job
on demand from the API.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

import alerts
import cameras
import incidents
import notifications
import retention
import segments
import store
import weather
from core import iso_now
from security import authority_token

router = APIRouter()


def collect_weather() -> dict:
    """Current conditions and forecasts for every zone centre (or the city centre)."""
    points = [(z["latitude"], z["longitude"]) for z in store.rows("SELECT latitude,longitude FROM zones WHERE latitude IS NOT NULL")]
    collected, failed = 0, 0
    for latitude, longitude in points or [(12.9716, 77.5946)]:
        try:
            weather.collect(round(latitude, 4), round(longitude, 4))
            collected += 1
        except HTTPException:
            failed += 1
    return {"points": collected, "failed": failed}


JOBS = {
    "collect_weather": {"every": 3600, "run": collect_weather, "description": "Weather observations and forecasts per zone"},
    "reassess_trips": {"every": 900, "run": notifications.reassess_due_trips, "description": "Re-check trips departing soon"},
    "trip_reminders": {"every": 300, "run": notifications.send_departure_reminders,
                       "description": "Advance notice of traffic, delays and better routes before each trip"},
    "expire_stale_reports": {"every": 900, "run": lambda: {"expired": incidents.expire_stale_reports()},
                             "description": "Expire unreviewed citizen reports"},
    "camera_collection": {"every": 60, "run": cameras.collect_due, "description": "Sample approved camera streams that are due"},
    "zone_alerts": {"every": 1800, "run": alerts.evaluate_zones, "description": "High-risk zones, closures, and demand hotspots"},
    "data_quality": {"every": 3600, "run": alerts.data_quality, "description": "Offline cameras and quarantine spikes"},
    "retention": {"every": 86400, "run": retention.apply, "description": "Footage deletion, summaries, and retention limits"},
    "sync_segments": {"every": 3600, "run": lambda: {"added": segments.sync_segments()}, "description": "Register new roads"},
}


def run_job(name: str) -> dict:
    """Run one job now and record the outcome."""
    job = JOBS[name]
    with store.connect() as db:
        cur = db.execute("INSERT INTO job_runs(job,started_at,status) VALUES(?,?,?)", (name, iso_now(), "running"))
        run_id = cur.lastrowid
    try:
        details, status = job["run"](), "ok"
    except Exception as exc:  # noqa: BLE001 - one failing job must not stop the others
        details, status = {"error": str(exc), "trace": traceback.format_exc(limit=3)}, "failed"
    with store.connect() as db:
        db.execute("UPDATE job_runs SET finished_at=?,status=?,details_json=? WHERE run_id=?",
                   (iso_now(), status, json.dumps(details, default=str), run_id))
    return {"job": name, "status": status, "details": details}


class Scheduler:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: dict[str, float] = {}

    def start(self) -> None:
        if os.getenv("SCHEDULER_ENABLED", "1") == "0" or self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="flowstate-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        self._stop.wait(20)  # let the API finish starting first
        while not self._stop.is_set():
            for name, job in JOBS.items():
                if self._stop.is_set():
                    break
                if time.monotonic() - self._last.get(name, -1e9) >= job["every"]:
                    self._last[name] = time.monotonic()
                    run_job(name)
            self._stop.wait(30)


SCHEDULER = Scheduler()


@router.get("/api/jobs")
def list_jobs(actor: str = Depends(authority_token)):
    result = []
    for name, job in JOBS.items():
        last = store.one("SELECT * FROM job_runs WHERE job=? ORDER BY run_id DESC LIMIT 1", (name,))
        if last:
            last["details"] = json.loads(last.pop("details_json") or "null")
        result.append({"job": name, "description": job["description"], "every_seconds": job["every"], "last_run": last})
    return {"jobs": result, "scheduler_running": SCHEDULER._thread is not None,
            "scheduler_enabled": os.getenv("SCHEDULER_ENABLED", "1") != "0"}


@router.post("/api/jobs/{name}/run")
async def run_now(name: str, actor: str = Depends(authority_token)):
    if name not in JOBS:
        raise HTTPException(404, "Unknown job.")
    return await run_in_threadpool(run_job, name)


def recent_failures(hours: int = 24) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return store.rows("SELECT job,started_at,details_json FROM job_runs WHERE status='failed' AND started_at>? ORDER BY run_id DESC", (since,))
