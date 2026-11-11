"""Stream warm-up, arrival-rate safeguards and approved preview access."""
import json
from types import SimpleNamespace
import cv2
import numpy as np


def test_mjpeg_reader_skips_placeholder_and_decodes_real_frame():
    from camera_capture import MJPEGCapture
    tiny = cv2.imencode('.jpg', np.zeros((1, 1, 3), np.uint8))[1].tobytes()
    image = np.indices((80, 120)).sum(axis=0) % 2 * 255
    normal = cv2.imencode('.jpg', np.repeat(image[:, :, None], 3, axis=2).astype(np.uint8))[1].tobytes()
    payload = b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + tiny + b'\r\n--frame\r\n' + normal
    response = SimpleNamespace(status_code=200, iter_content=lambda chunk_size: iter(payload[i:i + 128] for i in range(0, len(payload), 128)), close=lambda: None)
    capture = MJPEGCapture(response)
    ok, frame = capture.read()
    assert ok and frame.shape == (80, 120, 3)
    assert capture.placeholders_skipped == 1 and capture.get(cv2.CAP_PROP_FPS) == 0
    assert capture.read() == (False, None)
    capture.release()


def test_network_arrival_rate_controls_flow_not_invented_camera_fps(monkeypatch):
    import camera_capture
    import time
    import vision
    clock = {'now': 0.0, 'frames': 0}
    monkeypatch.setattr(time, 'monotonic', lambda: clock['now'])
    class Capture:
        def isOpened(self): return True
        def get(self, key): return 25
        def release(self): pass
        def read(self):
            if clock['frames'] == 3: return False, None
            clock['now'] += 1
            clock['frames'] += 1
            image = np.indices((80, 120)).sum(axis=0) % 2 * 220
            return True, np.repeat(image[:, :, None], 3, axis=2).astype(np.uint8) + clock['frames']
    class Tensor:
        def __init__(self, value): self.value = value
        def int(self): return self
        def tolist(self): return self.value
    class Boxes:
        def __len__(self): return 1
        @property
        def id(self): return Tensor([1])
        @property
        def xyxyn(self): return Tensor([[.2, .1 * clock['frames'], .4, .1 * clock['frames'] + .3]])
    calls = []
    def track(*a, **kw):
        calls.append(kw)
        return [SimpleNamespace(boxes=Boxes())]
    monkeypatch.setattr(camera_capture, 'open_capture', lambda *a: Capture())
    monkeypatch.setattr(vision, '_model', lambda: SimpleNamespace(track=track))
    result = vision.analyze_stream('rtsp://example.invalid/camera', 10, [[0, .5], [1, .5]])
    assert len(calls) == 3 and result['effective_sample_fps'] == 1
    assert result['low_refresh_rate'] and result['automatic_verification_blocked']
    assert result['line_crossings'] is None and result['flow_per_hour'] is None and result['queue_length'] is None
    assert result['peak_vehicles_in_frame'] == 1


def test_low_refresh_camera_cannot_auto_verify_or_store_track_totals_as_visible_counts(client, authority, monkeypatch):
    import vision
    import store
    camera = {'camera_id': 'slow-live-view', 'display_name': 'Slow public view', 'area': 'Banaswadi', 'road': 'Test road',
              'source_type': 'permitted_integration', 'authorization_status': 'approved', 'operational_status': 'online',
              'stream_url': 'https://camera.example/video_feed', 'measurement_validated': True,
              'validation_note': 'Reference comparison for the original camera stream.'}
    assert client.post('/api/cameras', json=camera, headers=authority).status_code == 200
    monkeypatch.setattr(vision, 'analyze_stream', lambda *a: {'duration_seconds': 10, 'line_crossings': None,
        'tracked_vehicles': 15, 'peak_vehicles_in_frame': 4, 'flow_per_hour': None, 'occupancy': .2, 'queue_length': None,
        'low_refresh_rate': True, 'automatic_verification_blocked': True,
        'measurement_flags': ['low_refresh_rate_visible_counts_only']})
    response = client.post('/api/cameras/slow-live-view/collect', headers=authority)
    assert response.status_code == 200 and response.json()['ok']
    row = store.one('SELECT * FROM traffic_observations WHERE id=?', (response.json()['observation_id'],))
    assert row['quality_status'] == 'unverified' and row['vehicle_count'] == 4
    assert 'low_refresh_rate_visible_counts_only' in json.loads(row['quality_flags_json'])


def test_snapshot_requires_approved_authority_camera_and_keeps_address_private(client, authority, citizen, monkeypatch):
    import camera_capture
    camera = {'camera_id': 'preview-test', 'display_name': 'Preview camera', 'area': 'Banaswadi', 'road': 'Test road',
              'source_type': 'permitted_integration', 'authorization_status': 'approved', 'operational_status': 'online',
              'stream_url': 'https://camera.example/private-stream'}
    client.post('/api/cameras', json=camera, headers=authority)
    calls = []
    monkeypatch.setattr(camera_capture, 'snapshot', lambda source: (calls.append(source) or b'jpeg-preview'))
    path = '/api/cameras/preview-test/snapshot'
    assert client.get(path).status_code in (401, 403) and client.get(path, headers=citizen).status_code == 403
    response = client.get(path, headers=authority)
    assert response.content == b'jpeg-preview' and response.headers['content-type'] == 'image/jpeg'
    assert 'x-frame-fetched-at' in response.headers and camera['stream_url'] not in str(response.headers)
    assert client.get(path, headers=authority).content == b'jpeg-preview' and len(calls) == 1
    client.post('/api/cameras', json={**camera, 'authorization_status': 'revoked'}, headers=authority)
    assert client.get(path, headers=authority).status_code == 403
