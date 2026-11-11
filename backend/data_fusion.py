"""Fresh verified measurements blended with historical/model probabilities.

Counts retain interval units, and are never substituted for multi-hour volumes.
The fusion weights are documented research heuristics, not calibrated accuracy.
"""
from datetime import datetime,timedelta,timezone
import math
import core
import store

MAX_AGE_MINUTES=15
MAX_HORIZON_MINUTES=120
LEVEL_LOAD={'Low':0.0,'Medium':0.5,'High':1.0}


def fuse(segment_id,probabilities,target,now=None):
    now=now or datetime.now(timezone.utc)
    horizon=max(0,(target-now).total_seconds()/60)
    details={'applied':False,'sources':[],
        'rule':'Verified measurements; 15-minute freshness decay; 2-hour horizon decay.',
        'notice':'No fresh verified traffic measurement is available.'}
    if horizon>=MAX_HORIZON_MINUTES:
        details['notice']='Current measurements are not projected beyond two hours.'
        return probabilities,details
    rows=store.rows('''SELECT o.*,c.authorization_status AS camera_authorization,c.operational_status AS camera_operation
      FROM traffic_observations o LEFT JOIN cameras c ON c.camera_id=o.camera_id
      WHERE o.segment_id=? AND o.source_type IN ('camera','manual') AND o.quality_status='verified'
      AND o.observed_end>=? AND o.observed_end<=? ORDER BY o.observed_end DESC,o.id DESC LIMIT 20''',
      (segment_id,(now-timedelta(minutes=MAX_AGE_MINUTES)).isoformat(),now.isoformat()))
    for row in rows:
        if 'clip_vehicle_count_is_not_calibrated_block_volume' in (row['quality_flags_json'] or ''):
            continue
        if row['source_type']=='camera' and (row['camera_authorization']!='approved' or row['camera_operation']!='online'):
            continue
        load,metric=None,None
        if row['congestion_level'] in LEVEL_LOAD:
            load,metric=LEVEL_LOAD[row['congestion_level']],'observed_congestion_level'
        elif row['occupancy'] is not None:
            load,metric=min(1,max(0,row['occupancy']*1.4)),'camera_occupancy'
        elif row['travel_time_index'] is not None:
            load,metric=min(1,max(0,row['travel_time_index']-1)),'observed_travel_time_index'
        elif row['queue_length'] is not None:
            load,metric=min(1,max(0,row['queue_length']/15)),'camera_queue'
        if load is None or not math.isfinite(load):
            continue
        age=max(0,(now-core.parse_utc(row['observed_end'])).total_seconds()/60)
        weight=(.8 if metric=='observed_congestion_level' else .65)*(1-age/MAX_AGE_MINUTES)*(1-horizon/MAX_HORIZON_MINUTES)
        if weight<=0:continue
        live={'Low':max(0,1-load*2),'Medium':max(0,1-abs(load-.5)*2),'High':max(0,load*2-1)}
        blended={c:(1-weight)*p+weight*live.get(c,0) for c,p in probabilities.items()}
        total=sum(blended.values());blended={c:p/total for c,p in blended.items()}
        details.update(applied=True,live_load=round(load,3),live_weight=round(weight,3),
            baseline_load=round(sum(LEVEL_LOAD.get(c,.5)*p for c,p in probabilities.items()),3),
            notice='Baseline adjusted by a fresh verified measurement; a research fusion estimate.',
            sources=[{'observation_id':row['id'],'source_type':row['source_type'],'camera_id':row['camera_id'],
                'observed_end':row['observed_end'],'age_minutes':round(age,1),'metric':metric,
                'vehicle_count':row['vehicle_count'],'flow_per_hour':row['flow_per_hour']}])
        return blended,details
    return probabilities,details
