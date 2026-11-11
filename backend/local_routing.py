"""Local OpenStreetMap pilot: turn-aware road graph and incremental D* Lite.

Edge states preserve the previous way so node-via turn restrictions are enforced.
Complex via-way restrictions are conservatively blocked, never silently ignored.
The pilot cannot claim city-wide coverage or live traffic where no sensor exists.
"""
from __future__ import annotations
from collections import defaultdict,OrderedDict,Counter
from dataclasses import dataclass
from datetime import datetime,timezone
import json,math,os,re,threading,gzip
from pathlib import Path

import core
from dstar_lite import DStarLite,astar,INF

DRIVABLE={'motorway','trunk','primary','secondary','tertiary','unclassified','residential','living_street','service',
          'motorway_link','trunk_link','primary_link','secondary_link','tertiary_link'}
_LOCK=threading.RLock()
_LOADED={'key':None,'map':None}


@dataclass(slots=True)
class Edge:
    u:int
    v:int
    way:int
    length:float
    seconds:float
    name:str
    shape:tuple=()


class RoadMap:
    def __init__(self,data):
        self.metadata=data.get('flowstate',{})
        self.points={e['id']:(e['lat'],e['lon']) for e in data['elements'] if e['type']=='node' and 'lat' in e}
        self.edges={};self.outgoing=defaultdict(list);self.incoming=defaultdict(list)
        self.by_way=defaultdict(list);self.by_physical=defaultdict(list)
        self.restrictions=defaultdict(list);self.complex_from=set();self.sessions=OrderedDict()
        uses=Counter(node for way in data['elements'] if way['type']=='way' and way.get('tags',{}).get('highway') in DRIVABLE for node in way['nodes'])
        anchors={node for node,count in uses.items() if count>1}
        anchors.update(m['ref'] for relation in data['elements'] if relation['type']=='relation' for m in relation.get('members',[]) if m.get('role')=='via' and m['type']=='node')
        edge_metres=float(os.getenv('LOCAL_ROUTING_EDGE_METRES','100'))
        next_derived=min(0,min(self.points,default=0))-1
        for item in data['elements']:
            if item['type']!='relation' or item.get('tags',{}).get('type')!='restriction':continue
            members=item.get('members',[]);tags=item['tags']
            source=next((m['ref'] for m in members if m['role']=='from' and m['type']=='way'),None)
            target=next((m['ref'] for m in members if m['role']=='to' and m['type']=='way'),None)
            via=next((m['ref'] for m in members if m['role']=='via' and m['type']=='node'),None)
            restriction=tags.get('restriction:motorcar') or tags.get('restriction:motor_vehicle') or tags.get('restriction')
            if 'motorcar' in tags.get('except','').split(';') or 'motor_vehicle' in tags.get('except','').split(';'):continue
            if source is not None and (via is None or not restriction or 'restriction:conditional' in tags):
                self.complex_from.add(source)
            elif source is not None and target is not None:
                self.restrictions[(via,source)].append((restriction,target))
        for way in data['elements']:
            if way['type']!='way':continue
            tags=way.get('tags',{});highway=tags.get('highway')
            if highway not in DRIVABLE:continue
            access=next((tags[k] for k in ('motorcar','motor_vehicle','vehicle','access') if k in tags),'yes')
            if access in {'no','private','agricultural','forestry','destination','customers','delivery','permit'} or any(k.endswith(':conditional') for k in tags):continue
            default=50 if highway in {'motorway','trunk'} else 40 if highway=='primary' else 30 if highway in {'secondary','tertiary'} else 20
            match=re.search(r'\d+(?:\.\d+)?',tags.get('maxspeed',''))
            speed=float(match.group()) if match else default
            if 'mph' in tags.get('maxspeed',''):speed*=1.609344
            speed=min(130,max(5,speed))/3.6
            oneway=tags.get('oneway:motorcar') or tags.get('oneway:motor_vehicle') or tags.get('oneway','yes' if tags.get('junction')=='roundabout' else 'no')
            # Collapse shape-only nodes while preserving every intermediate point
            # in edge geometry. Intersections, way ends and restriction-via nodes
            # remain anchors. Long straight sections gain derived snap anchors.
            groups=[];group=[]
            for node in way['nodes']:
                if node not in self.points:
                    if len(group)>1:groups.append(group)
                    group=[];continue
                if group and group[-1]==node:continue
                if group:
                    a,b=self.points[group[-1]],self.points[node]
                    pieces=max(1,math.ceil(core.haversine_m(*a,*b)/edge_metres))
                    for part in range(1,pieces):
                        self.points[next_derived]=(a[0]+(b[0]-a[0])*part/pieces,a[1]+(b[1]-a[1])*part/pieces)
                        group.append(next_derived);next_derived-=1
                group.append(node)
            if len(group)>1:groups.append(group)
            sections=[]
            for group in groups:
                shape=[group[0]];distance=0
                for position,(u,v) in enumerate(zip(group,group[1:]),1):
                    distance+=core.haversine_m(*self.points[u],*self.points[v]);shape.append(v)
                    if v in anchors or position==len(group)-1 or distance>=edge_metres:
                        if distance>.01:sections.append((tuple(shape),distance))
                        shape=[v];distance=0
            for i,(shape,distance) in enumerate(sections):
                u,v=shape[0],shape[-1]
                directions=[(u,v)] if oneway in {'yes','1','true'} else [(v,u)] if oneway=='-1' else [(u,v),(v,u)]
                for j,(a,b) in enumerate(directions):
                    eid=f"e{way['id']}:{i}:{j}"
                    self.edges[eid]=Edge(a,b,way['id'],distance,distance/speed,tags.get('name',highway),shape if a==u else tuple(reversed(shape)))
                    self.outgoing[a].append(eid);self.incoming[b].append(eid)
                    self.by_way[way['id']].append(eid)
                    self.by_physical[(way['id'],min(a,b),max(a,b))].append(eid)
        self.adj={};self.pred=defaultdict(list)
        for eid,edge in self.edges.items():
            self.adj[eid]=[n for n in self.outgoing[edge.v] if self.allowed(edge,self.edges[n])]
            for n in self.adj[eid]:self.pred[n].append(eid)
        for node,edges in self.outgoing.items():
            state='n'+str(node);self.adj[state]=edges
            for eid in edges:self.pred[eid].append(state)
        # The city graph is large enough that scanning every node/edge per request
        # is expensive. Spherical nearest-node trees and a road grid preserve the
        # distance semantics while narrowing work to the nearby road cells.
        import numpy as np
        from scipy.spatial import cKDTree
        self.snap_indexes={}
        self.xyz={}
        for kind,nodes in [('origin',list(self.outgoing)),('destination',list(self.incoming))]:
            if not nodes:continue
            positions=np.radians(np.array([self.points[n] for n in nodes]));lat,lon=positions[:,0],positions[:,1]
            xyz=np.column_stack([np.cos(lat)*np.cos(lon),np.cos(lat)*np.sin(lon),np.sin(lat)])
            self.snap_indexes[kind]=(nodes,cKDTree(xyz))
            self.xyz.update({node:tuple(point*core.EARTH_RADIUS_M) for node,point in zip(nodes,xyz)})
        self.road_grid=defaultdict(list);self.grid_size=.002
        for (way,u,v),ids in self.by_physical.items():
            positions=[self.points[n] for n in self.edges[ids[0]].shape]
            for y in range(math.floor(min(p[0] for p in positions)/self.grid_size),math.floor(max(p[0] for p in positions)/self.grid_size)+1):
                for x in range(math.floor(min(p[1] for p in positions)/self.grid_size),math.floor(max(p[1] for p in positions)/self.grid_size)+1):
                    self.road_grid[(y,x)].append(ids[0])

    def allowed(self,previous,next_edge):
        if previous.way in self.complex_from:return False
        for kind,target in self.restrictions.get((previous.v,previous.way),[]):
            if kind.startswith('only_') and next_edge.way!=target:return False
            if kind=='no_u_turn' and next_edge.way==target and next_edge.v==previous.u:return False
            if kind.startswith('no_') and kind!='no_u_turn' and next_edge.way==target:return False
        return True

    def position(self,state,goal):
        if state=='goal':return self.points[goal]
        return self.points[int(state[1:])] if state.startswith('n') else self.points[self.edges[state].v]

    def snap(self,point,origin=True):
        index=self.snap_indexes.get('origin' if origin else 'destination')
        if index is None:return None,INF
        nodes,tree=index;lat,lon=map(math.radians,point)
        _,position=tree.query([math.cos(lat)*math.cos(lon),math.cos(lat)*math.sin(lon),math.sin(lat)])
        node=nodes[int(position)]
        return node,core.haversine_m(*point,*self.points[node])

    def match_edge(self,lat,lon,max_distance=150):
        best,distance=None,max_distance
        radius=max(1,math.ceil(max_distance/(110000*self.grid_size*.95)))
        cell=(math.floor(lat/self.grid_size),math.floor(lon/self.grid_size))
        candidates={key for dy in range(-radius,radius+1) for dx in range(-radius,radius+1)
                    for key in self.road_grid.get((cell[0]+dy,cell[1]+dx),[])}
        for key in candidates:
            edge=self.edges[key]
            a,b=self.points[edge.u],self.points[edge.v]
            if abs(a[0]-lat)>.003 and abs(b[0]-lat)>.003:continue
            d=core.distance_to_polyline_m(lat,lon,[[self.points[n][1],self.points[n][0]] for n in edge.shape])
            if d<distance:best,distance=key,d
        return best


class GraphView:
    def __init__(self,roads,goal,weights=None,blocked=()):
        self.roads,self.goal=roads,goal;self.weights=weights or {};self.blocked=set(blocked)
        self.max_expansions=max(1_000_000,3*(len(roads.edges)+len(roads.outgoing)))

    def successors(self,node):
        result=self.roads.adj.get(node,[])
        if node in self.roads.edges and self.roads.edges[node].v==self.goal:return [*result,'goal']
        return result

    def predecessors(self,node):
        return self.roads.incoming[self.goal] if node=='goal' else self.roads.pred.get(node,[])

    def cost(self,u,v):
        if v=='goal':return 0
        return INF if v in self.blocked else self.roads.edges[v].seconds*self.weights.get(v,1)

    def heuristic(self,a,b):
        def node(state):
            return self.goal if state=='goal' else int(state[1:]) if state.startswith('n') else self.roads.edges[state].v
        p,q=self.roads.xyz[node(a)],self.roads.xyz[node(b)]
        # Chord distance is a metric and a lower bound on great-circle road
        # distance, so it remains consistent at the maximum allowed road speed.
        return math.sqrt((p[0]-q[0])**2+(p[1]-q[1])**2+(p[2]-q[2])**2)/(130/3.6)


def load_map():
    city=core.DATA_DIR/'routing'/'bengaluru_osm.json.gz'
    path=Path(os.getenv('LOCAL_OSM_GRAPH') or (city if city.is_file() else core.DATA_DIR/'routing'/'pilot_osm.json'))
    if not path.is_absolute():path=core.ROOT/path
    if os.getenv('LOCAL_ROUTING_ENABLED','1')=='0' or not path.is_file():return None
    key=(str(path),path.stat().st_mtime_ns)
    with _LOCK:
        if _LOADED['key']!=key:
            if path.suffix=='.gz':
                with gzip.open(path,'rt',encoding='utf-8') as handle:data=json.load(handle)
            else:data=json.loads(path.read_text())
            _LOADED.update(key=key,map=RoadMap(data))
        return _LOADED['map']


def status():
    roads=load_map()
    if not roads:return {'available':False,'message':'No local OpenStreetMap road graph is installed.'}
    return {'available':True,'directed_edges':len(roads.edges),'nodes':len(roads.outgoing),
            'geometry_points':len(roads.points),'simplified':True,
            'coverage':roads.metadata,'turn_restrictions':sum(map(len,roads.restrictions.values())),
            'conservatively_blocked_complex_restrictions':len(roads.complex_from)}


def closure_uses_route(route, latitude, longitude):
    """Match a local closure to its actual road edge, including the reverse direction.

    Nearby parallel roads can lie inside a 35 m corridor. The local graph has enough
    information to distinguish them; external geometry keeps the corridor fallback.
    """
    if not route or not route.get('osm_edges'):
        return None
    roads = load_map()
    if roads is None:
        return None
    matched = roads.match_edge(latitude, longitude, 50)
    if matched is None:
        return None
    edge = roads.edges[matched]
    return any(way == edge.way and {u, v} == {edge.u, edge.v} for way, u, v in route['osm_edges'])


def dynamic_weights(roads,departure):
    import incidents,forecasting,segments
    weights={}
    # A measured zone is attached to its closest mapped way, rather than inventing
    # live data over every road in the city.
    for segment in segments.coverage_registry().values():
        if segment.get('latitude') is None:continue
        matched=roads.match_edge(segment['latitude'],segment['longitude'])
        if not matched:continue
        outlook=forecasting.forecast(segment['segment_id'],departure,record=False)
        way=roads.edges[matched].way
        factor=1+2*outlook['expected_load']
        for eid in roads.by_way[way]:weights[eid]=max(weights.get(eid,1),factor)
    for incident in incidents.active(departure):
        matched=roads.match_edge(incident['latitude'],incident['longitude'],50)
        if not matched:continue
        target=roads.edges[matched]
        for eid in roads.by_physical[(target.way,min(target.u,target.v),max(target.u,target.v))]:
            weights[eid]=INF if incident['closure'] else max(weights.get(eid,1),1+3*forecasting.INCIDENT_WEIGHTS.get(incident['category'],.25))
    return weights


def routes(origin,destination,departure=None):
    roads=load_map()
    if roads is None:return None
    start,start_distance=roads.snap(origin);goal,goal_distance=roads.snap(destination,origin=False)
    if max(start_distance,goal_distance)>250 or start==goal:return None
    start_state='n'+str(start)
    with _LOCK:
        weights=dynamic_weights(roads,departure or datetime.now(timezone.utc))
        if goal in roads.sessions:
            view,planner=roads.sessions.pop(goal)
            changed=[e for e in set(weights)|set(view.weights) if weights.get(e,1)!=view.weights.get(e,1)]
            view.weights=weights;planner.move_start(start_state)
            if changed:planner.changed(p for e in changed for p in view.predecessors(e))
        else:
            view=GraphView(roads,goal,weights);planner=DStarLite(view,start_state,'goal',view.heuristic)
        roads.sessions[goal]=(view,planner)
        while len(roads.sessions)>int(os.getenv('LOCAL_ROUTING_MAX_GOALS','2' if len(roads.edges)>100000 else '4')):roads.sessions.popitem(last=False)
        primary=planner.path()
        if not primary:return []
        paths=[primary]
        road_states=[s for s in primary if s in roads.edges]
        # Distinct loop-free alternatives, using A* with an interior edge excluded.
        city=roads.metadata.get('scope')=='bengaluru_wide'
        for fraction in ((.35,.65) if city else (.33,.66,.5,.25)):
            if len(road_states)<3:break
            pivot=min(len(road_states)-2,max(1,int(len(road_states)*fraction)))
            span=max(1,int(len(road_states)*.06)) if city else 0
            excluded=road_states[max(1,pivot-span):min(len(road_states)-1,pivot+span+1)]
            alt_view=GraphView(roads,goal,weights,excluded)
            alt=astar(alt_view,start_state,'goal',alt_view.heuristic)
            if alt and all(len(set(alt)&set(p))/max(1,len(set(p)))<.9 for p in paths):paths.append(alt)
            if len(paths)>=(2 if city else 3):break
        result=[]
        for path in paths:
            edges=[roads.edges[e] for e in path if e in roads.edges]
            coordinates=[[roads.points[start][1],roads.points[start][0]]]+[[roads.points[n][1],roads.points[n][0]] for e in edges for n in e.shape[1:]]
            result.append({'source':'local_osm_route','coordinates':coordinates,'distance_m':sum(e.length for e in edges),
                'duration_s':sum(e.seconds for e in edges), 'static_duration_s':None,
                'dynamic_cost_s':sum(view.cost(a,b) for a,b in zip(path,path[1:])),
                'osm_edges':[[e.way,e.u,e.v] for e in edges],
                'algorithm':'D* Lite' if path is primary else 'A* alternative',
                'diagnostics':{'expanded_states':planner.expanded,'incremental_repairs':planner.repairs},
                'attribution':'© OpenStreetMap contributors','coverage':roads.metadata.get('coverage','Local OpenStreetMap pilot'),
                'snap_distance_m':[round(start_distance),round(goal_distance)]})
        return result
