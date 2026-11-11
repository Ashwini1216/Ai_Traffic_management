"""Native clip confirmation must not inflate categories or confuse frame visibility with unique tracks."""
from types import SimpleNamespace
import cv2
import numpy as np
import torch


def clip(path, frames):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (64, 64))
    for i in range(frames):
        writer.write(np.full((64, 64, 3), 80 + i, np.uint8))
    writer.release()
    return path


def fake_detector():
    from ultralytics.engine.results import Boxes
    counter = {'frame': 0}
    def predict(frame, classes, **kwargs):
        if classes == [0]:
            values = torch.empty((0, 6))
        else:
            counter['frame'] += 1
            i = counter['frame']
            values = torch.tensor([[10 + i, 20, 30 + i, 40, .9, 2 if i % 3 else 5]], dtype=torch.float32)
        return [SimpleNamespace(boxes=Boxes(values, (64, 64)))]
    return SimpleNamespace(predict=predict)


def test_confirmed_native_counts_one_stable_track_with_one_category(tmp_path, monkeypatch):
    import vision
    monkeypatch.setenv('YOLO_CLIP_TRACKING', 'confirmed_native')
    monkeypatch.setattr(vision, '_model', fake_detector)
    result = vision.analyze_video(clip(tmp_path / 'stable.avi', 12))
    assert result['sampled_frames'] == 12 and result['clip_fully_analyzed']
    assert result['clip_vehicle_count_estimate'] == 1 and result['count_method'] == 'confirmed_tracker_ids_in_clip'
    assert result['vehicle_counts_by_class']['car'] == 1 and sum(result['vehicle_counts_by_class'].values()) == 1
    assert result['average_vehicles_in_frame'] == 1 and result['peak_vehicles_in_frame'] == 1
    assert result['detector_settings']['minimum_track_frames'] == 5


def test_short_unconfirmed_track_does_not_hide_visible_vehicles(tmp_path, monkeypatch):
    import vision
    monkeypatch.setenv('YOLO_CLIP_TRACKING', 'confirmed_native')
    monkeypatch.setattr(vision, '_model', fake_detector)
    result = vision.analyze_video(clip(tmp_path / 'short.avi', 3))
    assert result['clip_vehicle_count_estimate'] == 0 and result['peak_vehicles_in_frame'] == 1
    assert 'visible_vehicles_without_enough_track_confirmation' in result['quality_flags']


def test_native_clip_region_filter_applies_to_tracks_and_visible_counts(tmp_path, monkeypatch):
    import vision
    monkeypatch.setenv('YOLO_CLIP_TRACKING', 'confirmed_native')
    monkeypatch.setattr(vision, '_model', fake_detector)
    result = vision.analyze_video(clip(tmp_path / 'outside.avi', 12), roi=[[0, 0], [.1, 0], [.1, .1], [0, .1]])
    assert result['clip_vehicle_count_estimate'] == 0 and result['average_vehicles_in_frame'] == 0
    assert sum(result['vehicle_counts_by_class'].values()) == 0
