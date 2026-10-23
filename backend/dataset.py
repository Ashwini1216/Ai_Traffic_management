"""Validation rules for the historical traffic dataset archive.

The database importer (store.py) and the training scripts share these rules, so
a row that is quarantined at import time is also excluded from model training.
"""
from __future__ import annotations

import csv
import io
import math
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from core import LOCAL_TZ, TIME_BLOCKS, local_today  # noqa: F401  (re-exported for callers)

REQUIRED_COLUMNS = ["Date", "Area Name", "Road/Intersection Name", "TimeBlock"]
# column: (field, minimum, maximum, minimum_is_exclusive) — same limits as API observations.
NUMERIC_COLUMNS = {
    "Traffic Volume": ("traffic_volume", 0, 1_000_000, False),
    "Average Speed": ("average_speed", 0, 250, False),
    "Travel Time Index": ("travel_time_index", 0, 20, True),
    "Incident Reports": ("incident_reports", 0, 10_000, False),
}
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def parse_observed_date(value: str | None) -> date | None:
    text = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def validate_row(row: dict, today: date) -> tuple[dict | None, list[str]]:
    """Return (normalized values, problems). Values are None when any problem exists."""
    problems: list[str] = []
    observed = parse_observed_date(row.get("Date"))
    if observed is None:
        problems.append("Date is missing or is not a valid YYYY-MM-DD date")
    elif observed > today:
        problems.append("Date is in the future")
    block = (row.get("TimeBlock") or "").strip()
    if block not in TIME_BLOCKS:
        problems.append("TimeBlock must be one of: " + ", ".join(TIME_BLOCKS))
    area, road = _text(row.get("Area Name")), _text(row.get("Road/Intersection Name"))
    if not area:
        problems.append("Area Name is missing")
    if not road:
        problems.append("Road/Intersection Name is missing")
    values: dict = {}
    for column, (field, low, high, exclusive) in NUMERIC_COLUMNS.items():
        text = (row.get(column) or "").strip()
        if not text:
            values[field] = None  # missing stays missing; it is never invented
            continue
        try:
            number = float(text)
        except ValueError:
            problems.append(f"{column} is not a number")
            continue
        if not math.isfinite(number) or number < low or number > high or (exclusive and number <= low):
            problems.append(f"{column} is outside the allowed range")
        elif field == "incident_reports" and not number.is_integer():
            problems.append(f"{column} must be a whole number")
        else:
            values[field] = int(number) if field == "incident_reports" else number
    if problems:
        return None, problems
    return {
        "observed_date": observed.isoformat(), "time_block": block, "area": area, "road": road,
        **values,
        "congestion_level": _text(row.get("TrafficSituation")),
        "weather": _text(row.get("Weather Conditions")),
        "roadwork": _text(row.get("Roadwork and Construction Activity")),
    }, []


def read_archive_rows(archive: Path) -> Iterator[dict]:
    """Yield the CSV rows of the first .csv file inside the archive."""
    with zipfile.ZipFile(archive) as bundle:
        name = next((entry for entry in bundle.namelist() if entry.lower().endswith(".csv")), None)
        if name is None:
            raise ValueError(f"No CSV file was found inside {archive.name}.")
        with bundle.open(name) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
            if missing:
                raise ValueError("Dataset is missing required columns: " + ", ".join(missing))
            yield from reader


def validated_rows(archive: Path) -> Iterator[tuple[int, dict, dict | None, list[str]]]:
    """Yield (line number, raw row, clean values or None, problems) for every row.

    Exact duplicates of an earlier valid row are reported as a problem.
    """
    today = local_today()
    seen: set[tuple] = set()
    for line_number, row in enumerate(read_archive_rows(archive), start=2):
        clean, problems = validate_row(row, today)
        if clean is not None:
            fingerprint = tuple(clean.values())
            if fingerprint in seen:
                clean, problems = None, ["Exact duplicate of an earlier row"]
            else:
                seen.add(fingerprint)
        yield line_number, row, clean, problems


def load_training_frame(archive: Path):
    """Return (DataFrame of valid rows using the dataset's column names, rejected row count)."""
    import numpy as np
    import pandas as pd

    records, rejected = [], 0
    for _, _, clean, _ in validated_rows(archive):
        if clean is None:
            rejected += 1
            continue
        records.append({
            "Date": clean["observed_date"], "Area Name": clean["area"],
            "Road/Intersection Name": clean["road"], "TimeBlock": clean["time_block"],
            "Traffic Volume": clean["traffic_volume"], "Average Speed": clean["average_speed"],
            "Travel Time Index": clean["travel_time_index"], "Incident Reports": clean["incident_reports"],
            "TrafficSituation": clean["congestion_level"], "Weather Conditions": clean["weather"],
            "Roadwork and Construction Activity": clean["roadwork"],
        })
    frame = pd.DataFrame.from_records(records, columns=[
        "Date", "Area Name", "Road/Intersection Name", "TimeBlock", "Traffic Volume", "Average Speed",
        "Travel Time Index", "Incident Reports", "TrafficSituation", "Weather Conditions",
        "Roadwork and Construction Activity",
    ])
    frame["Date"] = pd.to_datetime(frame["Date"], format="%Y-%m-%d")
    for column in ("Traffic Volume", "Average Speed", "Travel Time Index", "Incident Reports"):
        frame[column] = pd.to_numeric(frame[column]).astype("float64")
    for column in ("Area Name", "Road/Intersection Name", "TimeBlock", "TrafficSituation",
                   "Weather Conditions", "Roadwork and Construction Activity"):
        frame[column] = frame[column].astype(object).where(frame[column].notna(), np.nan)
    return frame, rejected
