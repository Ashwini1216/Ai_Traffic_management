"""Retrieve selected frames from the UA-DETRAC authors' public archive using ranges.

The 9.2 GB archive is not downloaded in full. Original annotations, frame IDs,
hashes and split assignment are kept beside each frame-aligned video sample.
"""
from __future__ import annotations
import argparse
from collections import OrderedDict, defaultdict
import hashlib, io, json, re, time, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
import requests
import core

ROOT = core.DATA_DIR/'research'/'vehicle-counts'
ARCHIVE_ID = '1fQC6CEWOeL9pnJ4ZdD7U2gromcnWDRgm'
SOURCE = 'https://sites.google.com/view/daweidu/projects/ua-detrac'
ANNOTATION_IDS = {'train': '1f22g3puG164gemY_njcPG8beuCcuMjqb', 'test': '1zc8KZWUa3Cw0DbcFqTCMBmUZzbfuhGKr'}


class RangeReader(io.RawIOBase):
    """A seekable remote file with a bounded block cache; reject servers ignoring Range."""
    def __init__(self,url,block_size=4*1024*1024):
        self.url=url;self.position=0;self.block_size=block_size;self.cache=OrderedDict();self.downloaded=0
        r=requests.get(url,headers={'Range':'bytes=-65536'},timeout=60)
        if r.status_code!=206:raise ValueError('Archive server did not honour byte ranges.')
        self.size=int(r.headers['Content-Range'].split('/')[-1])
        self.tail_start=self.size-len(r.content);self.tail=r.content
    def seekable(self):return True
    def readable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        self.position=offset if whence==0 else self.position+offset if whence==1 else self.size+offset
        return self.position
    def read(self,n=-1):
        if n<0:n=self.size-self.position
        n=min(n,self.size-self.position);chunks=[]
        while n>0:
            if self.position>=self.tail_start:
                chunk=self.tail[self.position-self.tail_start:self.position-self.tail_start+n]
            else:
                block=self.position//self.block_size;start=block*self.block_size
                if block not in self.cache:
                    end=min(self.size-1,start+self.block_size-1)
                    response=requests.get(self.url,headers={'Range':f'bytes={start}-{end}'},timeout=90)
                    if response.status_code!=206 or len(response.content)!=end-start+1:
                        raise ValueError('Incomplete byte-range download.')
                    self.cache[block]=response.content;self.downloaded+=len(response.content)
                    while len(self.cache)>8:self.cache.popitem(last=False)
                chunk=self.cache[block][self.position-start:self.position-start+n]
            if not chunk:break
            chunks.append(chunk);self.position+=len(chunk);n-=len(chunk)
        return b''.join(chunks)


def archive_url():
    response=requests.get('https://drive.google.com/uc',params={'export':'download','id':ARCHIVE_ID},timeout=45)
    parameters=dict(re.findall('<input[^>]*name="([^"]+)"[^>]*value="([^"]+)"',response.text))
    if parameters:
        return 'https://drive.usercontent.google.com/download?'+requests.compat.urlencode(parameters)
    return response.url


def select_sequences():
    """Use two official-train scenes for settings, four official-test weather scenes for scoring."""
    chosen=[]
    for split,weathers in [('train',['sunny','night']),('test',['sunny','cloudy','rainy','night'])]:
        with zipfile.ZipFile(ROOT/f'annotations-{split}.zip') as bundle:
            available=defaultdict(list)
            for name in sorted(bundle.namelist()):
                if not name.endswith('.xml'):continue
                root=ET.fromstring(bundle.read(name));attrs=root.find('sequence_attribute').attrib
                if len(root.findall('frame'))>=500 and attrs.get('camera_state')=='stable':
                    available[attrs['sence_weather']].append((name,root.attrib['name']))
            for weather in weathers:
                if not available[weather]:raise ValueError(f'No stable {split} {weather} scene found.')
                name,sequence=available[weather][0]
                target=ROOT/'clips'/sequence;target.mkdir(parents=True,exist_ok=True)
                annotation=bundle.read(name);(target/'annotation.xml').write_bytes(annotation)
                chosen.append({'sequence':sequence,'split':'validation' if split=='train' else 'test','weather':weather,
                    'annotation_sha256':hashlib.sha256(annotation).hexdigest(),'annotation_source_split':split})
    return chosen


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames',type=int,default=375,help='Original 25 fps frames per selected sequence (default 15 seconds).')
    parser.add_argument('--index-only',action='store_true')
    args=parser.parse_args();ROOT.mkdir(parents=True,exist_ok=True)
    for split, file_id in ANNOTATION_IDS.items():
        path = ROOT / f'annotations-{split}.zip'
        if not path.exists():
            import gdown
            temporary = path.with_suffix('.pending.zip')
            if not gdown.download(id=file_id, output=str(temporary), quiet=False):
                raise ValueError(f'Could not acquire official {split} annotations.')
            with zipfile.ZipFile(temporary) as bundle:
                if bundle.testzip() or not any(name.endswith('.xml') for name in bundle.namelist()):
                    raise ValueError(f'Invalid {split} annotation archive.')
            temporary.replace(path)
    chosen=select_sequences()
    reader=RangeReader(archive_url());archive=zipfile.ZipFile(reader)
    names=archive.namelist()
    (ROOT/'archive-index.json').write_text(json.dumps({'source':SOURCE,'archive_bytes':reader.size,'members':names},indent=2))
    print('Selected:',[(c['sequence'],c['split'],c['weather']) for c in chosen],flush=True)
    print('Archive:',len(names),'members;',reader.size,'bytes',flush=True)
    if args.index_only:return
    import cv2,numpy as np
    for sample in chosen:
        sequence=sample['sequence'];folder=ROOT/'clips'/sequence
        selected=sorted(n for n in names if f'/{sequence}/' in '/'+n and n.lower().endswith('.jpg'))[:args.frames]
        if len(selected)<args.frames:raise ValueError('Selected sequence is incomplete: '+sequence)
        digest=hashlib.sha256();writer=None
        try:
            for index,name in enumerate(selected):
                data=archive.read(name);digest.update(data)
                frame=cv2.imdecode(np.frombuffer(data,dtype=np.uint8),cv2.IMREAD_COLOR)
                if frame is None:raise ValueError('Invalid source frame: '+name)
                if writer is None:
                    h,w=frame.shape[:2];writer=cv2.VideoWriter(str(folder/'sample.avi'),cv2.VideoWriter_fourcc(*'MJPG'),25,(w,h))
                    if not writer.isOpened():raise ValueError('Could not create benchmark video.')
                    (folder/'first-frame.jpg').write_bytes(data)
                writer.write(frame)
                if index%100==0:print(sequence,index+1,'frames',flush=True)
        finally:
            if writer:writer.release()
        sample.update(source=SOURCE,fps=25,first_frame=1,frames=len(selected),original_jpeg_sequence_sha256=digest.hexdigest(),
                      video_sha256=hashlib.sha256((folder/'sample.avi').read_bytes()).hexdigest(),
                      notice='Public annotated traffic benchmark, not a live Bengaluru CCTV feed.')
        (folder/'manifest.json').write_text(json.dumps(sample,indent=2))
        print('Saved',sequence,'downloaded so far',round(reader.downloaded/1e6,1),'MB',flush=True)
    (ROOT/'selection.json').write_text(json.dumps(chosen,indent=2))


if __name__=='__main__':main()
