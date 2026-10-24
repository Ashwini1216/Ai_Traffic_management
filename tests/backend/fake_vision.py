"""A stand-in for the YOLOv8n clip analysis, for tests of the API and the page rather than the detector.

It reports progress like the real analysis and returns fixed counts, with a plain preview image.
"""
from __future__ import annotations

import time

# Pause between progress reports; the browser tests slow it down so the page shows the progress.
STEP_SECONDS = 0.15
RESULT = {
    "model": "YOLOv8n", "source_type": "authorized_camera_video_analysis", "sampled_frames": 30,
    "sample_interval_seconds": 0.1, "analyzed_seconds": 3.0, "clip_seconds": 3.0, "clip_fully_analyzed": True,
    "clip_vehicle_count_estimate": 3, "vehicle_counts_by_class": {"car": 2, "motorcycle": 0, "bus": 1, "truck": 0},
    "count_method": "unique_tracker_ids_in_clip",
    "people": {"most_in_one_frame": 4, "average_in_frame": 3.5, "different_people_tracked": 6},
    "timeline": [{"second": s, "people": 3 + s % 2, "vehicles": 2} for s in range(3)],
    "quality_status": "unverified", "quality_flags": ["dense_crowds_are_undercounted"],
    "notice": "Counts describe detections in this clip only.",
}


def _preview() -> str:
    import base64

    import cv2
    import numpy as np

    ok, encoded = cv2.imencode(".jpg", np.full((180, 320, 3), (70, 150, 90), np.uint8))
    return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")


def analyze_video(path, max_sampled_frames: int = 1500, progress=None, preview: bool = False) -> dict:
    report = progress or (lambda stage, done, total: None)
    for done in range(0, 31, 10):
        report("analysing", done, 30)
        time.sleep(STEP_SECONDS)
    result = dict(RESULT)
    if preview:
        result.update(preview_image=_preview(), preview_second=1.0)
    return result
