"""Acquire a metadata-selected, disjoint UA-DETRAC counting holdout before tracker selection."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile
import xml.etree.ElementTree as ET

import core
from download_validation_videos import RangeReader, archive_url, SOURCE

ROOT = core.DATA_DIR / 'research' / 'vehicle-counts-v2'
ORIGINAL = core.DATA_DIR / 'research' / 'vehicle-counts'


def select():
    previous = json.loads((ORIGINAL / 'selection.json').read_text())
    excluded = {s['sequence'] for s in previous if s['split'] == 'test'}
    chosen = []
    for split, weathers in [('train', ['sunny', 'night', 'rainy', 'cloudy']), ('test', ['sunny', 'cloudy', 'rainy', 'night'])]:
        with zipfile.ZipFile(ORIGINAL / f'annotations-{split}.zip') as archive:
            catalog = []
            for filename in sorted(archive.namelist()):
                if not filename.endswith('.xml'):
                    continue
                data = archive.read(filename)
                parser = ET.XMLPullParser(events=('start',))
                parser.feed(data[:4096])
                header = {element.tag: dict(element.attrib) for _, element in parser.read_events()}
                attrs = header.get('sequence_attribute', {})
                sequence = header.get('sequence', {}).get('name', '')
                if attrs.get('camera_state') == 'stable' and data.count(b'<frame ') >= 500 and sequence not in excluded:
                    catalog.append((filename, sequence, attrs.get('sence_weather')))
            for weather in weathers:
                options = [(filename, sequence) for filename, sequence, category in catalog if category == weather]
                if not options:
                    raise ValueError(f'No unused {weather} {split} scene.')
                filename, sequence = options[0]
                if not __import__('re').fullmatch(r'MVI_\d{5}', sequence):
                    raise ValueError('Invalid source sequence identifier.')
                folder = ROOT / 'clips' / sequence
                folder.mkdir(parents=True, exist_ok=True)
                annotation = archive.read(filename)
                (folder / 'annotation.xml').write_bytes(annotation)
                chosen.append({'sequence': sequence, 'split': 'validation' if split == 'train' else 'test',
                    'weather': weather, 'annotation_source_split': split,
                    'annotation_sha256': hashlib.sha256(annotation).hexdigest(), 'source': SOURCE,
                    'counting_line': [[.05, .6], [.95, .6]]})
    return chosen


def main():
    import cv2
    import numpy as np
    ROOT.mkdir(parents=True, exist_ok=True)
    chosen = select()
    protocol = {'selected_at': core.iso_now(), 'source': SOURCE,
        'selection_rule': 'First stable >=500-frame sequence per weather; previous test sequences excluded before selection.',
        'previous_test_excluded': [s['sequence'] for s in json.loads((ORIGINAL / 'selection.json').read_text()) if s['split'] == 'test'],
        'line_rule': 'Fixed full-width horizontal line at 60% image height; chosen before detector evaluation.',
        'samples': chosen}
    if not (ROOT / 'protocol.json').exists():
        (ROOT / 'protocol.json').write_text(json.dumps(protocol, indent=2))
    else:
        original = json.loads((ROOT / 'protocol.json').read_text())
        if [s['sequence'] for s in original['samples']] != [s['sequence'] for s in chosen]:
            raise ValueError('Frozen source selection changed.')
    print('Frozen scenes:', [(s['sequence'], s['split'], s['weather']) for s in chosen], flush=True)
    remote = None
    for sample in chosen:
        sequence = sample['sequence']
        folder = ROOT / 'clips' / sequence
        old = ORIGINAL / 'clips' / sequence
        if old.joinpath('manifest.json').exists():
            manifest = json.loads((old / 'manifest.json').read_text())
            for filename in ('sample.avi', 'first-frame.jpg'):
                if not (folder / filename).exists():
                    os.link(old / filename, folder / filename)
            sample.update({key: manifest[key] for key in ('fps', 'first_frame', 'frames', 'video_sha256', 'original_jpeg_sequence_sha256')})
        elif (folder / 'manifest.json').exists():
            manifest = json.loads((folder / 'manifest.json').read_text())
            sample.update({key: manifest[key] for key in ('fps', 'first_frame', 'frames', 'video_sha256', 'original_jpeg_sequence_sha256')})
        else:
            if remote is None:
                remote = RangeReader(archive_url())
                archive = zipfile.ZipFile(remote)
                names = archive.namelist()
            filenames = sorted(n for n in names if f'/{sequence}/' in '/' + n and n.lower().endswith('.jpg'))[:375]
            if len(filenames) != 375:
                raise ValueError('Incomplete source clip.')
            digest = hashlib.sha256()
            writer = None
            try:
                for i, filename in enumerate(filenames):
                    data = archive.read(filename)
                    digest.update(data)
                    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    if writer is None:
                        h, w = frame.shape[:2]
                        writer = cv2.VideoWriter(str(folder / 'sample.avi'), cv2.VideoWriter_fourcc(*'MJPG'), 25, (w, h))
                        if not writer.isOpened():
                            raise ValueError('Could not create clip.')
                        (folder / 'first-frame.jpg').write_bytes(data)
                    writer.write(frame)
                    if i % 125 == 0:
                        print(sequence, i + 1, 'source frames', flush=True)
            finally:
                if writer:
                    writer.release()
            sample.update(fps=25, first_frame=1, frames=375, original_jpeg_sequence_sha256=digest.hexdigest(),
                video_sha256=hashlib.sha256((folder / 'sample.avi').read_bytes()).hexdigest())
        sample['notice'] = 'Author-annotated Chinese reference traffic footage; not a live Bengaluru camera.'
        (folder / 'manifest.json').write_text(json.dumps(sample, indent=2))
    (ROOT / 'selection.json').write_text(json.dumps(chosen, indent=2))
    print('Holdout acquisition complete; new transfer:', round(remote.downloaded / 1e6, 1) if remote else 0, 'MB', flush=True)


if __name__ == '__main__':
    main()
