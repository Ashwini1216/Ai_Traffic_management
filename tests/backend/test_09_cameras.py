"""Camera registry, stream settings, and failure handling.

Real vehicle counting needs a video and the YOLOv8n weights; set FLOWSTATE_TEST_VIDEO to an
authorised clip with traffic to include that test.
"""
import os

import pytest

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
