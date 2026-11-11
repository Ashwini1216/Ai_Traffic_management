"""Train, evaluate, and register the hybrid LSTM -> Random Forest congestion model.

Run from the project root:
    python backend/train_hybrid.py              # train and register a new version
    python backend/train_hybrid.py --activate   # ...and put it into use if it passes acceptance

Training data: the validated historical dataset plus block-level observations that an
authority has verified (so newly collected, verified data joins the history). The split
is chronological by target date: 70% train, 15% validation, 15% test (the latest period).
The Random Forest learns from LSTM forecasts made for periods the LSTM did not train on
(expanding-window folds), so it does not over-trust in-sample LSTM accuracy.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support
from sklearn.pipeline import Pipeline

import hybrid_model as hm
import model_registry
import patterns
import segments
import store
from core import MODELS_DIR

MIN_LABELLED_WINDOWS = 300


def environment_versions():
    """Record versions with the TensorFlow distribution used on this platform."""
    libraries = {}
    for name in ('numpy', 'pandas', 'scikit-learn', 'tensorflow'):
        try:
            libraries[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            if name != 'tensorflow':
                raise
            libraries[name] = importlib.metadata.version('tensorflow-cpu')
    return libraries


# A new version is accepted only when all of these hold on the held-out test period. The
# baselines are the alternatives the system would otherwise use: always the most common
# class, "same as the last block", and the road's historical time-of-day profile. The LSTM
# must also earn its place: the hybrid has to beat the same Random Forest trained without
# the LSTM forecast features by the same margin.
ACCEPTANCE_RULES = {
    # Set MODEL_ACCEPTANCE_MIN_GAIN in .env to change the required margin (a project decision:
    # decide it before training, not after seeing a result).
    "min_macro_f1_gain_over_best_baseline": float(os.getenv("MODEL_ACCEPTANCE_MIN_GAIN", "0.02")),
    "lstm_must_beat_rf_without_lstm": True,
    "accuracy_at_least_majority_baseline": True,
    "every_class_recalled_when_support_at_least": 5,
    "max_macro_f1_drop_vs_active_model": 0.01,
}


def make_rf(min_samples_leaf=2, max_features='sqrt') -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("model", RandomForestClassifier(n_estimators=300, min_samples_leaf=min_samples_leaf,
                                         max_features=max_features, class_weight="balanced_subsample",
                                         random_state=42, n_jobs=-1)),
    ])


def scores(y_true: list, y_pred: list, classes: list[str]) -> dict:
    if not y_true:
        return {"count": 0}
    precision, recall, f1, support = precision_recall_fscore_support(y_true, y_pred, labels=classes, zero_division=0)
    return {
        "count": len(y_true),
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "macro_f1": round(float(f1_score(y_true, y_pred, labels=classes, average="macro", zero_division=0)), 4),
        "per_class": {c: {"precision": round(float(p), 4), "recall": round(float(r), 4), "f1": round(float(f), 4), "support": int(s)}
                      for c, p, r, f, s in zip(classes, precision, recall, f1, support)},
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=classes).tolist(),
    }


def target_day(window: hm.Window):
    grain = 'day' if window.rows[-1].get('time_block') == 'Daily' else 'block'
    return hm.date_block_for_slot(window.rows[-1]['slot']+1,grain)[0]


def features(windows: list[hm.Window], forecasts: list[dict] | None, classes: list[str], columns: list[str],
             entries: list[dict | None]) -> pd.DataFrame:
    rows = [hm.rf_feature_row(w.rows, forecasts[i] if forecasts is not None else None, w.rows[-1]["slot"] + 1,
                              w.rows[-1]["road_type"], classes, entries[i]) for i, w in enumerate(windows)]
    return pd.DataFrame(rows, columns=columns)


def chronological_entries(windows: list[hm.Window], frame: pd.DataFrame) -> list[dict | None]:
    """Each training feature sees only records strictly before its forecast target.

    Removing just the row's own label still exposes labels from later training dates.
    A growing history avoids that leakage and matches how a forecast is made in use.
    """
    labelled = frame[frame['label'].notna()].sort_values('slot').to_dict('records')
    counts, entries, cursor = {}, {}, 0
    for window in sorted(windows, key=lambda w: w.rows[-1]['slot']):
        target_slot = window.rows[-1]['slot'] + 1
        while cursor < len(labelled) and labelled[cursor]['slot'] < target_slot:
            row = labelled[cursor]
            entry = counts.setdefault(row['segment_id'], {'cell': defaultdict(Counter), 'block': defaultdict(Counter), 'all': Counter()})
            entry['cell'][(row['time_block'], patterns.day_type(row['date']))][row['label']] += 1
            entry['block'][row['time_block']][row['label']] += 1
            entry['all'][row['label']] += 1
            cursor += 1
        entries[id(window)] = copy.deepcopy(counts.get(window.segment_id))
    return [entries[id(window)] for window in windows]


def training_readiness(frame=None, granularity='auto') -> dict:
    if frame is None:
        frame = hm.load_history() if granularity != 'day' else hm.load_daily_history()
        if granularity == 'auto' and len([w for w in hm.build_windows(frame) if w.targets.get(1,{}).get('label')]) < MIN_LABELLED_WINDOWS:
            daily = hm.load_daily_history()
            if not daily.empty:
                frame = daily
    grain = 'day' if not frame.empty and frame.iloc[0]['time_block'] == 'Daily' else 'block'
    windows = hm.build_windows(frame)
    labelled = [w for w in windows if w.targets.get(1, {}).get('label')]
    days = sorted({target_day(w) for w in labelled})
    classes = hm.ordered_classes(frame['label'].dropna()) if not frame.empty else []
    reasons = []
    if len(labelled) < MIN_LABELLED_WINDOWS:
        reasons.append(f'{len(labelled)} usable labelled sequences; at least {MIN_LABELLED_WINDOWS} are required.')
    if len(classes) < 2:
        reasons.append('At least two observed congestion classes are required.')
    if len(days) < 10:
        reasons.append('At least ten target dates are needed for chronological train, validation and test periods.')
    if len(days) >= 10:
        early_end = days[max(1, int(len(days) * .70 * .25))]
        numeric_early = sum(target_day(w) < early_end and any(not hm._is_missing(w.targets[1].get(c)) for c in hm.NUMERIC)
                            for w in labelled)
        if numeric_early < 10:
            reasons.append('At least ten early training sequences need measured volume, speed or travel-time index for the LSTM comparison.')
    cameras = store.one("SELECT COUNT(*) AS n FROM cameras WHERE authorization_status='approved' AND operational_status='online' AND measurement_validated=1")
    return {'ready': not reasons, 'reasons': reasons, 'granularity':grain, 'block_records': len(frame) if grain=='block' else 0,
            'daily_records':len(frame) if grain=='day' else 0,'labelled_sequences': len(labelled),
            'minimum_labelled_sequences': MIN_LABELLED_WINDOWS, 'target_dates': len(days), 'classes': classes,
            'validated_online_cameras': cameras['n'],
            'notice': 'Readiness is a data check. Accuracy improvement must be measured on the held-out test period.'}


def select_forest(x_train, truth_train, x_validation, truth_validation, classes, configurations=None):
    """Pick settings and class decision weights on validation only; never inspect test labels."""
    trials = []
    best = None
    configurations = configurations or [dict(min_samples_leaf=2, max_features='sqrt'),
                      dict(min_samples_leaf=4, max_features=.5),
                      dict(min_samples_leaf=1, max_features=.75)]
    for settings in configurations:
        estimator = make_rf(**settings).fit(x_train, truth_train)
        probabilities = hm.aligned_probabilities(estimator, x_validation, classes)
        weights = hm.tune_decision_weights(probabilities, truth_validation, classes)
        guess = [classes[i] for i in np.argmax(hm.apply_weights(probabilities, classes, weights), axis=1)]
        result = scores(truth_validation, guess, classes)
        trials.append({'settings': settings, 'validation': result, 'decision_weights': weights})
        key = (result['macro_f1'], settings['min_samples_leaf'])
        if best is None or key > best[0]:
            best = (key, estimator, weights, settings)
    return best[1], best[2], {'selected_settings': best[3], 'trials': trials, 'selection_period': 'validation only'}


def regression_errors(windows: list[hm.Window], forecasts: list[dict]) -> dict:
    out = {}
    for column in hm.NUMERIC:
        pairs = [(f[column], w.targets[1][column], w.rows[-1][column]) for w, f in zip(windows, forecasts)
                 if not hm._is_missing(w.targets[1].get(column))]
        if not pairs:
            continue
        predicted, actual, last = (np.asarray(x, dtype=float) for x in zip(*pairs))
        persistence = ~np.isnan(last)
        out[column] = {
            "count": len(pairs),
            "mae": round(float(np.mean(np.abs(predicted - actual))), 4),
            "rmse": round(float(np.sqrt(np.mean((predicted - actual) ** 2))), 4),
            "persistence_mae": round(float(np.mean(np.abs(last[persistence] - actual[persistence]))), 4) if persistence.any() else None,
        }
    return out


def breakdowns(windows: list[hm.Window], predictions: list[str], classes: list[str]) -> dict:
    groups: dict[str, dict[str, tuple[list, list]]] = {"time_block": defaultdict(lambda: ([], [])), "day_type": defaultdict(lambda: ([], [])),
                                                       "weather": defaultdict(lambda: ([], [])), "area": defaultdict(lambda: ([], []))}
    for window, predicted in zip(windows, predictions):
        target = window.targets[1]
        wet = target.get("wet")
        keys = {"time_block": target["time_block"], "day_type": patterns.day_type(target["date"]),
                "weather": "unknown" if hm._is_missing(wet) else ("wet" if wet else "dry"), "area": target["area"]}
        for group, key in keys.items():
            groups[group][key][0].append(target["label"])
            groups[group][key][1].append(predicted)
    return {group: {key: {k: v for k, v in scores(t, p, classes).items() if k in ("count", "accuracy", "macro_f1")}
                    for key, (t, p) in sorted(values.items())} for group, values in groups.items()}


def validate_transfer(frame: pd.DataFrame, train_end, test_start, classes: list[str]) -> dict:
    """Leave-one-road-out check: predict each road's test-period labels only from similar roads."""
    train_counts = patterns.build_counts(frame[frame["date"] < train_end])
    metas = {row["segment_id"]: row for row in store.rows("SELECT segment_id,area,road,road_type,latitude,longitude FROM road_segments")}
    eligible = [s for s, entry in train_counts.items() if sum(entry["all"].values()) >= segments.MIN_PATTERN_RECORDS and s in metas]
    test_rows = frame[(frame["date"] >= test_start) & frame["label"].notna()]
    majority = Counter(frame.loc[frame["date"] < train_end, "label"].dropna()).most_common(1)[0][0]
    y_true, y_pred, per_road = [], [], {}
    for segment_id in eligible:
        donors = [metas[d] for d in eligible if d != segment_id]
        rows = test_rows[test_rows["segment_id"] == segment_id]
        truth, guess = [], []
        for block, day, label in zip(rows["time_block"], rows["date"], rows["label"]):
            probs, _ = patterns.transfer_distribution(metas[segment_id], donors, train_counts, block, patterns.day_type(day), classes)
            if probs is None:
                continue
            truth.append(label)
            guess.append(max(probs, key=probs.get))
        if truth:
            per_road[segment_id] = {"count": len(truth), "accuracy": round(float(accuracy_score(truth, guess)), 4)}
            y_true += truth
            y_pred += guess
    if not y_true:
        return {"enabled": False, "reason": "Not enough roads with history to validate transfer.", "count": 0}
    transfer = scores(y_true, y_pred, classes)
    baseline = scores(y_true, [majority] * len(y_true), classes)
    enabled = len(y_true) >= 50 and transfer["macro_f1"] >= baseline["macro_f1"] + patterns.TRANSFER_MIN_GAIN
    return {"enabled": enabled, "count": len(y_true), "roads_evaluated": len(per_road),
            "macro_f1": transfer["macro_f1"], "accuracy": transfer["accuracy"],
            "majority_baseline_macro_f1": baseline["macro_f1"], "required_gain": patterns.TRANSFER_MIN_GAIN,
            "per_road": per_road,
            "reason": "Transfer beat the majority baseline on held-out roads." if enabled else
                      "Transfer did not beat the majority baseline by the required margin, so it is disabled."}


def acceptance(test: dict, baselines: dict, classes: list[str], active_comparison: dict | None,
               rf_without_lstm: dict | None = None) -> dict:
    checks = []
    best_name, best = max(((n, b) for n, b in baselines.items() if b.get("count")), key=lambda item: item[1]["macro_f1"])
    gain = ACCEPTANCE_RULES["min_macro_f1_gain_over_best_baseline"]
    checks.append({"check": f"macro-F1 beats the best baseline ({best_name}) by at least {gain}",
                   "passed": test["macro_f1"] >= best["macro_f1"] + gain,
                   "value": test["macro_f1"], "threshold": round(best["macro_f1"] + gain, 4)})
    if ACCEPTANCE_RULES["lstm_must_beat_rf_without_lstm"] and rf_without_lstm and rf_without_lstm.get("count"):
        checks.append({"check": f"macro-F1 beats the Random Forest without LSTM features by at least {gain} (the LSTM adds value)",
                       "passed": test["macro_f1"] >= rf_without_lstm["macro_f1"] + gain,
                       "value": test["macro_f1"], "threshold": round(rf_without_lstm["macro_f1"] + gain, 4)})
    checks.append({"check": "accuracy is at least the majority-class baseline",
                   "passed": test["accuracy"] >= baselines["majority"]["accuracy"],
                   "value": test["accuracy"], "threshold": baselines["majority"]["accuracy"]})
    minimum = ACCEPTANCE_RULES["every_class_recalled_when_support_at_least"]
    missed = [c for c, m in test["per_class"].items() if m["support"] >= minimum and m["recall"] == 0]
    checks.append({"check": f"every class with at least {minimum} test records is predicted correctly at least once",
                   "passed": not missed, "value": missed or "all classes recalled"})
    if active_comparison and active_comparison.get("macro_f1") is not None:
        drop = ACCEPTANCE_RULES["max_macro_f1_drop_vs_active_model"]
        checks.append({"check": f"macro-F1 is no more than {drop} below the active model ({active_comparison['version']}) on the same test period",
                       "passed": test["macro_f1"] >= active_comparison["macro_f1"] - drop,
                       "value": test["macro_f1"], "threshold": round(active_comparison["macro_f1"] - drop, 4)})
    return {"passed": all(c["passed"] for c in checks), "rules": ACCEPTANCE_RULES, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--activate", action="store_true", help="activate the new version if it passes acceptance")
    parser.add_argument("--trained-by", default="command_line")
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--notes", default=None)
    parser.add_argument('--granularity',choices=['block','day'],default='block')
    args = parser.parse_args(argv)
    started = time.time()

    store.initialize()
    segments.sync_segments()
    frame = hm.load_daily_history() if args.granularity=='day' else hm.load_history()
    readiness = training_readiness(frame)
    if not readiness['ready']:
        print('Not ready to train: ' + ' '.join(readiness['reasons']), file=sys.stderr)
        return 1
    windows = hm.build_windows(frame)
    labelled = [w for w in windows if 1 in w.targets and w.targets[1].get("label")]
    if len(labelled) < MIN_LABELLED_WINDOWS:
        print(f"Not enough history to train: {len(labelled)} labelled sequences found, {MIN_LABELLED_WINDOWS} needed.", file=sys.stderr)
        return 1
    classes = hm.ordered_classes(frame["label"].dropna())
    days = sorted({target_day(w) for w in labelled})
    train_end, test_start = days[int(len(days) * 0.70)], days[int(len(days) * 0.85)]
    split = {"train": [], "validation": [], "test": []}
    for window in windows:
        day = target_day(window)
        split["train" if day < train_end else "validation" if day < test_start else "test"].append(window)
    print(f"History: {len(frame)} {'daily' if args.granularity == 'day' else 'block'} records, {frame['segment_id'].nunique()} roads, {len(windows)} sequences "
          f"(train {len(split['train'])}, validation {len(split['validation'])}, test {len(split['test'])}).")

    scaler = hm.StepScaler().fit(frame[frame["date"] < train_end])
    train = sorted(split["train"], key=lambda w: w.rows[-1]["slot"])
    train_days = sorted({target_day(w) for w in train})
    cuts = [train_days[int(len(train_days) * q)] for q in (0.25, 0.5, 0.75)]
    chunk_of = {id(w): sum(target_day(w) >= cut for cut in cuts) for w in train}
    out_of_fold: dict[int, dict] = {}
    for k in (1, 2, 3):
        fold_cut = cuts[k - 1]
        fold_scaler = hm.StepScaler().fit(frame[frame['date'] < fold_cut])
        fold_model, _ = hm.fit_lstm([w for w in train if chunk_of[id(w)] < k], fold_scaler, epochs=args.epochs)
        targets = [w for w in train if chunk_of[id(w)] == k and 1 in w.targets]
        for window, forecast in zip(targets, hm.lstm_forecast(fold_model, [w.rows for w in targets], fold_scaler)):
            out_of_fold[id(window)] = forecast
        print(f"LSTM fold {k}/3 done.")
    lstm, epochs_run = hm.fit_lstm(train, scaler, epochs=args.epochs)
    print(f"Final LSTM trained ({epochs_run} epochs).")

    history_train = frame[frame["date"] < train_end]
    majority = Counter(history_train["label"].dropna()).most_common(1)[0][0]
    profile_counts = patterns.build_counts(history_train)
    columns, ablation_columns = hm.rf_columns(classes), hm.rf_columns(classes, with_lstm=False)
    rf_rows = [w for w in train if id(w) in out_of_fold and w.targets[1].get("label")]
    x_train = features(rf_rows, [out_of_fold[id(w)] for w in rf_rows], classes, columns,
                       chronological_entries(rf_rows, history_train))
    y_train = [w.targets[1]["label"] for w in rf_rows]
    validation_rows = [w for w in split['validation'] if w.targets.get(1, {}).get('label')]
    validation_truth = [w.targets[1]['label'] for w in validation_rows]
    validation_forecasts = hm.lstm_forecast(lstm, [w.rows for w in validation_rows], scaler)
    x_validation = features(validation_rows, validation_forecasts, classes, columns,
                            [profile_counts.get(w.segment_id) for w in validation_rows])
    rf, hybrid_weights, hybrid_search = select_forest(x_train[columns], y_train, x_validation[columns], validation_truth, classes)
    rf_ablation, rf_weights, rf_search = select_forest(x_train[ablation_columns], y_train,
                                                      x_validation[ablation_columns], validation_truth, classes)

    version = ('daily-' if args.granularity=='day' else '') + model_registry.new_version_name()
    model = hm.HybridModel(lstm, rf, scaler, classes, columns, version,granularity=args.granularity)

    def prepare(windows_: list[hm.Window]) -> dict:
        usable = [w for w in windows_ if 1 in w.targets and w.targets[1].get("label")]
        forecasts = hm.lstm_forecast(lstm, [w.rows for w in usable], scaler)
        x = features(usable, forecasts, classes, columns, [profile_counts.get(w.segment_id) for w in usable])
        profile = []
        for w in usable:
            entry = profile_counts.get(w.segment_id)
            if entry:
                probs, _, _ = patterns.pattern_distribution(entry, w.targets[1]["time_block"], patterns.day_type(w.targets[1]["date"]), classes)
                profile.append([probs[c] for c in classes])
            else:
                profile.append([1.0 if c == majority else 0.0 for c in classes])
        return {"windows": usable, "forecasts": forecasts, "truth": [w.targets[1]["label"] for w in usable],
                "hybrid_p": model.class_probabilities(x[columns]),
                "ablation_p": hm.aligned_probabilities(rf_ablation, x[ablation_columns], classes),
                "profile_p": np.asarray(profile)}

    validation, test = prepare(split["validation"]), prepare(split["test"])
    # Decision weights are tuned on the validation period only, for the hybrid model and for the
    # probabilistic comparison models alike, then applied unchanged to the test period.
    weights = {'hybrid': hybrid_weights, 'ablation': rf_weights,
               'profile': hm.tune_decision_weights(validation['profile_p'], validation['truth'], classes)}
    model.weights = weights["hybrid"]
    rf_version = version.replace('hybrid-', 'rf-', 1)
    rf_model = hm.HybridModel(None, rf_ablation, scaler, classes, ablation_columns, rf_version, weights['ablation'],args.granularity)

    def decide(probabilities: np.ndarray, name: str) -> list[str]:
        return [classes[i] for i in np.argmax(hm.apply_weights(probabilities, classes, weights[name]), axis=1)]

    for part in (validation, test):
        truth = part["truth"]
        part["hybrid"] = decide(part["hybrid_p"], "hybrid")
        part["metrics"] = scores(truth, part["hybrid"], classes)
        part["baselines"] = {
            "majority": scores(truth, [majority] * len(truth), classes),
            "persistence": scores(truth, [w.rows[-1].get("label") or majority for w in part["windows"]], classes),
            "historical_profile": scores(truth, decide(part["profile_p"], "profile"), classes),
        }
        part["rf_without_lstm"] = scores(truth, decide(part["ablation_p"], "ablation"), classes)

    horizon_windows = [w for w in split["test"] if any(w.targets.get(h, {}).get("label") for h in range(1, hm.MAX_HORIZON + 1))]
    def score_horizons(estimator):
        paths = estimator.predict_many([w.rows for w in horizon_windows], [w.rows[-1]['slot'] for w in horizon_windows],
                                       [w.rows[-1]['road_type'] for w in horizon_windows], hm.MAX_HORIZON,
                                       [profile_counts.get(w.segment_id) for w in horizon_windows])
        result = {}
        for h in range(1, hm.MAX_HORIZON + 1):
            truth = [w.targets[h]['label'] for w in horizon_windows if w.targets.get(h, {}).get('label')]
            guess = [path[h - 1]['predicted_class'] for w, path in zip(horizon_windows, paths) if w.targets.get(h, {}).get('label')]
            result[str(h)] = {k: v for k, v in scores(truth, guess, classes).items() if k in ('count', 'accuracy', 'macro_f1')}
        return result
    horizons = score_horizons(model)
    rf_horizons = score_horizons(rf_model)

    active_comparison = None
    active_model, active_row = (None, None)
    try:
        active_model, active_row = model_registry.load_active(args.granularity)
    except (OSError, ValueError) as exc:
        print(f"Active model could not be loaded for comparison: {exc}", file=sys.stderr)
    if active_model is not None and test["windows"]:
        paths_active = active_model.predict_many([w.rows for w in test["windows"]], [w.rows[-1]["slot"] for w in test["windows"]],
                                                 [w.rows[-1]["road_type"] for w in test["windows"]], 1,
                                                 [profile_counts.get(w.segment_id) for w in test["windows"]])
        active_scores = scores(test["truth"], [p[0]["predicted_class"] for p in paths_active], classes)
        active_comparison = {"version": active_row["version"], "macro_f1": active_scores["macro_f1"], "accuracy": active_scores["accuracy"]}

    transfer = validate_transfer(frame, train_end, test_start, classes)
    decision = acceptance(test["metrics"], test["baselines"], classes, active_comparison, test["rf_without_lstm"])
    metrics = {
        "task": "Congestion class of a road's next time block (1-3 blocks ahead recursively) from its last 6 consecutive blocks.",
        "classes": classes, "sequence_length": hm.SEQ_LEN, "max_horizon_blocks": hm.MAX_HORIZON,
        "split": {"method": "chronological by target date: 70% train, 15% validation, 15% test",
                  "train_before": str(train_end), "test_from": str(test_start)},
        "test": test["metrics"], "validation": validation["metrics"],
        "baselines_test": test["baselines"], "baselines_validation": validation["baselines"],
        "decision_weights": weights,
        "lstm_contribution": {
            "hybrid_macro_f1": test["metrics"]["macro_f1"], "rf_without_lstm_macro_f1": test["rf_without_lstm"]["macro_f1"],
            "difference": round(test["metrics"]["macro_f1"] - test["rf_without_lstm"]["macro_f1"], 4),
            "rf_without_lstm_test": test["rf_without_lstm"],
            "note": "Ablation: the same Random Forest trained without the LSTM forecast features. It shows how much the "
                    "LSTM adds on this dataset; the hybrid must beat it by the acceptance margin to be used.",
        },
        "horizons_test": horizons,
        "lstm_regression_test": regression_errors(test["windows"], test["forecasts"]),
        "breakdowns_test": breakdowns(test["windows"], test["hybrid"], classes),
        "active_model_comparison": active_comparison,
        "transfer_validation": transfer,
        "epochs_run": epochs_run, "training_seconds": round(time.time() - started, 1),
        "caveat": "Offline evaluation on historical records. It does not prove reliable live performance.",
    }
    metrics['time_granularity'] = args.granularity
    if args.granularity=='day':
        metrics['task'] = 'Next-day congestion class from the previous six consecutive daily observations; up to three days recursively. Source severity bins are project-defined, not sensor ground truth.'
    sources = Counter(frame["source_type"])
    data = {"block_records": len(frame), "roads": int(frame["segment_id"].nunique()),
            "date_range": [str(frame["date"].min()), str(frame["date"].max())],
            "sources": dict(sources), "verified_records": int((frame["quality_status"] == "verified").sum()),
            "sequences": {k: len(v) for k, v in split.items()}, "rf_training_rows": len(rf_rows)}
    data['time_granularity'] = args.granularity
    if args.granularity=='day':
        data['daily_records'] = data.pop('block_records')
    # Record exactly what was evaluated and how candidates were selected. No test-set
    # result is used to choose settings or switch families after the comparison.
    fingerprint_columns = ['segment_id', 'slot', 'label', *hm.NUMERIC, 'incident_reports', 'source_type', 'quality_status']
    data['sha256'] = hashlib.sha256(frame[fingerprint_columns].sort_values(['segment_id', 'slot']).to_csv(index=False).encode()).hexdigest()
    selected_family = 'random_forest' if validation['rf_without_lstm']['macro_f1'] >= validation['metrics']['macro_f1'] else 'hybrid_lstm_rf'
    experiment = {'seed': 42, 'libraries': environment_versions(),
                  'selection_period': 'validation only', 'selected_family': selected_family,
                  'search': {'hybrid': hybrid_search, 'random_forest': rf_search},
                  'preprocessing': 'Each expanding LSTM fold fits its own scaler. Training profiles use only earlier records.',
                  'class_counts': {name: dict(Counter(w.targets[1]['label'] for w in rows if w.targets.get(1, {}).get('label')))
                                   for name, rows in split.items()}}
    metrics['experiment'] = experiment
    metrics['model_kind'] = model.kind
    metrics['selected_on_validation'] = selected_family == 'hybrid_lstm_rf'
    rf_guess = decide(test['ablation_p'], 'ablation')
    rf_metrics = {**metrics, 'model_kind': rf_model.kind, 'selected_on_validation': selected_family == 'random_forest',
                  'test': test['rf_without_lstm'], 'validation': validation['rf_without_lstm'],
                  'horizons_test': rf_horizons, 'lstm_regression_test': {}, 'lstm_contribution': None,
                  'breakdowns_test': breakdowns(test['windows'], rf_guess, classes),
                  'task': 'Congestion class from recent blocks and calendar/history features. Numeric inputs are carried forward for later horizons; no numeric forecasts are claimed.'}
    rf_decision = acceptance(rf_metrics['test'], test['baselines'], classes, active_comparison)
    rf_model.save(MODELS_DIR / rf_version, {'classes': classes, 'data': data})
    rf_status = model_registry.register(rf_version, rf_metrics, rf_decision, data, args.trained_by, args.notes, kind=rf_model.kind)
    model.save(MODELS_DIR / version, {"classes": classes, "data": data})
    status = model_registry.register(version, metrics, decision, data, args.trained_by, args.notes,kind=model.kind)
    for name, result, checks in ((version, metrics, decision), (rf_version, rf_metrics, rf_decision)):
        (MODELS_DIR / name / 'benchmark.json').write_text(json.dumps({'metrics': result, 'acceptance': checks, 'data': data}, indent=2, default=str))
    selected_version = rf_version if selected_family == 'random_forest' else version
    selected_decision = rf_decision if selected_family == 'random_forest' else decision
    if args.activate and selected_decision['passed']:
        model_registry.activate(selected_version, args.trained_by)
        if selected_version == version: status = 'active'
        else: rf_status = 'active'
    print(json.dumps({"version": version, "status": status, "accepted": decision["passed"],
                      "test_accuracy": test["metrics"]["accuracy"], "test_macro_f1": test["metrics"]["macro_f1"],
                      "baselines_macro_f1": {n: b["macro_f1"] for n, b in test["baselines"].items()},
                      "rf_without_lstm_macro_f1": test["rf_without_lstm"]["macro_f1"],
                      "per_class_recall": {c: m["recall"] for c, m in test["metrics"]["per_class"].items()},
                      "decision_weights": weights["hybrid"], "horizons": horizons, "transfer_enabled": transfer["enabled"],
                      "rf_version": rf_version, "rf_status": rf_status, "selected_on_validation": selected_version,
                      "failed_checks": [c["check"] for c in decision["checks"] if not c["passed"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
