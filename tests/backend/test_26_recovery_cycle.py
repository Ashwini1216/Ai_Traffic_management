"""Archive provenance, causal features and cycle artifact compatibility."""
from datetime import date, timedelta
import csv
import io
import json
import zipfile

import numpy as np
import pandas as pd


def training_days():
    import core
    import hybrid_model as hm
    rows = []
    factors = [.24, .48, 1.56, 1.14, 1.68, .9]
    for day in range(12):
        current = date(2024, 1, 1) + timedelta(days=day)
        for i, block in enumerate(core.TIME_BLOCKS):
            rows.append({'date': current, 'slot': hm.slot_of(current, block), 'time_block': block,
                'segment_id': 'road', 'road_type': 'local', 'label': 'Medium', 'traffic_volume': (1000 + day) * factors[i],
                'average_speed': 30 / factors[i], 'travel_time_index': 1.2 * factors[i], 'incident_reports': 0,
                'wet': 0, 'roadwork_flag': 0})
    return pd.DataFrame(rows)


def test_cycle_projection_uses_same_date_observations_and_no_next_day_anchor():
    import cycle_features as cycle
    import hybrid_model as hm
    frame = training_days()
    scales = cycle.fit_scales(frame[frame.date < date(2024, 1, 11)])
    rows = frame[frame.date == date(2024, 1, 11)].to_dict('records')
    target = rows[2]['slot']
    projected = cycle.projected_values(rows[:2], target, scales)
    assert np.isclose(projected['traffic_volume'], rows[2]['traffic_volume'])
    assert cycle.projected_values(rows, hm.slot_of(date(2024, 1, 12), 'Midnight'), scales) is None
    assert cycle.projected_values(rows, target, scales) == projected  # target and later rows are ignored


def test_cycle_bundle_roundtrip_and_recursive_predictions_do_not_invent_a_new_day(tmp_path):
    import cycle_features as cycle
    import hybrid_model as hm
    from sklearn.dummy import DummyClassifier
    frame = training_days()
    scales = cycle.fit_scales(frame)
    classes = ['Low', 'Medium', 'High']
    columns = hm.rf_columns(classes, False) + cycle.columns()
    estimator = DummyClassifier(strategy='prior').fit(pd.DataFrame(np.zeros((3, len(columns))), columns=columns), classes)
    model = hm.HybridModel(None, estimator, hm.StepScaler().fit(frame), classes, columns, 'cycle-unit', cycle_scales=scales)
    model.save(tmp_path / 'cycle')
    restored = hm.HybridModel.load(tmp_path / 'cycle')
    assert restored.kind == 'cycle_random_forest' and restored.cycle_scales == model.cycle_scales
    rows = frame.tail(6).to_dict('records')
    path = restored.predict(rows, rows[-1]['slot'], 'local', 3)
    assert all(step['predicted_values'] == {} for step in path)
    midday = frame.iloc[-8:-2].to_dict('records')  # Noon; the third target crosses midnight.
    path = restored.predict(midday, midday[-1]['slot'], 'local', 3)
    assert path[0]['predicted_values'] and path[-1]['predicted_values'] == {}


def test_daily_features_ignore_target_and_future_rows_and_keep_gap_information():
    from daily_challenger import feature_row, samples
    frame = training_days().drop_duplicates(['date']).copy()
    frame['time_block'] = 'Daily'
    frame['congestion_index'] = 50
    frame['slot'] = frame.date.map(date.toordinal)
    rows = frame.to_dict('records')
    target = rows[8]['date']
    assert feature_row(rows, target) == feature_row(rows[:8], target)
    before = feature_row(rows[:8], target)
    assert before['history_age_days'] == 1
    changed = [*rows[:6], rows[7]]
    assert feature_row(changed, target)['records_56_days'] == 7
    assert len(samples(frame)) == 9


def test_restored_source_audit_confirms_daily_shares_without_claiming_sensors(tmp_path):
    from recover_project_data import SHARES, audit
    common = {'Date': '2024-01-01', 'Area Name': 'Test area', 'Road/Intersection Name': 'Test road'}
    block_rows = [{**common, 'TimeBlock': block, 'Traffic Volume': round(10000 * share),
                   'Average Speed': 30, 'Travel Time Index': 1.2, 'TrafficSituation': 'High'} for block, share in SHARES.items()]
    def archive(path, rows):
        text = io.StringIO()
        writer = csv.DictWriter(text, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        with zipfile.ZipFile(path, 'w') as output:
            output.writestr('source.csv', text.getvalue())
    blocks, daily = tmp_path / 'blocks.zip', tmp_path / 'daily.zip'
    archive(blocks, block_rows)
    archive(daily, [{**common, 'Traffic Volume': 10000}])
    report = audit(blocks, daily)
    assert report['derived_daily_volume_shares_confirmed'] and report['valid_block_records'] == 6
    assert not report['matches_saved_project_facts'] and 'not six independently measured' in report['notice']
    json.dumps(report)
