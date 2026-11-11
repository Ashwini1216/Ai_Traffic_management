"""Chronological features, deployable simpler models and verified forecast outcomes."""
from datetime import date, datetime, timedelta, timezone
import json
import pandas as pd


def test_training_profiles_cannot_see_their_target_or_future_labels():
    import hybrid_model as hm
    from train_hybrid import chronological_entries
    day = date(2024, 1, 1)
    records = [{'segment_id': 'road', 'slot': hm.slot_of(day, block), 'date': day,
                'time_block': block, 'label': 'Low' if i < 2 else 'High'}
               for i, block in enumerate(hm.TIME_BLOCKS)]
    window = hm.Window('road', records[:2], {1: records[2]})
    first = chronological_entries([window], pd.DataFrame(records))[0]
    for row in records[2:]: row['label'] = 'Medium'
    second = chronological_entries([window], pd.DataFrame(records))[0]
    assert first == second and dict(first['all']) == {'Low': 2}


def test_rf_candidate_can_load_and_forecast_without_invented_numeric_values(client, authority, trained_version):
    import core, hybrid_model as hm, model_registry
    versions = client.get('/api/models', headers=authority).json()['models']
    candidate = next(v for v in versions if v['kind'] == 'random_forest')
    detail = model_registry.get(candidate['version'])
    directory = core.MODELS_DIR / detail['artifact_dir']
    assert not (directory / 'lstm.keras').exists()
    benchmark = json.loads((directory / 'benchmark.json').read_text())
    assert len(benchmark['data']['sha256']) == 64
    assert benchmark['metrics']['experiment']['selection_period'] == 'validation only'
    assert len(benchmark['metrics']['experiment']['search']['random_forest']['trials']) == 3
    model = hm.HybridModel.load(directory, candidate['version'])
    window = next(w for w in hm.build_windows(hm.load_history()) if 1 in w.targets)
    result = model.predict(window.rows, window.rows[-1]['slot'], 'arterial', 3)
    assert len(result) == 3 and all(r['predicted_values'] == {} for r in result)
    assert all(abs(sum(r['probabilities'].values()) - 1) < 1e-8 for r in result)


def test_only_preblock_forecasts_use_verified_complete_block_outcomes(client, authority):
    import core, segments, store
    day = core.local_today() - timedelta(days=2)
    start, end = core.block_bounds(day, 'Morning')
    area, road = 'Model validation', 'Verified outcome road'
    segment = segments.ensure_segment(area, road)
    for name, issued in [('honest', start - timedelta(minutes=1)), ('late', start + timedelta(minutes=1))]:
        with store.connect() as db:
            db.execute('''INSERT INTO model_predictions(prediction_id,issue_time,target_date,target_area,target_road,
              target_time_block,source_type,model_version,predicted_class,probabilities_json,input_json,segment_id,
              target_start,method,predicted_values_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              ('quality-'+name, issued.isoformat(), day.isoformat(), area, road, 'Morning', 'model_prediction',
               'quality-test', 'High', '{"High":1}', '{}', segment, start.isoformat(), 'quality_test', '{"average_speed":20}'))
    stored = client.post('/api/observations', json={'area':area, 'road':road, 'observed_date':day.isoformat(),
        'time_block':'Morning', 'congestion_level':'High', 'average_speed':25, 'source_name':'quality_reference'}, headers=authority)
    assert stored.status_code == 200, stored.text
    obs_id = stored.json()['id']
    assert store.one("SELECT observed_outcome FROM model_predictions WHERE prediction_id='quality-honest'")['observed_outcome'] is None
    client.patch(f'/api/observations/{obs_id}', json={'quality_status':'verified'}, headers=authority)
    good = store.one("SELECT * FROM model_predictions WHERE prediction_id='quality-honest'")
    assert good['outcome_observation_id'] == obs_id and json.loads(good['errors_json'])['average_speed'] == 5
    assert store.one("SELECT observed_outcome FROM model_predictions WHERE prediction_id='quality-late'")['observed_outcome'] is None
    evaluation = client.get('/api/evaluation').json()['by_model']['quality-test']
    assert evaluation['evaluated'] == 1 and evaluation['accuracy'] == 1 and evaluation['per_class']['High']['support'] == 1
    client.patch(f'/api/observations/{obs_id}', json={'quality_status':'rejected'}, headers=authority)
    assert 'quality-test' not in client.get('/api/evaluation').json()['by_model']


def test_readiness_explains_missing_history_and_blocks_unusable_training(client, authority, monkeypatch):
    import hybrid_model as hm
    monkeypatch.setattr(hm, 'load_history', lambda: pd.DataFrame())
    monkeypatch.setattr(hm, 'load_daily_history', lambda: pd.DataFrame())
    readiness = client.get('/api/model-readiness', headers=authority).json()
    assert not readiness['ready'] and readiness['labelled_sequences'] == 0 and readiness['reasons']
    response = client.post('/api/models/train', headers=authority)
    assert response.status_code == 422 and 'readiness' in response.json()['detail']


def test_benchmark_export_supports_the_cpu_tensorflow_distribution(monkeypatch):
    import train_hybrid as trainer
    def version(name):
        if name == 'tensorflow':
            raise trainer.importlib.metadata.PackageNotFoundError(name)
        return 'cpu-test-version' if name == 'tensorflow-cpu' else 'other-version'
    monkeypatch.setattr(trainer.importlib.metadata, 'version', version)
    assert trainer.environment_versions()['tensorflow'] == 'cpu-test-version'
