"""Bounded camera readers, including public MJPEG feeds with warm-up placeholders."""
from __future__ import annotations
import time

import cv2
import numpy as np
import requests


class MJPEGCapture:
    def __init__(self, response, seconds=20):
        self.response = response
        self.chunks = response.iter_content(chunk_size=512)
        self.buffer = bytearray()
        self.closed = False
        self.deadline = time.monotonic() + max(10, seconds) + 5
        self.placeholders_skipped = 0
        self.capture_kind = 'mjpeg'

    def isOpened(self):
        return not self.closed and self.response.status_code == 200

    def get(self, prop):
        return 0.0  # JPEG arrival rate must be measured, not invented as 25 or 30 fps.

    def read(self):
        try:
            while not self.closed and time.monotonic() < self.deadline:
                start = self.buffer.find(b'\xff\xd8')
                end = self.buffer.find(b'\xff\xd9', start + 2) if start >= 0 else -1
                if end >= 0:
                    encoded = bytes(self.buffer[start:end + 2])
                    del self.buffer[:end + 2]
                    frame = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None and min(frame.shape[:2]) >= 64:
                        return True, frame
                    self.placeholders_skipped += 1
                    continue
                self.buffer.extend(next(self.chunks))
                if len(self.buffer) > 4 * 1024 * 1024:
                    raise ValueError('Camera JPEG exceeds the 4 MB frame limit.')
        except (StopIteration, requests.RequestException):
            pass
        return False, None

    def release(self):
        self.closed = True
        self.response.close()


def open_capture(source, seconds=20):
    if str(source).startswith(('https://', 'http://')):
        try:
            response = requests.get(source, stream=True, timeout=(5, 5))
            content_type = response.headers.get('Content-Type', '').lower()
            if 'multipart/x-mixed-replace' in content_type or 'image/jpeg' in content_type:
                if response.status_code != 200:
                    response.close()
                    raise ValueError('The camera did not permit access to its JPEG stream.')
                return MJPEGCapture(response, seconds)
            response.close()
        except requests.RequestException:
            pass  # Other authorised HTTP video formats can still be read by OpenCV.
    if str(source).startswith(('rtsp://', 'rtsps://', 'http://', 'https://', 'rtmp://')):
        return cv2.VideoCapture(source, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
    return cv2.VideoCapture(str(source))


def snapshot(source):
    capture = open_capture(source, seconds=10)
    try:
        if not capture.isOpened():
            raise ValueError('The camera stream could not be opened.')
        for _ in range(30):
            ok, frame = capture.read()
            if not ok:
                break
            if min(frame.shape[:2]) < 64:
                continue
            h, w = frame.shape[:2]
            if max(h, w) > 1280:
                frame = cv2.resize(frame, (round(w * 1280 / max(h, w)), round(h * 1280 / max(h, w))))
            encoded, content = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if encoded:
                return content.tobytes()
        raise ValueError('The camera returned no usable image after warm-up.')
    finally:
        capture.release()
