"""Create a clearly labelled, reproducible synthetic Bengaluru traffic dataset.

Synthetic rows are deliberately written to a separate CSV. They are not
imported into historical observations or model training automatically.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "bangalore_traffic_timely_dataset.zip"
OUTPUT = ROOT / "data" / "synthetic_traffic_dataset.csv"
MANIFEST = ROOT / "data" / "synthetic_traffic_dataset_manifest.json"
BLOCKS = ["Midnight", "Morning", "Daytime", "Noon", "Evening", "Night"]
SEED = 20261021


def main() -> None:
    if not ARCHIVE.exists():
        raise SystemExit(f"Reference dataset copy not found: {ARCHIVE}")
    with zipfile.ZipFile(ARCHIVE) as archive:
        csv_name = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
        with archive.open(csv_name) as raw:
            source = pd.read_csv(raw)

    rng = np.random.default_rng(SEED)
    source["Date"] = pd.to_datetime(source["Date"], errors="coerce")
    source = source.dropna(subset=["Date", "Area Name", "Road/Intersection Name"])
    segments = source[["Area Name", "Road/Intersection Name"]].drop_duplicates().reset_index(drop=True)
    # Add one simulated year after the observed archive; avoid invented future dates.
    dates = pd.date_range("2025-01-01", "2025-12-31", freq="D")
    block_profile = {"Midnight": 0.28, "Morning": 0.79, "Daytime": 0.62,
                     "Noon": 0.69, "Evening": 0.94, "Night": 0.52}
    weather_options = ["Clear", "Cloudy", "Rainy", "Foggy"]
    weather_probabilities = [0.51, 0.28, 0.17, 0.04]
    rows: list[dict] = []
    for segment in segments.itertuples(index=False, name=None):
        area, road = segment
        group = source[(source["Area Name"] == area) & (source["Road/Intersection Name"] == road)]
        volume_median = pd.to_numeric(group["Traffic Volume"], errors="coerce").median()
        speed_median = pd.to_numeric(group["Average Speed"], errors="coerce").median()
        tti_median = pd.to_numeric(group["Travel Time Index"], errors="coerce").median()
        volume_median = float(volume_median) if pd.notna(volume_median) else 900.0
        speed_median = float(speed_median) if pd.notna(speed_median) else 35.0
        tti_median = float(tti_median) if pd.notna(tti_median) else 1.5
        typical_volume = max(80.0, min(volume_median, 5000.0))
        typical_speed = max(8.0, min(speed_median, 90.0))
        typical_tti = max(1.0, min(tti_median, 5.0))
        road_factor = float(rng.uniform(0.72, 1.3))
        for day in dates:
            weekend = day.dayofweek >= 5
            for block in BLOCKS:
                weather = str(rng.choice(weather_options, p=weather_probabilities))
                work = "Yes" if rng.random() < (0.11 if not weekend else 0.055) else "No"
                incident_count = int(rng.poisson(0.12))
                peak = block_profile[block] * (0.79 if weekend else 1.0)
                rain = 1.13 if weather == "Rainy" else 1.0
                event_factor = 1.18 if incident_count else 1.0
                work_factor = 1.12 if work == "Yes" else 1.0
                volume = typical_volume * road_factor * peak * rain * event_factor * work_factor * rng.lognormal(0, 0.16)
                speed = typical_speed * (1.08 - 0.53 * min(1.0, peak)) / (rain * work_factor) * rng.lognormal(0, 0.09)
                speed = float(np.clip(speed, 4.0, 100.0))
                tti = float(np.clip(typical_tti * (1.0 + 0.72 * peak) * rain * work_factor * rng.lognormal(0, 0.13), 0.7, 8.0))
                # A documented synthetic rule creates coherent labels with noise;
                # this is not presented as a sensor-confirmed ground truth.
                score = (0.42 * peak + 0.22 * (1 - speed / 100) + 0.22 * (tti / 5)
                         + (0.07 if weather == "Rainy" else 0)
                         + (0.06 if work == "Yes" else 0)
                         + min(0.12, incident_count * 0.04) + rng.normal(0, 0.075))
                label = "Low" if score < 0.52 else ("Medium" if score < 0.78 else "High")
                rows.append({
                    "Date": day.date().isoformat(), "Area Name": area,
                    "Road/Intersection Name": road, "Weather Conditions": weather,
                    "Roadwork and Construction Activity": work, "TimeBlock": block,
                    "Traffic Volume": round(float(volume), 2), "Average Speed": round(speed, 2),
                    "Travel Time Index": round(tti, 3), "Incident Reports": incident_count,
                    "TrafficSituation": label, "synthetic_record": True,
                    "source_type": "synthetic", "quality_status": "simulated",
                })
    result = pd.DataFrame(rows)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT, index=False)
    manifest = {
        "dataset": OUTPUT.name,
        "source_type": "synthetic",
        "quality_status": "simulated_not_observed",
        "record_count": int(len(result)),
        "area_count": int(result["Area Name"].nunique()),
        "road_segment_count": int(result["Road/Intersection Name"].nunique()),
        "date_range": [str(result["Date"].min()), str(result["Date"].max())],
        "random_seed": SEED,
        "generation_method": "Seeded synthetic scenarios using per-road medians from the copied historical dataset, time-block/weekend patterns, stochastic weather, roadworks, incidents, measurement noise and a documented heuristic label rule.",
        "warning": "Simulated records are not real sensor data, ground truth or evidence of model performance. They are separated from the observed historical dataset and are excluded from production model training by default.",
        "label_counts": {str(key): int(value) for key, value in result["TrafficSituation"].value_counts().items()},
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"dataset": str(OUTPUT), **manifest}, indent=2))


if __name__ == "__main__":
    main()
