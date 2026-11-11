"""Compare causal cycle-aware RF against the original feature set, with unchanged acceptance checks."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import time
import numpy as np
import pandas as pd

import core
import cycle_features as cycle
import hybrid_model as hm
import model_registry
import patterns
import store
import segments
from train_hybrid import (acceptance, breakdowns, chronological_entries, environment_versions, scores,
                          select_forest, target_day, validate_transfer)


def train(actor='accuracy-improvement', activate=False):
    started = time.monotonic()
    store.initialize()
    segments.sync_segments()
    frame = hm.load_history()
    windows = [w for w in hm.build_windows(frame) if w.targets.get(1, {}).get('label')]
    days = sorted({target_day(w) for w in windows})
    if len(windows) < 300 or len(days) < 30:
        raise ValueError('Not enough chronological block sequences for a cycle comparison.')
    train_end, test_start = days[int(len(days) * .7)], days[int(len(days) * .85)]
    split = {name: [w for w in windows if predicate(target_day(w))] for name, predicate in
             [('train', lambda d: d < train_end), ('validation', lambda d: train_end <= d < test_start), ('test', lambda d: d >= test_start)]}
    training = frame[frame.date < train_end]
    scales = cycle.fit_scales(training)
    classes = hm.ordered_classes(training.label.dropna())
    base_columns = hm.rf_columns(classes, with_lstm=False)
    columns = base_columns + cycle.columns(scales)
    counts = patterns.build_counts(training)
    train_entries = chronological_entries(split['train'], training)
    x, truth, profile = {}, {}, {}
    for name, part in split.items():
        entries = train_entries if name == 'train' else [counts.get(w.segment_id) for w in part]
        x[name] = pd.DataFrame([{**hm.rf_feature_row(w.rows, None, w.rows[-1]['slot'] + 1, w.rows[-1]['road_type'], classes, entry),
                               **cycle.feature_row(w.rows, w.rows[-1]['slot'] + 1, scales)} for w, entry in zip(part, entries)], columns=columns)
        truth[name] = [w.targets[1]['label'] for w in part]
        profile[name] = np.array([[patterns.pattern_distribution(counts[w.segment_id], w.targets[1]['time_block'], patterns.day_type(w.targets[1]['date']), classes)[0][c] for c in classes] for w in part])
    print('Block sequences:', {k: len(v) for k, v in split.items()}, 'cutoffs:', train_end, test_start, flush=True)
    previous, previous_weights, previous_search = select_forest(x['train'][base_columns], truth['train'], x['validation'][base_columns], truth['validation'], classes)
    estimator, weights, search = select_forest(x['train'], truth['train'], x['validation'], truth['validation'], classes,
        configurations=[dict(min_samples_leaf=1, max_features=1.0), dict(min_samples_leaf=2, max_features=1.0),
                        dict(min_samples_leaf=4, max_features=.75), dict(min_samples_leaf=2, max_features='sqrt')])
    # Broader model families are compared on validation only. The historical test scores
    # remain confirmation/re-evaluation; they do not select a family or decision weights.
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    def validation_score(candidate, decision_weights):
        probabilities = hm.apply_weights(hm.aligned_probabilities(candidate, x['validation'], classes), classes, decision_weights)
        return scores(truth['validation'], [classes[i] for i in np.argmax(probabilities, axis=1)], classes)
    selected_score = validation_score(estimator, weights)
    family_trials = [{'family': 'random_forest', 'validation': selected_score, 'decision_weights': weights}]
    candidates = [
        ('hist_gradient_balanced_15', HistGradientBoostingClassifier(max_iter=300, learning_rate=.05, max_leaf_nodes=15, min_samples_leaf=10, l2_regularization=10, class_weight='balanced', early_stopping=False, random_state=42)),
        ('hist_gradient_balanced_31', HistGradientBoostingClassifier(max_iter=500, learning_rate=.05, max_leaf_nodes=31, min_samples_leaf=15, l2_regularization=10, class_weight='balanced', early_stopping=False, random_state=42)),
        ('hist_gradient_unweighted', HistGradientBoostingClassifier(max_iter=300, learning_rate=.08, max_leaf_nodes=15, min_samples_leaf=10, l2_regularization=10, early_stopping=False, random_state=42)),
        ('logistic_regularized', LogisticRegression(C=.1, class_weight='balanced', max_iter=2000, random_state=42)),
    ]
    for family, classifier in candidates:
        candidate = Pipeline([('impute', SimpleImputer(strategy='median', keep_empty_features=True)),
                              ('scale', StandardScaler()), ('model', classifier)]).fit(x['train'], truth['train'])
        candidate_weights = hm.tune_decision_weights(hm.aligned_probabilities(candidate, x['validation'], classes), truth['validation'], classes)
        result = validation_score(candidate, candidate_weights)
        family_trials.append({'family': family, 'validation': result, 'decision_weights': candidate_weights})
        print('Cycle validation:', family, result['accuracy'], result['macro_f1'], flush=True)
        if (result['macro_f1'], result['accuracy']) > (selected_score['macro_f1'], selected_score['accuracy']):
            estimator, weights, selected_score = candidate, candidate_weights, result
    search['family_trials'] = family_trials
    # Prefer the simpler classifier when its validation result is within one paired,
    # date-clustered standard error of the larger feature/model search. This uses no
    # test labels and retains the same held-out acceptance thresholds.
    base_prob = hm.apply_weights(hm.aligned_probabilities(previous, x['validation'][base_columns], classes), classes, previous_weights)
    complex_prob = hm.apply_weights(hm.aligned_probabilities(estimator, x['validation'], classes), classes, weights)
    base_guess = np.argmax(base_prob, axis=1)
    complex_guess = np.argmax(complex_prob, axis=1)
    dates = sorted({target_day(w) for w in split['validation']})
    date_index = {day: i for i, day in enumerate(dates)}
    matrices = np.zeros((len(dates), 2, len(classes), len(classes)), dtype=int)
    for w, true, simple, larger in zip(split['validation'], truth['validation'], base_guess, complex_guess):
        matrices[date_index[target_day(w)], 0, classes.index(true), simple] += 1
        matrices[date_index[target_day(w)], 1, classes.index(true), larger] += 1
    rng = np.random.default_rng(42)
    bootstrap = matrices[rng.integers(0, len(dates), size=(400, len(dates)))].sum(axis=1)
    diagonals = np.diagonal(bootstrap, axis1=-2, axis2=-1)
    denominators = bootstrap.sum(axis=-2) + bootstrap.sum(axis=-1)
    bootstrap_f1 = np.divide(2 * diagonals, denominators, out=np.zeros_like(diagonals, dtype=float), where=denominators > 0).mean(axis=-1)
    standard_error = float(np.std(bootstrap_f1[:, 1] - bootstrap_f1[:, 0], ddof=1))
    simple_validation = scores(truth['validation'], [classes[i] for i in base_guess], classes)
    simple_selected = selected_score['macro_f1'] - simple_validation['macro_f1'] <= standard_error
    search['simplicity_selection'] = {'method': 'One paired date-clustered validation standard error; 400 resamples, seed 42.',
        'validation_difference': round(selected_score['macro_f1'] - simple_validation['macro_f1'], 6),
        'standard_error': standard_error, 'simple_selected': simple_selected}
    version = ('rf-' if simple_selected else 'cycle-') + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    model = hm.HybridModel(None, previous if simple_selected else estimator, hm.StepScaler().fit(training), classes,
        base_columns if simple_selected else columns, version, previous_weights if simple_selected else weights,
        cycle_scales=None if simple_selected else scales)
    majority = Counter(training.label.dropna()).most_common(1)[0][0]
    profile_weights = hm.tune_decision_weights(profile['validation'], truth['validation'], classes)
    def guess(probabilities, decision_weights):
        return [classes[i] for i in np.argmax(hm.apply_weights(probabilities, classes, decision_weights), axis=1)]
    predictions = {name: guess(model.class_probabilities(x[name][model.columns]), model.weights) for name in ('validation', 'test')}
    baseline_results = {}
    for name in ('validation', 'test'):
        baseline_results[name] = {
            'majority': scores(truth[name], [majority] * len(truth[name]), classes),
            'persistence': scores(truth[name], [w.rows[-1].get('label') or majority for w in split[name]], classes),
            'historical_profile': scores(truth[name], guess(profile[name], profile_weights), classes),
            'previous_random_forest': scores(truth[name], guess(hm.aligned_probabilities(previous, x[name][base_columns], classes), previous_weights), classes)}
    test_scores = scores(truth['test'], predictions['test'], classes)
    active_comparison = None
    active, row = model_registry.load_active()
    if active is not None:
        paths = active.predict_many([w.rows for w in split['test']], [w.rows[-1]['slot'] for w in split['test']],
            [w.rows[-1]['road_type'] for w in split['test']], 1, [counts.get(w.segment_id) for w in split['test']])
        active_comparison = {'version': row['version'], **scores(truth['test'], [p[0]['predicted_class'] for p in paths], classes)}
    acceptance_baselines = {k: v for k, v in baseline_results['test'].items() if not (simple_selected and k == 'previous_random_forest')}
    decision = acceptance(test_scores, acceptance_baselines, classes, active_comparison)
    horizon_windows = [w for w in hm.build_windows(frame) if target_day(w) >= test_start and w.targets]
    paths = model.predict_many([w.rows for w in horizon_windows], [w.rows[-1]['slot'] for w in horizon_windows],
        [w.rows[-1]['road_type'] for w in horizon_windows], hm.MAX_HORIZON, [counts.get(w.segment_id) for w in horizon_windows])
    horizons = {}
    for h in range(1, hm.MAX_HORIZON + 1):
        available = [(w.targets[h]['label'], p[h - 1]['predicted_class']) for w, p in zip(horizon_windows, paths) if w.targets.get(h, {}).get('label')]
        horizons[str(h)] = scores([a for a, _ in available], [b for _, b in available], classes)
    metrics = {'task': 'Next-block severity on the restored project archive. Classifier and feature complexity selected using validation only; simple candidates retain the original input definition.',
        'classes': classes, 'sequence_length': hm.SEQ_LEN, 'max_horizon_blocks': hm.MAX_HORIZON, 'time_granularity': 'block',
        'split': {'method': 'chronological target dates: 70% training / 15% validation / 15% test', 'train_before': str(train_end), 'test_from': str(test_start)},
        'test': test_scores, 'validation': scores(truth['validation'], predictions['validation'], classes),
        'within_date_test': scores([y for w, y in zip(split['test'], truth['test']) if w.rows[-1]['date'] == w.targets[1]['date']],
            [y for w, y in zip(split['test'], predictions['test']) if w.rows[-1]['date'] == w.targets[1]['date']], classes),
        'next_date_test': scores([y for w, y in zip(split['test'], truth['test']) if w.rows[-1]['date'] != w.targets[1]['date']],
            [y for w, y in zip(split['test'], predictions['test']) if w.rows[-1]['date'] != w.targets[1]['date']], classes),
        'baselines_test': acceptance_baselines,
        'baselines_validation': {k: v for k, v in baseline_results['validation'].items() if not (simple_selected and k == 'previous_random_forest')},
        'existing_feature_rf_reference': {'test': baseline_results['test']['previous_random_forest'],
                                         'validation': baseline_results['validation']['previous_random_forest']},
        'horizons_test': horizons, 'lstm_regression_test': {},
        'breakdowns_test': breakdowns(split['test'], predictions['test'], classes),
        'transfer_validation': validate_transfer(frame, train_end, test_start, classes), 'active_model_comparison': active_comparison,
        'experiment': {'seed': 42, 'libraries': environment_versions(), 'selection_period': 'validation only',
            'selected_family': model.kind, 'search': search, 'previous_feature_search': previous_search,
            'cycle_scales': scales, 'preprocessing': 'Cycle scales fitted on complete training road-days. Training profiles see earlier labels only. Cross-date projections are absent.'},
        'caveat': 'Historical re-evaluation: this source/test era was already examined in the project and earlier experiments. The source derives six blocks from daily values. Scores do not validate six independently measured traffic intervals or current Bengaluru forecasts.',
        'training_seconds': round(time.monotonic() - started, 1)}
    data = {'block_records': len(frame), 'roads': int(frame.segment_id.nunique()), 'date_range': [str(frame.date.min()), str(frame.date.max())],
        'sources': dict(Counter(frame.source_type)), 'verified_records': int((frame.quality_status == 'verified').sum()),
        'sequences': {name: len(v) for name, v in split.items()}, 'rf_training_rows': len(split['train']), 'time_granularity': 'block',
        'sha256': hashlib.sha256(frame[['segment_id', 'slot', 'label', *hm.NUMERIC]].to_csv(index=False).encode()).hexdigest()}
    directory = core.MODELS_DIR / version
    model.save(directory, {'data': data})
    report = {'version': version, 'kind': model.kind, 'metrics': metrics, 'acceptance': decision, 'data': data}
    (directory / 'benchmark.json').write_text(json.dumps(report, indent=2, default=str))
    (core.ROOT / 'evaluation' / 'block-model-selection-benchmark.json').write_text(json.dumps(report, indent=2, default=str))
    status = model_registry.register(version, metrics, decision, data, actor, 'Restored original dataset; validation-only model comparison with a date-clustered simplicity rule.', kind=model.kind)
    if activate and decision['passed']:
        model_registry.activate(version, actor)
    print(json.dumps({'version': version, 'status': status, 'test': test_scores, 'baselines': {k: {'accuracy': v['accuracy'], 'macro_f1': v['macro_f1']} for k, v in baseline_results['test'].items()}, 'acceptance': decision}, indent=2), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trained-by', default='accuracy-improvement')
    parser.add_argument('--activate', action='store_true')
    args = parser.parse_args()
    train(args.trained_by, args.activate)
