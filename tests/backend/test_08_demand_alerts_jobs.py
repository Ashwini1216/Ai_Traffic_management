"""Planned demand and privacy, operational alerts, and background jobs."""
from datetime import datetime, timedelta, timezone

from conftest import register


def _plan(client, headers, segments, origin: str, destination: str, hours: float = 3) -> dict:
    a, b = segments[origin], segments[destination]
    response = client.post("/api/trips", json={"origin": origin, "destination": destination, "origin_lat": a["latitude"], "origin_lon": a["longitude"],
                                               "destination_lat": b["latitude"], "destination_lon": b["longitude"], "vehicle_type": "bus",
                                               "departure_at": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_demand_is_aggregated_and_small_groups_stay_hidden(client, authority, segments):
    names = []
    first_name, first = register(client)
    names.append(first_name)
    _plan(client, first, segments, "Jayanagar 4th Block", "Hosur Road")
    demand = client.get("/api/demand?hours=48", headers=authority).json()
    assert demand["segments"] and demand["pcu_factors"]["bus"] > demand["pcu_factors"]["car"]
    corridor = lambda d: [c for c in d["corridors"] if c["planned_trips"] >= 1 and "jayanagar" in c["origin_zone"].lower()]
    assert not corridor(demand), "a single trip must not appear as a corridor"
    for _ in range(2):
        name, headers = register(client)
        names.append(name)
        _plan(client, headers, segments, "Jayanagar 4th Block", "Hosur Road")
    demand = client.get("/api/demand?hours=48", headers=authority).json()
    assert corridor(demand) and corridor(demand)[0]["planned_trips"] >= 3
    text = str(demand)
    assert not any(name in text for name in names), "individual commuters must never appear in demand data"


def test_zone_alerts_are_raised_once_and_can_be_acknowledged(client, authority):
    notice = client.post("/api/incidents/official", json={"category": "road_closure", "description": "Junction closed for repairs",
                                                          "area": "Indiranagar", "road": "CMH Road", "latitude": 12.9784, "longitude": 77.6440,
                                                          "closure": True}, headers=authority).json()
    try:
        first = client.post("/api/jobs/zone_alerts/run", headers=authority).json()
        second = client.post("/api/jobs/zone_alerts/run", headers=authority).json()
        assert first["status"] == "ok" and second["details"]["alerts_created"] == 0, (first, second)
        alerts = client.get("/api/alerts?status=active", headers=authority).json()["alerts"]
        closure = next(a for a in alerts if a["kind"] == "closure_in_zone" and "CMH Road" in a["message"])
        assert closure["recommendation"]
        assert client.patch(f"/api/alerts/{closure['alert_id']}", json={"status": "acknowledged"}, headers=authority).status_code == 200
        assert client.patch(f"/api/alerts/{closure['alert_id']}", json={"status": "resolved"}, headers=authority).status_code == 200
    finally:
        client.patch(f"/api/incidents/{notice['incident_id']}", json={"status": "resolved"}, headers=authority)


def test_every_background_job_is_listed_and_unknown_jobs_are_refused(client, authority):
    jobs = {j["job"] for j in client.get("/api/jobs", headers=authority).json()["jobs"]}
    assert jobs == {"collect_weather", "reassess_trips", "trip_reminders", "expire_stale_reports", "camera_collection",
                    "zone_alerts", "data_quality", "retention", "sync_segments"}
    assert client.post("/api/jobs/no_such_job/run", headers=authority).status_code == 404


def test_weather_and_retention_jobs_run(client, authority):
    weather = client.post("/api/jobs/collect_weather/run", headers=authority).json()
    assert weather["status"] == "ok", weather
    retention = client.post("/api/jobs/retention/run", headers=authority).json()
    assert retention["status"] == "ok", retention
