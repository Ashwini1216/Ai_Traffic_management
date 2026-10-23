"""Historical congestion patterns and transfer from similar road segments.

A pattern estimate uses a road's own labelled history for the same time block and day
type. Transfer borrows the patterns of similar roads (same road type, nearby, and with a
similar daily profile) for a road that lacks history; it is only used after validation
has shown that it beats a simple baseline.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date

import pandas as pd

import core
from core import TIME_BLOCKS

ALPHA = 1.0                 # Laplace smoothing for class distributions
MIN_SLOT_RECORDS = 3        # records needed for a (block, day type) cell before falling back
TRANSFER_DONORS = 3
MIN_DONOR_SIMILARITY = 0.4
TRANSFER_MIN_GAIN = 0.02    # transfer must beat the majority baseline macro-F1 by this much


def day_type(day: date) -> str:
    return "weekend" if day.weekday() >= 5 else "weekday"


def build_counts(frame: pd.DataFrame) -> dict[str, dict]:
    """Labelled-record counts per segment by (block, day type), by block, and overall."""
    counts: dict[str, dict] = defaultdict(lambda: {"cell": defaultdict(Counter), "block": defaultdict(Counter), "all": Counter()})
    if frame.empty:
        return counts
    labelled = frame[frame["label"].notna()]
    for segment_id, block, day, label in zip(labelled["segment_id"], labelled["time_block"], labelled["date"], labelled["label"]):
        entry = counts[segment_id]
        entry["cell"][(block, day_type(day))][label] += 1
        entry["block"][block][label] += 1
        entry["all"][label] += 1
    return counts


def without(entry: dict, block: str, kind: str, label: str) -> dict:
    """A copy of a segment's counts with one record removed (leave-one-out features)."""
    cell = Counter(entry["cell"].get((block, kind), Counter()))
    by_block = Counter(entry["block"].get(block, Counter()))
    overall = Counter(entry["all"])
    for counter in (cell, by_block, overall):
        if counter.get(label, 0) > 0:
            counter[label] -= 1
    return {"cell": {**entry["cell"], (block, kind): cell}, "block": {**entry["block"], block: by_block}, "all": overall}


def smoothed(counter: Counter, classes: list[str]) -> dict[str, float]:
    total = sum(counter.get(c, 0) for c in classes) + ALPHA * len(classes)
    return {c: (counter.get(c, 0) + ALPHA) / total for c in classes}


def pattern_distribution(entry: dict, block: str, kind: str, classes: list[str]) -> tuple[dict[str, float], int, str]:
    """(probabilities, records used, which cell) for a segment's own history."""
    cell = entry["cell"].get((block, kind), Counter())
    if sum(cell.values()) >= MIN_SLOT_RECORDS:
        return smoothed(cell, classes), sum(cell.values()), f"{block}, {kind}"
    by_block = entry["block"].get(block, Counter())
    if sum(by_block.values()) >= MIN_SLOT_RECORDS:
        return smoothed(by_block, classes), sum(by_block.values()), f"{block}, all days"
    return smoothed(entry["all"], classes), sum(entry["all"].values()), "all blocks"


def profile(entry: dict | None, classes: list[str]) -> dict[str, float]:
    """Average congestion level (0 = lowest class, 1 = highest) per time block."""
    if not entry or len(classes) < 2:
        return {}
    result = {}
    for block in TIME_BLOCKS:
        counter = entry["block"].get(block)
        if counter and sum(counter.values()):
            result[block] = sum(classes.index(c) * n for c, n in counter.items() if c in classes) / ((len(classes) - 1) * sum(counter.values()))
    return result


def similarity(target: dict, donor: dict, target_profile: dict | None, donor_profile: dict | None) -> float:
    """0..1 similarity from road type, distance, and daily congestion profile."""
    if target["road_type"] == "unknown" or donor["road_type"] == "unknown":
        type_term = 0.5
    else:
        type_term = 1.0 if target["road_type"] == donor["road_type"] else 0.0
    if target.get("latitude") is not None and donor.get("latitude") is not None:
        km = core.haversine_m(target["latitude"], target["longitude"], donor["latitude"], donor["longitude"]) / 1000
        geo_term = math.exp(-km / 5)
    else:
        geo_term = 0.5
    shared = [b for b in TIME_BLOCKS if target_profile and donor_profile and b in target_profile and b in donor_profile]
    if shared:
        pattern_term = 1 - sum(abs(target_profile[b] - donor_profile[b]) for b in shared) / len(shared)
        return 0.35 * type_term + 0.35 * geo_term + 0.30 * pattern_term
    return 0.5 * type_term + 0.5 * geo_term


def transfer_distribution(target: dict, donors: list[dict], counts: dict, block: str, kind: str, classes: list[str],
                          target_entry: dict | None = None) -> tuple[dict[str, float] | None, list[dict]]:
    """Similarity-weighted class distribution from the most similar donor segments."""
    target_profile = profile(target_entry, classes) if target_entry else None
    scored = []
    for donor in donors:
        if donor["segment_id"] == target["segment_id"] or donor["segment_id"] not in counts:
            continue
        score = similarity(target, donor, target_profile, profile(counts[donor["segment_id"]], classes))
        if score >= MIN_DONOR_SIMILARITY:
            scored.append((score, donor))
    scored.sort(key=lambda item: item[0], reverse=True)
    chosen = scored[:TRANSFER_DONORS]
    if not chosen:
        return None, []
    total = sum(score for score, _ in chosen)
    mixed = {c: 0.0 for c in classes}
    used = []
    for score, donor in chosen:
        probs, records, cell = pattern_distribution(counts[donor["segment_id"]], block, kind, classes)
        for c in classes:
            mixed[c] += probs[c] * score / total
        used.append({"segment_id": donor["segment_id"], "area": donor["area"], "road": donor["road"],
                     "similarity": round(score, 3), "records": records})
    return mixed, used
