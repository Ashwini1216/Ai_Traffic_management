from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "bangalore_traffic_timely_dataset.zip"
MODEL_PATH = ROOT / "models" / "next_block_rf.joblib"
METRICS_PATH = ROOT / "models" / "next_block_rf_metrics.json"
TARGET = "TrafficSituation"
BLOCK_ORDER = {"Midnight": 0, "Morning": 1, "Daytime": 2, "Noon": 3, "Evening": 4, "Night": 5}
CATEGORICAL = ["Area Name", "Road/Intersection Name", "Weather Conditions", "Roadwork and Construction Activity", "TimeBlock"]
NUMERIC = ["Traffic Volume", "Average Speed", "Travel Time Index", "Incident Reports", "month", "day_of_week"]


def main() -> int:
    if not ARCHIVE.exists():
        print(f"Dataset archive not found: {ARCHIVE}", file=sys.stderr)
        return 1
    with zipfile.ZipFile(ARCHIVE) as archive:
        csv_name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
        with archive.open(csv_name) as stream:
            frame = pd.read_csv(stream)
    required = set(CATEGORICAL + NUMERIC[:4] + ["Date", TARGET])
    missing = sorted(required - set(frame.columns))
    if missing:
        print("Dataset is missing required fields: " + ", ".join(missing), file=sys.stderr)
        return 1

    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame = frame.dropna(subset=["Date", TARGET]).copy()
    unknown_blocks = sorted(set(frame["TimeBlock"].dropna()) - set(BLOCK_ORDER))
    if unknown_blocks:
        print("Unrecognized time blocks: " + ", ".join(unknown_blocks), file=sys.stderr)
        return 1
    frame["block_order"] = frame["TimeBlock"].map(BLOCK_ORDER)
    frame = frame.sort_values(["Area Name", "Road/Intersection Name", "Date", "block_order"])
    group_columns = ["Area Name", "Road/Intersection Name"]
    frame["next_label"] = frame.groupby(group_columns)[TARGET].shift(-1)
    frame["next_date"] = frame.groupby(group_columns)["Date"].shift(-1)
    frame["next_block"] = frame.groupby(group_columns)["block_order"].shift(-1)
    frame["next_block_date"] = frame.groupby(group_columns)["Date"].shift(-1)
    same_day_next = (frame["next_date"] == frame["Date"]) & (frame["next_block"] == frame["block_order"] + 1)
    next_day_midnight = (
        (frame["block_order"] == 5)
        & (frame["next_block"] == 0)
        & (frame["next_date"] == frame["Date"] + pd.Timedelta(days=1))
    )
    frame = frame.loc[same_day_next | next_day_midnight].copy()
    frame["month"] = frame["Date"].dt.month
    frame["day_of_week"] = frame["Date"].dt.dayofweek
    frame["split_date"] = frame["next_date"].dt.strftime("%Y-%m-%d")

    date_values = sorted(frame["split_date"].unique())
    train_end = date_values[int(len(date_values) * 0.70)]
    validation_end = date_values[int(len(date_values) * 0.85)]
    train = frame[frame["split_date"] < train_end]
    validation = frame[(frame["split_date"] >= train_end) & (frame["split_date"] < validation_end)]
    test = frame[frame["split_date"] >= validation_end]
    feature_columns = CATEGORICAL + NUMERIC
    X_train, y_train = train[feature_columns], train["next_label"].astype(str)
    X_validation, y_validation = validation[feature_columns], validation["next_label"].astype(str)
    X_test, y_test = test[feature_columns], test["next_label"].astype(str)

    preprocessor = ColumnTransformer([
        ("categories", Pipeline([
            ("fill", SimpleImputer(strategy="most_frequent")),
            ("encode", OneHotEncoder(handle_unknown="ignore")),
        ]), CATEGORICAL),
        ("numbers", SimpleImputer(strategy="median"), NUMERIC),
    ])
    pipeline = Pipeline([
        ("features", preprocessor),
        ("classifier", RandomForestClassifier(
            n_estimators=300,
            min_samples_leaf=2,
            class_weight="balanced_subsample",
            random_state=42,
            n_jobs=-1,
        )),
    ])
    pipeline.fit(X_train, y_train)
    validation_predictions = pipeline.predict(X_validation)
    test_predictions = pipeline.predict(X_test)
    classes = sorted(frame["next_label"].astype(str).unique())
    baseline_label = y_train.value_counts().idxmax()
    metrics = {
        "dataset_archive": ARCHIVE.name,
        "source_type": "historical_dataset",
        "prediction_task": "forecast the next available time block on the same road",
        "target": "next time block TrafficSituation",
        "features": feature_columns,
        "split_method": "chronological by target date: 70% train, 15% validation, 15% test",
        "train_target_date_range": [str(train["split_date"].min()), str(train["split_date"].max())],
        "validation_target_date_range": [str(validation["split_date"].min()), str(validation["split_date"].max())],
        "test_target_date_range": [str(test["split_date"].min()), str(test["split_date"].max())],
        "record_counts": {"train": len(train), "validation": len(validation), "test": len(test)},
        "class_counts": frame["next_label"].astype(str).value_counts().to_dict(),
        "majority_class_baseline": baseline_label,
        "baseline_test_accuracy": float(accuracy_score(y_test, [baseline_label] * len(y_test))),
        "validation_accuracy": float(accuracy_score(y_validation, validation_predictions)),
        "validation_macro_f1": float(f1_score(y_validation, validation_predictions, average="macro", zero_division=0)),
        "test_accuracy": float(accuracy_score(y_test, test_predictions)),
        "test_macro_f1": float(f1_score(y_test, test_predictions, average="macro", zero_division=0)),
        "test_report": classification_report(y_test, test_predictions, labels=classes, output_dict=True, zero_division=0),
        "test_confusion_matrix": confusion_matrix(y_test, test_predictions, labels=classes).tolist(),
        "class_order": classes,
        "caveat": "The source labels are heavily imbalanced and TrafficSituation duplicates the supplied congestion label. This is an initial next-time-block experiment on that dataset, not evidence of deployment-ready forecasts. Time-block durations are not uniform.",
    }
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipeline, "feature_columns": feature_columns, "categorical": CATEGORICAL, "numeric": NUMERIC, "classes": classes, "block_order": BLOCK_ORDER}, MODEL_PATH)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps({
        "model": str(MODEL_PATH),
        "metrics": str(METRICS_PATH),
        "records": metrics["record_counts"],
        "test_accuracy": metrics["test_accuracy"],
        "baseline_test_accuracy": metrics["baseline_test_accuracy"],
        "test_macro_f1": metrics["test_macro_f1"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
