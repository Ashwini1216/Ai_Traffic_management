from datetime import datetime,timedelta,timezone

import pytest


def observation(client,authority,segments,age=1,**changes):
    end=datetime.now(timezone.utc)-timedelta(minutes=age)
    payload={'area':'Indiranagar','road':'100 Feet Road','observed_start':(end-timedelta(minutes=1)).isoformat(),
             'observed_end':end.isoformat(),'congestion_level':'Low','source_name':'fusion-test',**changes}
    result=client.post('/api/observations',json=payload,headers=authority)
    assert result.status_code==200,result.text
    return result.json()


def test_fresh_verified_record_changes_historical_baseline(client,authority,citizen,segments):
    import data_fusion
    segment=segments['100 Feet Road']['segment_id'];now=datetime.now(timezone.utc)
    row=observation(client,authority,segments)
    base={'Low':.05,'Medium':.1,'High':.85}
    unchanged,details=data_fusion.fuse(segment,base,now)
    assert unchanged==base and not details['applied']
    client.patch('/api/observations/'+str(row['id']),json={'quality_status':'verified'},headers=authority)
    fused,details=data_fusion.fuse(segment,base,now)
    assert details['applied'] and fused['Low']>fused['High'] and abs(sum(fused.values())-1)<1e-6
    forecast=client.get('/api/forecast',params={'segment_id':segment},headers=citizen).json()
    assert forecast['method']=='historical_pattern' and forecast['fusion']['applied']
    client.patch('/api/observations/'+str(row['id']),json={'quality_status':'rejected'},headers=authority)
    assert not data_fusion.fuse(segment,base,now)[1]['applied']


def test_stale_and_far_future_are_not_current_traffic(client,authority,segments):
    import data_fusion
    row=observation(client,authority,segments,age=20)
    client.patch('/api/observations/'+str(row['id']),json={'quality_status':'verified'},headers=authority)
    segment=segments['100 Feet Road']['segment_id'];base={'Low':.1,'Medium':.2,'High':.7}
    assert data_fusion.fuse(segment,base,datetime.now(timezone.utc))[0]==base
    assert not data_fusion.fuse(segment,base,datetime.now(timezone.utc)+timedelta(hours=3))[1]['applied']


def test_raw_count_without_calibration_does_not_become_traffic_volume(client,authority,segments):
    import data_fusion
    row=observation(client,authority,segments,age=2,congestion_level=None,vehicle_count=500)
    client.patch('/api/observations/'+str(row['id']),json={'quality_status':'verified'},headers=authority)
    assert not data_fusion.fuse(segments['100 Feet Road']['segment_id'],{'Low':.2,'Medium':.2,'High':.6},datetime.now(timezone.utc))[1]['applied']


def test_interval_end_requires_start_and_is_quarantined(client,authority):
    r=client.post('/api/observations',headers=authority,json={'area':'Indiranagar','road':'100 Feet Road',
        'observed_date':'2022-03-05','time_block':'Morning','observed_end':datetime.now(timezone.utc).isoformat(),
        'vehicle_count':10,'flow_per_hour':600})
    assert r.status_code==422 and r.json()['detail']['quarantine_id']


def test_verified_cctv_adjusts_history_but_revoked_camera_is_ignored(client,authority,citizen,segments):
    import data_fusion
    camera={'camera_id':'fusion-camera','display_name':'Fusion check','area':'Indiranagar','road':'100 Feet Road',
        'source_type':'approved_ip_camera','authorization_status':'approved','operational_status':'online'}
    assert client.post('/api/cameras',json=camera,headers=authority).status_code==200
    row=observation(client,authority,segments,age=1,source_type='camera',camera_id='fusion-camera',
        source_name='camera_stream_collector',congestion_level=None,occupancy=.9)
    client.patch('/api/observations/'+str(row['id']),json={'quality_status':'verified'},headers=authority)
    segment=segments['100 Feet Road']['segment_id'];base={'Low':.7,'Medium':.2,'High':.1}
    blended,details=data_fusion.fuse(segment,base,datetime.now(timezone.utc))
    assert details['applied'] and blended['High']>base['High']
    assert details['sources'][0]['camera_id']=='fusion-camera'
    assert client.get('/api/forecast',params={'segment_id':segment},headers=citizen).json()['signals']['camera']['camera_id']=='fusion-camera'
    camera['authorization_status']='revoked'
    client.post('/api/cameras',json=camera,headers=authority)
    assert not data_fusion.fuse(segment,base,datetime.now(timezone.utc))[1]['applied']
    assert client.get('/api/forecast',params={'segment_id':segment},headers=citizen).json()['signals']['camera'] is None
