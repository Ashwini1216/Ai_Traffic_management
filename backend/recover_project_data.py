"""Audit a supplied project archive, retain provenance and restore it without replacing private settings."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import zipfile

import numpy as np
import pandas as pd

import core
import dataset

SHARES = {'Midnight': .04, 'Morning': .08, 'Daytime': .26, 'Noon': .19, 'Evening': .28, 'Night': .15}


def csv_frame(path):
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith('.csv')]
        if len(names) != 1:
            raise ValueError('Expected one CSV per supplied archive.')
        return pd.read_csv(io.BytesIO(archive.read(names[0])))


def audit(block_archive: Path, daily_archive: Path):
    valid, rejected = 0, 0
    for _, _, clean, _ in dataset.validated_rows(block_archive):
        valid += clean is not None
        rejected += clean is None
    if rejected:
        raise ValueError(f'The archive contains {rejected} invalid or duplicate rows; review before restoring.')
    block, daily = csv_frame(block_archive), csv_frame(daily_archive)
    keys = ['Date', 'Area Name', 'Road/Intersection Name']
    merged = block.merge(daily[keys + ['Traffic Volume']], on=keys, suffixes=('_block', '_day'), validate='many_to_one')
    error = np.abs(merged['Traffic Volume_block'] - merged['Traffic Volume_day'] * merged.TimeBlock.map(SHARES))
    derived = len(merged) == len(block) and bool((error <= .500001).all())
    return {'restored_at': core.iso_now(), 'block_archive': block_archive.name,
        'block_archive_sha256': hashlib.sha256(block_archive.read_bytes()).hexdigest(),
        'daily_archive_sha256': hashlib.sha256(daily_archive.read_bytes()).hexdigest(),
        'block_archive_bytes': block_archive.stat().st_size, 'valid_block_records': valid, 'rejected_records': rejected,
        'roads': int(block['Road/Intersection Name'].nunique()), 'areas': int(block['Area Name'].nunique()),
        'date_range': [str(block.Date.min()), str(block.Date.max())],
        'labels': block.TrafficSituation.value_counts().to_dict(),
        'complete_six_block_days': int(block.groupby(keys).TimeBlock.nunique().eq(6).sum()),
        'matches_saved_project_facts': bool(valid == 33072 and block_archive.stat().st_size == 695179
            and block.iloc[0]['Traffic Volume'] == 1524 and block.iloc[0]['Average Speed'] == 34.52),
        'derived_daily_volume_shares_confirmed': derived, 'volume_shares': SHARES,
        'max_volume_rounding_error': float(error.max()),
        'notice': 'Original supplied project archive. Its six block volumes are rounded fixed shares of the public daily source; '
                  'these are derived time blocks, not six independently measured CCTV/sensor intervals. Labels are unverified.'}


def restore(block_archive, daily_archive):
    report = audit(block_archive, daily_archive)
    encoded = json.dumps(report, indent=2)
    target = core.DATA_DIR / 'bangalore_traffic_timely_dataset.zip'
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != report['block_archive_sha256']:
        raise ValueError('A different historical archive is already installed; refusing to overwrite it.')
    backup = core.ROOT.parent / 'flowstate-before-original-restoration.db'
    database = core.DATA_DIR / 'traffic_monitor.db'
    if database.exists() and not backup.exists():
        with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)
        backup.chmod(0o600)
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(block_archive, target)
    folder = core.DATA_DIR / 'research' / 'original-archive'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'source-manifest.json').write_text(encoded)
    import store
    import segments
    store.initialize()
    with store.connect() as db:
        db.execute('INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   ('original_archive_provenance', json.dumps(report)))
        if report['derived_daily_volume_shares_confirmed']:
            rows = db.execute("SELECT id,quality_flags_json FROM traffic_observations WHERE source_type='historical_dataset'").fetchall()
            for row in rows:
                flags = json.loads(row['quality_flags_json'] or '[]')
                if 'time_block_volume_derived_from_fixed_daily_shares' not in flags:
                    flags.append('time_block_volume_derived_from_fixed_daily_shares')
                    db.execute('UPDATE traffic_observations SET quality_flags_json=? WHERE id=?', (json.dumps(flags), row['id']))
    segments.sync_segments()
    segments.invalidate_coverage()
    output = core.ROOT / 'evaluation' / 'original-dataset-recovery.json'
    output.write_text(encoded)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blocks', type=Path, required=True)
    parser.add_argument('--daily', type=Path, required=True)
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    print(json.dumps((restore if args.restore else audit)(args.blocks, args.daily), indent=2))
