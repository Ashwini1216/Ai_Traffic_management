import gzip,json,math,random
import core
from local_routing import RoadMap,GraphView
from dstar_lite import astar


def geometry():
    points=[(12.97,77.64),(12.9701,77.6401),(12.97015,77.6402),(12.9701,77.6403),
            (12.97,77.6404),(12.9699,77.6405),(12.9706,77.6403)]
    nodes=[{'type':'node','id':i,'lat':p[0],'lon':p[1]} for i,p in enumerate(points)]
    return {'flowstate':{'scope':'bengaluru_wide'},'elements':nodes+[
        {'type':'way','id':10,'nodes':[0,1,2,3,4,5],'tags':{'highway':'residential'}},
        {'type':'way','id':20,'nodes':[3,6],'tags':{'highway':'residential'}}]}


def test_shape_contraction_preserves_junction_and_full_road_geometry():
    roads=RoadMap(geometry());assert 3 in roads.outgoing and 1 not in roads.outgoing
    view=GraphView(roads,5);path=astar(view,'n0','goal',view.heuristic)
    shape=[0]+[n for state in path if state in roads.edges for n in roads.edges[state].shape[1:]]
    assert shape==[0,1,2,3,4,5]
    length=sum(roads.edges[e].length for e in path if e in roads.edges)
    assert math.isclose(length,sum(core.haversine_m(*roads.points[a],*roads.points[b]) for a,b in zip(shape,shape[1:])),abs_tol=1e-7)


def test_city_spatial_indexes_match_bruteforce_distances_on_curves():
    roads=RoadMap(geometry());rng=random.Random(42)
    for _ in range(30):
        point=(12.9695+rng.random()*.0015,77.6395+rng.random()*.0015)
        node,distance=roads.snap(point)
        expected=min(core.haversine_m(*point,*roads.points[n]) for n in roads.outgoing)
        assert math.isclose(distance,expected,abs_tol=1e-6)
        match=roads.match_edge(*point)
        brute=min(core.distance_to_polyline_m(*point,[[roads.points[n][1],roads.points[n][0]] for n in edge.shape]) for edge in roads.edges.values())
        if brute<150:
            assert match is not None
            actual=core.distance_to_polyline_m(*point,[[roads.points[n][1],roads.points[n][0]] for n in roads.edges[match].shape])
            assert math.isclose(actual,brute,abs_tol=1e-6)


def test_compressed_city_graph_loads_and_reports_scope(client,tmp_path,monkeypatch):
    import local_routing
    target=tmp_path/'city.json.gz'
    with gzip.open(target,'wt') as handle:json.dump(geometry(),handle)
    monkeypatch.setenv('LOCAL_OSM_GRAPH',str(target))
    status=local_routing.status()
    assert status['available'] and status['coverage']['scope']=='bengaluru_wide' and status['simplified']
