import time
from count_validation import compare_counts
from vision import count_line_crossings


def test_line_counts_direction_touching_boundary_and_not_jitter_or_outside():
    tracks={1:[(0,.5,.2),(1,.5,.5),(2,.5,.8),(3,.5,.2)],
            2:[(0,.4,.8),(1,.4,.2)],3:[(0,1.5,.2),(1,1.5,.8)],
            4:[(0,.6,.4999),(1,.6,.5001)]}
    assert count_line_crossings(tracks,[[0,.5],[1,.5]])=={'in':1,'out':1,'total':2}


def test_count_errors_do_not_invent_accuracy_for_zero_references():
    result=compare_counts({'car':10,'bus':0},{'car':8,'bus':1})
    assert result['mean_absolute_count_error']==1.5
    assert result['per_class']['car']['relative_error_percent']==20
    assert result['per_class']['bus']['relative_error_percent'] is None


def test_validated_stream_observations_are_verified_for_fusion(client,authority,monkeypatch):
    import vision,store
    camera={'camera_id':'validated-stream','display_name':'Counted road view','area':'Indiranagar','road':'100 Feet Road',
        'source_type':'approved_ip_camera','authorization_status':'approved','operational_status':'online',
        'stream_url':'rtsp://camera.example/stream','counting_line':[[0,.5],[1,.5]],'measurement_validated':True,
        'validation_note':'Manually compared cars and buses in road footage.'}
    assert client.post('/api/cameras',json={**camera,'validation_note':''},headers=authority).status_code==422
    assert client.post('/api/cameras',json=camera,headers=authority).status_code==200
    monkeypatch.setattr(vision,'analyze_stream',lambda *args:{'duration_seconds':10,'line_crossings':3,'tracked_vehicles':3,
        'flow_per_hour':1080,'occupancy':.8,'queue_length':2,'direction_counts':{'in':2,'out':1,'total':3}})
    response=client.post('/api/cameras/validated-stream/collect',headers=authority)
    assert response.status_code==200,response.text
    obs=store.one('SELECT * FROM traffic_observations WHERE id=?',(response.json()['observation_id'],))
    assert obs['quality_status']=='verified' and obs['traffic_volume'] is None and obs['vehicle_count']==3
    assert obs['verified_by']=='validated_camera:validated-stream'
    camera['authorization_status']='revoked';client.post('/api/cameras',json=camera,headers=authority)


def test_video_reference_counts_are_owner_checked_and_saved(client,authority,citizen,monkeypatch):
    import vision,fake_vision
    monkeypatch.setattr(vision,'analyze_video',fake_vision.analyze_video)
    job=client.post('/api/video-samples',files={'video':('reference.mp4',b'fixture','video/mp4')},headers=authority).json()
    for _ in range(50):
        state=client.get('/api/video-samples/'+job['job_id'],headers=authority).json()
        if state['status']!='running':break
        time.sleep(.05)
    assert state['status']=='done'
    url='/api/video-samples/'+job['job_id']+'/validate'
    body={'expected':{'car':3,'bus':1},'note':'Manually counted distinct vehicles in the complete clip.'}
    assert client.post(url,json=body,headers=citizen).status_code==403
    result=client.post(url,json=body,headers=authority)
    assert result.status_code==200 and result.json()['mean_absolute_count_error']==.5
    assert client.post(url,json={**body,'expected':{'car':-1}},headers=authority).status_code==422
    assert client.post(url,json={**body,'expected':{'car':1.5}},headers=authority).status_code==422
