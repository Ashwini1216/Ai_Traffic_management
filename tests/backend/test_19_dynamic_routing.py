"""Compare incremental replanning with A* on directed graphs and OSM restrictions."""
import random
from dstar_lite import DStarLite,astar,INF
from local_routing import RoadMap,GraphView


class Graph:
    def __init__(self,adj):self.adj=adj
    def successors(self,u):return self.adj.get(u,{})
    def predecessors(self,v):return [u for u,links in self.adj.items() if v in links]
    def cost(self,u,v):return self.adj[u][v]


def price(graph,path):return sum(graph.cost(u,v) for u,v in zip(path,path[1:])) if path else INF


def test_dstar_repairs_closed_and_reopened_road_and_moves_origin():
    graph=Graph({'a':{'b':1,'c':3},'b':{'d':1},'c':{'d':1},'d':{}})
    planner=DStarLite(graph,'a','d')
    assert planner.path()==['a','b','d']
    graph.adj['b']['d']=INF;planner.changed(['b'])
    assert planner.path()==['a','c','d']
    graph.adj['b']['d']=.5;planner.changed(['b'])
    assert planner.path()==['a','b','d']
    planner.move_start('c');assert planner.path()==['c','d']
    graph.adj['c']['d']=INF;planner.changed(['c'])
    assert planner.path()==[] and planner.repairs==3


def test_random_directed_updates_match_astar_reference():
    rng=random.Random(42)
    graph=Graph({u:{v:rng.randint(1,20) for v in range(25) if u!=v and rng.random()<.15} for u in range(25)})
    planner=DStarLite(graph,0,24)
    for _ in range(100):
        u=rng.randrange(24)
        if graph.adj[u]:
            v=rng.choice(list(graph.adj[u]));graph.adj[u][v]=rng.choice([INF,1,5,20]);planner.changed([u])
        planner.move_start(rng.randrange(24))
        assert price(graph,planner.path())==price(graph,astar(graph,planner.start,24))


def map_data(restriction=False):
    nodes=[{'type':'node','id':i,'lat':12.97+(i//3)*.001,'lon':77.64+(i%3)*.001} for i in range(4)]
    ways=[{'type':'way','id':10,'nodes':[0,1],'tags':{'highway':'residential','oneway':'yes'}},
          {'type':'way','id':20,'nodes':[1,2],'tags':{'highway':'residential'}},
          {'type':'way','id':30,'nodes':[1,3,2],'tags':{'highway':'residential'}}]
    if restriction:ways.append({'type':'relation','id':99,'tags':{'type':'restriction','restriction':'no_right_turn'},
        'members':[{'type':'way','role':'from','ref':10},{'type':'way','role':'to','ref':20},{'type':'node','role':'via','ref':1}]})
    return {'elements':nodes+ways}


def test_osm_oneway_and_turn_restriction_are_enforced():
    roads=RoadMap(map_data(True));view=GraphView(roads,2)
    path=astar(view,'n0','goal',view.heuristic)
    assert path and any(roads.edges[s].way==30 for s in path if s in roads.edges)
    assert not astar(GraphView(roads,0),'n2','goal')


def test_local_route_uses_real_graph_and_excludes_changed_edge(client,monkeypatch):
    import local_routing
    roads=RoadMap(map_data())
    monkeypatch.setattr(local_routing,'load_map',lambda:roads)
    weights={};monkeypatch.setattr(local_routing,'dynamic_weights',lambda *args:dict(weights))
    before=local_routing.routes(roads.points[0],roads.points[2])
    assert before and before[0]['algorithm']=='D* Lite'
    blocked=next(e for e,v in roads.edges.items() if v.way==20 and v.u==1)
    weights[blocked]=INF
    after=local_routing.routes(roads.points[0],roads.points[2])
    assert after and after[0]['diagnostics']['incremental_repairs']==1
    assert len(after[0]['coordinates'])>len(before[0]['coordinates'])
    assert local_routing.routes((12.8,77.4),roads.points[2]) is None


def test_local_closure_does_not_close_a_parallel_road_twenty_metres_away(client, authority, monkeypatch):
    from datetime import datetime, timezone
    import local_routing, routing, core
    nodes = [{'type':'node','id':i,'lat':lat,'lon':lon} for i,(lat,lon) in enumerate(
        [(12.97,77.64),(12.97,77.642),(12.97015,77.64),(12.97015,77.642)])]
    ways = [{'type':'way','id':way,'nodes':pair,'tags':{'highway':'residential'}} for way,pair in [(10,[0,1]),(20,[2,3])]]
    roads = RoadMap({'elements':nodes+ways})
    monkeypatch.setattr(local_routing,'load_map',lambda:roads)
    closure = client.post('/api/incidents/official',json={'category':'road_closure','description':'Only the southern road is closed',
        'latitude':12.97,'longitude':77.641,'closure':True},headers=authority).json()
    try:
        route = {'source':'local_osm_route','coordinates':[[77.64,12.97015],[77.642,12.97015]],
                 'osm_edges':[[e.way,e.u,e.v] for e in roads.edges.values() if e.way==20],'duration_s':60}
        found = routing._incidents_on_route(route['coordinates'],datetime.now(timezone.utc),60,
                                            core.polyline_length_m(route['coordinates']),route)
        assert not any(i['incident_id']==closure['incident_id'] for i in found)
        assert local_routing.closure_uses_route({'osm_edges':[[e.way,e.u,e.v] for e in roads.edges.values() if e.way==10]},12.97,77.641)
    finally:
        client.patch('/api/incidents/'+closure['incident_id'],json={'status':'resolved'},headers=authority)
