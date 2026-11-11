from datetime import datetime,timezone

import pytest

POINTS={'origin_lat':12.9716,'origin_lon':77.6412,'destination_lat':12.9727,'destination_lon':77.6196}
MID={'latitude':(12.9716+12.9727)/2,'longitude':(77.6412+77.6196)/2}


def test_unverified_report_then_verification_changes_route(client,authority,citizen):
    import store
    before=client.get('/api/routes/state',headers=citizen).json()['revision']
    incident=client.post('/api/incidents',json={'category':'road_blockage','description':'Road fully blocked at the crossing',**MID},headers=citizen).json()
    try:
        assert client.get('/api/routes/state',headers=citizen).json()['revision']==before
        assert len(client.get('/api/routes',params=POINTS,headers=citizen).json()['routes'])==2
        verified=client.patch('/api/incidents/'+incident['incident_id'],json={'status':'verified','closure':True},headers=authority)
        assert verified.status_code==200
        result=client.get('/api/routes',params=POINTS,headers=citizen)
        assert result.status_code==200,result.text
        body=result.json()
        assert body['revision']!=before
        assert incident['incident_id'] in body['avoided_closure_ids']
        assert body['routes'] and not any(r['restricted'] for r in body['routes'])
        assert all(not any(i['incident_id']==incident['incident_id'] for i in r['incidents']) for r in body['routes'])
    finally:client.patch('/api/incidents/'+incident['incident_id'],json={'status':'resolved'},headers=authority)


def test_all_closed_routes_return_no_open_route(client,citizen,authority):
    closure=client.post('/api/incidents/official',json={'category':'road_closure','description':'Destination access is closed',
        'latitude':POINTS['destination_lat'],'longitude':POINTS['destination_lon'],'closure':True},headers=authority).json()
    try:
        result=client.get('/api/routes',params=POINTS,headers=citizen)
        assert result.status_code==409 and result.json()['detail']['code']=='no_open_route'
    finally:client.patch('/api/incidents/'+closure['incident_id'],json={'status':'resolved'},headers=authority)


def test_closed_osm_primary_triggers_road_detour(client,citizen,authority,monkeypatch):
    import road_routing,core
    monkeypatch.setenv('ROUTING_PROVIDER','openstreetmap')
    calls=[]
    def route(o,d,profile='driving',via=None,**kwargs):
        calls.append(via)
        points=[[o[1],o[0]],*[ [p[1],p[0]] for p in (via or []) ],[d[1],d[0]]]
        return [{'source':'osm_route','coordinates':points,'duration_s':900,'distance_m':core.polyline_length_m(points)}]
    monkeypatch.setattr(road_routing,'candidates',route)
    closure=client.post('/api/incidents/official',json={'category':'road_closure','description':'Road closed midway',**MID,'closure':True},headers=authority).json()
    try:
        response=client.get('/api/routes',params=POINTS,headers=citizen)
        assert response.status_code==200,response.text
        assert any(calls) and not any(r['restricted'] for r in response.json()['routes'])
    finally:client.patch('/api/incidents/'+closure['incident_id'],json={'status':'resolved'},headers=authority)
