"""Public daily records must retain their units and never become invented time blocks."""
from datetime import date, timedelta
import csv, io, zipfile
import numpy as np
import pytest


def source_archive(path, duplicate=False):
    rows = []
    for i in range(24):
        rows.append({'Date':str(date(2024,1,1)+timedelta(days=i)),'Area Name':'Daily test area',
            'Road/Intersection Name':'Daily test road','Traffic Volume':12000+i*500,'Average Speed':30,
            'Travel Time Index':1.4,'Congestion Level':[20,50,90][i%3],'Incident Reports':0,
            'Weather Conditions':'Clear','Roadwork and Construction Activity':'No'})
    if duplicate: rows.append(rows[0])
    text = io.StringIO(); writer=csv.DictWriter(text,fieldnames=list(rows[0])); writer.writeheader();writer.writerows(rows)
    with zipfile.ZipFile(path,'w') as bundle:bundle.writestr('daily.csv',text.getvalue())
    return path


def test_daily_source_audit_has_explicit_granularity_and_no_time_labels(client,tmp_path):
    import public_data
    frame,manifest=public_data.audit(source_archive(tmp_path/'daily.zip'))
    assert len(frame)==24 and manifest['time_granularity']=='day' and not manifest['time_of_day_available']
    assert set(frame['class'])=={'Low','Medium','High'} and 'TimeBlock' not in frame
    with pytest.raises(ValueError,match='duplicate'):
        public_data.audit(source_archive(tmp_path/'duplicates.zip',True))


def test_daily_import_is_idempotent_and_excluded_from_block_training(client,tmp_path):
    import public_data,hybrid_model as hm,store
    archive=source_archive(tmp_path/'daily.zip')
    public_data.import_daily(archive);public_data.import_daily(archive)
    rows=store.rows("SELECT * FROM traffic_observations WHERE source_type='public_daily_dataset'")
    assert len(rows)==24 and {r['time_block'] for r in rows}=={'Daily'}
    assert all(r['interval_seconds']==86400 and r['time_granularity']=='day' for r in rows)
    daily=hm.load_daily_history();block=hm.load_history()
    assert set(daily.segment_id).isdisjoint(set(block.segment_id))
    windows=hm.build_windows(daily)
    assert len([w for w in windows if 1 in w.targets])==18
    assert windows[0].targets[1]['date']-windows[0].rows[-1]['date']==timedelta(days=1)


def test_daily_calendar_and_saved_model_advance_days_not_six_blocks(client,tmp_path):
    import hybrid_model as hm
    from train_hybrid import make_rf,target_day
    frame=hm.load_daily_history(); windows=[w for w in hm.build_windows(frame) if 1 in w.targets]
    classes=['Low','Medium','High'];columns=hm.rf_columns(classes,with_lstm=False)
    import pandas as pd
    features=pd.DataFrame([hm.rf_feature_row(w.rows,None,w.rows[-1]['slot']+1,'local',classes) for w in windows],columns=columns)
    rf=make_rf().fit(features,[w.targets[1]['label'] for w in windows])
    model=hm.HybridModel(None,rf,hm.StepScaler().fit(frame),classes,columns,'daily-unit',granularity='day')
    directory=tmp_path/'model';model.save(directory);restored=hm.HybridModel.load(directory)
    window=windows[0];path=restored.predict(window.rows,window.rows[-1]['slot'],'local',3)
    assert restored.kind=='daily_random_forest'
    assert [hm.date_block_for_slot(step['slot'],'day')[0] for step in path]==[
        window.rows[-1]['date']+timedelta(days=k) for k in (1,2,3)]
    assert target_day(window)==window.targets[1]['date']
    assert not any(step['predicted_values'] for step in path)


def test_daily_forecast_does_not_claim_a_measured_time_of_day_profile(client,citizen):
    import hybrid_model as hm,public_data
    frame=hm.load_daily_history();segment=frame.iloc[0]['segment_id']
    response=client.get('/api/forecast',params={'segment_id':segment},headers=citizen)
    assert response.status_code==200,response.text
    body=response.json()
    assert body['method']=='daily_historical_pattern' and body['data_granularity']=='day'
    assert body['target_time_block']=='Daily' and 'predicted_values' not in body
    assert public_data.outlook(segment,date(2023,1,1)) is None
