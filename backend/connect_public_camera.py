"""Check the deliberately published Banaswadi webcam and optionally register/sample it locally."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
import requests

import core

SOURCE = 'https://view.custardlev.uk/video_feed'
PUBLICATION = 'https://diagramchasing.fun/2026/mapping-clouds/stations/bengaluru'
CAMERA_ID = 'public-banaswadi-webcam'


def check(register=False, collect=False):
    from camera_capture import open_capture
    response = requests.get(PUBLICATION, timeout=30)
    response.raise_for_status()
    if SOURCE not in response.text:
        raise ValueError('The publication no longer links this camera; review its public source before connecting.')
    capture = open_capture(SOURCE, seconds=15)
    frames = []
    try:
        if not capture.isOpened():
            raise ValueError('The public camera could not be opened.')
        for _ in range(4):
            ok, image = capture.read()
            if not ok:
                break
            frames.append({'received_at': core.iso_now(), 'monotonic': time.monotonic(),
                'width': int(image.shape[1]), 'height': int(image.shape[0]),
                'sha256': hashlib.sha256(image.tobytes()).hexdigest()})
    finally:
        capture.release()
    if len(frames) < 2 or len({f['sha256'] for f in frames}) < 2:
        raise ValueError('The source did not demonstrate changing usable images; camera was not registered.')
    fps = (len(frames) - 1) / (frames[-1]['monotonic'] - frames[0]['monotonic'])
    for frame in frames:
        del frame['monotonic']
    report = {'checked_at': core.iso_now(), 'source': SOURCE, 'publication': PUBLICATION,
        'camera_id': CAMERA_ID, 'available': True, 'observed_frame_rate': round(fps, 2),
        'width': frames[-1]['width'], 'height': frames[-1]['height'], 'changing_frames': len(frames),
        'warmup_placeholders_skipped': getattr(capture, 'placeholders_skipped', 0), 'frames': frames,
        'location': {'latitude': 13.0145, 'longitude': 77.64935, 'confidence': 'Approximate public Banaswadi junction reference, not surveyed.'},
        'affiliation': 'Independent public webcam; not a Bengaluru Traffic Police feed.',
        'measurement_validated': False,
        'notice': 'Low-refresh public imagery. Visible-frame estimates require review; reliable distinct counts, flow and queues are not established.'}
    if register or collect:
        import cameras
        import store
        store.initialize()
        payload = cameras.CameraInput(camera_id=CAMERA_ID, display_name='Public Banaswadi road webcam',
            area='Banaswadi', road='Banaswadi Main Road – 2nd Cross', latitude=13.0145, longitude=77.64935,
            source_type='permitted_integration', authorization_status='approved', operational_status='online',
            stream_url=SOURCE, collection_interval_seconds=300, sample_seconds=20,
            measurement_validated=False, validation_note='Deliberately public webcam linked by Diagram Chasing; approximate location. '
                'Low-refresh imagery; site counts not calibrated. Independent source, not an official police feed.')
        cameras.register_camera(payload, actor=os.getenv('AUTHORITY_USERNAME') or 'local-project-setup')
        report['registered'] = True
        if collect:
            camera = store.one('SELECT * FROM cameras WHERE camera_id=?', (CAMERA_ID,))
            result = cameras.collect(camera)
            if not result['ok']:
                raise ValueError(result['error'])
            row = store.one('SELECT quality_status,vehicle_count,flow_per_hour FROM traffic_observations WHERE id=?', (result['observation_id'],))
            report['measurement'] = {key: result.get(key) for key in ('observation_id', 'sampled_frames', 'effective_sample_fps',
                'average_vehicles_in_frame', 'peak_vehicles_in_frame', 'low_refresh_rate', 'measurement_notice')}
            report['measurement'].update(row)
            assert row['quality_status'] == 'unverified', 'Uncalibrated public source must not be automatically verified.'
    target = core.DATA_DIR / 'research' / 'public-camera-status.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2))
    (core.ROOT / 'evaluation' / 'public-camera-inspection.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key != 'frames'}, indent=2), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--register', action='store_true')
    parser.add_argument('--collect', action='store_true')
    args = parser.parse_args()
    check(args.register, args.collect)
