"""Historical import, quarantine, and the road and coverage registry."""
from conftest import FIXTURE


def test_import_keeps_only_valid_rows(client):
    import store

    row = store.one("SELECT COUNT(*) AS n FROM traffic_observations WHERE source_type='historical_dataset'")
    assert row["n"] == FIXTURE["valid"]
    assert client.get("/api/summary").json()["road_count"] == FIXTURE["roads"]


def test_invalid_rows_are_quarantined_with_a_reason(client, authority):
    records = client.get("/api/quarantine?limit=200", headers=authority).json()["records"]
    reasons = " | ".join(str(r["reason"]) for r in records if r["source_type"] == "historical_dataset")
    for expected in ("Date is missing", "TimeBlock must be one of", "Area Name is missing", "Traffic Volume is outside",
                     "Traffic Volume is not a number", "Average Speed is outside", "Exact duplicate"):
        assert expected in reasons, expected


def test_registry_lists_every_road_with_its_data_method(segments):
    assert len(segments) == FIXTURE["roads"]
    for row in segments.values():
        assert row["has_map_location"] and row["latitude"] is not None, row["road"]
        assert row["has_traffic_data"] and row["record_count"] > 1000, row["road"]
        assert row["data_method"] == "historical_pattern", (row["road"], row["data_method"])


def test_coverage_report_is_public_and_matches_the_registry(client, segments):
    coverage = client.get("/api/coverage").json()["segments"]
    assert len(coverage) == len(segments)


def test_only_authorities_change_road_locations(client, authority, citizen, segments):
    road = segments["CMH Road"]
    path = f"/api/segments/{road['segment_id']}"
    assert client.patch(path, json={"latitude": 12.979, "longitude": 77.645}).status_code == 401
    assert client.patch(path, json={"latitude": 12.979, "longitude": 77.645}, headers=citizen).status_code == 403
    assert client.patch(path, json={"latitude": 28.6, "longitude": 77.2}, headers=authority).status_code == 422
    updated = client.patch(path, json={"latitude": 12.9785, "longitude": 77.6441}, headers=authority)
    assert updated.status_code == 200, updated.text
    assert abs(updated.json()["latitude"] - 12.9785) < 1e-6


def test_zones_are_derived_from_road_locations(client):
    zones = client.get("/api/zones").json()["zones"]
    assert len(zones) == 8 and all(z["latitude"] is not None and z["radius_m"] > 0 for z in zones)
