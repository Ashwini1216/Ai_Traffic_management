"""Training-only block scale estimates for the project's derived daily cycle.

Projection is permitted only when an actual earlier observation from the target date
exists. A forecast made yesterday cannot obtain today's factor from later records.
"""
from __future__ import annotations
import math
import numpy as np
import core

NUMERIC = ('traffic_volume', 'average_speed', 'travel_time_index')


def fit_scales(training_frame):
    values = {column: {block: [] for block in core.TIME_BLOCKS} for column in NUMERIC}
    for _, group in training_frame.groupby(['segment_id', 'date']):
        if set(group.time_block) != set(core.TIME_BLOCKS) or len(group) != len(core.TIME_BLOCKS):
            continue
        for column in NUMERIC:
            raw = group[column].to_numpy(dtype=float)
            if not np.isfinite(raw).all() or raw.mean() <= 0:
                continue
            for block, value in zip(group.time_block, raw / raw.mean()):
                values[column][block].append(float(value))
    if any(len(values[column][block]) < 10 for column in NUMERIC for block in core.TIME_BLOCKS):
        raise ValueError('Cycle estimation needs at least ten complete training road-days for each numeric field.')
    return {**{column: {block: float(np.median(numbers)) for block, numbers in blocks.items()}
            for column, blocks in values.items()}, '_roads': sorted(training_frame.segment_id.unique().tolist())}


def projected_values(observed_rows, target_slot, scales):
    target_day = target_slot // len(core.TIME_BLOCKS)
    target_block = core.TIME_BLOCKS[target_slot % len(core.TIME_BLOCKS)]
    anchors = [r for r in observed_rows if r['slot'] < target_slot
               and r['slot'] // len(core.TIME_BLOCKS) == target_day]
    if not anchors:
        return None
    result = {}
    for column in NUMERIC:
        candidates = [float(r[column]) / scales[column][r['time_block']] * scales[column][target_block]
                      for r in anchors if r.get(column) is not None and math.isfinite(float(r[column]))]
        if candidates:
            result[column] = float(np.median(candidates))
    return result or None


def feature_row(observed_rows, target_slot, scales):
    projected = projected_values(observed_rows, target_slot, scales)
    target_day = target_slot // len(core.TIME_BLOCKS)
    anchors = {r['time_block']: r for r in observed_rows if r['slot'] < target_slot
               and r['slot'] // len(core.TIME_BLOCKS) == target_day}
    result = {'cycle_same_day_anchor': float(projected is not None),
              **{f'cycle_{column}': (projected or {}).get(column, math.nan) for column in NUMERIC}}
    for block in core.TIME_BLOCKS:
        for label in ('Low', 'Medium', 'High'):
            result[f'cycle_seen_{block}_{label}'] = float(anchors.get(block, {}).get('label') == label)
    for lag in range(1, 7):
        previous = observed_rows[-lag] if len(observed_rows) >= lag else {}
        for label in ('Low', 'Medium', 'High'):
            result[f'cycle_lag{lag}_label_{label}'] = float(previous.get('label') == label)
        for column in NUMERIC:
            result[f'cycle_lag{lag}_{column}'] = previous.get(column, math.nan)
    for road in scales.get('_roads', []):
        result[f'cycle_road_{road}'] = float(observed_rows[-1].get('segment_id') == road)
    return result


def columns(scales=None):
    return ['cycle_same_day_anchor', *[f'cycle_{column}' for column in NUMERIC],
            *[f'cycle_seen_{block}_{label}' for block in core.TIME_BLOCKS for label in ('Low', 'Medium', 'High')],
            *[f'cycle_lag{lag}_{name}' for lag in range(1, 7) for name in
              ('label_Low', 'label_Medium', 'label_High', *NUMERIC)],
            *[f'cycle_road_{road}' for road in (scales or {}).get('_roads', [])]]
