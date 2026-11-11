"""Publish dated, reproducible public-benchmark results without treating them as live data."""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

import core
from security import authority_token

router = APIRouter(prefix='/api/validation', tags=['Public validation'])


def read_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def _sample_files():
    """Expose only the acquired, manifest-listed clips, never arbitrary local files."""
    result = {}
    for name in ('vehicle-counts', 'vehicle-counts-v2'):
        folder = core.DATA_DIR / 'research' / name
        for item in read_json(folder / 'selection.json', []):
            sequence = item.get('sequence', '')
            if not re.fullmatch(r'MVI_\d{5}', sequence):
                continue
            path = folder / 'clips' / sequence / 'sample.avi'
            if path.is_file() and sequence not in result:
                result[sequence] = (item, path)
    return result


def samples() -> list[dict]:
    return [{key: item[key] for key in ('sequence', 'split', 'weather', 'frames', 'fps', 'source')}
            for item, _ in _sample_files().values()]


@router.get('/summary')
def summary(actor: str = Depends(authority_token)):
    report = read_json(core.DATA_DIR / 'research' / 'validation-summary.json', {'available': False})
    return {**report, 'samples': samples()}


@router.get('/samples/{sequence}')
def sample(sequence: str, actor: str = Depends(authority_token)):
    files = _sample_files()
    if sequence not in files:
        raise HTTPException(404, 'Public validation clip not found.')
    return FileResponse(files[sequence][1],
                        media_type='video/x-msvideo', filename=f'UA-DETRAC-{sequence}-15s.avi')


def build() -> dict:
    """Run after the three real benchmarks; keep small reports separate from raw downloads."""
    import model_registry
    import store

    store.initialize()
    output = core.ROOT / 'evaluation'
    output.mkdir(exist_ok=True)
    source = read_json(core.DATA_DIR / 'research' / 'traffic-pulse' / 'source-manifest.json')
    restored = read_json(core.DATA_DIR / 'research' / 'original-archive' / 'source-manifest.json')
    daily = []
    for kind in ('daily_hybrid_lstm_rf', 'daily_random_forest'):
        row = next((r for r in model_registry.versions() if r['kind'] == kind
                    and r['data'].get('sources', {}).get('public_daily_dataset')), None)
        if row:
            daily.append({key: row[key] for key in ('version', 'kind', 'status', 'created_at', 'metrics', 'acceptance', 'data')})
    forecast = None
    if source and daily:
        (output / 'daily-forecast-benchmark.json').write_text(json.dumps({'source': source, 'candidates': daily}, indent=2))
        forecast = {'source': source, 'candidates': [
            {'version': r['version'], 'kind': r['kind'], 'status': r['status'], 'test': r['metrics']['test'],
             'acceptance_passed': r['acceptance']['passed']} for r in daily],
            'baselines': daily[0]['metrics']['baselines_test'], 'split': daily[0]['metrics']['split'],
            'notice': 'Daily publisher data only. ' + ('The separate original block archive is now restored. ' if restored else 'The original six-block archive was not recovered. ') +
                      'Daily Low/Medium/High are project-defined bins of the source index, not verified sensor labels.'}
    vehicle = read_json(output / 'vehicle-count-benchmark.json')
    routing = read_json(output / 'city-routing-benchmark.json')
    report = {'available': bool(forecast or vehicle or routing), 'generated_at': core.iso_now(), 'forecast': forecast,
              'vehicles': {key: vehicle[key] for key in ('source', 'evaluated_at', 'scope', 'classes', 'results',
                           'acceptance', 'distinct_count_validation')} if vehicle else None, 'routing': routing}
    report['original_archive'] = restored
    upgrade = read_json(output / 'block-model-selection-benchmark.json')
    if upgrade:
        row = model_registry.get(upgrade['version'])
        report['model_upgrade'] = {**upgrade, 'status': row['status'] if row else 'unregistered'}
    tracking = read_json(output / 'tracking-upgrade-benchmark.json')
    if tracking:
        report['tracking_upgrade'] = {key: tracking[key] for key in ('evaluated_at', 'source', 'scope', 'metric_scope', 'results', 'acceptance', 'notice')}
        report['tracking_upgrade']['clip_only_scope'] = 'Confirmed uploaded-clip tracks only. The full flow check failed; stream line counting retains the previous policy.'
    report['public_camera'] = read_json(core.DATA_DIR / 'research' / 'public-camera-status.json')
    target = core.DATA_DIR / 'research' / 'validation-summary.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2))
    (output / 'public-validation-summary.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    result = build()
    print('Public validation report ready:', ', '.join(k for k in ('forecast', 'vehicles', 'routing') if result[k]))
