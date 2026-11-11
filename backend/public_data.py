"""Audit and import the author's public daily Bengaluru dataset without inventing blocks.

Acquisition: python backend/public_data.py --download --import-data
The publisher declares CC0. Measurement provenance is not independently verified.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import urllib.request
import ssl
import zipfile

import certifi
import pandas as pd
import core
import store

SOURCE = 'https://www.kaggle.com/datasets/preethamgouda/banglore-city-traffic-dataset'
API = 'https://www.kaggle.com/api/v1/datasets/'
FOLDER = core.DATA_DIR / 'research' / 'traffic-pulse'
ARCHIVE = FOLDER / 'original-kaggle.zip'
SOURCE_TYPE = 'public_daily_dataset'
SOURCE_NAME = 'kaggle_preethamgouda_bangalore_traffic_pulse'
LABEL_RULE = 'Low: index < 100/3; Medium: 100/3 <= index < 200/3; High: index >= 200/3. Project-defined equal-third severity bins, not sensor ground-truth labels.'


def download():
    FOLDER.mkdir(parents=True, exist_ok=True)
    context = ssl.create_default_context(cafile=certifi.where())
    for suffix, target in [('view/preethamgouda/banglore-city-traffic-dataset', FOLDER/'publisher-metadata.json'),
                           ('download/preethamgouda/banglore-city-traffic-dataset', ARCHIVE)]:
        if target.exists():
            continue
        request = urllib.request.Request(API+suffix, headers={'User-Agent':'FlowStateTrafficMonitor/1.0'})
        with urllib.request.urlopen(request, timeout=90, context=context) as response:
            content = response.read(10_000_001)
        if len(content) > 10_000_000:
            raise ValueError('Unexpectedly large daily dataset response.')
        target.write_bytes(content)


def audit(archive: Path = ARCHIVE):
    with zipfile.ZipFile(archive) as bundle:
        names = [n for n in bundle.namelist() if n.lower().endswith('.csv')]
        if len(names) != 1:
            raise ValueError('Expected one source CSV in the public archive.')
        frame = pd.read_csv(io.BytesIO(bundle.read(names[0])))
    required = ['Date','Area Name','Road/Intersection Name','Traffic Volume','Average Speed',
                'Travel Time Index','Congestion Level','Incident Reports']
    missing = set(required)-set(frame.columns)
    if missing:
        raise ValueError('Missing source columns: '+', '.join(sorted(missing)))
    frame['parsed_date'] = pd.to_datetime(frame['Date'], format='%Y-%m-%d', errors='raise').dt.date
    invalid = frame['parsed_date'] > core.local_today()
    for column, low, high in [('Traffic Volume',0,1_000_000),('Average Speed',0,250),
                               ('Travel Time Index',.01,20),('Congestion Level',0,100),('Incident Reports',0,10000)]:
        frame[column] = pd.to_numeric(frame[column], errors='raise')
        invalid |= frame[column].isna() | ~frame[column].between(low,high)
    invalid |= frame['Incident Reports'] % 1 != 0
    for column in ('Area Name','Road/Intersection Name'):
        invalid |= frame[column].isna() | frame[column].astype(str).str.strip().eq('')
    duplicates = frame.duplicated(['Date','Area Name','Road/Intersection Name'], keep=False)
    if invalid.any() or duplicates.any():
        raise ValueError(f'Source audit failed: {int(invalid.sum())} invalid rows, {int(duplicates.sum())} duplicate road/day rows. Resolve before import.')
    frame['class'] = frame['Congestion Level'].map(lambda v:'Low' if v < 100/3 else 'Medium' if v < 200/3 else 'High')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = {'source':SOURCE,'licence':'CC0: Public Domain (publisher metadata)', 'archive_sha256':digest,
                'audited_at':core.iso_now(),'rows':len(frame),'roads':int(frame['Road/Intersection Name'].nunique()),
                'date_range':[str(frame.parsed_date.min()),str(frame.parsed_date.max())],
                'time_granularity':'day','time_of_day_available':False,'severity_rule':LABEL_RULE,
                'class_counts':frame['class'].value_counts().to_dict(),
                'warning':'Public publisher data, not independently verified Bengaluru sensor records. This is not the missing six-block project archive.'}
    return frame, manifest


def import_daily(archive: Path = ARCHIVE):
    import segments
    frame, manifest = audit(archive)
    previous = store.one('SELECT value FROM app_meta WHERE key=?', ('public_daily_sha256',))
    if previous and previous['value'] == manifest['archive_sha256']:
        return manifest
    ids = {(area,road):segments.ensure_segment(area,road) for area,road in
           frame[['Area Name','Road/Intersection Name']].drop_duplicates().itertuples(index=False,name=None)}
    flags = json.dumps(['daily_record_not_a_time_block','publisher_measurement_not_independently_verified',
                        'severity_derived_from_publisher_congestion_index_equal_thirds'])
    rows = []
    for index, row in frame.iterrows():
        start = datetime.combine(row.parsed_date,time(),core.LOCAL_TZ).astimezone(timezone.utc)
        rows.append((SOURCE_TYPE,SOURCE_NAME,str(index+2),row.parsed_date.isoformat(),'Daily',row['Area Name'],row['Road/Intersection Name'],
            float(row['Traffic Volume']),float(row['Average Speed']),float(row['Travel Time Index']),row['class'],
            row.get('Weather Conditions'),row.get('Roadwork and Construction Activity'),int(row['Incident Reports']),
            'unverified',flags,core.iso_now(),ids[(row['Area Name'],row['Road/Intersection Name'])],
            start.isoformat(),(start+timedelta(days=1)).isoformat(),86400,'day',float(row['Congestion Level'])))
    with store.connect() as db:
        db.execute('DELETE FROM traffic_observations WHERE source_type=? AND source_name=?',(SOURCE_TYPE,SOURCE_NAME))
        db.executemany('''INSERT INTO traffic_observations(source_type,source_name,source_record_id,observed_date,time_block,area,road,
          traffic_volume,average_speed,travel_time_index,congestion_level,weather,roadwork,incident_reports,quality_status,
          quality_flags_json,ingested_at,segment_id,observed_start,observed_end,interval_seconds,time_granularity,congestion_index)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows)
        db.execute('INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   ('public_daily_sha256',manifest['archive_sha256']))
    segments.invalidate_coverage()
    FOLDER.mkdir(parents=True, exist_ok=True)
    (FOLDER/'source-manifest.json').write_text(json.dumps(manifest,indent=2))
    return manifest


_CACHE = {'key':None,'frame':None,'counts':None}


def history():
    import hybrid_model as hm, patterns
    key = store.one("SELECT COUNT(*) AS n,MAX(id) AS last,MAX(verified_at) AS reviewed FROM traffic_observations WHERE time_block='Daily'")
    if _CACHE['key'] != key:
        frame = hm.load_daily_history()
        _CACHE.update(key=key,frame=frame,counts=patterns.build_counts(frame))
    return _CACHE['frame'], _CACHE['counts']


def outlook(segment_id, target_day):
    import hybrid_model as hm, model_registry, patterns
    frame, counts = history()
    if frame.empty or segment_id not in counts:
        return None
    available = frame[(frame.segment_id==segment_id) & (frame.date<target_day)].sort_values('slot')
    if len(available) < 3:
        return None
    # Exclude target/future rows even when an authority replays a past date.
    entry = patterns.build_counts(available)[segment_id]
    rows = available.tail(hm.SEQ_LEN).to_dict('records')
    horizon = target_day.toordinal()-rows[-1]['slot']
    model, registry = model_registry.load_active('day')
    if model is not None and len(rows)==hm.SEQ_LEN and rows[-1]['slot']-rows[0]['slot']==hm.SEQ_LEN-1 and 1<=horizon<=hm.MAX_HORIZON:
        step = model.predict(rows,rows[-1]['slot'],rows[-1]['road_type'],horizon,entry)[-1]
        return {'method':'daily_model','probabilities':step['probabilities'],'predicted_values':step['predicted_values'],
                'model_version':registry['version'],'model_kind':registry['kind'],'confidence':min(.8,max(step['probabilities'].values())),
                'latest_observation':str(available.date.max()),'data_granularity':'day','horizon_days':horizon,
                'evidence':{'daily_records':len(available),'source':SOURCE,'severity_rule':LABEL_RULE}}
    probabilities, records, cell = patterns.pattern_distribution(entry,'Daily',patterns.day_type(target_day),['Low','Medium','High'])
    age = (core.local_today()-available.date.max()).days
    return {'method':'daily_historical_pattern','probabilities':probabilities,'model_version':'daily-pattern-v1',
            'confidence':min(.6,records/(records+30))*(.6 if age>730 else .75 if age>365 else 1),
            'latest_observation':str(available.date.max()),'data_granularity':'day',
            'evidence':{'daily_records':len(available),'cell':cell,'history_age_days':age,'source':SOURCE,'severity_rule':LABEL_RULE}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download',action='store_true')
    parser.add_argument('--import-data',action='store_true')
    args = parser.parse_args()
    if args.download:
        download()
    store.initialize()
    result = import_daily() if args.import_data else audit()[1]
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
