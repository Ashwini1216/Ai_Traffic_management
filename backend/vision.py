"""Authorized video clip analysis with YOLOv8 and OpenCV.

The returned counts are clip detections, not calibrated traffic volume or speed.
This module only reads the given file; the API decides whether the clip is kept.
"""
from __future__ import annotations

import math
import os
import threading
from pathlib import Path

_MODEL = None
_MODEL_LOCK = threading.Lock()
# The detector and its tracker keep state between frames, so clips are analysed one at a time.
_ANALYSIS_LOCK = threading.Lock()
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
# The tracker links detections only when a vehicle moves a short distance between sampled
# frames; at one frame per second most moving vehicles are never followed. Ten frames per
# second keeps highway tracking close to every-frame results at a fraction of the cost.
SAMPLE_FPS = float(os.getenv("VIDEO_SAMPLE_FPS", "10"))
MAX_SAMPLED_FRAMES = int(os.getenv("VIDEO_MAX_SAMPLED_FRAMES", "1500"))


def _model():
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            try:
                from ultralytics import YOLO
            except ImportError as exc:
                raise RuntimeError("Install the project dependencies from the root requirements.txt to use video analysis.") from exc
            weights = os.getenv("YOLO_WEIGHTS", "yolov8n.pt")
            _MODEL = YOLO(weights)
    return _MODEL


def _reset_trackers(detector) -> None:
    """Start each clip with a fresh tracker so IDs and lost tracks never carry over."""
    for tracker in getattr(getattr(detector, "predictor", None), "trackers", None) or []:
        tracker.reset()


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_intersect(p1, p2, q1, q2) -> bool:
    d1, d2, d3, d4 = _cross(q1, q2, p1), _cross(q1, q2, p2), _cross(p1, p2, q1), _cross(p1, p2, q2)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def _inside(point, polygon) -> bool:
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


def _area(polygon) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]))) / 2


def analyze_stream(source: str, seconds: float, counting_line: list | None = None, roi: list | None = None) -> dict:
    """Sample an approved camera stream for a short interval and derive traffic indicators.

    counting_line: two points [[x, y], [x, y]] in 0..1 image coordinates. A tracked vehicle
    whose centre crosses it is counted once; flow per hour comes from these crossings.
    roi: polygon of 0..1 points for occupancy and queues (defaults to the whole image).
    occupancy: mean share of the region covered by vehicle boxes (approximate; overlaps
    are not removed). queue: vehicles in the region that barely moved during the last
    three seconds. Without a counting line, flow is not estimated rather than guessed.
    """
    import time
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is not installed. Reinstall the project dependencies from the root requirements.txt.") from exc

    region = roi or [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
    region_area = _area(region) or 1.0
    with _ANALYSIS_LOCK:
        # Open the stream before loading the detector, so an unreachable camera fails fast.
        capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            capture.release()
            raise ValueError("The camera stream could not be opened.")
        fps = capture.get(cv2.CAP_PROP_FPS)
        known_fps = fps if fps and 0 < fps < 240 else None
        frame_limit = int(seconds * known_fps) if known_fps and os.path.exists(source) else None
        every_n = max(1, round((known_fps or 25) / SAMPLE_FPS))
        tracks: dict[int, list[tuple[float, float, float]]] = {}
        occupancy: list[float] = []
        frame_number = sampled = 0
        try:
            detector = _model()
            _reset_trackers(detector)
            started = time.monotonic()
            while True:
                if frame_limit is not None and frame_number >= frame_limit:
                    break
                if frame_limit is None and time.monotonic() - started >= seconds:
                    break
                ok, frame = capture.read()
                if not ok:
                    break
                frame_number += 1
                if (frame_number - 1) % every_n:
                    continue
                sampled += 1
                moment = frame_number / known_fps if known_fps else time.monotonic() - started
                boxes = detector.track(frame, persist=sampled > 1, classes=list(VEHICLE_CLASSES), conf=0.35, verbose=False)[0].boxes
                covered = 0.0
                if boxes is not None and len(boxes):
                    ids = boxes.id.int().tolist() if boxes.id is not None else [None] * len(boxes)
                    for (x1, y1, x2, y2), track_id in zip(boxes.xyxyn.tolist(), ids):
                        centre = ((x1 + x2) / 2, (y1 + y2) / 2)
                        if _inside(centre, region):
                            covered += (x2 - x1) * (y2 - y1)
                        if track_id is not None:
                            tracks.setdefault(int(track_id), []).append((moment, centre[0], centre[1]))
                occupancy.append(min(1.0, covered / region_area))
        finally:
            capture.release()
    if sampled == 0:
        raise ValueError("No frames could be read from the camera stream.")
    duration = frame_number / known_fps if known_fps else time.monotonic() - started
    crossings = None
    if counting_line:
        a, b = counting_line
        crossings = sum(any(_segments_intersect(p[1:], q[1:], a, b) for p, q in zip(points, points[1:])) for points in tracks.values())
    queue = None
    if duration >= 5:
        recent = duration - 3
        queue = 0
        for points in tracks.values():
            late = [p for p in points if p[0] >= recent]
            if len(late) >= 2 and _inside(late[-1][1:], region) and math.dist(late[0][1:], late[-1][1:]) < 0.02:
                queue += 1
    return {"model": "YOLOv8n", "duration_seconds": round(duration, 1), "sampled_frames": sampled,
            "tracked_vehicles": len(tracks), "line_crossings": crossings,
            "flow_per_hour": round(crossings / duration * 3600, 1) if crossings is not None and duration > 0 else None,
            "occupancy": round(sum(occupancy) / len(occupancy), 3), "queue_length": queue}


def analyze_video(path: Path, max_sampled_frames: int = MAX_SAMPLED_FRAMES) -> dict:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is not installed. Reinstall the project dependencies from the root requirements.txt.") from exc

    detector = _model()
    with _ANALYSIS_LOCK:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise ValueError("OpenCV could not read this video. Use a supported MP4, AVI, MOV, or MKV file.")
        fps = capture.get(cv2.CAP_PROP_FPS)
        known_fps = fps if fps and fps > 0 else None
        total_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        every_n = max(1, round((known_fps or 30) / SAMPLE_FPS))
        frame_number = sampled = peak = 0
        unique_tracks: set[int] = set()
        per_class_tracks: dict[str, set[int]] = {name: set() for name in VEHICLE_CLASSES.values()}
        per_class_peak = {name: 0 for name in VEHICLE_CLASSES.values()}
        tracked_boxes_available = False
        reached_end = False
        _reset_trackers(detector)
        try:
            while sampled < max_sampled_frames:
                ok, frame = capture.read()
                if not ok:
                    reached_end = True
                    break
                frame_number += 1
                if (frame_number - 1) % every_n:
                    continue
                sampled += 1
                # persist=False on the first frame creates a new tracker for this clip.
                result = detector.track(frame, persist=sampled > 1, classes=list(VEHICLE_CLASSES),
                                        conf=0.35, verbose=False)[0]
                boxes = result.boxes
                if boxes is None or len(boxes) == 0:
                    continue
                classes = boxes.cls.int().tolist()
                peak = max(peak, len(classes))
                ids = boxes.id
                if ids is not None:
                    tracked_boxes_available = True
                    track_ids = ids.int().tolist()
                    for class_id, track_id in zip(classes, track_ids):
                        label = VEHICLE_CLASSES.get(int(class_id))
                        if label:
                            unique_tracks.add(int(track_id))
                            per_class_tracks[label].add(int(track_id))
                counts = {name: classes.count(class_id) for class_id, name in VEHICLE_CLASSES.items()}
                for name, count in counts.items():
                    per_class_peak[name] = max(per_class_peak[name], count)
            else:
                reached_end = not capture.grab()  # the frame limit stopped the loop; is anything left?
        finally:
            capture.release()
    if sampled == 0:
        raise ValueError("The video contains no readable frames.")
    if not tracked_boxes_available:
        count_estimate = peak
        vehicle_counts = per_class_peak
        count_method = "peak_vehicles_in_sampled_frame"
    else:
        count_estimate = len(unique_tracks)
        vehicle_counts = {name: len(ids) for name, ids in per_class_tracks.items()}
        count_method = "unique_tracker_ids_in_clip"
    quality_flags = ["clip_count_is_not_block_volume", "camera_view_requires_site_calibration",
                     "not_an_independently_verified_traffic_measurement"]
    if not reached_end:
        quality_flags.append("clip_partially_analyzed_frame_limit_reached")
    return {
        "model": "YOLOv8n", "source_type": "authorized_camera_video_analysis",
        "sampled_frames": sampled, "sample_interval_seconds": round(every_n / known_fps, 2) if known_fps else None,
        "analyzed_seconds": round(frame_number / known_fps, 1) if known_fps else None,
        "clip_seconds": round(total_frames / known_fps, 1) if known_fps and total_frames > 0 else None,
        "clip_fully_analyzed": reached_end,
        "clip_vehicle_count_estimate": count_estimate, "vehicle_counts_by_class": vehicle_counts,
        "count_method": count_method,
        "quality_status": "unverified",
        "quality_flags": quality_flags,
        "notice": "Counts describe detections in this clip only. They are not calibrated traffic volume, flow rate, speed, or verified ground truth.",
    }
