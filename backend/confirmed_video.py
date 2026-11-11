"""Native-frame clip counting with low-score association and confirmed vehicle tracks.

This policy applies to uploaded clips only. Stream line-crossing logic is separate;
the upgrade's full flow benchmark did not pass its preservation check.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from pathlib import Path
import math
from types import SimpleNamespace

import cv2
import numpy as np


POLICY = {'confidence': .1, 'new_track': .55, 'buffer': 90, 'min_hits': 5}


def make_tracker(people=False):
    from ultralytics.trackers.byte_tracker import BYTETracker
    return BYTETracker(SimpleNamespace(track_high_thresh=.25, track_low_thresh=.1,
        new_track_thresh=.25 if people else POLICY['new_track'], track_buffer=POLICY['buffer'],
        match_thresh=.8, fuse_score=True))


def analyze_video(path: Path, max_sampled_frames=1500, progress=None, preview=False, roi=None, reference_ignored_regions=None):
    import vision
    probe = cv2.VideoCapture(str(path))
    readable = probe.isOpened() and probe.read()[0]
    probe.release()
    if not readable:
        raise ValueError('OpenCV could not read this video. Use a supported MP4, AVI, MOV, or MKV file.')
    report = progress or (lambda *args: None)
    settings = vision.detector_settings()
    report('loading', 0, None)
    detector = vision._model()
    report('waiting', 0, None)
    with vision._ANALYSIS_LOCK:
        # Predict-mode detections must not inherit track-mode callbacks from a prior job.
        if hasattr(detector, 'reset_callbacks'):
            detector.reset_callbacks()
            detector.predictor = None
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            capture.release()
            raise ValueError('OpenCV could not read this video. Use a supported MP4, AVI, MOV, or MKV file.')
        fps = capture.get(cv2.CAP_PROP_FPS)
        fps = float(fps) if fps and 0 < fps < 240 else None
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        expected = min(total, max_sampled_frames) if total > 0 else None
        vehicles_tracker, people_tracker = make_tracker(), make_tracker(people=True)
        hits, votes = Counter(), defaultdict(Counter)
        people_ids = set()
        timeline = {}
        visible_counts, people_counts = [], []
        frame_number = 0
        complete = False
        best = (-1, -1, None, [], 0)
        inference = {key: settings[key] for key in ('imgsz', 'agnostic_nms', 'device')}
        def permitted(box, width, height):
            x, y = float(box[0] + box[2]) / 2, float(box[1] + box[3]) / 2
            if roi is not None and not vision._inside((x / width, y / height), roi):
                return False
            return not any(a <= x <= a + c and b <= y <= b + d for a, b, c, d in (reference_ignored_regions or []))
        def raw(result):
            return result.boxes.data.cpu().numpy()[:, :6] if result.boxes is not None else np.empty((0, 6), np.float32)
        from ultralytics.engine.results import Boxes
        try:
            while frame_number < max_sampled_frames:
                ok, frame = capture.read()
                if not ok:
                    complete = True
                    break
                frame_number += 1
                h, w = frame.shape[:2]
                vehicle_boxes = raw(detector.predict(frame, classes=list(vision.VEHICLE_CLASSES),
                    conf=min(POLICY['confidence'], settings['conf']), **inference, verbose=False)[0])
                # Separate class filtering preserves the detector/NMS configuration used
                # in the benchmark while retaining the existing people-count feature.
                person_boxes = raw(detector.predict(frame, classes=[vision.PERSON_CLASS], conf=settings['conf'],
                    **inference, verbose=False)[0])
                vehicle_tracks = vehicles_tracker.update(Boxes(vehicle_boxes, (h, w)))
                people_tracks = people_tracker.update(Boxes(person_boxes, (h, w)))
                visible = [box for box in vehicle_boxes if box[4] >= settings['conf'] and permitted(box, w, h)]
                for row in vehicle_tracks:
                    if permitted(row, w, h):
                        ident, class_id = int(row[4]), int(row[6])
                        hits[ident] += 1
                        votes[ident][vision.VEHICLE_CLASSES[class_id]] += 1
                people_ids.update(int(row[4]) for row in people_tracks)
                vehicle_count, person_count = len(visible), len(person_boxes)
                visible_counts.append(vehicle_count)
                people_counts.append(person_count)
                second = (frame_number - 1) / (fps or vision.SAMPLE_FPS)
                bucket = timeline.setdefault(int(second), {'people': 0, 'vehicles': 0})
                bucket['people'] = max(bucket['people'], person_count)
                bucket['vehicles'] = max(bucket['vehicles'], vehicle_count)
                if preview and (person_count, vehicle_count) > best[:2]:
                    outlines = [(list(box[:4]), int(box[5])) for box in [*visible, *person_boxes]]
                    best = (person_count, vehicle_count, frame.copy(), outlines, second)
                if frame_number % 10 == 0:
                    report('analysing', frame_number, expected)
            else:
                complete = not capture.grab()
        finally:
            capture.release()
    if not frame_number:
        raise ValueError('The video contains no readable frames.')
    confirmed = {ident for ident, count in hits.items() if count >= POLICY['min_hits']}
    categories = Counter(votes[ident].most_common(1)[0][0] for ident in confirmed)
    flags = ['clip_count_is_not_block_volume', 'camera_view_requires_site_calibration',
             'not_an_independently_verified_traffic_measurement', 'distinct_count_is_confirmed_track_estimate']
    if not complete:
        flags.append('clip_partially_analyzed_frame_limit_reached')
    if fps is not None and fps < 3:
        flags.append('low_source_frame_rate_distinct_counts_unreliable')
    if max(visible_counts, default=0) and not confirmed:
        flags.append('visible_vehicles_without_enough_track_confirmation')
    report('analysing', frame_number, frame_number)
    result = {'tracker': 'ByteTrack', 'model': settings['model'],
        'detector_settings': {**settings, 'association_confidence': POLICY['confidence'], 'clip_tracking_policy': 'confirmed_native',
                              'new_track_confidence': POLICY['new_track'], 'track_buffer': POLICY['buffer'], 'minimum_track_frames': POLICY['min_hits']},
        'counting_roi': roi, 'source_type': 'authorized_camera_video_analysis', 'sampled_frames': frame_number,
        'sample_interval_seconds': round(1 / fps, 3) if fps else None,
        'analyzed_seconds': round(frame_number / fps, 2) if fps else None,
        'clip_seconds': round(total / fps, 2) if fps and total > 0 else None, 'clip_fully_analyzed': complete,
        'clip_vehicle_count_estimate': len(confirmed),
        'vehicle_counts_by_class': {name: categories[name] for name in vision.VEHICLE_CLASSES.values()},
        'peak_vehicles_in_frame': max(visible_counts, default=0),
        'average_vehicles_in_frame': round(sum(visible_counts) / len(visible_counts), 2),
        'count_method': 'confirmed_tracker_ids_in_clip', 'raw_vehicle_tracks': len(hits),
        'vehicle_class_method': 'Most frequent vehicle label per confirmed track ID.',
        'track_count_notice': 'Vehicle tracks need at least five analysed frames. Occlusion can still give one vehicle multiple IDs; this is an estimate of distinct vehicles, not a verified total.',
        'people': {'most_in_one_frame': max(people_counts, default=0), 'average_in_frame': round(sum(people_counts) / len(people_counts), 1),
                   'different_people_tracked': len(people_ids)},
        'timeline': [{'second': second, **value} for second, value in sorted(timeline.items())],
        'quality_status': 'unverified', 'quality_flags': flags,
        'notice': 'Visible-frame counts and confirmed clip tracks have different meanings. Counts require independent camera-site references; they are not block traffic volumes or speed measurements.'}
    if preview and best[2] is not None:
        result['preview_image'] = vision._preview_image(best[2], best[3])
        result['preview_second'] = round(best[4], 2)
    return result
