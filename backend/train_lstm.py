from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import tensorflow as tf
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder, OneHotEncoder, StandardScaler
from sklearn.utils.class_weight import compute_class_weight
from tensorflow import keras
from tensorflow.keras import layers

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "bangalore_traffic_timely_dataset.zip"
MODEL_PATH = ROOT / "models" / "next_block_lstm.keras"
PREPROCESS_PATH = ROOT / "models" / "next_block_lstm_preprocess.joblib"
METRICS_PATH = ROOT / "models" / "next_block_lstm_metrics.json"
SEQ_LEN = 6
BLOCKS = ["Midnight", "Morning", "Daytime", "Noon", "Evening", "Night"]
BLOCK_INDEX = {name: i for i, name in enumerate(BLOCKS)}
GROUP = ["Area Name", "Road/Intersection Name"]
CATEGORICAL = ["Area Name", "Road/Intersection Name", "Weather Conditions", "Roadwork and Construction Activity", "TimeBlock"]
NUMERIC = ["Traffic Volume", "Average Speed", "Travel Time Index", "Incident Reports", "month", "day_of_week"]
FEATURES = CATEGORICAL + NUMERIC
TARGET = "TrafficSituation"


def read_data() -> pd.DataFrame:
    with zipfile.ZipFile(ARCHIVE) as archive:
        name = next(item for item in archive.namelist() if item.lower().endswith(".csv"))
        with archive.open(name) as stream:
            return pd.read_csv(stream)


def consecutive(a: pd.Series, b: pd.Series) -> bool:
    ad, bd = a["Date"], b["Date"]
    ai, bi = int(a["block_order"]), int(b["block_order"])
    return (ad == bd and bi == ai + 1) or (ai == len(BLOCKS)-1 and bi == 0 and bd == ad + pd.Timedelta(days=1))


def main() -> int:
    if not ARCHIVE.exists():
        print(f"Dataset archive not found: {ARCHIVE}", file=sys.stderr)
        return 1
    tf.keras.utils.set_random_seed(42)
    frame = read_data()
    needed = set(CATEGORICAL + NUMERIC[:4] + ["Date", TARGET])
    missing = sorted(needed - set(frame.columns))
    if missing:
        print("Dataset is missing required fields: " + ", ".join(missing), file=sys.stderr)
        return 1
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame = frame.dropna(subset=["Date", TARGET]).copy()
    frame["block_order"] = frame["TimeBlock"].map(BLOCK_INDEX)
    frame = frame.dropna(subset=["block_order"])
    frame["month"] = frame["Date"].dt.month
    frame["day_of_week"] = frame["Date"].dt.dayofweek
    frame = frame.sort_values(GROUP + ["Date", "block_order"]).reset_index(drop=True)

    sequence_indices, labels, target_dates = [], [], []
    for _, group in frame.groupby(GROUP, sort=False):
        row_indices = group.index.to_list()
        records = group.to_dict("records")
        for target_i in range(SEQ_LEN, len(records)):
            chain = records[target_i-SEQ_LEN:target_i+1]
            if all(consecutive(chain[i], chain[i+1]) for i in range(len(chain)-1)):
                sequence_indices.append(row_indices[target_i-SEQ_LEN:target_i])
                labels.append(str(records[target_i][TARGET]))
                target_dates.append(records[target_i]["Date"].date().isoformat())
    if not sequence_indices:
        print("No contiguous six-observation windows were found.", file=sys.stderr)
        return 1

    dates = sorted(set(target_dates))
    train_end = dates[int(len(dates)*0.70)]
    validation_end = dates[int(len(dates)*0.85)]
    target_dates = np.asarray(target_dates)
    labels = np.asarray(labels)
    train_idx = np.where(target_dates < train_end)[0]
    validation_idx = np.where((target_dates >= train_end) & (target_dates < validation_end))[0]
    test_idx = np.where(target_dates >= validation_end)[0]
    if not len(train_idx) or not len(validation_idx) or not len(test_idx):
        print("Chronological partition produced an empty split.", file=sys.stderr)
        return 1

    categories = Pipeline([
        ("fill", SimpleImputer(strategy="most_frequent")),
        ("encode", OneHotEncoder(handle_unknown="ignore")),
    ])
    numbers = Pipeline([
        ("fill", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    preprocessor = ColumnTransformer([
        ("categories", categories, CATEGORICAL),
        ("numbers", numbers, NUMERIC),
    ])
    sequence_indices = np.asarray(sequence_indices, dtype=np.int32)
    train_rows = np.unique(sequence_indices[train_idx].reshape(-1))
    preprocessor.fit(frame.loc[train_rows, FEATURES])
    transformed_rows = preprocessor.transform(frame[FEATURES])
    if hasattr(transformed_rows, "toarray"):
        transformed_rows = transformed_rows.toarray()
    transformed = transformed_rows[sequence_indices].astype("float32")
    encoder = LabelEncoder()
    encoder.fit(labels[train_idx])
    y_train = keras.utils.to_categorical(encoder.transform(labels[train_idx]), num_classes=len(encoder.classes_))
    y_validation = keras.utils.to_categorical(encoder.transform(labels[validation_idx]), num_classes=len(encoder.classes_))
    y_test = keras.utils.to_categorical(encoder.transform(labels[test_idx]), num_classes=len(encoder.classes_))
    weights = compute_class_weight(class_weight="balanced", classes=encoder.classes_, y=labels[train_idx])
    class_weights = {i: float(value) for i, value in enumerate(weights)}

    model = keras.Sequential([
        layers.Input(shape=(SEQ_LEN, transformed.shape[2])),
        layers.LSTM(64, dropout=0.2, recurrent_dropout=0.0),
        layers.Dense(32, activation="relu"),
        layers.Dropout(0.2),
        layers.Dense(len(encoder.classes_), activation="softmax"),
    ])
    model.compile(optimizer=keras.optimizers.Adam(learning_rate=0.001),
                  loss="categorical_crossentropy", metrics=["accuracy"])
    callbacks = [
        keras.callbacks.EarlyStopping(monitor="val_loss", patience=6, restore_best_weights=True),
        keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=3, min_lr=0.00005),
    ]
    history = model.fit(
        transformed[train_idx], y_train,
        validation_data=(transformed[validation_idx], y_validation),
        epochs=40, batch_size=64, class_weight=class_weights, callbacks=callbacks, verbose=2,
    )
    probabilities = model.predict(transformed[test_idx], verbose=0)
    predictions = encoder.inverse_transform(probabilities.argmax(axis=1))
    y_true = labels[test_idx]
    majority = pd.Series(labels[train_idx]).value_counts().idxmax()
    metrics = {
        "dataset_archive": ARCHIVE.name,
        "source_type": "historical_dataset",
        "prediction_task": "classify the next observed time block on the same road from the preceding six contiguous observations",
        "sequence_length": SEQ_LEN,
        "features": FEATURES,
        "split_method": "chronological by target date: 70% train, 15% validation, 15% test",
        "train_target_date_range": [str(min(target_dates[train_idx])), str(max(target_dates[train_idx]))],
        "validation_target_date_range": [str(min(target_dates[validation_idx])), str(max(target_dates[validation_idx]))],
        "test_target_date_range": [str(min(target_dates[test_idx])), str(max(target_dates[test_idx]))],
        "record_counts": {"train": int(len(train_idx)), "validation": int(len(validation_idx)), "test": int(len(test_idx))},
        "class_counts": pd.Series(labels).value_counts().to_dict(),
        "class_weights": {str(encoder.classes_[i]): class_weights[i] for i in range(len(class_weights))},
        "majority_class_baseline": majority,
        "baseline_test_accuracy": float(accuracy_score(y_true, [majority]*len(y_true))),
        "test_accuracy": float(accuracy_score(y_true, predictions)),
        "test_macro_f1": float(f1_score(y_true, predictions, average="macro", zero_division=0)),
        "test_report": classification_report(y_true, predictions, labels=list(encoder.classes_), output_dict=True, zero_division=0),
        "test_confusion_matrix": confusion_matrix(y_true, predictions, labels=list(encoder.classes_)).tolist(),
        "class_order": list(encoder.classes_),
        "epochs_run": len(history.history["loss"]),
        "caveat": "Historical labels are heavily imbalanced and not independently verified against sensors. Time blocks have unequal durations. These are offline research metrics, not live deployment performance.",
    }
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.save(MODEL_PATH)
    joblib.dump({"preprocessor": preprocessor, "label_encoder": encoder, "features": FEATURES, "categorical": CATEGORICAL, "numeric": NUMERIC, "sequence_length": SEQ_LEN}, PREPROCESS_PATH)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps({
        "model": str(MODEL_PATH), "preprocessing": str(PREPROCESS_PATH),
        "metrics": str(METRICS_PATH), "records": metrics["record_counts"],
        "baseline_test_accuracy": metrics["baseline_test_accuracy"],
        "test_accuracy": metrics["test_accuracy"], "test_macro_f1": metrics["test_macro_f1"],
        "epochs_run": metrics["epochs_run"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
