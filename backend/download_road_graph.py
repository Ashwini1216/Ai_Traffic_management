"""Download a small, real OpenStreetMap pilot graph for local routing.

Usage: python backend/download_road_graph.py
The default pilot covers Indiranagar, M.G. Road and nearby corridors. Map data is
not traffic data. Network expansion is explicit; no map is generated/invented.
"""
import json
import ssl
import urllib.request
import urllib.parse
from pathlib import Path
import certifi
import core


def main():
    bounds=(12.955,77.610,12.990,77.655)
    bbox=','.join(map(str,bounds))
    query=f'''[out:json][timeout:90];
      way["highway"]["highway"!~"footway|path|cycleway|steps|pedestrian|construction|proposed|bridleway|corridor"]["area"!="yes"]({bbox})->.roads;
      (.roads;relation(bw.roads)["type"="restriction"];);out body;>;out skel qt;'''
    errors=[]
    for endpoint in ['https://overpass-api.de/api/interpreter','https://overpass.kumi.systems/api/interpreter']:
        try:
            request=urllib.request.Request(endpoint,data=urllib.parse.urlencode({'data':query}).encode(),
                headers={'User-Agent':'FlowStateTrafficMonitor/1.0 (+https://github.com/Ashwini1216/Ai_Traffic_management)'})
            with urllib.request.urlopen(request,timeout=120,context=ssl.create_default_context(cafile=certifi.where())) as response:
                data=json.load(response)
            if data.get('remark') or not data.get('elements'):
                raise ValueError(data.get('remark','No map elements returned'))
            data['flowstate']={'bounds':bounds,'downloaded_at':core.iso_now(),'source':endpoint,
                'attribution':'© OpenStreetMap contributors, ODbL','coverage':'Pilot area, not all Bengaluru'}
            target=core.DATA_DIR/'routing'/'pilot_osm.json';target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_suffix('.pending.json');temporary.write_text(json.dumps(data));temporary.replace(target)
            print('Saved real OpenStreetMap pilot:',len(data['elements']),'elements;',target.stat().st_size,'bytes')
            return
        except Exception as exc:errors.append(str(exc))
    raise SystemExit('Road-map download failed: '+'; '.join(errors))


if __name__=='__main__':main()
