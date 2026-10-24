"""Camera registry, uploaded clip review, sample video analysis, and continuous collection
from approved streams.

Only cameras an authority marked approved and online are used for observations. A stream is
sampled for a few seconds at its collection interval; the derived measurements (vehicles
crossing the counting line, flow per hour, occupancy, queue) are stored as timestamped
observations. The video itself is not stored. A camera that keeps failing is marked offline
and reported. A sample video can also be checked for people and vehicles without a camera;
it is deleted as soon as its analysis ends.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

import core
import observations
import segments
import store
from core import DATA_DIR, TIME_BLOCKS, iso_now
from security import authority_token

router = APIRouter()

FOOTAGE_DIR = DATA_DIR / "cctv_footage"
VIDEO_MEDIA_TYPES = {".mp4": "video/mp4", ".avi": "video/x-msvideo", ".mov": "video/quicktime", ".mkv": "video/x-matroska"}
STREAM_SCHEMES = ("rtsp://", "rtsps://", "rtmp://", "http://", "https://")
OFFLINE_AFTER_FAILURES = 3
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class CameraInput(BaseModel):
    camera_id: str = Field(min_length=2, max_length=80)
    display_name: str = Field(min_length=2, max_length=160)
    area: str | None = Field(default=None, max_length=100)
    road: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    source_type: Literal["authorized_cctv", "approved_ip_camera", "permitted_integration"]
    authorization_status: Literal["approved", "pending", "revoked"] = "pending"
    operational_status: Literal["online", "offline", "unknown"] = "unknown"
    stream_url: str | None = Field(default=None, max_length=1000)
    collection_interval_seconds: int = Field(default=300, ge=60, le=3600)
    sample_seconds: int = Field(default=20, ge=5, le=120)
    counting_line: list[list[float]] | None = None
    roi: list[list[float]] | None = None

    @field_validator("stream_url")
    @classmethod
    def _stream(cls, value: str | None) -> str | None:
        if not value:
            return None
        if value.startswith(STREAM_SCHEMES):
            return value
        # Local files are accepted only when explicitly allowed (for testing with recorded clips).
        if os.getenv("ALLOW_FILE_STREAMS") == "1" and os.path.isfile(value):
            return value
        raise ValueError("stream_url must be an rtsp://, rtmp://, http:// or https:// address of an approved camera")

    @field_validator("counting_line")
    @classmethod
    def _line(cls, value):
        if value is not None and (len(value) != 2 or any(len(p) != 2 or not all(0 <= c <= 1 for c in p) for p in value)):
            raise ValueError("counting_line must be two [x, y] points between 0 and 1")
        return value

    @field_validator("roi")
    @classmethod
    def _roi(cls, value):
        if value is not None and (len(value) < 3 or any(len(p) != 2 or not all(0 <= c <= 1 for c in p) for p in value)):
            raise ValueError("roi must be at least three [x, y] points between 0 and 1")
        return value


def _camera_view(row: dict) -> dict:
    row = dict(row)
    row["counting_line"] = json.loads(row.pop("counting_line_json") or "null")
    row["roi"] = json.loads(row.pop("roi_json") or "null")
    row["has_stream"] = bool(row.pop("stream_url", None))
    return row


@router.get("/api/cameras")
def cameras(actor: str = Depends(authority_token)):
    rows = store.rows("""SELECT camera_id,display_name,area,road,latitude,longitude,source_type,authorization_status,
      operational_status,last_success_at,stream_url,collection_interval_seconds,sample_seconds,counting_line_json,roi_json,
      last_attempt_at,last_error,consecutive_failures,segment_id FROM cameras ORDER BY display_name""")
    return {"cameras": [_camera_view(row) for row in rows]}


@router.post("/api/cameras")
def register_camera(payload: CameraInput, actor: str = Depends(authority_token)):
    if (payload.latitude is None) != (payload.longitude is None):
        raise HTTPException(422, "Provide both latitude and longitude, or neither.")
    if payload.latitude is not None and not core.in_bengaluru(payload.latitude, payload.longitude):
        raise HTTPException(422, "The camera location is outside the Bengaluru area.")
    segment_id = segments.ensure_segment(payload.area, payload.road) if payload.area and payload.road else None
    existing = store.one("SELECT stream_url FROM cameras WHERE camera_id=?", (payload.camera_id,))
    # An empty stream_url on an edit keeps the saved address (the list never returns it).
    stream_url = payload.stream_url or (existing or {}).get("stream_url")
    with store.connect() as db:
        db.execute("""INSERT INTO cameras(camera_id,display_name,area,road,latitude,longitude,source_type,authorization_status,
          operational_status,created_at,stream_url,collection_interval_seconds,sample_seconds,counting_line_json,roi_json,
          segment_id,consecutive_failures) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
          ON CONFLICT(camera_id) DO UPDATE SET display_name=excluded.display_name,area=excluded.area,road=excluded.road,
          latitude=excluded.latitude,longitude=excluded.longitude,source_type=excluded.source_type,
          authorization_status=excluded.authorization_status,operational_status=excluded.operational_status,
          stream_url=excluded.stream_url,collection_interval_seconds=excluded.collection_interval_seconds,
          sample_seconds=excluded.sample_seconds,counting_line_json=excluded.counting_line_json,roi_json=excluded.roi_json,
          segment_id=excluded.segment_id""",
                   (payload.camera_id, payload.display_name, payload.area, payload.road, payload.latitude, payload.longitude,
                    payload.source_type, payload.authorization_status, payload.operational_status, iso_now(), stream_url,
                    payload.collection_interval_seconds, payload.sample_seconds, json.dumps(payload.counting_line) if payload.counting_line else None,
                    json.dumps(payload.roi) if payload.roi else None, segment_id))
        db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
                   ("camera_registry_update", "camera", payload.camera_id, actor, iso_now(),
                    payload.model_dump_json(exclude={"stream_url"})))
    return {"camera_id": payload.camera_id, "authorization_status": payload.authorization_status,
            "operational_status": payload.operational_status, "has_stream": bool(stream_url)}


# ---------- continuous stream collection ----------

def collect(camera: dict) -> dict:
    """Sample one camera stream once and store the derived measurements as an observation."""
    from vision import analyze_stream

    started = datetime.now(timezone.utc)
    with store.connect() as db:
        db.execute("UPDATE cameras SET last_attempt_at=? WHERE camera_id=?", (started.isoformat(), camera["camera_id"]))
    try:
        result = analyze_stream(camera["stream_url"], camera.get("sample_seconds") or 20,
                                json.loads(camera["counting_line_json"]) if camera.get("counting_line_json") else None,
                                json.loads(camera["roi_json"]) if camera.get("roi_json") else None)
        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=max(1.0, result["duration_seconds"]))
        crossings = result["line_crossings"]
        stored = observations.store_observation({
            "observed_start": start.isoformat(), "observed_end": end.isoformat(),
            "area": camera["area"], "road": camera["road"], "source_type": "camera", "source_name": "camera_stream_collector",
            "camera_id": camera["camera_id"],
            # With a counting line, the count is vehicles crossing it, matching the flow figure.
            "vehicle_count": crossings if crossings is not None else result["tracked_vehicles"],
            "flow_per_hour": result["flow_per_hour"], "occupancy": result["occupancy"], "queue_length": result["queue_length"],
        })
    except (ValueError, RuntimeError, OSError, HTTPException) as exc:
        message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        failures = (camera.get("consecutive_failures") or 0) + 1
        with store.connect() as db:
            db.execute("""UPDATE cameras SET last_error=?,consecutive_failures=?,
              operational_status=CASE WHEN ?>=? THEN 'offline' ELSE operational_status END WHERE camera_id=?""",
                       (str(message)[:500], failures, failures, OFFLINE_AFTER_FAILURES, camera["camera_id"]))
        return {"camera_id": camera["camera_id"], "ok": False, "error": str(message), "consecutive_failures": failures,
                "marked_offline": failures >= OFFLINE_AFTER_FAILURES}
    with store.connect() as db:
        db.execute("UPDATE cameras SET last_error=NULL,consecutive_failures=0 WHERE camera_id=?", (camera["camera_id"],))
    return {"camera_id": camera["camera_id"], "ok": True, "observation_id": stored["id"], **result}


def collect_due(now: datetime | None = None) -> dict:
    """Sample every approved, online camera whose collection interval has elapsed."""
    now = now or datetime.now(timezone.utc)
    results = []
    for camera in store.rows("""SELECT * FROM cameras WHERE stream_url IS NOT NULL AND authorization_status='approved'
      AND operational_status='online' AND area IS NOT NULL AND road IS NOT NULL"""):
        last = core.parse_utc(camera["last_attempt_at"])
        if last and (now - last).total_seconds() < (camera["collection_interval_seconds"] or 300):
            continue
        results.append(collect(camera))
    return {"cameras_sampled": len(results), "failed": sum(not r["ok"] for r in results),
            "results": [{k: r.get(k) for k in ("camera_id", "ok", "error", "line_crossings", "occupancy")} for r in results]}


@router.post("/api/cameras/{camera_id}/collect")
async def collect_now(camera_id: str, actor: str = Depends(authority_token)):
    camera = store.one("SELECT * FROM cameras WHERE camera_id=?", (camera_id,))
    if not camera or not camera["stream_url"]:
        raise HTTPException(404, "Camera with a stream address not found.")
    if camera["authorization_status"] != "approved" or camera["operational_status"] != "online":
        raise HTTPException(403, "Only approved, online cameras are sampled.")
    return await run_in_threadpool(collect, camera)


# ---------- uploaded clip review ----------

def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, "Date must use YYYY-MM-DD format.") from None


async def _read_upload(video: UploadFile) -> tuple[bytes, str]:
    """The uploaded video and its vetted extension."""
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in VIDEO_MEDIA_TYPES:
        raise HTTPException(415, "Upload a supported MP4, AVI, MOV, or MKV video.")
    contents = await video.read(MAX_UPLOAD_BYTES + 1)
    await video.close()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Video clips must be 100 MB or smaller.")
    return contents, suffix


@router.post("/api/cameras/{camera_id}/analyze-video")
async def analyze_camera_video(camera_id: str, video: UploadFile = File(...),
                               observed_date: str = Form(...), time_block: str = Form(...),
                               actor: str = Depends(authority_token)):
    camera = store.one("SELECT * FROM cameras WHERE camera_id=?", (camera_id,))
    if not camera or camera["authorization_status"] != "approved" or camera["operational_status"] != "online":
        raise HTTPException(403, "Video analysis requires a camera marked approved and online by an authority.")
    if not camera["area"] or not camera["road"]:
        raise HTTPException(422, "Add the camera area and road to its registry before analyzing video.")
    if _parse_date(observed_date) > core.local_today():
        raise HTTPException(422, "observed_date cannot be in the future.")
    if time_block not in TIME_BLOCKS:
        raise HTTPException(422, "time_block must be one of: " + ", ".join(TIME_BLOCKS))
    contents, suffix = await _read_upload(video)
    footage_id = str(uuid.uuid4())
    FOOTAGE_DIR.mkdir(parents=True, exist_ok=True)
    # Stage the upload inside the footage folder rather than the system temp folder: the
    # final rename then never crosses drives, which fails on Windows when the project is
    # not on the system drive.
    pending_path = FOOTAGE_DIR / f"{footage_id}.pending{suffix}"
    stored_path = FOOTAGE_DIR / f"{footage_id}{suffix}"
    pending_path.write_bytes(contents)
    try:
        try:
            from vision import analyze_video
            # Analysis takes seconds to minutes; run it off the event loop so the API stays responsive.
            analysis = await run_in_threadpool(analyze_video, pending_path)
        except ImportError as exc:
            raise HTTPException(503, "Install the project dependencies from the root requirements.txt before using video analysis.") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        pending_path.replace(stored_path)
        observation = observations.store_observation({
            "observed_date": observed_date, "time_block": time_block, "area": camera["area"], "road": camera["road"],
            "traffic_volume": analysis["clip_vehicle_count_estimate"], "source_type": "camera",
            "source_name": "yolov8n_video_count", "camera_id": camera_id,
        })
        with store.connect() as db:
            db.execute("""INSERT INTO camera_footage(footage_id,camera_id,original_filename,stored_path,content_type,
              uploaded_by,uploaded_at,analysis_json) VALUES(?,?,?,?,?,?,?,?)""",
                       (footage_id, camera_id, Path(video.filename or "camera-footage" + suffix).name, stored_path.name,
                        VIDEO_MEDIA_TYPES[suffix], actor, iso_now(), json.dumps(analysis)))
    except BaseException:
        for leftover in (pending_path, stored_path):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return {"camera_id": camera_id, "observation_id": observation["id"], "observation_quality": observation["quality_status"],
            "footage_id": footage_id, "playback_url": f"/api/camera-footage/{footage_id}/video", "analysis": analysis, "actor": actor}


# ---------- sample video analysis ----------
# Results stay in memory for an hour so the page can show them; nothing is written to the database.

SAMPLE_DIR = DATA_DIR / "video_samples"
SAMPLE_RESULT_SECONDS = 3600
_SAMPLES: dict[str, dict] = {}
_SAMPLES_LOCK = threading.Lock()


def _run_sample(job_id: str, path: Path) -> None:
    from vision import analyze_video

    def progress(stage: str, done: int, total: int | None) -> None:
        with _SAMPLES_LOCK:
            _SAMPLES[job_id].update(stage=stage, frames_done=done, frames_total=total)

    try:
        outcome = {"status": "done", "analysis": analyze_video(path, progress=progress, preview=True)}
    except (ValueError, RuntimeError) as exc:
        outcome = {"status": "failed", "error": str(exc)}
    except Exception as exc:  # report it on the page rather than leave the page waiting
        outcome = {"status": "failed", "error": f"The analysis stopped unexpectedly: {exc}"}
    finally:
        path.unlink(missing_ok=True)
    with _SAMPLES_LOCK:
        _SAMPLES[job_id].update(outcome, finished_at=iso_now())


def _sample_view(job: dict) -> dict:
    return {key: value for key, value in job.items() if key != "owner"}


@router.post("/api/video-samples", status_code=202)
async def start_sample_analysis(video: UploadFile = File(...), actor: str = Depends(authority_token)):
    """Count people and vehicles in an authorised video without registering a camera. Poll the
    returned job for progress; the video is deleted when the analysis ends."""
    contents, suffix = await _read_upload(video)
    job_id = str(uuid.uuid4())
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    path = SAMPLE_DIR / f"{job_id}{suffix}"
    path.write_bytes(contents)
    now = datetime.now(timezone.utc)
    with _SAMPLES_LOCK:
        for old_id, old in list(_SAMPLES.items()):
            finished = core.parse_utc(old.get("finished_at"))
            if finished and (now - finished).total_seconds() > SAMPLE_RESULT_SECONDS:
                del _SAMPLES[old_id]
        _SAMPLES[job_id] = {"job_id": job_id, "status": "running", "stage": "starting", "frames_done": 0, "frames_total": None,
                            "filename": Path(video.filename or "video").name, "started_at": now.isoformat(), "owner": actor}
        view = _sample_view(_SAMPLES[job_id])
    threading.Thread(target=_run_sample, args=(job_id, path), daemon=True, name=f"video-sample-{job_id[:8]}").start()
    return view


@router.get("/api/video-samples/{job_id}")
def sample_analysis(job_id: str, actor: str = Depends(authority_token)):
    with _SAMPLES_LOCK:
        job = _SAMPLES.get(job_id)
        if not job or job["owner"] != actor:
            raise HTTPException(404, "This video analysis was not found. Results are kept for one hour.")
        return _sample_view(job)


@router.get("/api/camera-footage")
def list_camera_footage(actor: str = Depends(authority_token)):
    return {"footage": store.rows("""SELECT f.footage_id,f.camera_id,c.display_name,f.original_filename,f.content_type,
      f.uploaded_by,f.uploaded_at,f.analysis_json,f.file_deleted_at FROM camera_footage f
      JOIN cameras c ON c.camera_id=f.camera_id ORDER BY f.uploaded_at DESC LIMIT 200""")}


@router.get("/api/camera-footage/{footage_id}/video")
def play_camera_footage(footage_id: str, actor: str = Depends(authority_token)):
    record = store.one("SELECT stored_path,content_type,original_filename,file_deleted_at FROM camera_footage WHERE footage_id=?", (footage_id,))
    if not record:
        raise HTTPException(404, "Camera footage not found.")
    if record["file_deleted_at"]:
        raise HTTPException(410, "The video was deleted under the footage retention policy; its analysis is kept.")
    # Clips always live in the footage folder. Older records stored an absolute path, so
    # only the file name is used; PureWindowsPath splits on both / and \ for either OS.
    path = (FOOTAGE_DIR / PureWindowsPath(record["stored_path"]).name).resolve()
    if not path.is_relative_to(FOOTAGE_DIR.resolve()) or not path.is_file():
        raise HTTPException(404, "Camera footage file is unavailable.")
    # The media type comes from the vetted extension, never from the uploader's header.
    media_type = VIDEO_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=record["original_filename"], content_disposition_type="inline")
