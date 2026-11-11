"""Real city graph path/cost/closure benchmark; no route-provider stand-ins."""
import json,math,time,resource,sys
from datetime import datetime,timezone
import core,local_routing
from dstar_lite import astar,INF

CASES=[('Indiranagar–Hebbal',(12.9716,77.6412),(13.0356,77.5971)),
       ('Kengeri–Whitefield',(12.914,77.482),(12.985,77.73)),
       ('Central Bengaluru–Electronic City',(12.9749,77.6008),(12.8458,77.6604)),
       ('Hoskote–Yeshwanthpur',(13.074,77.797),(13.0236,77.5497)),
       ('Bannerghatta–Yelahanka',(12.800,77.577),(13.101,77.594)),
       ('Nagarbhavi–Sarjapur',(12.961,77.507),(12.860,77.786)),
       ('City centre–airport',(12.975,77.607),(13.198,77.706)),
       ('Nelamangala–Attibele',(13.097,77.397),(12.779,77.771))]


def cost(view,path):return sum(view.cost(a,b) for a,b in zip(path,path[1:])) if path else INF


def main():
    started=time.monotonic();roads=local_routing.load_map()
    if not roads or roads.metadata.get('scope')!='bengaluru_wide':raise ValueError('The complete Bengaluru graph is not installed.')
    report={'graph':local_routing.status(),'load_seconds':round(time.monotonic()-started,3),'cases':[],
            'source':'Real Geofabrik/OpenStreetMap extract; all routes computed locally.','checked_at':core.iso_now()}
    for name,origin,destination in CASES:
        begin=time.monotonic()
        try:
            candidates=local_routing.routes(origin,destination)
            if not candidates:raise ValueError('No local route or unsupported snap distance.')
            route=candidates[0];elapsed=time.monotonic()-begin
            goal,_=roads.snap(destination,origin=False);view,planner=roads.sessions[goal]
            begin=time.monotonic();reference=astar(view,planner.start,'goal',view.heuristic)
            reference_cost=cost(view,reference)
            same=math.isclose(route['dynamic_cost_s'],reference_cost,rel_tol=1e-9,abs_tol=1e-5)
            if not same:raise ValueError('D* Lite path cost differs from A*.')
            result={'name':name,'passed':True,'distance_m':round(route['distance_m']),
                'seconds':round(elapsed,3),'astar_seconds':round(time.monotonic()-begin,3),
                'cost_matches_astar':same,'geometry_points':len(route['coordinates']),
                'snap_distance_m':route['snap_distance_m'],'expanded_states':route['diagnostics']['expanded_states']}
            # Close a real interior road edge in an isolated planner view, repair,
            # and reopen it. Never create simulated incidents in the live database.
            path=planner.path();interior=[s for s in path if s in roads.edges]
            edge_id=interior[len(interior)//2];edge=roads.edges[edge_id]
            physical=roads.by_physical[(edge.way,min(edge.u,edge.v),max(edge.u,edge.v))]
            previous=dict(view.weights)
            view.weights={**view.weights,**{e:INF for e in physical}}
            planner.changed(p for e in physical for p in view.predecessors(e))
            begin=time.monotonic();repaired=planner.path()
            repair_seconds=time.monotonic()-begin
            after_ref=astar(view,planner.start,'goal',view.heuristic)
            result['closure_repair_matches_astar']=math.isclose(cost(view,repaired),cost(view,after_ref),rel_tol=1e-9,abs_tol=1e-5)
            result['closure_avoided']=not set(repaired)&set(physical)
            result['repair_seconds']=round(repair_seconds,3)
            view.weights=previous;planner.changed(p for e in physical for p in view.predecessors(e))
            result['reopened_matches_original']=math.isclose(cost(view,planner.path()),reference_cost,rel_tol=1e-9,abs_tol=1e-5)
            result['passed']=all(result[k] for k in ('cost_matches_astar','closure_repair_matches_astar','closure_avoided','reopened_matches_original'))
        except Exception as error:
            result={'name':name,'passed':False,'error':str(error),'seconds':round(time.monotonic()-begin,3)}
        report['cases'].append(result);print(result,flush=True)
        (core.ROOT/'evaluation'/'city-routing-benchmark.json').write_text(json.dumps(report,indent=2))
    report['peak_rss_gib']=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**3 if sys.platform=='darwin' else 1024**2),3)
    report['passed']=all(c['passed'] for c in report['cases'])
    (core.ROOT/'evaluation'/'city-routing-benchmark.json').write_text(json.dumps(report,indent=2))
    print('Complete:',report['passed'],'peak memory',report['peak_rss_gib'],'GiB',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
