"""Observation ingestion: validation, quarantine, duplicates, and review."""
from datetime import date, datetime, timedelta, timezone


def _observation(**overrides) -> dict:
    body = {"observed_date": "2026-01-05", "time_block": "Morning", "area": "Indiranagar", "road": "100 Feet Road",
            "traffic_volume": 1450, "average_speed": 28.5, "travel_time_index": 1.7, "congestion_level": "Medium",
            "source_name": "test_entry"}
    body.update(overrides)
    return body


def test_valid_observation_is_stored_unverified(client, authority):
    stored = client.post("/api/observations", json=_observation(), headers=authority)
    assert stored.status_code == 200, stored.text
    body = stored.json()
    assert body["quality_status"] == "unverified" and "manual_source_not_independently_verified" in body["quality_flags"]
    pending = client.get("/api/observations?quality_status=unverified&limit=200", headers=authority).json()["observations"]
    assert any(o["id"] == body["id"] for o in pending)


def test_commuters_cannot_submit_observations(client, citizen):
    assert client.post("/api/observations", json=_observation(observed_date="2026-01-06"), headers=citizen).status_code == 403


def test_invalid_observations_are_quarantined(client, authority):
    future = (date.today() + timedelta(days=3)).isoformat()
    for bad in (_observation(observed_date=future), _observation(time_block="Lunch"),
                _observation(observed_date="2026-01-07", latitude=28.61, longitude=77.21),
                _observation(observed_date="2026-01-07", latitude=12.97)):
        rejected = client.post("/api/observations", json=bad, headers=authority)
        assert rejected.status_code == 422, (bad, rejected.text)
        assert rejected.json()["detail"]["quarantine_id"]


def test_duplicates_are_rejected_and_quarantined(client, authority):
    body = _observation(observed_date="2026-01-08")
    assert client.post("/api/observations", json=body, headers=authority).status_code == 200
    duplicate = client.post("/api/observations", json=body, headers=authority)
    assert duplicate.status_code == 409 and duplicate.json()["detail"]["duplicate_of"]


def test_review_moves_an_observation_to_verified(client, authority):
    stored = client.post("/api/observations", json=_observation(observed_date="2026-01-09"), headers=authority).json()
    reviewed = client.patch(f"/api/observations/{stored['id']}", json={"quality_status": "verified", "note": "checked on site"}, headers=authority)
    assert reviewed.status_code == 200, reviewed.text
    verified = client.get("/api/observations?quality_status=verified&limit=200", headers=authority).json()["observations"]
    assert any(o["id"] == stored["id"] for o in verified)


def test_interval_counts_must_be_consistent_with_their_flow(client, authority):
    end = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
    start = end - timedelta(minutes=10)
    base = {"area": "Koramangala", "road": "Sony World Junction", "observed_start": start.isoformat(), "observed_end": end.isoformat(),
            "vehicle_count": 100, "source_name": "test_counter"}
    inconsistent = client.post("/api/observations", json={**base, "flow_per_hour": 9000}, headers=authority)
    assert inconsistent.status_code == 422, inconsistent.text
    stored = client.post("/api/observations", json={**base, "flow_per_hour": 600}, headers=authority)
    assert stored.status_code == 200, stored.text
    assert stored.json()["observed_end"].startswith(end.isoformat()[:16])
