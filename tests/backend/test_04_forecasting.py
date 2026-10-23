"""Forecasts: method fallbacks, validation of the target time, signals, and zones."""
from datetime import datetime, timedelta, timezone

from conftest import block_target


def test_roads_without_recent_data_use_their_historical_pattern(client, citizen, segments):
    road = segments["Hebbal Flyover"]
    forecast = client.get("/api/forecast", params={"segment_id": road["segment_id"], "target": block_target(2)}, headers=citizen)
    assert forecast.status_code == 200, forecast.text
    body = forecast.json()
    assert body["method"] == "historical_pattern" and body["confidence"]["level"] in ("low", "medium", "high")
    assert abs(sum(body["probabilities"].values()) - 1) < 1e-3 and body["predicted_class"] in body["probabilities"]
    assert body["prediction_id"], "every forecast is recorded so it can be evaluated later"


def test_forecast_targets_must_be_now_or_later(client, citizen, segments):
    road = segments["Hebbal Flyover"]
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert client.get("/api/forecast", params={"segment_id": road["segment_id"], "target": past}, headers=citizen).status_code == 422
    assert client.get("/api/forecast", params={"segment_id": road["segment_id"], "target": "2026-01-01T09:00"}, headers=citizen).status_code == 422
    assert client.get("/api/forecast", params={"segment_id": road["segment_id"]}).status_code == 401
    assert client.get("/api/forecast", params={"segment_id": "no-such-road"}, headers=citizen).status_code == 404


def test_new_roads_get_a_clearly_labelled_limited_data_estimate(client, authority, citizen):
    stored = client.post("/api/observations", json={"observed_date": "2022-03-05", "time_block": "Noon", "area": "Domlur",
                                                    "road": "Test Lane", "traffic_volume": 900, "source_name": "test_entry"}, headers=authority)
    assert stored.status_code == 200, stored.text
    forecast = client.get("/api/forecast", params={"area": "Domlur", "road": "Test Lane", "target": block_target(1)}, headers=citizen).json()
    assert forecast["method"] == "cold_start" and forecast["confidence"]["level"] == "low", forecast["method"]


def test_forecasts_carry_the_rain_forecast_as_evidence(client, citizen, segments):
    road = segments["Silk Board Junction"]
    fetched = client.post(f"/api/weather/fetch?latitude={road['latitude']}&longitude={road['longitude']}", headers=citizen)
    assert fetched.status_code == 200, fetched.text
    in_rain = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()   # the fake forecast rains 2-4 hours ahead
    signals = client.get("/api/forecast", params={"segment_id": road["segment_id"], "target": in_rain}, headers=citizen).json()["signals"]
    assert signals["weather"] and signals["weather"]["wet"] is True, signals["weather"]


def test_zone_outlook_covers_every_zone(client, citizen):
    import fixture_dataset

    zones = {z["name"]: z for z in client.get("/api/forecast/zones", headers=citizen).json()["zones"]}
    assert set(fixture_dataset.AREAS) <= set(zones), sorted(zones)
    for name in fixture_dataset.AREAS:
        assert zones[name]["level"] in ("Low", "Medium", "High") and zones[name]["roads"], name
