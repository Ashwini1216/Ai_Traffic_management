"""Data retention: keep trustworthy history for years, raw video only briefly.

* Uploaded CCTV video is deleted after FOOTAGE_RETENTION_DAYS; its analysis is kept.
* Daily per-road summaries are maintained, so long-term patterns survive detail removal.
* Interval camera measurements (finer than an hour) are kept for DETAILED_RETENTION_DAYS.
* Other observations are kept for OBSERVATION_RETENTION_YEARS. The imported historical
  dataset is never deleted: it is a versioned research archive that can be re-imported.
* Read notifications older than 90 days and long-ended journeys are removed.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import PureWindowsPath

import store
from core import DATA_DIR, iso_now

FOOTAGE_RETENTION_DAYS = int(os.getenv("FOOTAGE_RETENTION_DAYS", "30"))
DETAILED_RETENTION_DAYS = int(os.getenv("DETAILED_RETENTION_DAYS", "365"))
OBSERVATION_RETENTION_YEARS = int(os.getenv("OBSERVATION_RETENTION_YEARS", "5"))
NOTIFICATION_RETENTION_DAYS = 90


def refresh_summaries(since: str | None = None) -> int:
    """Rebuild daily summaries for every date on or after `since` (all dates when None)."""
    where, params = ("WHERE segment_id IS NOT NULL AND observed_date>=?", (since,)) if since else ("WHERE segment_id IS NOT NULL", ())
    rows = store.rows(f"""SELECT segment_id,observed_date,traffic_volume,average_speed,travel_time_index,vehicle_count,
      congestion_level,source_type FROM traffic_observations {where}""", params)
    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        groups[(row["segment_id"], row["observed_date"])].append(row)

    def mean(values):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 3) if values else None

    with store.connect() as db:
        for (segment_id, observed_date), items in groups.items():
            values = (len(items), mean(i["traffic_volume"] for i in items), mean(i["average_speed"] for i in items),
                      mean(i["travel_time_index"] for i in items),
                      sum(i["vehicle_count"] or 0 for i in items if i["source_type"] == "camera") or None,
                      json.dumps(Counter(i["congestion_level"] for i in items if i["congestion_level"])),
                      json.dumps(Counter(i["source_type"] for i in items)), iso_now())
            db.execute("""INSERT INTO observation_daily_summaries(segment_id,observed_date,records,mean_volume,mean_speed,
              mean_travel_time_index,camera_vehicles,labels_json,sources_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(segment_id,observed_date) DO UPDATE SET records=excluded.records,mean_volume=excluded.mean_volume,
              mean_speed=excluded.mean_speed,mean_travel_time_index=excluded.mean_travel_time_index,
              camera_vehicles=excluded.camera_vehicles,labels_json=excluded.labels_json,sources_json=excluded.sources_json,
              updated_at=excluded.updated_at""", (segment_id, observed_date, *values))
    return len(groups)


def apply(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    result: dict = {}
    footage_dir = DATA_DIR / "cctv_footage"
    cutoff = (now - timedelta(days=FOOTAGE_RETENTION_DAYS)).isoformat()
    deleted_files = 0
    for row in store.rows("SELECT footage_id,stored_path FROM camera_footage WHERE file_deleted_at IS NULL AND uploaded_at<?", (cutoff,)):
        path = footage_dir / PureWindowsPath(row["stored_path"]).name
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue  # try again on the next run
        with store.connect() as db:
            db.execute("UPDATE camera_footage SET file_deleted_at=? WHERE footage_id=?", (iso_now(), row["footage_id"]))
        deleted_files += 1
    result["footage_files_deleted"] = deleted_files
    has_summaries = store.one("SELECT COUNT(*) AS n FROM observation_daily_summaries")["n"]
    result["summary_days_refreshed"] = refresh_summaries(None if not has_summaries else (now - timedelta(days=3)).date().isoformat())
    with store.connect() as db:
        detailed = db.execute("""DELETE FROM traffic_observations WHERE source_type='camera' AND interval_seconds IS NOT NULL
          AND interval_seconds<3600 AND observed_start<?""", ((now - timedelta(days=DETAILED_RETENTION_DAYS)).isoformat(),))
        old = db.execute("""DELETE FROM traffic_observations WHERE source_type<>'historical_dataset' AND observed_date<?""",
                         ((now - timedelta(days=365 * OBSERVATION_RETENTION_YEARS)).date().isoformat(),))
        notes = db.execute("DELETE FROM notifications WHERE read_at IS NOT NULL AND created_at<?",
                           ((now - timedelta(days=NOTIFICATION_RETENTION_DAYS)).isoformat(),))
        journeys = db.execute("DELETE FROM active_journeys WHERE status<>'active' AND ended_at<?", ((now - timedelta(days=30)).isoformat(),))
    result.update(interval_observations_deleted=detailed.rowcount or 0, old_observations_deleted=old.rowcount or 0,
                  notifications_deleted=notes.rowcount or 0, journeys_deleted=journeys.rowcount or 0,
                  policy={"footage_days": FOOTAGE_RETENTION_DAYS, "interval_measurement_days": DETAILED_RETENTION_DAYS,
                          "observation_years": OBSERVATION_RETENTION_YEARS, "historical_dataset": "kept"})
    return result
