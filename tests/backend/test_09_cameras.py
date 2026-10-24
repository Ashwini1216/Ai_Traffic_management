"""Camera registry, stream settings, failure handling, and sample video analysis.

Real vehicle counting needs a video and the YOLOv8n weights; set FLOWSTATE_TEST_VIDEO to an
authorised clip with traffic to include that test. The people-counting test builds its own clip
from Ultralytics' sample photo and runs when models/yolov8n.pt is present.
"""
import os
import time
from pathlib import Path

import pytest

import fake_vision

CAMERA = {"camera_id": "cam-test-01", "display_name": "Hebbal test camera", "area": "Hebbal", "road": "Hebbal Flyover",
          "latitude": 13.0356, "longitude": 77.5971, "source_type": "approved_ip_camera", "authorization_status": "approved",
          "operational_status": "online", "stream_url": "rtsp://camera.example/stream", "counting_line": [[0.0, 0.5], [1.0, 0.5]]}


def test_streams_must_use_a_network_address(client, authority):
    bad = client.post("/api/cameras", json={**CAMERA, "camera_id": "cam-bad", "stream_url": "file:///etc/passwd"}, headers=authority)
    assert bad.status_code == 422


def test_saved_stream_addresses_are_never_returned_and_survive_edits(client, authority):
    assert client.post("/api/cameras", json=CAMERA, headers=authority).status_code == 200
    without_address = {k: v for k, v in CAMERA.items() if k != "stream_url"}
    assert client.post("/api/cameras", json={**without_address, "display_name": "Hebbal camera (renamed)"}, headers=authority).status_code == 200
    listed = next(c for c in client.get("/api/cameras", headers=authority).json()["cameras"] if c["camera_id"] == CAMERA["camera_id"])
    assert listed["has_stream"] and "stream_url" not in listed and listed["display_name"] == "Hebbal camera (renamed)"
    assert listed["counting_line"] == CAMERA["counting_line"]


def test_a_camera_that_keeps_failing_goes_offline(client, authority, tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video")
    camera = {**CAMERA, "camera_id": "cam-flaky", "stream_url": str(broken), "sample_seconds": 5}
    assert client.post("/api/cameras", json=camera, headers=authority).status_code == 200
    outcomes = [client.post("/api/cameras/cam-flaky/collect", headers=authority).json() for _ in range(3)]
    assert [o["ok"] for o in outcomes] == [False, False, False] and outcomes[-1]["marked_offline"], outcomes
    assert client.post("/api/cameras/cam-flaky/collect", headers=authority).status_code == 403
    client.post("/api/jobs/data_quality/run", headers=authority)
    kinds = {a["kind"] for a in client.get("/api/alerts?status=active", headers=authority).json()["alerts"]}
    assert "camera_offline" in kinds


@pytest.mark.skipif(not os.environ.get("FLOWSTATE_TEST_VIDEO"), reason="set FLOWSTATE_TEST_VIDEO to a traffic clip to run")
def test_stream_sampling_counts_vehicles_crossing_the_line(client, authority):
    camera = {**CAMERA, "camera_id": "cam-video", "stream_url": os.environ["FLOWSTATE_TEST_VIDEO"], "sample_seconds": 20}
    assert client.post("/api/cameras", json=camera, headers=authority).status_code == 200
    result = client.post("/api/cameras/cam-video/collect", headers=authority).json()
    assert result["ok"] and result["line_crossings"] >= 1 and 0 <= result["occupancy"] <= 1, result


def wait_for(client, headers, job_id, timeout_s=120):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = client.get(f"/api/video-samples/{job_id}", headers=headers).json()
        if job["status"] != "running":
            return job
        time.sleep(0.2)
    raise AssertionError("the analysis did not finish in time")


def test_a_sample_video_is_analysed_without_a_camera_and_then_deleted(client, authority, citizen, monkeypatch, tmp_path):
    import cameras
    import vision

    monkeypatch.setattr(vision, "analyze_video", fake_vision.analyze_video)
    clip = tmp_path / "junction.mp4"
    clip.write_bytes(b"not checked by the stand-in analysis")
    with clip.open("rb") as handle:
        started = client.post("/api/video-samples", files={"video": ("junction.mp4", handle, "video/mp4")}, headers=authority)
    assert started.status_code == 202, started.text
    job = started.json()
    assert job["status"] == "running" and job["filename"] == "junction.mp4" and "owner" not in job
    assert client.get(f"/api/video-samples/{job['job_id']}", headers=citizen).status_code == 403
    done = wait_for(client, authority, job["job_id"])
    assert done["status"] == "done" and done["frames_done"] == 30
    assert done["analysis"]["people"]["most_in_one_frame"] == 4 and done["analysis"]["preview_image"].startswith("data:image/jpeg")
    assert not list(cameras.SAMPLE_DIR.glob(f"{job['job_id']}*")), "the uploaded video is deleted after the analysis"


def test_a_file_that_is_not_a_video_is_reported(client, authority, tmp_path):
    clip = tmp_path / "notes.mp4"
    clip.write_bytes(b"this is not a video")
    with clip.open("rb") as handle:
        job = client.post("/api/video-samples", files={"video": ("notes.mp4", handle, "video/mp4")}, headers=authority).json()
    failed = wait_for(client, authority, job["job_id"])
    assert failed["status"] == "failed" and "could not read this video" in failed["error"]
    with clip.open("rb") as handle:
        wrong_type = client.post("/api/video-samples", files={"video": ("notes.txt", handle, "text/plain")}, headers=authority)
    assert wrong_type.status_code == 415


NEEDS_WEIGHTS = pytest.mark.skipif(not Path(os.environ["YOLO_WEIGHTS"]).is_file(),
                                   reason="needs models/yolov8n.pt (downloaded on first video analysis)")


def bus_stop_clip(folder: Path) -> Path:
    """Three seconds of Ultralytics' sample photo of people beside a bus, drifting sideways."""
    import cv2
    import numpy as np
    from ultralytics.utils import ASSETS

    image = cv2.imread(str(ASSETS / "bus.jpg"))
    height, width = image.shape[:2]
    clip = folder / "bus-stop.mp4"
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (width, height))
    for frame in range(30):
        writer.write(cv2.warpAffine(image, np.float32([[1, 0, frame * 2], [0, 1, 0]]), (width, height), borderMode=cv2.BORDER_REPLICATE))
    writer.release()
    return clip


@NEEDS_WEIGHTS
def test_people_and_vehicles_are_counted_in_a_real_clip(client, authority, tmp_path):
    clip = bus_stop_clip(tmp_path)
    with clip.open("rb") as handle:
        job = client.post("/api/video-samples", files={"video": ("bus-stop.mp4", handle, "video/mp4")}, headers=authority).json()
    done = wait_for(client, authority, job["job_id"])
    assert done["status"] == "done", done
    analysis = done["analysis"]
    assert analysis["people"]["most_in_one_frame"] >= 3 and analysis["people"]["different_people_tracked"] >= 3, analysis["people"]
    assert analysis["vehicle_counts_by_class"]["bus"] >= 1 and analysis["clip_fully_analyzed"]
    assert len(analysis["timeline"]) == 3 and analysis["preview_image"].startswith("data:image/jpeg;base64,")


@NEEDS_WEIGHTS
def test_a_clip_from_a_registered_camera_is_saved_with_its_counts(client, authority, tmp_path):
    camera = {**CAMERA, "camera_id": "cam-clip", "stream_url": None}
    assert client.post("/api/cameras", json=camera, headers=authority).status_code == 200
    with bus_stop_clip(tmp_path).open("rb") as handle:
        saved = client.post("/api/cameras/cam-clip/analyze-video", files={"video": ("bus-stop.mp4", handle, "video/mp4")},
                            data={"observed_date": "2026-10-01", "time_block": "Morning"}, headers=authority)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["analysis"]["clip_vehicle_count_estimate"] >= 1 and body["analysis"]["people"]["most_in_one_frame"] >= 3
    assert "preview_image" not in body["analysis"], "saved clips keep their video, so no preview frame is stored"
    footage = client.get("/api/camera-footage", headers=authority).json()["footage"]
    assert any(f["footage_id"] == body["footage_id"] for f in footage)
