"""Authorized video clip analysis with YOLOv8 and OpenCV.

The returned counts are clip detections, not calibrated traffic volume or speed.
Videos are read from a temporary file and are not persisted by this module.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

_MODEL = None
_MODEL_LOCK = threading.Lock()
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


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


def analyze_video(path: Path, max_sampled_frames: int = 600) -> dict:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is not installed. Reinstall the project dependencies from the root requirements.txt.") from exc

    detector = _model()
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError("OpenCV could not read this video. Use a supported MP4, AVI, or MOV file.")
    fps = capture.get(cv2.CAP_PROP_FPS)
    every_n = max(1, round(fps)) if fps and fps > 0 else 15
    frame_number = sampled = peak = 0
    unique_tracks: set[int] = set()
    per_class_tracks: dict[str, set[int]] = {name: set() for name in VEHICLE_CLASSES.values()}
    per_class_peak = {name: 0 for name in VEHICLE_CLASSES.values()}
    tracked_boxes_available = False
    try:
        while sampled < max_sampled_frames:
            ok, frame = capture.read()
            if not ok:
                break
            frame_number += 1
            if (frame_number - 1) % every_n:
                continue
            sampled += 1
            result = detector.track(frame, persist=True, classes=list(VEHICLE_CLASSES),
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
    return {
        "model": "YOLOv8n", "source_type": "authorized_camera_video_analysis",
        "sampled_frames": sampled, "sample_interval_seconds": round(every_n / fps, 2) if fps and fps > 0 else None,
        "clip_vehicle_count_estimate": count_estimate, "vehicle_counts_by_class": vehicle_counts,
        "count_method": count_method,
        "quality_status": "unverified",
        "quality_flags": ["clip_count_is_not_block_volume", "camera_view_requires_site_calibration", "not_an_independently_verified_traffic_measurement"],
        "notice": "Counts describe detections in this clip only. They are not calibrated traffic volume, flow rate, speed, or verified ground truth.",
    }
