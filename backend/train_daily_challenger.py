"""Validation-selected daily challengers; existing historical test period is explicitly a re-evaluation."""
from datetime import date, datetime, timezone
import copy
import hashlib
import itertools
import json
import time

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import core
import daily_challenger as daily
import hybrid_model as hm
import patterns
import store
from train_hybrid import acceptance, scores, environment_versions

TRAIN_END = date(2023, 10, 26)
TEST_START = date(2024, 3, 4)


def train():
    started = time.monotonic()
    store.initialize()
    frame = hm.load_daily_history()
    rows = daily.samples(frame)
    parts = {name: [r for r in rows if predicate(r['date'])] for name, predicate in
             [('train', lambda d: d < TRAIN_END), ('validation', lambda d: TRAIN_END <= d < TEST_START),
              ('test', lambda d: d >= TEST_START)]}
    x = {name: daily.matrix(part) for name, part in parts.items()}
    columns = list(x['train'])
    x = {name: values.reindex(columns=columns, fill_value=0) for name, values in x.items()}
    y = {name: [r['label'] for r in part] for name, part in parts.items()}
    if len(y['train']) < 300 or len(y['validation']) < 50 or len(y['test']) < 50:
        raise ValueError('Not enough independent chronological target dates for this comparison.')
    print('Daily targets:', {name: len(part) for name, part in parts.items()}, flush=True)
    majority = max(daily.CLASSES, key=y['train'].count)
    majority_val_accuracy = scores(y['validation'], [majority] * len(y['validation']), daily.CLASSES)['accuracy']
    constructors = [
        ('random_forest_leaf2', RandomForestClassifier(n_estimators=400, min_samples_leaf=2, max_features=.8, class_weight='balanced_subsample', random_state=42, n_jobs=2)),
        ('random_forest_leaf8', RandomForestClassifier(n_estimators=400, min_samples_leaf=8, max_features=.8, class_weight='balanced_subsample', random_state=42, n_jobs=2)),
        ('extra_trees_leaf2', ExtraTreesClassifier(n_estimators=400, min_samples_leaf=2, class_weight='balanced', random_state=42, n_jobs=2)),
        ('extra_trees_leaf8', ExtraTreesClassifier(n_estimators=400, min_samples_leaf=8, class_weight='balanced', random_state=42, n_jobs=2)),
        ('hist_gradient_balanced', HistGradientBoostingClassifier(max_iter=400, max_leaf_nodes=15, l2_regularization=10, learning_rate=.05, class_weight='balanced', early_stopping=False, random_state=42)),
        ('hist_gradient_unweighted', HistGradientBoostingClassifier(max_iter=400, max_leaf_nodes=15, l2_regularization=10, learning_rate=.05, early_stopping=False, random_state=42)),
        ('logistic_C01', LogisticRegression(C=.1, class_weight='balanced', max_iter=2000, random_state=42)),
        ('logistic_C1', LogisticRegression(C=1, class_weight='balanced', max_iter=2000, random_state=42)),
    ]
    trials = []
    best = None
    # The grid and selection rule are declared before scoring any new test predictions.
    for family, estimator in constructors:
        pipeline = make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True), StandardScaler(), estimator)
        pipeline.fit(x['train'], y['train'])
        probability = hm.aligned_probabilities(pipeline, x['validation'], daily.CLASSES)
        chosen = None
        for low, medium in itertools.product((.5, .75, 1, 1.5, 2, 3, 4), repeat=2):
            weights = {'Low': low, 'Medium': medium, 'High': 1}
            adjusted = hm.apply_weights(probability, daily.CLASSES, weights)
            predicted = [daily.CLASSES[i] for i in np.argmax(adjusted, axis=1)]
            metrics = scores(y['validation'], predicted, daily.CLASSES)
            recalled = all(v['support'] < 5 or v['recall'] > 0 for v in metrics['per_class'].values())
            feasible = metrics['accuracy'] >= majority_val_accuracy and recalled
            key = (feasible, metrics['macro_f1'], metrics['accuracy'], -abs(np.log(low)) - abs(np.log(medium)))
            if chosen is None or key > chosen[0]:
                chosen = (key, weights, metrics)
        trials.append({'family': family, 'validation': chosen[2], 'weights': chosen[1], 'feasible': chosen[0][0]})
        print('Validation:', family, chosen[2]['accuracy'], chosen[2]['macro_f1'], 'feasible:', chosen[0][0], flush=True)
        if best is None or chosen[0] > best[0]:
            best = (chosen[0], pipeline, chosen[1], family)
    version = 'daily-history-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    model = daily.DailyChallenger(best[1], columns, best[2], version, best[3])
    probability = model.probabilities(x['test'])
    predicted = [daily.CLASSES[i] for i in np.argmax(probability, axis=1)]
    counts = patterns.build_counts(frame[frame.date < TRAIN_END])
    baselines_pred = {'majority': [majority] * len(parts['test']), 'persistence': [r['last_label'] for r in parts['test']],
        'historical_profile': [max(patterns.pattern_distribution(counts[r['segment_id']], 'Daily', patterns.day_type(r['date']), daily.CLASSES)[0], key=patterns.pattern_distribution(counts[r['segment_id']], 'Daily', patterns.day_type(r['date']), daily.CLASSES)[0].get) for r in parts['test']]}
    metrics = scores(y['test'], predicted, daily.CLASSES)
    baseline_scores = {name: scores(y['test'], values, daily.CLASSES) for name, values in baselines_pred.items()}
    decision = acceptance(metrics, baseline_scores, daily.CLASSES, None)
    common = [i for i, row in enumerate(parts['test']) if row['consecutive_six']]
    common_metrics = scores([y['test'][i] for i in common], [predicted[i] for i in common], daily.CLASSES)
    report = {'version': version, 'kind': model.kind, 'family': model.family,
        'scope': 'Historical re-evaluation on previously inspected public daily data; not a new untouched test or the missing six-block archive.',
        'task': 'Daily severity outlook using only earlier available history, last observation no older than seven days.',
        'selection': 'Validation only; prefer majority-accuracy and supported-class-recall feasibility, then macro-F1.',
        'input_rule': 'Target-day measurements and all later observations are excluded; no gaps are filled with invented records.',
        'split': {'train_before': str(TRAIN_END), 'test_from': str(TEST_START)}, 'sizes': {name: len(part) for name, part in parts.items()},
        'classes': daily.CLASSES, 'test': metrics, 'baselines_test': baseline_scores, 'acceptance': decision,
        'common_six_day_test': common_metrics, 'validation_trials': trials, 'decision_weights': best[2],
        'columns': columns, 'data_fingerprint': hashlib.sha256(frame.to_json(date_format='iso').encode()).hexdigest(),
        'software': environment_versions(), 'training_seconds': round(time.monotonic() - started, 2)}
    directory = core.MODELS_DIR / version
    model.save(directory)
    (directory / 'benchmark.json').write_text(json.dumps(report, indent=2, default=str))
    output = core.ROOT / 'evaluation' / 'daily-challenger-benchmark.json'
    output.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: report[k] for k in ('version', 'family', 'test', 'baselines_test', 'acceptance', 'common_six_day_test')}, indent=2), flush=True)
    return report


if __name__ == '__main__':
    train()
