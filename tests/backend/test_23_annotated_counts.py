"""Independent-reference matching and a real detector's vehicle-region filter."""
from evaluate_vehicle_counts import detection_counts,summarize_frames,countable
from test_09_cameras import NEEDS_WEIGHTS,bus_stop_clip
import pytest


def test_reference_matching_counts_missed_objects_and_false_detections():
    truth=[[0,0,10,10],[100,100,10,10]]
    guessed=[[1,1,10,10],[200,200,10,10]]
    assert detection_counts(truth,guessed)==(1,1,1)
    result=summarize_frames([{'reference':2,'predicted':2,'tp':1,'fp':1,'fn':1}])
    assert result['count_mae']==0 and result['f1_iou50']==.5  # equal counts alone do not imply correct detection
    assert summarize_frames([{'reference':0,'predicted':1,'tp':0,'fp':1,'fn':0}])['count_wape'] is None


def test_source_ignored_regions_apply_to_reference_and_predictions():
    assert not countable([10,10,20,20],[[0,0,40,40]],100,100)
    assert countable([50,50,20,20],[[0,0,40,40]],100,100)


@pytest.mark.parametrize('tracked', [True, False])
def test_category_changes_never_count_the_same_vehicle_twice(tmp_path, monkeypatch, tracked):
    import cv2
    import numpy as np
    import vision
    from types import SimpleNamespace

    path = tmp_path / 'changing-labels.avi'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (64, 64))
    for _ in range(3):
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.release()

    class Tensor:
        def __init__(self, value): self.value = value
        def int(self): return self
        def tolist(self): return self.value

    class Boxes:
        def __init__(self, classes):
            self.cls = Tensor(classes)
            self.id = Tensor([1, 2]) if tracked else None
            self.xyxyn = Tensor([[.1, .1, .3, .3], [.6, .6, .8, .8]])
        def __len__(self): return 2

    # The first ID is car/bus/car; the second is truck/truck/bus.
    frames = iter([[2, 7], [5, 7], [2, 5]])
    detector = SimpleNamespace(track=lambda *a, **kw: [SimpleNamespace(boxes=Boxes(next(frames)))])
    monkeypatch.setattr(vision, '_model', lambda: detector)
    result = vision.analyze_video(path)
    assert result['clip_vehicle_count_estimate'] == 2
    assert result['vehicle_counts_by_class'] == {'car': 1, 'motorcycle': 0, 'bus': 0, 'truck': 1}
    assert sum(result['vehicle_counts_by_class'].values()) == result['clip_vehicle_count_estimate']
    assert result['count_method'] == ('unique_tracker_ids_in_clip' if tracked else 'peak_vehicles_in_sampled_frame')


@NEEDS_WEIGHTS
def test_vehicle_region_excludes_bus_but_keeps_people_analysis(client,tmp_path):
    import vision
    result=vision.analyze_video(bus_stop_clip(tmp_path),roi=[[0,0],[.2,0],[.2,.15],[0,.15]])
    assert result['clip_vehicle_count_estimate']==0 and result['vehicle_counts_by_class']['bus']==0
    assert result['people']['most_in_one_frame']>=3
    assert result['detector_settings']['tracker']=='ByteTrack'
