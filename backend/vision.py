"""Authorized video clip analysis with YOLOv8 and OpenCV.

The returned counts are clip detections of people and vehicles, not calibrated traffic
volume, speed or crowd size. This module only reads the given file; the API decides whether
the clip is kept.
"""
from __future__ import annotations

import base64
import math
import os
import threading
from collections import Counter
from pathlib import Path
from typing import Callable

import core

_MODEL = None
_MODEL_LOCK = threading.Lock()
# The detector and its tracker keep state between frames, so clips are analysed one at a time.
_ANALYSIS_LOCK = threading.Lock()
PERSON_CLASS = 0
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
# Progress reports: (stage, sampled frames done, frames that will be sampled or None).
Progress = Callable[[str, int, "int | None"], None]
# The tracker links detections only when a vehicle moves a short distance between sampled
# frames; at one frame per second most moving vehicles are never followed. Ten frames per
# second keeps highway tracking close to every-frame results at a fraction of the cost.
SAMPLE_FPS = float(os.getenv("VIDEO_SAMPLE_FPS", "10"))
MAX_SAMPLED_FRAMES = int(os.getenv("VIDEO_MAX_SAMPLED_FRAMES", "1500"))


def detector_settings():
    confidence=float(os.getenv('YOLO_CONFIDENCE','0.35'))
    image_size=int(os.getenv('YOLO_IMAGE_SIZE','640'))
    if not math.isfinite(confidence) or not 0<confidence<1 or not 320<=image_size<=1536:
        raise RuntimeError('Detector confidence must be between 0 and 1, and image size between 320 and 1536.')
    weights=Path(os.getenv('YOLO_WEIGHTS') or core.MODELS_DIR/'yolov8n.pt')
    return {'model':weights.stem,'weights_file':weights.name,'conf':confidence,'imgsz':image_size,
            'agnostic_nms':os.getenv('YOLO_AGNOSTIC_NMS','0')=='1','device':os.getenv('YOLO_DEVICE','cpu'),
            'tracker':'ByteTrack'}


def _model():
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            try:
                from ultralytics import YOLO
            except ImportError as exc:
                raise RuntimeError("Install the project dependencies from the root requirements.txt to use video analysis.") from exc
            _disable_usage_statistics()
            # Default weights download on first use; configured weights may already be installed.
            weights = os.getenv("YOLO_WEIGHTS") or str(core.MODELS_DIR / "yolov8n.pt")
            try:
                _MODEL = YOLO(weights)
            except Exception as exc:  # download, file and format errors all mean the detector is unavailable
                raise RuntimeError(f"The detector could not be loaded from {weights}. Check the weights file or "
                                   f"the internet connection needed for its initial download. ({exc})") from exc
    return _MODEL


def _disable_usage_statistics() -> None:
    """Ultralytics sends anonymous usage statistics unless switched off; this app keeps video work local."""
    import importlib

    for module in ("ultralytics.utils.events", "ultralytics.hub.utils"):  # newer and older releases
        try:
            importlib.import_module(module).events.enabled = False
        except (ImportError, AttributeError):
            continue


def _reset_trackers(detector) -> None:
    """Start each clip with a fresh tracker so IDs and lost tracks never carry over."""
    for tracker in getattr(getattr(detector, "predictor", None), "trackers", None) or []:
        tracker.reset()


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_intersect(p1, p2, q1, q2) -> bool:
    d1, d2, d3, d4 = _cross(q1, q2, p1), _cross(q1, q2, p2), _cross(p1, p2, q1), _cross(p1, p2, q2)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def count_line_crossings(tracks, line):
    """Unique tracked vehicles crossing a finite line, split by direction.

    Points exactly on the line retain their previous stable side. A track counts
    once per sample, so boundary jitter cannot repeatedly count the same vehicle.
    """
    a,b=line;length=math.dist(a,b)
    if length<=0:raise ValueError('The counting line needs two different points.')
    counts={'in':0,'out':0,'total':0}
    for points in tracks.values():
        previous=None
        for _,x,y in points:
            point=(x,y);side=_cross(a,b,point)/length
            if abs(side)<.0005:continue
            if previous and side*previous[1]<0 and _segments_intersect(previous[0],point,a,b):
                counts['in' if side>0 else 'out']+=1;counts['total']+=1;break
            previous=(point,side)
    return counts


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
    settings=detector_settings()
    inference={k:settings[k] for k in ('conf','imgsz','agnostic_nms','device')}
    region_area = _area(region) or 1.0
    with _ANALYSIS_LOCK:
        # Open the stream before loading the detector, so an unreachable camera fails fast.
        from camera_capture import open_capture
        capture = open_capture(source, seconds)
        if not capture.isOpened():
            capture.release()
            raise ValueError("The camera stream could not be opened.")
        fps = capture.get(cv2.CAP_PROP_FPS)
        known_fps = fps if fps and 0 < fps < 240 else None
        file_timing = bool(known_fps and os.path.isfile(source))
        frame_limit = int(seconds * known_fps) if file_timing else None
        every_n = max(1, round((known_fps or 25) / SAMPLE_FPS)) if file_timing else 1
        tracks: dict[int, list[tuple[float, float, float]]] = {}
        occupancy: list[float] = []
        visible_counts=[]
        valid_moments = []
        valid_hashes = set()
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
                if min(frame.shape[:2]) < 64 or float(frame.std()) < .5:
                    continue  # Placeholders/dark failed views are not empty-road measurements.
                frame_number += 1
                if (frame_number - 1) % every_n:
                    continue
                sampled += 1
                moment = frame_number / known_fps if file_timing else time.monotonic() - started
                valid_moments.append(moment)
                if not file_timing:
                    import hashlib
                    valid_hashes.add(hashlib.sha256(frame.tobytes()).hexdigest())
                boxes = detector.track(frame, persist=sampled > 1, classes=list(VEHICLE_CLASSES), **inference,
                                       tracker='bytetrack.yaml',verbose=False)[0].boxes
                covered = 0.0
                visible=0
                if boxes is not None and len(boxes):
                    ids = boxes.id.int().tolist() if boxes.id is not None else [None] * len(boxes)
                    for (x1, y1, x2, y2), track_id in zip(boxes.xyxyn.tolist(), ids):
                        centre = ((x1 + x2) / 2, (y1 + y2) / 2)
                        if not _inside(centre,region):continue
                        visible+=1
                        covered += (x2 - x1) * (y2 - y1)
                        if track_id is not None:
                            tracks.setdefault(int(track_id), []).append((moment, centre[0], centre[1]))
                occupancy.append(min(1.0, covered / region_area))
                visible_counts.append(visible)
        finally:
            capture.release()
    if sampled == 0:
        raise ValueError("No frames could be read from the camera stream.")
    duration = frame_number / known_fps if file_timing else time.monotonic() - started
    crossings = None
    directions = None
    effective_fps = ((len(valid_moments) - 1) / (valid_moments[-1] - valid_moments[0])
                     if len(valid_moments) >= 2 and valid_moments[-1] > valid_moments[0] else 0)
    low_refresh = not file_timing and effective_fps < 3
    motion_unconfirmed = not file_timing and len(valid_hashes) < 2
    if counting_line and not low_refresh and not motion_unconfirmed:
        directions=count_line_crossings(tracks,counting_line)
        crossings=directions['total']
    queue = None
    if duration >= 5 and not low_refresh and not motion_unconfirmed:
        recent = duration - 3
        queue = 0
        for points in tracks.values():
            late = [p for p in points if p[0] >= recent]
            if len(late) >= 2 and _inside(late[-1][1:], region) and math.dist(late[0][1:], late[-1][1:]) < 0.02:
                queue += 1
    return {"model": settings['model'], 'detector_settings':settings, "duration_seconds": round(duration, 1), "sampled_frames": sampled,
            "tracked_vehicles": len(tracks), "line_crossings": crossings,
            'peak_vehicles_in_frame':max(visible_counts,default=0),'average_vehicles_in_frame':round(sum(visible_counts)/len(visible_counts),2) if visible_counts else 0,
            "direction_counts":directions,"tracker":"ByteTrack","source":"sampled_camera_measurement",
            'time_basis':'recording_frame_rate' if file_timing else 'elapsed_wall_time',
            'effective_sample_fps':round(effective_fps, 2), 'low_refresh_rate':low_refresh,
            'distinct_images':len(valid_hashes) if not file_timing else None,
            'placeholders_skipped':getattr(capture, 'placeholders_skipped', 0),
            'automatic_verification_blocked':low_refresh or motion_unconfirmed,
            'measurement_flags':(['low_refresh_rate_visible_counts_only'] if low_refresh else []) +
                                (['live_motion_not_confirmed'] if motion_unconfirmed else []),
            'measurement_notice':'Low-refresh imagery supports visible-frame estimates; reliable distinct counts, line flow and queues need a higher frame rate.' if low_refresh else
                                 'Images did not demonstrate changing live content; automatic verification is disabled.' if motion_unconfirmed else
                                 'Interval estimates require camera-site calibration.',
            "flow_notice":"Flow per hour is extrapolated from this sampled interval, not a full-hour observed count.",
            "flow_per_hour": round(crossings / duration * 3600, 1) if crossings is not None and duration > 0 else None,
            "occupancy": round(sum(occupancy) / len(occupancy), 3), "queue_length": queue}


def _preview_image(frame, boxes: list[tuple[list[float], int]]) -> str:
    """The frame as a JPEG data URL, with people outlined in orange and vehicles in blue."""
    import cv2

    for (x1, y1, x2, y2), class_id in boxes:
        colour = (0, 120, 230) if class_id == PERSON_CLASS else (200, 90, 20)  # BGR
        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), colour, 3)
    height, width = frame.shape[:2]
    if width > 960:
        frame = cv2.resize(frame, (960, round(height * 960 / width)))
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii") if ok else ""


def analyze_video(path: Path, max_sampled_frames: int = MAX_SAMPLED_FRAMES, progress: Progress | None = None,
                  preview: bool = False, roi: list | None = None) -> dict:
    """Count people and vehicles in a clip. With `preview`, the frame with the most people (or, with
    no people, the most vehicles) is returned with the detections drawn on it."""
    mode = os.getenv('YOLO_CLIP_TRACKING', 'legacy').strip().lower()
    if mode == 'confirmed_native':
        from confirmed_video import analyze_video as confirmed_video
        return confirmed_video(path, max_sampled_frames, progress, preview, roi)
    if mode != 'legacy':
        raise RuntimeError('YOLO_CLIP_TRACKING must be legacy or confirmed_native.')
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is not installed. Reinstall the project dependencies from the root requirements.txt.") from exc

    # Check the file before loading the detector, so a file that is not a video fails fast.
    probe = cv2.VideoCapture(str(path))
    readable = probe.isOpened() and probe.read()[0]
    probe.release()
    if not readable:
        raise ValueError("OpenCV could not read this video. Use a supported MP4, AVI, MOV, or MKV file.")
    report = progress or (lambda stage, done, total: None)
    settings=detector_settings()
    inference={k:settings[k] for k in ('conf','imgsz','agnostic_nms','device')}
    report("loading", 0, None)
    detector = _model()
    report("waiting", 0, None)  # until any other analysis has finished
    with _ANALYSIS_LOCK:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            capture.release()
            raise ValueError("OpenCV could not read this video. Use a supported MP4, AVI, MOV, or MKV file.")
        fps = capture.get(cv2.CAP_PROP_FPS)
        known_fps = fps if fps and fps > 0 else None
        total_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        every_n = max(1, round((known_fps or 30) / SAMPLE_FPS))
        expected = min(max_sampled_frames, math.ceil(total_frames / every_n)) if total_frames > 0 else None
        frame_number = sampled = peak = vehicle_total = 0
        track_class_votes: dict[int, Counter] = {}
        peak_frame_counts = {name: 0 for name in VEHICLE_CLASSES.values()}
        people_tracks: set[int] = set()
        people_peak = people_total = 0
        timeline: dict[int, dict[str, int]] = {}
        best = (-1, -1, None, [], 0.0)  # people, vehicles, frame, boxes, second
        tracked_boxes_available = False
        reached_end = False
        _reset_trackers(detector)
        report("analysing", 0, expected)
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
                second = (frame_number - 1) / known_fps if known_fps else (sampled - 1) / SAMPLE_FPS
                # persist=False on the first frame creates a new tracker for this clip.
                result = detector.track(frame, persist=sampled > 1, classes=[PERSON_CLASS, *VEHICLE_CLASSES],
                                        **inference, tracker='bytetrack.yaml', verbose=False)[0]
                if sampled % 10 == 0:
                    report("analysing", sampled, expected)
                boxes = result.boxes
                raw_classes = boxes.cls.int().tolist() if boxes is not None and len(boxes) else []
                centres = boxes.xyxyn.tolist() if raw_classes else []
                keep=[i for i,c in enumerate(raw_classes) if c==PERSON_CLASS or roi is None or
                      _inside(((centres[i][0]+centres[i][2])/2,(centres[i][1]+centres[i][3])/2),roi)]
                classes=[raw_classes[i] for i in keep]
                people = classes.count(PERSON_CLASS)
                vehicles = len(classes) - people
                vehicle_total+=vehicles
                people_total += people
                people_peak = max(people_peak, people)
                if vehicles > peak:
                    peak = vehicles
                    peak_frame_counts = {name: classes.count(class_id) for class_id, name in VEHICLE_CLASSES.items()}
                bucket = timeline.setdefault(int(second), {"people": 0, "vehicles": 0})
                bucket["people"], bucket["vehicles"] = max(bucket["people"], people), max(bucket["vehicles"], vehicles)
                if preview and (people, vehicles) > best[:2]:
                    best = (people, vehicles, frame.copy(), list(zip([boxes.xyxy.tolist()[i] for i in keep],classes)) if classes else [], second)
                if not classes:
                    continue
                ids = boxes.id
                if ids is not None:
                    tracked_boxes_available = True
                    for class_id, track_id in zip(classes,[ids.int().tolist()[i] for i in keep]):
                        if class_id == PERSON_CLASS:
                            people_tracks.add(int(track_id))
                        elif (label := VEHICLE_CLASSES.get(int(class_id))):
                            track_class_votes.setdefault(int(track_id), Counter())[label] += 1
            else:
                reached_end = not capture.grab()  # the frame limit stopped the loop; is anything left?
        finally:
            capture.release()
    if sampled == 0:
        raise ValueError("The video contains no readable frames.")
    report("analysing", sampled, sampled)
    if not tracked_boxes_available:
        count_estimate = peak
        vehicle_counts = peak_frame_counts
        count_method = "peak_vehicles_in_sampled_frame"
    else:
        count_estimate = len(track_class_votes)
        # A changing class label must not count one ID as both a car and a bus.
        labels = Counter(votes.most_common(1)[0][0] for votes in track_class_votes.values())
        vehicle_counts = {name: labels[name] for name in VEHICLE_CLASSES.values()}
        count_method = "unique_tracker_ids_in_clip"
    quality_flags = ["clip_count_is_not_block_volume", "camera_view_requires_site_calibration",
                     "not_an_independently_verified_traffic_measurement", "dense_crowds_are_undercounted"]
    if not reached_end:
        quality_flags.append("clip_partially_analyzed_frame_limit_reached")
    analysis = {
        "tracker":"ByteTrack",
        "model": settings['model'], 'detector_settings':settings, 'counting_roi':roi, "source_type": "authorized_camera_video_analysis",
        "sampled_frames": sampled, "sample_interval_seconds": round(every_n / known_fps, 2) if known_fps else None,
        "analyzed_seconds": round(frame_number / known_fps, 1) if known_fps else None,
        "clip_seconds": round(total_frames / known_fps, 1) if known_fps and total_frames > 0 else None,
        "clip_fully_analyzed": reached_end,
        "clip_vehicle_count_estimate": count_estimate, "vehicle_counts_by_class": vehicle_counts,
        'peak_vehicles_in_frame':peak,'average_vehicles_in_frame':round(vehicle_total/sampled,2),
        'track_count_notice': 'Different tracker IDs can belong to the same vehicle after occlusion; the clip track total is not a verified distinct-vehicle count.' if tracked_boxes_available else
                             'Tracking IDs were unavailable. The estimate and its categories describe the peak vehicle frame, not distinct vehicles across the clip.',
        'vehicle_class_method': 'Most frequent vehicle label per track ID; ties retain the first label.' if tracked_boxes_available else 'Category counts in the peak vehicle frame.',
        "count_method": count_method,
        # People: the most seen in one frame, the average per sampled frame, and different tracked people.
        "people": {"most_in_one_frame": people_peak, "average_in_frame": round(people_total / sampled, 1),
                   "different_people_tracked": len(people_tracks) if tracked_boxes_available else None},
        # Per second of video: the most people and vehicles seen in any sampled frame of that second.
        "timeline": [{"second": second, **counts} for second, counts in sorted(timeline.items())],
        "quality_status": "unverified",
        "quality_flags": quality_flags,
        "notice": "Counts describe detections in this clip only. They are not calibrated traffic volume, flow rate, speed, "
                  "crowd size, or verified ground truth. In dense crowds people hide behind each other, so real numbers are higher.",
    }
    if preview and best[2] is not None:
        analysis["preview_image"] = _preview_image(best[2], best[3])
        analysis["preview_second"] = round(best[4], 1)
    return analysis
