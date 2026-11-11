"""Road routing without Google: geometry, persistence, failure and coverage checks."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException


@pytest.fixture
def osm(monkeypatch):
    import road_routing
    monkeypatch.setenv("ROUTING_PROVIDER", "openstreetmap")
    road_routing._CACHE.clear()
    return road_routing


def test_open_routes_have_road_geometry_and_alternatives(client, citizen, osm):
    result=client.get('/api/routes',params={'origin_lat':12.9716,'origin_lon':77.6412,
        'destination_lat':12.9727,'destination_lon':77.6196},headers=citizen)
    assert result.status_code==200,result.text
    body=result.json()
    assert len(body['routes'])==2
    assert all(r['source_type']=='osm_route' for r in body['routes'])
    assert any(len(r['geometry']['coordinates'])>2 for r in body['routes'])
    assert all(r['traffic_delay_s'] is None for r in body['routes'])


def test_osm_trip_preserves_the_actual_road_path(client,citizen,osm):
    import core,store,notifications
    result=client.post('/api/trips',json={'origin':'Any searched starting place','destination':'Any searched destination',
        'origin_lat':12.9716,'origin_lon':77.6412,'destination_lat':12.9727,'destination_lon':77.6196,
        'departure_at':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()},headers=citizen)
    assert result.status_code==201,result.text
    trip=store.one('SELECT * FROM planned_trips WHERE trip_id=?',(result.json()['trip_id'],))
    assert trip['route_source']=='osm_route' and trip['route_polyline']
    assert notifications._trip_route(trip)['coordinates']==core.decode_google_polyline(trip['route_polyline'])


def test_no_road_route_does_not_become_a_fake_corridor(client,citizen,osm,monkeypatch):
    import core
    monkeypatch.setattr(core,'external_json',lambda *a,**kw:{'code':'NoRoute'})
    with pytest.raises(HTTPException) as error:
        osm.candidates((12.97,77.64),(12.98,77.65))
    assert error.value.status_code==404


def test_invalid_geometry_and_nonfinite_duration_are_rejected(osm,monkeypatch):
    import core
    monkeypatch.setattr(core,'external_json',lambda *a,**kw:{'code':'Ok','routes':[
        {'geometry':{'coordinates':[[77.64,12.97],[77.65,12.98]]},'distance':20,'duration':float('nan')}]})
    with pytest.raises(HTTPException) as error:osm.candidates((12.97,77.64),(12.98,77.65))
    assert error.value.status_code==502


def test_places_outside_bengaluru_and_unconfigured_modes_are_rejected(osm):
    with pytest.raises(HTTPException):osm.candidates((28.6,77.2),(12.98,77.65))
    with pytest.raises(HTTPException):osm.candidates((12.97,77.64),(12.98,77.65),'walking')


def test_polyline_round_trip_retains_road_bends(osm):
    import core
    points=[[77.6412,12.9716],[77.6321,12.9789],[77.6196,12.9727]]
    assert core.decode_google_polyline(osm.encode_polyline(points))==points
