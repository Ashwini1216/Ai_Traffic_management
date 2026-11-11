"""Frozen counting-policy selection on train scenes, followed once by four new official test scenes."""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np
from scipy.optimize import linear_sum_assignment

import core
import vision
from evaluate_vehicle_counts import annotations, countable, detection_counts, iou_matrix, summarize_frames

DATA = core.DATA_DIR / 'research' / 'vehicle-counts-v2'
POLICIES = [
    {'name': 'previous_sampled', 'step': 2, 'confidence': .35, 'new_track': .25, 'buffer': 30, 'min_hits': 1},
    {'name': 'native_rate', 'step': 1, 'confidence': .35, 'new_track': .25, 'buffer': 30, 'min_hits': 1},
    {'name': 'native_recovery', 'step': 1, 'confidence': .1, 'new_track': .35, 'buffer': 60, 'min_hits': 2},
    {'name': 'native_confirmed', 'step': 1, 'confidence': .1, 'new_track': .45, 'buffer': 60, 'min_hits': 3},
    {'name': 'native_strict', 'step': 1, 'confidence': .1, 'new_track': .55, 'buffer': 90, 'min_hits': 5},
    {'name': 'sampled_recovery', 'step': 2, 'confidence': .1, 'new_track': .45, 'buffer': 60, 'min_hits': 3},
]


def tracker(policy):
    from ultralytics.trackers.byte_tracker import BYTETracker
    return BYTETracker(SimpleNamespace(track_high_thresh=.25, track_low_thresh=.1, new_track_thresh=policy['new_track'],
        track_buffer=policy['buffer'], match_thresh=.8, fuse_score=True))


def cache(sample, detector):
    import cv2
    folder = DATA / 'clips' / sample['sequence']
    for filename, key in [('sample.avi', 'video_sha256'), ('annotation.xml', 'annotation_sha256')]:
        if hashlib.sha256((folder / filename).read_bytes()).hexdigest() != sample[key]:
            raise ValueError('Source hash mismatch: ' + sample['sequence'])
    weights = core.MODELS_DIR / 'yolov8s.pt'
    from importlib.metadata import version
    identity = {'source': sample['video_sha256'], 'weights': hashlib.sha256(weights.read_bytes()).hexdigest(),
                'confidence': .1, 'image_size': 960, 'agnostic_nms': True, 'ultralytics': version('ultralytics')}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    target = DATA / 'cache' / f"{sample['sequence']}-{digest}.npz"
    if target.exists():
        return np.load(target, allow_pickle=False)
    target.parent.mkdir(exist_ok=True)
    capture = cv2.VideoCapture(str(folder / 'sample.avi'))
    values = []
    index = 0
    started = time.monotonic()
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            result = detector.predict(frame, classes=list(vision.VEHICLE_CLASSES), conf=.1, imgsz=960,
                                      agnostic_nms=True, device='cpu', verbose=False)[0]
            boxes = result.boxes.data.cpu().numpy()
            if len(boxes):
                values.append(np.column_stack([np.full(len(boxes), index), boxes[:, :6]]))
            index += 1
    finally:
        capture.release()
    data = np.vstack(values).astype(np.float32) if values else np.empty((0, 7), np.float32)
    np.savez_compressed(target, boxes=data, width=w, height=h, frames=index,
                        processing_seconds=time.monotonic() - started)
    target.with_suffix('.json').write_text(json.dumps(identity, indent=2))
    print('Cached native detections:', sample['sequence'], index, 'frames', flush=True)
    return np.load(target, allow_pickle=False)


def scene(sample, cached, policy):
    from ultralytics.engine.results import Boxes
    w, h = int(cached['width']), int(cached['height'])
    reference, ignored = annotations(DATA / 'clips' / sample['sequence'])
    source = cached['boxes']
    detections = {i: source[source[:, 0] == i, 1:] for i in range(int(cached['frames']))}
    engine = tracker(policy)
    frame_tracks = {}
    trajectories = defaultdict(list)
    hits = Counter()
    reference_tracks = defaultdict(list)
    display_rows = []
    for i in range(int(cached['frames'])):
        if i % policy['step']:
            continue
        raw = detections[i]
        associated = engine.update(Boxes(raw[raw[:, 4] >= policy['confidence']], (h, w)))
        kept = []
        for row in associated:
            x1, y1, x2, y2, ident = row[:5]
            box = [float(x1), float(y1), float(x2 - x1), float(y2 - y1)]
            if not countable(box, ignored, w, h):
                continue
            ident = int(ident)
            kept.append((ident, box))
            hits[ident] += 1
            trajectories[ident].append(((i + 1) / sample['fps'], (x1 + x2) / (2 * w), (y1 + y2) / (2 * h)))
        frame_tracks[i] = kept
    confirmed = {ident for ident, hit in hits.items() if hit >= policy['min_hits']}
    pair_counts = Counter()
    identity_truth = identity_predictions = 0
    for i in range(0, int(cached['frames']), 2):
        truth = [(ident, box) for ident, box in reference.get(i + 1, []) if countable(box, ignored, w, h)]
        raw = detections[i]
        visible = [[float(a), float(b), float(c - a), float(d - b)] for a, b, c, d, score, _ in raw
                   if score >= .35 and countable([a, b, c - a, d - b], ignored, w, h)]
        tp, fp, fn = detection_counts([box for _, box in truth], visible)
        display_rows.append({'reference': len(truth), 'predicted': len(visible), 'tp': tp, 'fp': fp, 'fn': fn})
        guessed = [(ident, box) for ident, box in frame_tracks.get(i, []) if ident in confirmed]
        identity_truth += len(truth)
        identity_predictions += len(guessed)
        if truth and guessed:
            overlap = iou_matrix([box for _, box in truth], [box for _, box in guessed])
            a, b = linear_sum_assignment((overlap < .5) * 100 + 1 - overlap)
            for x, y in zip(a, b):
                if overlap[x, y] >= .5:
                    pair_counts[(truth[x][0], guessed[y][0])] += 1
    truth_ids = sorted({a for a, _ in pair_counts})
    predicted_ids = sorted({b for _, b in pair_counts})
    associations = np.array([[pair_counts[a, b] for b in predicted_ids] for a in truth_ids])
    idtp = 0
    if associations.size:
        a, b = linear_sum_assignment(-associations)
        idtp = int(associations[a, b].sum())
    for frame_id, objects in reference.items():
        if frame_id > sample['frames']:
            continue
        for ident, box in objects:
            if countable(box, ignored, w, h):
                x, y, bw, bh = box
                reference_tracks[ident].append((frame_id / sample['fps'], (x + bw / 2) / w, (y + bh / 2) / h))
    truth_crossings = vision.count_line_crossings(reference_tracks, sample['counting_line'])['total']
    guessed_crossings = vision.count_line_crossings({ident: points for ident, points in trajectories.items() if ident in confirmed}, sample['counting_line'])['total']
    result = {'sequence': sample['sequence'], 'split': sample['split'], 'weather': sample['weather'],
        **summarize_frames(display_rows), 'reference_distinct_vehicles': len(reference_tracks), 'predicted_distinct_tracks': len(confirmed),
        'distinct_count_absolute_error': abs(len(reference_tracks) - len(confirmed)),
        'reference_crossings': truth_crossings, 'predicted_crossings': guessed_crossings,
        'crossing_absolute_error': abs(truth_crossings - guessed_crossings),
        'idtp': idtp, 'idfp': identity_predictions - idtp, 'idfn': identity_truth - idtp,
        'identity_f1': round(2 * idtp / max(1, identity_truth + identity_predictions), 4),
        'counting_line': sample['counting_line'], 'counted_tracks_min_hits': policy['min_hits']}
    return result, display_rows


def aggregate(scenes, rows):
    result = summarize_frames(rows)
    for key in ('reference_distinct_vehicles', 'predicted_distinct_tracks', 'distinct_count_absolute_error',
                'reference_crossings', 'predicted_crossings', 'crossing_absolute_error', 'idtp', 'idfp', 'idfn'):
        result[key] = sum(s[key] for s in scenes)
    result['identity_f1'] = round(2 * result['idtp'] / max(1, 2 * result['idtp'] + result['idfp'] + result['idfn']), 4)
    return result


def checks(candidate, baseline):
    return {'distinct_count_error_reduced': candidate['distinct_count_absolute_error'] < baseline['distinct_count_absolute_error'],
        'identity_f1_not_worse': candidate['identity_f1'] >= baseline['identity_f1'],
        'line_error_not_worse': candidate['crossing_absolute_error'] <= baseline['crossing_absolute_error'],
        'frame_count_error_preserved': candidate['count_mae'] <= baseline['count_mae'] + .25,
        'detection_f1_preserved': candidate['f1_iou50'] >= baseline['f1_iou50'] - .015}


def main():
    import torch
    from ultralytics import YOLO
    torch.set_num_threads(2)
    vision._disable_usage_statistics()
    selected = json.loads((DATA / 'selection.json').read_text())
    validation = [s for s in selected if s['split'] == 'validation']
    test = [s for s in selected if s['split'] == 'test']
    detector = YOLO(str(core.MODELS_DIR / 'yolov8s.pt'))
    cached = {s['sequence']: cache(s, detector) for s in validation}
    trials = []
    for policy in POLICIES:
        scenes, rows = [], []
        for sample in validation:
            result, frames = scene(sample, cached[sample['sequence']], policy)
            scenes.append(result)
            rows += frames
        summary = aggregate(scenes, rows)
        trials.append({'policy': policy, 'aggregate': summary, 'scenes': scenes})
        print('Validation:', policy['name'], summary, flush=True)
    baseline = trials[0]['aggregate']
    candidates = [trial for trial in trials[1:] if all(checks(trial['aggregate'], baseline).values())]
    chosen = min(candidates, key=lambda trial: (trial['aggregate']['distinct_count_absolute_error'],
        -trial['aggregate']['identity_f1'], trial['aggregate']['crossing_absolute_error'])) if candidates else trials[0]
    output = core.ROOT / 'evaluation'
    selection = {'selected_at': core.iso_now(), 'policy': chosen['policy'], 'selection_period': 'Official train scenes only',
        'criterion': 'Pass fixed preservation checks, then lower distinct-count error, higher identity F1, lower crossing error.', 'trials': trials}
    (output / 'tracking-upgrade-selection.json').write_text(json.dumps(selection, indent=2))
    print('Policy frozen before new test inference:', chosen['policy'], flush=True)
    cached = {s['sequence']: cache(s, detector) for s in test}
    results = {}
    for name, policy in [('previous', POLICIES[0]), ('selected', chosen['policy'])]:
        scenes, rows = [], []
        for sample in test:
            result, frames = scene(sample, cached[sample['sequence']], policy)
            scenes.append(result)
            rows += frames
        results[name] = {'policy': policy, 'aggregate': aggregate(scenes, rows), 'scenes': scenes}
    decision = checks(results['selected']['aggregate'], results['previous']['aggregate'])
    report = {'evaluated_at': core.iso_now(), 'source': 'https://sites.google.com/view/daweidu/projects/ua-detrac',
        'scope': 'Four newly acquired official test scenes; previous test scenes excluded. Chinese road footage, not Bengaluru site validation.',
        'metric_scope': 'Common every-second-source-frame metric frames. Visible detections use >=0.35 confidence independently of track confirmation. Identity metrics use confirmed association trajectories.',
        'detector': {'weights': 'yolov8s.pt', 'imgsz': 960, 'detection_confidence': .35, 'agnostic_nms': True},
        'selection': selection, 'results': results, 'acceptance': {'passed': all(decision.values()), 'checks': decision},
        'notice': 'Confirmed track IDs remain estimates of distinct vehicles; identity switches and occlusion can still cause errors.'}
    (output / 'tracking-upgrade-benchmark.json').write_text(json.dumps(report, indent=2))
    print('Held-out result:', json.dumps({'acceptance': report['acceptance'], 'results': {k: v['aggregate'] for k, v in results.items()}}, indent=2), flush=True)


if __name__ == '__main__':
    main()
