"""Public reports are authority-only, and sample downloads stay within the source manifest."""
import json


def test_reports_and_samples_require_authority(client, authority, citizen, tmp_path, monkeypatch):
    import core
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path)
    research = tmp_path / 'research'
    research.mkdir()
    (research / 'validation-summary.json').write_text(json.dumps({'available': True, 'forecast': {'dated': True}}))
    folder = research / 'vehicle-counts'
    clip = folder / 'clips' / 'MVI_39051'
    clip.mkdir(parents=True)
    (clip / 'sample.avi').write_bytes(b'public sample')
    (folder / 'selection.json').write_text(json.dumps([
        {'sequence': 'MVI_39051', 'split': 'test', 'weather': 'sunny', 'frames': 375, 'fps': 25, 'source': 'author'},
        {'sequence': '../../private'}, {'sequence': 'MVI_00000'}]))
    for path in ('/api/validation/summary', '/api/validation/samples/MVI_39051'):
        assert client.get(path).status_code in (401, 403)
        assert client.get(path, headers=citizen).status_code == 403
    report = client.get('/api/validation/summary', headers=authority).json()
    assert report['available'] and report['forecast']['dated']
    assert [row['sequence'] for row in report['samples']] == ['MVI_39051']
    response = client.get('/api/validation/samples/MVI_39051', headers=authority)
    assert response.content == b'public sample'
    assert 'attachment' in response.headers['content-disposition']
    assert client.get('/api/validation/samples/MVI_00000', headers=authority).status_code == 404


def test_absent_report_does_not_read_another_installation(client, authority, tmp_path, monkeypatch):
    import core
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path)
    assert client.get('/api/validation/summary', headers=authority).json() == {'available': False, 'samples': []}


def test_daily_only_summary_does_not_claim_the_missing_block_archive(appmod, monkeypatch):
    totals = {'record_count': 30, 'road_count': 2, 'area_count': 1, 'date_start': '2024-01-01', 'date_end': '2024-01-30'}
    monkeypatch.setattr(appmod.store, 'one', lambda sql: {'n': 30} if "source_type='public_daily_dataset'" in sql else totals)
    monkeypatch.setattr(appmod.store, 'rows', lambda sql: [{'label': 'High', 'count': 30}])
    result = appmod.summary()
    assert result['source_type'] == 'public_daily_dataset' and result['time_granularity'] == 'day'
    assert result['block_record_count'] == 0 and 'zip' not in result['dataset']
    assert 'equal thirds' in result['daily_label_notice'] and 'time-of-day' in result['notice']


def test_benchmark_rejects_modified_video_before_scoring(tmp_path, monkeypatch):
    import evaluate_vehicle_counts as evaluation
    import pytest
    monkeypatch.setattr(evaluation, 'DATA', tmp_path)
    folder = tmp_path / 'clips' / 'MVI_39051'
    folder.mkdir(parents=True)
    (folder / 'sample.avi').write_bytes(b'changed source')
    with pytest.raises(ValueError, match='source hash mismatch'):
        evaluation.evaluate_scene({'sequence': 'MVI_39051', 'video_sha256': 'wrong'}, evaluation.BASELINE)
