"""Hybrid LSTM -> Random Forest congestion model shared by training and inference.

1. The LSTM reads the last SEQ_LEN consecutive time blocks of a road and forecasts the
   next block's traffic volume, average speed, and travel time index.
2. The Random Forest classifies the next block as Low / Medium / High from the LSTM
   forecast, the latest observed conditions, and calendar features of the target block.
Forecasts further ahead are made recursively, feeding each forecast back as an input.
Weather at the target time is never an input, so later observations cannot leak in.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import core
import store
from core import TIME_BLOCKS

FEATURE_VERSION = 3
SEQ_LEN = 6
MAX_HORIZON = 3
NUMERIC = ["traffic_volume", "average_speed", "travel_time_index"]
STEP_NUMERIC = NUMERIC + ["incident_reports"]
# Volumes span two orders of magnitude (night to evening peak), so they are modelled on a
# log scale; otherwise low night-time volumes, where class boundaries sit, are poorly resolved.
LOG_SCALED = {"traffic_volume"}
ROAD_TYPES = ["junction", "arterial", "highway", "elevated", "local", "unknown"]
CLIP_FLAG = "clip_vehicle_count_is_not_calibrated_block_volume"


def wet_flag(label: str | None, precipitation: float | None) -> float:
    if precipitation is not None and not (isinstance(precipitation, float) and math.isnan(precipitation)) and precipitation >= 0.1:
        return 1.0
    if isinstance(label, str) and label.strip():
        return 1.0 if any(word in label.casefold() for word in ("rain", "drizzle", "shower", "storm", "thunder")) else 0.0
    return math.nan


def roadwork_flag(text: str | None) -> float:
    if not isinstance(text, str) or not text.strip():
        return math.nan
    value = text.strip().casefold()
    if value in ("yes", "y", "true", "1", "ongoing", "active"):
        return 1.0
    if value in ("no", "n", "false", "0", "none"):
        return 0.0
    return math.nan


def ordered_classes(labels) -> list[str]:
    found = sorted({str(label) for label in labels})
    preferred = ["Low", "Medium", "High"]
    return preferred if set(found) == set(preferred) else found


def load_history() -> pd.DataFrame:
    """Block-level observations usable for learning: the historical dataset plus records an
    authority has verified. Interval camera counts and uncalibrated clip counts are left out
    because their units differ from block-level volumes."""
    rows = store.rows("""SELECT o.segment_id,o.area,o.road,o.observed_date,o.time_block,o.observed_start,o.observed_end,
        o.traffic_volume,o.average_speed,o.travel_time_index,o.incident_reports,o.congestion_level,o.weather,o.roadwork,
        o.precipitation_mm,o.source_type,o.quality_status,o.quality_flags_json,o.ingested_at,s.road_type,s.zone_id
      FROM traffic_observations o LEFT JOIN road_segments s ON s.segment_id=o.segment_id
      WHERE o.quality_status<>'rejected' AND (o.source_type='historical_dataset' OR o.quality_status='verified')""")
    kept = []
    for row in rows:
        if row["time_block"] not in TIME_BLOCKS or not row["segment_id"] or CLIP_FLAG in (row["quality_flags_json"] or ""):
            continue
        day = date.fromisoformat(row["observed_date"])
        start, end = core.block_bounds(day, row["time_block"])
        if row["observed_start"] and (row["observed_start"] != start.isoformat() or row["observed_end"] != end.isoformat()):
            continue  # an interval measurement, not a block-level record
        kept.append({**row, "date": day})
    frame = pd.DataFrame(kept)
    if frame.empty:
        return frame
    # One record per road and block: verified first, then the most recently ingested.
    frame["trust"] = (frame["quality_status"] == "verified").astype(int)
    frame = frame.sort_values(["trust", "ingested_at"]).drop_duplicates(["segment_id", "date", "time_block"], keep="last")
    frame["block_index"] = frame["time_block"].map(TIME_BLOCKS.index)
    frame["slot"] = frame["date"].map(date.toordinal) * len(TIME_BLOCKS) + frame["block_index"]
    frame["wet"] = [wet_flag(w, p) for w, p in zip(frame["weather"], frame["precipitation_mm"])]
    frame["roadwork_flag"] = [roadwork_flag(r) for r in frame["roadwork"]]
    frame["road_type"] = frame["road_type"].fillna("unknown")
    for column in STEP_NUMERIC:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    frame["label"] = frame["congestion_level"].where(frame["congestion_level"].notna(), None)
    return frame.sort_values(["segment_id", "slot"]).reset_index(drop=True)


def slot_of(day: date, block: str) -> int:
    return day.toordinal() * len(TIME_BLOCKS) + TIME_BLOCKS.index(block)


def slot_date_block(slot: int) -> tuple[date, str]:
    return date.fromordinal(slot // len(TIME_BLOCKS)), TIME_BLOCKS[slot % len(TIME_BLOCKS)]


@dataclass
class Window:
    segment_id: str
    rows: list[dict]            # SEQ_LEN consecutive block records, oldest first
    targets: dict[int, dict]    # horizon -> observed record at that horizon (when it exists)


def build_windows(frame: pd.DataFrame, max_horizon: int = MAX_HORIZON) -> list[Window]:
    windows: list[Window] = []
    if frame.empty:
        return windows
    for segment_id, group in frame.groupby("segment_id", sort=False):
        records = group.to_dict("records")
        by_slot = {record["slot"]: record for record in records}
        for end in range(SEQ_LEN - 1, len(records)):
            start = end - SEQ_LEN + 1
            if records[end]["slot"] - records[start]["slot"] != SEQ_LEN - 1:
                continue  # a gap inside the window
            targets = {h: by_slot[records[end]["slot"] + h] for h in range(1, max_horizon + 1)
                       if records[end]["slot"] + h in by_slot}
            windows.append(Window(segment_id, records[start:end + 1], targets))
    return windows


class StepScaler:
    """Standardises the numeric inputs; missing values become 0 with a missing-value flag."""

    def __init__(self):
        self.mean: dict[str, float] = {}
        self.std: dict[str, float] = {}

    def fit(self, frame: pd.DataFrame) -> "StepScaler":
        for column in STEP_NUMERIC:
            values = frame[column].dropna()
            if column in LOG_SCALED:
                values = np.log1p(values.clip(lower=0))
            self.mean[column] = float(values.mean()) if len(values) else 0.0
            std = float(values.std()) if len(values) > 1 else 1.0
            self.std[column] = std if std > 1e-9 else 1.0
        return self

    def z(self, column: str, value) -> float:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return math.nan
        value = math.log1p(max(0.0, float(value))) if column in LOG_SCALED else float(value)
        return (value - self.mean[column]) / self.std[column]

    def unz(self, column: str, value: float) -> float:
        value = float(value) * self.std[column] + self.mean[column]
        return math.expm1(min(value, 20.0)) if column in LOG_SCALED else value

    def to_dict(self) -> dict:
        return {"mean": self.mean, "std": self.std}

    @classmethod
    def from_dict(cls, data: dict) -> "StepScaler":
        scaler = cls()
        scaler.mean, scaler.std = dict(data["mean"]), dict(data["std"])
        return scaler


STEP_FEATURES = ([f"z_{c}" for c in STEP_NUMERIC] + [f"missing_{c}" for c in STEP_NUMERIC]
                 + ["wet", "wet_missing", "roadwork", "roadwork_missing"]
                 + [f"block_{b}" for b in TIME_BLOCKS] + ["weekend"])


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def step_vector(record: dict, scaler: StepScaler) -> list[float]:
    values = [scaler.z(c, record.get(c)) for c in STEP_NUMERIC]
    vector = [0.0 if math.isnan(v) else v for v in values] + [1.0 if math.isnan(v) else 0.0 for v in values]
    wet, roadwork = record.get("wet"), record.get("roadwork_flag")
    vector += [0.0 if _is_missing(wet) else float(wet), 1.0 if _is_missing(wet) else 0.0,
               0.0 if _is_missing(roadwork) else float(roadwork), 1.0 if _is_missing(roadwork) else 0.0]
    vector += [1.0 if record["time_block"] == b else 0.0 for b in TIME_BLOCKS]
    vector.append(1.0 if record["date"].weekday() >= 5 else 0.0)
    return vector


def sequence_matrix(windows_rows: list[list[dict]], scaler: StepScaler) -> np.ndarray:
    return np.asarray([[step_vector(r, scaler) for r in rows] for rows in windows_rows], dtype="float32")


def lstm_targets(targets: list[dict], scaler: StepScaler) -> np.ndarray:
    return np.asarray([[scaler.z(c, t.get(c)) for c in NUMERIC] for t in targets], dtype="float32")


def rf_columns(classes: list[str], with_lstm: bool = True) -> list[str]:
    columns = (["lstm_traffic_volume", "lstm_average_speed", "lstm_travel_time_index"] if with_lstm else [])
    columns += ["last_traffic_volume", "last_average_speed", "last_travel_time_index", "last_incident_reports",
                "mean_traffic_volume", "mean_average_speed", "mean_travel_time_index", "last_wet", "last_roadwork"]
    columns += [f"last_label_{c}" for c in classes] + [f"share_label_{c}" for c in classes]
    columns += [f"target_block_{b}" for b in TIME_BLOCKS] + ["target_dow", "target_weekend", "target_month"]
    columns += [f"road_type_{t}" for t in ROAD_TYPES]
    columns += [f"profile_{c}" for c in classes] + ["profile_records"]
    return columns


def profile_features(entry: dict | None, target_slot: int, classes: list[str]) -> dict:
    """The road's historical class distribution for the target block and day type
    (missing for a road without history, so the model also works for new roads)."""
    import patterns

    if not entry or not sum(entry["all"].values()):
        return {**{f"profile_{c}": math.nan for c in classes}, "profile_records": 0.0}
    day, block = slot_date_block(target_slot)
    probabilities, records, _ = patterns.pattern_distribution(entry, block, patterns.day_type(day), classes)
    return {**{f"profile_{c}": probabilities[c] for c in classes}, "profile_records": float(records)}


def rf_feature_row(rows: list[dict], lstm_forecast: dict | None, target_slot: int, road_type: str,
                   classes: list[str], profile_entry: dict | None = None) -> dict:
    last = rows[-1]
    target_day, target_block = slot_date_block(target_slot)
    labels = [r.get("label") for r in rows]
    known = [label for label in labels if label]

    def mean(column):
        values = [r.get(column) for r in rows if not _is_missing(r.get(column))]
        return float(np.mean(values)) if values else math.nan

    row = {}
    if lstm_forecast is not None:
        row.update({f"lstm_{c}": lstm_forecast[c] for c in NUMERIC})
    row.update({f"last_{c}": (math.nan if _is_missing(last.get(c)) else float(last[c])) for c in STEP_NUMERIC})
    row.update({f"mean_{c}": mean(c) for c in NUMERIC})
    row["last_wet"] = last.get("wet", math.nan)
    row["last_roadwork"] = last.get("roadwork_flag", math.nan)
    row.update({f"last_label_{c}": 1.0 if last.get("label") == c else 0.0 for c in classes})
    row.update({f"share_label_{c}": (known.count(c) / len(known) if known else math.nan) for c in classes})
    row.update({f"target_block_{b}": 1.0 if target_block == b else 0.0 for b in TIME_BLOCKS})
    row["target_dow"] = float(target_day.weekday())
    row["target_weekend"] = 1.0 if target_day.weekday() >= 5 else 0.0
    row["target_month"] = float(target_day.month)
    row.update({f"road_type_{t}": 1.0 if road_type == t else 0.0 for t in ROAD_TYPES})
    row.update(profile_features(profile_entry, target_slot, classes))
    return row


def masked_mse(y_true, y_pred):
    """Mean squared error over the targets that were actually observed."""
    import tensorflow as tf

    mask = tf.math.is_finite(y_true)
    diff = tf.where(mask, tf.where(mask, y_true, tf.zeros_like(y_true)) - y_pred, tf.zeros_like(y_pred))
    return tf.reduce_sum(tf.square(diff)) / tf.maximum(tf.reduce_sum(tf.cast(mask, y_pred.dtype)), 1.0)


def build_lstm(n_features: int):
    from tensorflow import keras
    from tensorflow.keras import layers

    inputs = keras.Input(shape=(SEQ_LEN, n_features))
    x = layers.LSTM(48)(inputs)
    x = layers.Dense(32, activation="relu")(x)
    outputs = layers.Dense(len(NUMERIC))(x)
    model = keras.Model(inputs, outputs)
    model.compile(optimizer=keras.optimizers.Adam(learning_rate=0.002), loss=masked_mse)
    return model


def fit_lstm(windows: list[Window], scaler: StepScaler, epochs: int = 30, seed: int = 42):
    """Train the LSTM on windows whose next block has at least one observed numeric value."""
    from tensorflow import keras

    keras.utils.set_random_seed(seed)
    usable = [w for w in windows if 1 in w.targets and any(not _is_missing(w.targets[1].get(c)) for c in NUMERIC)]
    usable.sort(key=lambda w: w.rows[-1]["slot"])
    x = sequence_matrix([w.rows for w in usable], scaler)
    y = lstm_targets([w.targets[1] for w in usable], scaler)
    split = max(1, int(len(usable) * 0.85))  # the latest 15% (by time) decides when to stop
    model = build_lstm(x.shape[2])
    callbacks = [keras.callbacks.EarlyStopping(monitor="val_loss", patience=4, restore_best_weights=True)]
    history = model.fit(x[:split], y[:split], validation_data=(x[split:], y[split:]) if split < len(x) else None,
                        epochs=epochs, batch_size=256, verbose=0, callbacks=callbacks if split < len(x) else None)
    return model, len(history.history["loss"])


def lstm_forecast(model, rows_list: list[list[dict]], scaler: StepScaler) -> list[dict]:
    """Next-block forecast in original units for each window."""
    if not rows_list:
        return []
    predictions = model(sequence_matrix(rows_list, scaler), training=False).numpy()
    out = []
    for values in predictions:
        forecast = {c: scaler.unz(c, values[i]) for i, c in enumerate(NUMERIC)}
        forecast["traffic_volume"] = max(0.0, forecast["traffic_volume"])
        forecast["average_speed"] = min(250.0, max(0.0, forecast["average_speed"]))
        forecast["travel_time_index"] = max(0.5, forecast["travel_time_index"])
        out.append(forecast)
    return out


def tune_decision_weights(probabilities: np.ndarray, truth: list[str], classes: list[str]) -> dict[str, float]:
    """Per-class weights on the predicted probabilities that maximise macro-F1 on validation
    data. Rare classes otherwise almost never win the argmax. The most common class keeps 1."""
    from itertools import product

    from sklearn.metrics import f1_score

    if probabilities.size == 0:
        return {c: 1.0 for c in classes}
    common = max(classes, key=truth.count)
    others = [c for c in classes if c != common]
    grid = [0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
    best, best_key = {c: 1.0 for c in classes}, None
    for combo in product(grid, repeat=len(others)):
        weights = {common: 1.0, **dict(zip(others, combo))}
        vector = np.asarray([weights[c] for c in classes])
        guess = [classes[i] for i in np.argmax(probabilities * vector, axis=1)]
        score = f1_score(truth, guess, labels=classes, average="macro", zero_division=0)
        key = (round(score, 6), -sum(abs(math.log(w)) for w in combo))  # ties: stay closest to 1
        if best_key is None or key > best_key:
            best, best_key = weights, key
    return best


def aligned_probabilities(estimator, features: pd.DataFrame, classes: list[str]) -> np.ndarray:
    """predict_proba columns re-ordered to `classes` (classes unseen in training get 0)."""
    raw = estimator.predict_proba(features)
    known = list(estimator.classes_)
    return np.asarray([[row[known.index(c)] if c in known else 0.0 for c in classes] for row in raw])


def apply_weights(probabilities: np.ndarray, classes: list[str], weights: dict[str, float]) -> np.ndarray:
    vector = np.asarray([weights.get(c, 1.0) for c in classes])
    weighted = probabilities * vector
    totals = weighted.sum(axis=1, keepdims=True)
    return np.divide(weighted, totals, out=np.zeros_like(weighted), where=totals > 0)


class HybridModel:
    """A trained LSTM + Random Forest pair with everything needed to reproduce its inputs."""

    def __init__(self, lstm, rf, scaler: StepScaler, classes: list[str], columns: list[str], version: str = "",
                 weights: dict[str, float] | None = None):
        self.lstm, self.rf, self.scaler, self.classes, self.columns, self.version = lstm, rf, scaler, classes, columns, version
        self.weights = weights or {c: 1.0 for c in classes}

    def class_probabilities(self, features: pd.DataFrame) -> np.ndarray:
        """Random Forest probabilities in self.classes order (before decision weights)."""
        return aligned_probabilities(self.rf, features, self.classes)

    def save(self, directory: Path, extra: dict | None = None) -> None:
        import joblib

        directory.mkdir(parents=True, exist_ok=True)
        self.lstm.save(directory / "lstm.keras")
        joblib.dump({"rf": self.rf, "classes": self.classes, "columns": self.columns, "scaler": self.scaler.to_dict(),
                     "weights": self.weights, "feature_version": FEATURE_VERSION, "seq_len": SEQ_LEN, "numeric": NUMERIC},
                    directory / "rf_bundle.joblib")
        (directory / "manifest.json").write_text(json.dumps({"version": self.version, "feature_version": FEATURE_VERSION,
                                                             "decision_weights": self.weights, **(extra or {})},
                                                            indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path, version: str = "") -> "HybridModel":
        import joblib
        from tensorflow import keras

        bundle = joblib.load(directory / "rf_bundle.joblib")
        if bundle.get("feature_version") != FEATURE_VERSION:
            raise ValueError("The model was trained with a different feature definition; retrain it.")
        lstm = keras.models.load_model(directory / "lstm.keras", compile=False)
        return cls(lstm, bundle["rf"], StepScaler.from_dict(bundle["scaler"]), bundle["classes"], bundle["columns"], version,
                   bundle.get("weights"))

    def predict(self, rows: list[dict], start_slot: int, road_type: str, horizons: int, profile_entry: dict | None = None) -> list[dict]:
        """Forecast `horizons` blocks after the window, feeding forecasts back recursively."""
        return self.predict_many([rows], [start_slot], [road_type], horizons, [profile_entry])[0]

    def predict_many(self, rows_list: list[list[dict]], start_slots: list[int], road_types: list[str], horizons: int,
                     profile_entries: list[dict | None] | None = None) -> list[list[dict]]:
        """Batch version of predict: one list of horizon forecasts per window."""
        sequences = [[dict(r) for r in rows[-SEQ_LEN:]] for rows in rows_list]
        entries = profile_entries or [None] * len(sequences)
        results: list[list[dict]] = [[] for _ in sequences]
        for h in range(1, horizons + 1):
            forecasts = lstm_forecast(self.lstm, [s[-SEQ_LEN:] for s in sequences], self.scaler)
            target_slots = [slot + h for slot in start_slots]
            features = pd.DataFrame([rf_feature_row(s[-SEQ_LEN:], f, t, rt, self.classes, e)
                                     for s, f, t, rt, e in zip(sequences, forecasts, target_slots, road_types, entries)],
                                    columns=self.columns)
            # Decision weights (tuned on validation data) let rare classes win when warranted.
            probabilities = apply_weights(self.class_probabilities(features), self.classes, self.weights)
            for i, (sequence, forecast, target_slot) in enumerate(zip(sequences, forecasts, target_slots)):
                probs = {c: float(probabilities[i][j]) for j, c in enumerate(self.classes)}
                predicted = max(probs, key=probs.get)
                results[i].append({"horizon": h, "slot": target_slot, "probabilities": probs, "predicted_class": predicted,
                                   "predicted_values": {k: round(v, 3) for k, v in forecast.items()}})
                day, block = slot_date_block(target_slot)
                last = sequence[-1]
                sequence.append({**{c: forecast.get(c, last.get(c)) for c in NUMERIC}, "incident_reports": last.get("incident_reports"),
                                 "wet": last.get("wet"), "roadwork_flag": last.get("roadwork_flag"), "label": predicted,
                                 "time_block": block, "date": day, "slot": target_slot})
        return results
