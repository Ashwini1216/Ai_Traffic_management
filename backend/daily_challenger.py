"""Causal daily-history challenger. Missing dates stay missing; no target-day measurements are inputs."""
from __future__ import annotations

from collections import Counter
from datetime import date
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

CLASSES = ['Low', 'Medium', 'High']
FEATURE_VERSION = 1
MEASUREMENTS = ('traffic_volume', 'average_speed', 'travel_time_index', 'congestion_index', 'incident_reports', 'wet', 'roadwork_flag')


def feature_row(history: list[dict], target: date) -> dict:
    """Only strictly earlier observations, even if a caller accidentally passes later rows."""
    prior = sorted((r for r in history if r['date'] < target), key=lambda r: r['date'])
    if not prior:
        raise ValueError('Daily outlook needs earlier observations.')
    recent = [r for r in prior if (target - r['date']).days <= 56]
    latest = prior[-1]
    features = {'day_ordinal': (target.toordinal() - date(2022, 1, 1).toordinal()) / 365.25,
                'weekday': target.weekday(), 'weekend': float(target.weekday() >= 5),
                'month_sin': math.sin(2 * math.pi * target.month / 12),
                'month_cos': math.cos(2 * math.pi * target.month / 12),
                'year_day_sin': math.sin(2 * math.pi * target.timetuple().tm_yday / 365.25),
                'year_day_cos': math.cos(2 * math.pi * target.timetuple().tm_yday / 365.25),
                'history_age_days': (target - latest['date']).days, 'records_56_days': len(recent)}
    for i in range(7):
        features[f'weekday_{i}'] = float(target.weekday() == i)
    # These category identities are known before the target; unseen roads get all-zero identity features.
    features['road_identity'] = latest['segment_id']
    for column in MEASUREMENTS:
        values = []
        for row in prior:
            v = row.get(column)
            values.append(float(v) if v is not None and pd.notna(v) else np.nan)
        features[f'{column}_last'] = values[-1]
        for days in (7, 28, 56):
            chosen = [v for r, v in zip(prior, values) if (target - r['date']).days <= days and math.isfinite(v)]
            features[f'{column}_mean_{days}'] = float(np.mean(chosen)) if chosen else np.nan
            features[f'{column}_std_{days}'] = float(np.std(chosen)) if chosen else np.nan
        same = [v for r, v in zip(prior, values) if r['date'].weekday() == target.weekday()
                and (target - r['date']).days <= 84 and math.isfinite(v)]
        features[f'{column}_same_weekday_mean'] = float(np.mean(same)) if same else np.nan
    for days in (28, 84, 10000):
        labels = Counter(r.get('label') for r in prior if (target - r['date']).days <= days)
        for label in CLASSES:
            features[f'prior_{label}_{days}'] = (labels[label] + 1) / (sum(labels.values()) + len(CLASSES))
    return features


def samples(frame: pd.DataFrame) -> list[dict]:
    result = []
    for segment, group in frame.groupby('segment_id', sort=False):
        history = []
        for row in group.sort_values('date').to_dict('records'):
            if len(history) >= 3 and (row['date'] - history[-1]['date']).days <= 7 and row.get('label') in CLASSES:
                result.append({'segment_id': segment, 'date': row['date'], 'label': row['label'],
                               'features': feature_row(history, row['date']), 'last_label': history[-1]['label'],
                               'consecutive_six': len(history) >= 6 and
                               (row['date'] - history[-6]['date']).days == 6})
            history.append(row)
    return sorted(result, key=lambda r: (r['date'], r['segment_id']))


def matrix(rows: list[dict], columns=None):
    frame = pd.DataFrame([r['features'] for r in rows])
    identities = pd.get_dummies(frame.pop('road_identity'), prefix='road', dtype=float)
    frame = pd.concat([frame, identities], axis=1)
    return frame.reindex(columns=columns, fill_value=0) if columns is not None else frame


class DailyChallenger:
    kind = 'daily_history_challenger'

    def __init__(self, estimator, columns, weights, version='', family=''):
        self.estimator, self.columns, self.weights = estimator, list(columns), weights
        self.version, self.family, self.classes, self.granularity = version, family, CLASSES, 'day'

    def probabilities(self, features):
        import hybrid_model as hm
        return hm.apply_weights(hm.aligned_probabilities(self.estimator, features, self.classes), self.classes, self.weights)

    def outlook(self, history, target):
        values = matrix([{'features': feature_row(history, target)}], self.columns)
        return dict(zip(self.classes, map(float, self.probabilities(values)[0])))

    def save(self, directory: Path):
        import joblib
        directory.mkdir(parents=True, exist_ok=True)
        joblib.dump({'estimator': self.estimator, 'columns': self.columns, 'weights': self.weights,
                     'version': self.version, 'family': self.family, 'feature_version': FEATURE_VERSION}, directory / 'daily_bundle.joblib')
        (directory / 'manifest.json').write_text(json.dumps({'kind': self.kind, 'version': self.version,
            'feature_version': FEATURE_VERSION, 'family': self.family, 'granularity': 'day'}, indent=2))

    @classmethod
    def load(cls, directory: Path, version=''):
        import joblib
        bundle = joblib.load(directory / 'daily_bundle.joblib')
        if bundle['feature_version'] != FEATURE_VERSION:
            raise ValueError('Daily feature version changed; retrain the challenger.')
        return cls(bundle['estimator'], bundle['columns'], bundle['weights'], version or bundle['version'], bundle['family'])
