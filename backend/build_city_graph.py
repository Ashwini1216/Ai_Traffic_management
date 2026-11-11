"""Build Bengaluru-wide local routing from an official Geofabrik OSM PBF extract.

python backend/build_city_graph.py --pbf data/routing/southern-zone-latest.osm.pbf
Install requirements-research.txt for the optional osmium acquisition dependency.
The output includes the entire supported Bengaluru box plus a boundary buffer.
"""
from __future__ import annotations
import argparse, gzip, hashlib, json, time
from pathlib import Path
import core

SOURCE='https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf'
DRIVABLE={'motorway','trunk','primary','secondary','tertiary','unclassified','residential','living_street','service',
          'motorway_link','trunk_link','primary_link','secondary_link','tertiary_link'}


def main():
    import osmium
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pbf',type=Path,default=core.DATA_DIR/'routing'/'southern-zone-latest.osm.pbf')
    parser.add_argument('--output',type=Path,default=core.DATA_DIR/'routing'/'bengaluru_osm.json.gz')
    args=parser.parse_args();started=time.monotonic()
    if not args.pbf.is_file():raise SystemExit('Download the Geofabrik southern-zone extract first: '+SOURCE)
    bounds=core.BENGALURU_BOUNDS;buffer=.02
    class Extract(osmium.SimpleHandler):
        def __init__(self):
            super().__init__();self.points={};self.ways=[];self.way_ids=set();self.relations=[];self.used=set()
        def node(self,node):
            if node.location.valid() and bounds['south']-buffer<=node.location.lat<=bounds['north']+buffer and bounds['west']-buffer<=node.location.lon<=bounds['east']+buffer:
                self.points[node.id]=(node.location.lat,node.location.lon)
        def way(self,way):
            if way.tags.get('highway') not in DRIVABLE or way.tags.get('area')=='yes':return
            nodes=[n.ref for n in way.nodes]
            if not any(n in self.points for n in nodes):return
            keys={'highway','name','maxspeed','junction','oneway','motorcar','motor_vehicle','vehicle','access','oneway:motorcar','oneway:motor_vehicle'}
            tags={t.k:t.v for t in way.tags if t.k in keys or t.k.endswith(':conditional')}
            self.ways.append({'type':'way','id':way.id,'nodes':nodes,'tags':tags});self.way_ids.add(way.id)
            self.used.update(n for n in nodes if n in self.points)
        def relation(self,relation):
            if relation.tags.get('type')!='restriction':return
            members=[{'type':{'n':'node','w':'way','r':'relation'}[m.type],'ref':m.ref,'role':m.role} for m in relation.members]
            if not any(m['type']=='way' and m['ref'] in self.way_ids for m in members):return
            self.relations.append({'type':'relation','id':relation.id,'members':members,'tags':dict(relation.tags)})
    reader=osmium.io.Reader(str(args.pbf));header=reader.header();timestamp=header.get('osmosis_replication_timestamp');reader.close()
    extractor=Extract();print('Reading the OSM extract; keeping Bengaluru road geometry only.',flush=True)
    extractor.apply_file(str(args.pbf))
    if len(extractor.ways)<20000:raise ValueError('Too few city road ways; refusing to label a partial graph as Bengaluru-wide.')
    elements=[{'type':'node','id':n,'lat':extractor.points[n][0],'lon':extractor.points[n][1]} for n in sorted(extractor.used)]
    elements+=extractor.ways+extractor.relations
    metadata={'scope':'bengaluru_wide','coverage':'Entire supported Bengaluru region with 2 km boundary buffer',
        'bounds':[bounds['south'],bounds['west'],bounds['north'],bounds['east']], 'buffer_degrees':buffer,
        'source':SOURCE,'source_sha256':hashlib.file_digest(args.pbf.open('rb'),'sha256').hexdigest(),
        'osm_timestamp':timestamp,'built_at':core.iso_now(),'attribution':'© OpenStreetMap contributors, ODbL; extract by Geofabrik',
        'ways':len(extractor.ways),'nodes':len(extractor.used),'restriction_relations':len(extractor.relations),
        'notice':'Real mapped road geometry, not live traffic. Unmapped roads and access restrictions can limit routing.'}
    reference=core.DATA_DIR/'routing'/'bengaluru-boundary-reference.json'
    if reference.exists():
        city=json.loads(reference.read_text())[0];s,n,w,e=map(float,city['boundingbox'])
        metadata['mapped_city_boundary']={'osm_id':city['osm_id'],'bbox':[s,w,n,e],
            'contained':bounds['south']<=s and n<=bounds['north'] and bounds['west']<=w and e<=bounds['east']}
        if not metadata['mapped_city_boundary']['contained']:raise ValueError('The selected box does not include the city boundary.')
    args.output.parent.mkdir(parents=True,exist_ok=True);temporary=args.output.with_suffix('.pending.gz')
    with gzip.open(temporary,'wt',encoding='utf-8',compresslevel=5) as handle:
        json.dump({'flowstate':metadata,'elements':elements},handle,separators=(',',':'))
    temporary.replace(args.output)
    (args.output.parent/'bengaluru-source-manifest.json').write_text(json.dumps(metadata,indent=2))
    print(json.dumps({**metadata,'compressed_bytes':args.output.stat().st_size,'build_seconds':round(time.monotonic()-started,2)},indent=2),flush=True)


if __name__=='__main__':main()
