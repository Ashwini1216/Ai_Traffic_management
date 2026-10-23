"""A small FAKE dataset shaped like the FlowState historical archive, for tests only.

16 roads in 8 areas, 181 days from 1 January 2022, six time blocks a day with a few gaps,
plus deliberately invalid rows that the import must quarantine. Values are generated with a
fixed seed, so every run sees the same data. Never place this file in the real data/ folder.
"""
from __future__ import annotations

import csv
import io
import random
import zipfile
from datetime import date, timedelta
from pathlib import Path

BLOCKS = ["Midnight", "Morning", "Daytime", "Noon", "Evening", "Night"]
PEAK = {"Midnight": 0.2, "Morning": 0.85, "Daytime": 0.6, "Noon": 0.65, "Evening": 0.95, "Night": 0.45}
AREAS = {
    "Indiranagar": ["100 Feet Road", "CMH Road"],
    "Whitefield": ["Marathahalli Bridge", "ITPL Main Road"],
    "Koramangala": ["Sony World Junction", "Sarjapur Road"],
    "M.G. Road": ["Trinity Circle", "Anil Kumble Circle"],
    "Jayanagar": ["Jayanagar 4th Block", "South End Circle"],
    "Hebbal": ["Hebbal Flyover", "Ballari Road"],
    "Yeshwanthpur": ["Yeshwanthpur Circle", "Tumkur Road"],
    "Electronic City": ["Silk Board Junction", "Hosur Road"],
}
HEADER = ["Date", "Area Name", "Road/Intersection Name", "Traffic Volume", "Average Speed",
          "Travel Time Index", "TrafficSituation", "Weather Conditions",
          "Roadwork and Construction Activity", "Incident Reports", "TimeBlock"]
START = date(2022, 1, 1)
DAYS = 181
ROAD_COUNT = sum(len(roads) for roads in AREAS.values())
# Each invalid row breaks exactly one rule; the import must keep it out of the history.
INVALID_ROWS = [
    ["", "Indiranagar", "100 Feet Road", "1000", "30", "1.5", "Low", "Clear", "No", "0", "Morning"],
    ["2022-02-30", "Indiranagar", "100 Feet Road", "1000", "30", "1.5", "Low", "Clear", "No", "0", "Morning"],
    ["2022-03-01", "Indiranagar", "100 Feet Road", "1000", "30", "1.5", "Low", "Clear", "No", "0", "Lunch"],
    ["2022-03-01", "", "100 Feet Road", "1000", "30", "1.5", "Low", "Clear", "No", "0", "Morning"],
    ["2022-03-02", "Indiranagar", "100 Feet Road", "-5", "30", "1.5", "Low", "Clear", "No", "0", "Morning"],
    ["2022-03-02", "Indiranagar", "100 Feet Road", "many", "30", "1.5", "Low", "Clear", "No", "0", "Noon"],
    ["2022-03-03", "Indiranagar", "100 Feet Road", "1000", "300", "1.5", "Low", "Clear", "No", "0", "Morning"],
]


def valid_rows() -> list[list[str]]:
    rng = random.Random(7)
    rows = []
    for area, roads in AREAS.items():
        for road in roads:
            base = rng.uniform(900, 2600)
            for offset in range(DAYS):
                day = START + timedelta(days=offset)
                weekend = day.weekday() >= 5
                for block in BLOCKS:
                    if rng.random() < 0.03:  # realistic gaps
                        continue
                    weather = rng.choices(["Clear", "Overcast", "Rain", "Fog"], [0.55, 0.25, 0.15, 0.05])[0]
                    work = "Yes" if rng.random() < 0.1 else "No"
                    load = PEAK[block] * (0.8 if weekend else 1.0) * (1.15 if weather == "Rain" else 1.0)
                    volume = base * load * rng.lognormvariate(0, 0.15)
                    speed = max(5.0, 55 * (1.1 - 0.6 * load) * rng.lognormvariate(0, 0.1))
                    tti = 1 + 1.4 * load * rng.lognormvariate(0, 0.1)
                    score = load + (0.1 if work == "Yes" else 0) + rng.gauss(0, 0.12)
                    label = "Low" if score < 0.5 else ("Medium" if score < 0.8 else "High")
                    rows.append([day.isoformat(), area, road, f"{volume:.0f}", f"{speed:.2f}", f"{tti:.3f}",
                                 label, weather, work, str(int(rng.random() < 0.08)), block])
    return rows


def write_archive(path: Path, invalid: bool = True) -> dict:
    """Write the fake archive; returns how many rows should be imported and rejected."""
    good = valid_rows()
    rejected = INVALID_ROWS + [list(good[0])] if invalid else []  # the copy of row 1 is an exact duplicate
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADER)
    writer.writerows(good + rejected)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("bangalore_traffic_timely_dataset.csv", buffer.getvalue())
    return {"valid": len(good), "rejected": len(rejected), "roads": ROAD_COUNT}
