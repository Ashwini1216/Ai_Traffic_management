"""Independent UA-DETRAC vehicle/detection/count benchmark with a frozen test split.

Tune detector settings on the two selected official-train scenes only. Then compare
the frozen choice and the original settings on four untouched official-test scenes.
All vehicle categories are evaluated together: UA-DETRAC van/others do not have a
one-to-one COCO class mapping. Ignored annotation regions apply to both sides.
"""
from __future__ import annotations
import hashlib, json, math, os, time
from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from scipy.optimize import linear_sum_assignment
import core, vision

DATA = core.DATA_DIR/'research'/'vehicle-counts'
OUTPUT = core.ROOT/'evaluation'
LINES = {'MVI_41063':[[.55,.15],[.55,1]], 'MVI_39761':[[.1,.6],[.95,.6]],
         'MVI_39051':[[.5,.35],[.5,1]], 'MVI_39501':[[.1,.6],[.9,.6]],
         'MVI_40863':[[.55,.1],[.55,.95]], 'MVI_40742':[[.1,.65],[.95,.65]]}
BASELINE = {'weights':'yolov8n.pt','imgsz':640,'conf':.35,'agnostic_nms':False}


def annotations(folder):
    root = ET.parse(folder/'annotation.xml').getroot()
    ignored = [[float(box.attrib[k]) for k in ('left','top','width','height')] for box in root.findall('ignored_region/box')]
    frames = {}
    for frame in root.findall('frame'):
        rows = []
        for target in frame.findall('target_list/target'):
            box = [float(target.find('box').attrib[k]) for k in ('left','top','width','height')]
            rows.append((int(target.attrib['id']),box))
        frames[int(frame.attrib['num'])] = rows
    return frames, ignored


def countable(box, ignored, width, height):
    x,y,w,h=box;cx,cy=x+w/2,y+h/2
    return 0<=cx<=width and 0<=cy<=height and not any(a<=cx<=a+c and b<=cy<=b+d for a,b,c,d in ignored)


def iou_matrix(truth, guessed):
    if not truth or not guessed:return np.empty((len(truth),len(guessed)))
    a=np.array(truth,float);b=np.array(guessed,float)
    a2=a[:,:2]+a[:,2:];b2=b[:,:2]+b[:,2:]
    intersection=np.maximum(0,np.minimum(a2[:,None,:],b2[None,:,:])-np.maximum(a[:,:2,None].transpose(0,2,1),b[None,:,:2])).prod(axis=2)
    union=(a[:,2]*a[:,3])[:,None]+(b[:,2]*b[:,3])[None,:]-intersection
    return intersection/np.maximum(union,1e-9)


def detection_counts(truth,guessed):
    if not truth or not guessed:return 0,len(guessed),len(truth)
    overlaps=iou_matrix(truth,guessed)
    a,b=linear_sum_assignment((overlaps<.5)*100+1-overlaps)
    tp=int(sum(overlaps[i,j]>=.5 for i,j in zip(a,b)))
    return tp,len(guessed)-tp,len(truth)-tp


def summarize_frames(rows):
    tp=sum(r['tp'] for r in rows);fp=sum(r['fp'] for r in rows);fn=sum(r['fn'] for r in rows)
    precision=tp/(tp+fp) if tp+fp else 0;recall=tp/(tp+fn) if tp+fn else 0
    truth=sum(r['reference'] for r in rows)
    return {'frames':len(rows),'reference_vehicle_instances':truth,'predicted_vehicle_instances':sum(r['predicted'] for r in rows),
        'count_mae':round(sum(abs(r['predicted']-r['reference']) for r in rows)/max(1,len(rows)),4),
        'count_wape':round(sum(abs(r['predicted']-r['reference']) for r in rows)/truth,4) if truth else None,
        'precision_iou50':round(precision,4),'recall_iou50':round(recall,4),
        'f1_iou50':round(2*precision*recall/(precision+recall),4) if precision+recall else 0,'tp':tp,'fp':fp,'fn':fn}


def frames(folder):
    import cv2
    capture=cv2.VideoCapture(str(folder/'sample.avi'));fps=capture.get(cv2.CAP_PROP_FPS)
    every=max(1,round(fps/vision.SAMPLE_FPS));index=0
    try:
        while True:
            ok,image=capture.read()
            if not ok:break
            index+=1
            if (index-1)%every==0:yield index,image,fps
    finally:capture.release()


def xywh(boxes):
    return [[float(a),float(b),float(c-a),float(d-b)] for a,b,c,d in boxes]


def model(settings):
    from ultralytics import YOLO
    return YOLO(str(core.MODELS_DIR/settings['weights']))


def evaluate_scene(sample,settings,tracking=False):
    folder=DATA/'clips'/sample['sequence']
    for name, key in (('sample.avi', 'video_sha256'), ('annotation.xml', 'annotation_sha256')):
        if hashlib.sha256((folder/name).read_bytes()).hexdigest() != sample[key]:
            raise ValueError(f"Validation source hash mismatch: {sample['sequence']} {name}")
    truth,ignored=annotations(folder)
    detector=model(settings);vision._reset_trackers(detector)
    rows=[];tracks=defaultdict(list);reference_tracks=defaultdict(list);started=time.monotonic()
    height=width=0
    for frame_id,image,fps in frames(folder):
        height,width=image.shape[:2]
        refs=[(ident,box) for ident,box in truth.get(frame_id,[]) if countable(box,ignored,width,height)]
        arguments=dict(classes=list(vision.VEHICLE_CLASSES),conf=settings['conf'],imgsz=settings['imgsz'],
                       agnostic_nms=settings['agnostic_nms'],verbose=False,device='cpu')
        result=(detector.track(image,persist=True,tracker='bytetrack.yaml',**arguments) if tracking else detector.predict(image,**arguments))[0]
        boxes=result.boxes
        detected=[]
        if boxes is not None:
            ids=boxes.id.int().tolist() if boxes.id is not None else [None]*len(boxes)
            for box,ident in zip(xywh(boxes.xyxy.tolist()),ids):
                if not countable(box,ignored,width,height):continue
                detected.append(box)
                if ident is not None:
                    x,y,w,h=box;tracks[ident].append((frame_id/fps,(x+w/2)/width,(y+h/2)/height))
        tp,fp,fn=detection_counts([b for _,b in refs],detected)
        rows.append({'frame':frame_id,'reference':len(refs),'predicted':len(detected),'tp':tp,'fp':fp,'fn':fn})
    # Reference crossings use all original annotation frames, not model outputs.
    for frame_id,objects in truth.items():
        if frame_id>sample['frames']:continue
        for ident,box in objects:
            if countable(box,ignored,width,height):
                x,y,w,h=box;reference_tracks[ident].append((frame_id/sample['fps'],(x+w/2)/width,(y+h/2)/height))
    result={'sequence':sample['sequence'],'split':sample['split'],'weather':sample['weather'],
            **summarize_frames(rows),'processing_seconds':round(time.monotonic()-started,2),
            'counting_line':LINES[sample['sequence']],'annotation_sha256':sample['annotation_sha256']}
    if tracking:
        reference=vision.count_line_crossings(reference_tracks,LINES[sample['sequence']])
        predicted=vision.count_line_crossings(tracks,LINES[sample['sequence']])
        result.update(reference_crossings=reference,predicted_crossings=predicted,
            crossing_absolute_error=abs(reference['total']-predicted['total']),
            reference_distinct_vehicles=len(reference_tracks),predicted_distinct_tracks=len(tracks),
            distinct_count_absolute_error=abs(len(reference_tracks)-len(tracks)))
    return result,rows


def main():
    import torch
    torch.set_num_threads(2);vision._disable_usage_statistics()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    samples=json.loads((DATA/'selection.json').read_text());validation=[s for s in samples if s['split']=='validation']
    tests=[s for s in samples if s['split']=='test'];trials=[]
    # The search space and acceptance checks are fixed before evaluating test scenes.
    candidates=[BASELINE,{'weights':'yolov8s.pt','imgsz':960,'conf':.25,'agnostic_nms':True},
                {'weights':'yolov8s.pt','imgsz':960,'conf':.35,'agnostic_nms':True}]
    for setting in candidates:
        all_rows=[];scenes=[]
        for sample in validation:
            metrics,rows=evaluate_scene(sample,setting);all_rows+=rows;scenes.append(metrics)
        trials.append({'settings':setting,'aggregate':summarize_frames(all_rows),'scenes':scenes})
        print('Validation',setting,trials[-1]['aggregate'],flush=True)
    choice=max(trials,key=lambda t:(t['aggregate']['f1_iou50'],-t['aggregate']['count_mae'],-t['settings']['imgsz']))
    selected=choice['settings']
    (OUTPUT/'vehicle-count-selection.json').write_text(json.dumps({'selection_period':'validation only','selected':selected,
        'criterion':'Highest class-agnostic IoU50 detection F1, then lower frame-count MAE; no test tuning.',
        'trials':trials},indent=2))
    results={}
    for name,setting in [('baseline',BASELINE),('selected',selected)]:
        scenes=[];all_rows=[]
        for sample in tests:
            metrics,rows=evaluate_scene(sample,setting,tracking=True);scenes.append(metrics);all_rows+=rows
            print('Held out',name,metrics,flush=True)
        aggregate=summarize_frames(all_rows)
        aggregate['crossing_absolute_error']=sum(s['crossing_absolute_error'] for s in scenes)
        aggregate['reference_crossings']=sum(s['reference_crossings']['total'] for s in scenes)
        aggregate['distinct_count_absolute_error']=sum(s['distinct_count_absolute_error'] for s in scenes)
        aggregate['reference_distinct_vehicles']=sum(s['reference_distinct_vehicles'] for s in scenes)
        results[name]={'settings':setting,'aggregate':aggregate,'scenes':scenes}
    baseline=results['baseline']['aggregate'];candidate=results['selected']['aggregate']
    checks={'frame_count_mae_not_worse':candidate['count_mae']<=baseline['count_mae'],
            'detection_f1_not_worse':candidate['f1_iou50']>=baseline['f1_iou50'],
            'line_crossing_error_not_worse':candidate['crossing_absolute_error']<=baseline['crossing_absolute_error']}
    from importlib.metadata import version
    report={'source':'https://sites.google.com/view/daweidu/projects/ua-detrac','evaluated_at':core.iso_now(),
        'reference':'Independent author-provided per-frame boxes and vehicle IDs. Source ignored regions applied symmetrically.',
        'scope':'Six public Chinese traffic scenes; 15 seconds each. Not a Bengaluru camera-site calibration or all-weather guarantee.',
        'sampling':'Actual sampled frames from round(source_fps / VIDEO_SAMPLE_FPS); reference crossing tracks use every original frame.',
        'classes':'All vehicles combined; per-class accuracy not claimed because source/COCO taxonomies differ.',
        'validation_selection':choice,'results':results,'acceptance':{'passed':all(checks.values()),'scope':'Frame-count and line-crossing estimates only; not distinct clip totals.','checks':checks},
        'distinct_count_validation':{'passed':candidate['distinct_count_absolute_error']<=baseline['distinct_count_absolute_error'],
            'notice':'Tracker IDs can fragment and count a vehicle again. Do not present track totals as verified distinct vehicles.'},
        'software':{name:version(name) for name in ('ultralytics','torch','opencv-python','numpy')},
        'weights_sha256':{name:hashlib.sha256((core.MODELS_DIR/name).read_bytes()).hexdigest() for name in {c['weights'] for c in candidates}}}
    (OUTPUT/'vehicle-count-benchmark.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'selected':selected,'acceptance':report['acceptance'],'baseline':baseline,'candidate':candidate},indent=2),flush=True)


if __name__=='__main__':main()
