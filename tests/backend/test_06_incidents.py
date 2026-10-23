"""Community reports, duplicates, verification, official notices, and broadcasts."""
from datetime import datetime, timedelta, timezone

POINT = {"latitude": 12.9363, "longitude": 77.6265}   # Sony World Junction


def _public_ids(client) -> set:
    return {i["incident_id"] for i in client.get("/api/incidents/public").json()["incidents"]}


def test_reports_wait_for_review_and_nearby_duplicates_become_confirmations(client, citizen, authority):
    first = client.post("/api/incidents", json={"category": "breakdown", "description": "Bus broken down in the left lane", **POINT}, headers=citizen)
    assert first.status_code == 201 and first.json()["status"] == "reported", first.text
    nearby = {"latitude": POINT["latitude"] + 0.0004, "longitude": POINT["longitude"]}
    second = client.post("/api/incidents", json={"category": "breakdown", "description": "Same bus, still there", **nearby}, headers=citizen)
    assert second.json()["status"] == "duplicate" and second.json()["duplicate_of"] == first.json()["incident_id"]
    listed = {i["incident_id"]: i for i in client.get("/api/incidents?limit=200", headers=authority).json()["incidents"]}
    assert listed[first.json()["incident_id"]]["corroboration_count"] == 1
    assert first.json()["incident_id"] not in _public_ids(client), "unverified reports must not be public"


def test_verified_reports_are_public_without_the_citizen_text_until_resolved(client, citizen, authority):
    created = client.post("/api/incidents", json={"category": "waterlogging", "description": "Knee-deep water, my car stalled", **POINT}, headers=citizen).json()
    incident_id = created["incident_id"]
    verified = client.patch(f"/api/incidents/{incident_id}", json={"status": "verified", "official_note": "Waterlogging near the junction"}, headers=authority)
    assert verified.status_code == 200, verified.text
    public = {i["incident_id"]: i for i in client.get("/api/incidents/public").json()["incidents"]}
    assert public[incident_id]["details"] == "Waterlogging near the junction"
    assert client.patch(f"/api/incidents/{incident_id}", json={"status": "resolved"}, headers=authority).status_code == 200
    assert incident_id not in _public_ids(client)


def test_commuters_cannot_publish_official_notices(client, citizen):
    notice = {"category": "road_closure", "description": "Closed for repairs", **POINT, "closure": True}
    assert client.post("/api/incidents/official", json=notice, headers=citizen).status_code == 403


def test_official_closures_are_in_force_immediately(client, authority):
    notice = client.post("/api/incidents/official", json={"category": "road_closure", "description": "Junction closed for metro work",
                                                          "area": "Koramangala", "road": "Sony World Junction", **POINT, "closure": True}, headers=authority)
    assert notice.status_code == 201, notice.text
    public = {i["incident_id"]: i for i in client.get("/api/incidents/public").json()["incidents"]}
    item = public[notice.json()["incident_id"]]
    assert item["official"] and item["closure"] and item["details"] == "Junction closed for metro work"
    client.patch(f"/api/incidents/{notice.json()['incident_id']}", json={"status": "resolved"}, headers=authority)


def test_only_verified_incidents_can_be_broadcast(client, citizen, authority):
    created = client.post("/api/incidents", json={"category": "accident", "description": "Two-wheeler skid near the flyover", **POINT}, headers=citizen).json()
    path = f"/api/incidents/{created['incident_id']}/broadcast"
    message = {"message": "Accident near Sony World Junction, expect delays.", "severity": "warning"}
    assert client.post(path, json=message, headers=authority).status_code == 409
    client.patch(f"/api/incidents/{created['incident_id']}", json={"status": "verified"}, headers=authority)
    assert client.post(path, json=message, headers=authority).status_code == 201
    assert any(b["message"] == message["message"] for b in client.get("/api/broadcasts").json()["broadcasts"])
    client.patch(f"/api/incidents/{created['incident_id']}", json={"status": "resolved"}, headers=authority)
    assert not any(b["message"] == message["message"] for b in client.get("/api/broadcasts").json()["broadcasts"])


def test_unreviewed_reports_go_stale(client, citizen, authority):
    import store

    created = client.post("/api/incidents", json={"category": "signal_failure", "description": "Signal dark at the junction",
                                                  "latitude": 13.0356, "longitude": 77.5971}, headers=citizen).json()
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    with store.connect() as db:  # test-only: age the report past the review window
        db.execute("UPDATE incidents SET created_at=? WHERE incident_id=?", (old, created["incident_id"]))
    assert client.post("/api/jobs/expire_stale_reports/run", headers=authority).status_code == 200
    listed = {i["incident_id"]: i for i in client.get("/api/incidents?limit=300", headers=authority).json()["incidents"]}
    assert listed[created["incident_id"]]["status"] == "stale"
