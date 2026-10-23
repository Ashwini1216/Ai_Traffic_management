"""Model training through the API, acceptance rules, activation, rollback, and inference."""
from datetime import datetime, timezone

from conftest import TRAINING, block_target


def test_training_records_the_evaluation_and_acceptance_checks(client, authority, trained_version):
    detail = client.get(f"/api/models/{trained_version}", headers=authority).json()
    metrics, checks = detail["metrics"], detail["acceptance"]["checks"]
    assert set(metrics["baselines_test"]) >= {"majority", "persistence", "historical_profile"}
    assert set(metrics["horizons_test"]) == {"1", "2", "3"}
    assert "rf_without_lstm_macro_f1" in metrics["lstm_contribution"]
    names = " | ".join(c["check"] for c in checks)
    assert "beats the best baseline" in names and "Random Forest without LSTM" in names, names
    assert detail["status"] in ("candidate", "rejected") and detail["acceptance"]["passed"] == (detail["status"] == "candidate")


def test_a_second_training_run_cannot_start_while_one_is_running(trained_version):
    assert TRAINING["second_start_status"] == 409


def test_rejected_versions_cannot_be_activated(client, authority, citizen, trained_version):
    assert client.post(f"/api/models/{trained_version}/activate", headers=citizen).status_code == 403
    if client.get(f"/api/models/{trained_version}", headers=authority).json()["status"] == "rejected":
        assert client.post(f"/api/models/{trained_version}/activate", headers=authority).status_code == 409


def test_activation_retires_the_previous_version_and_rollback_restores_it(client, authority, trained_version):
    import store

    copy = trained_version + "-copy"
    with store.connect() as db:  # test-only: make both versions eligible so activation can be exercised
        db.execute("UPDATE model_versions SET status='candidate' WHERE version=?", (trained_version,))
        db.execute("""INSERT INTO model_versions(version,kind,status,created_at,trained_by,artifact_dir,metrics_json,acceptance_json,data_json)
          SELECT ?,kind,'candidate',created_at,trained_by,artifact_dir,metrics_json,acceptance_json,data_json FROM model_versions WHERE version=?""",
                   (copy, trained_version))
    assert client.post(f"/api/models/{trained_version}/activate", headers=authority).status_code == 200
    assert client.post(f"/api/models/{copy}/activate", headers=authority).status_code == 200
    statuses = {m["version"]: m["status"] for m in client.get("/api/models", headers=authority).json()["models"]}
    assert statuses[copy] == "active" and statuses[trained_version] == "retired", statuses
    assert client.post(f"/api/models/{trained_version}/activate", headers=authority).status_code == 200  # roll back
    statuses = {m["version"]: m["status"] for m in client.get("/api/models", headers=authority).json()["models"]}
    assert statuses[trained_version] == "active" and statuses[copy] == "retired", statuses


def test_the_active_model_needs_six_verified_blocks_to_forecast(client, authority, citizen, trained_version):
    import core

    if client.get(f"/api/models/{trained_version}", headers=authority).json()["status"] != "active":
        test_activation_retires_the_previous_version_and_rollback_restores_it(client, authority, trained_version)
    area, road = "Jayanagar", "South End Circle"
    day, block = core.block_for(datetime.now(timezone.utc))
    ids = []
    for k in range(5, -1, -1):
        d, b = core.next_block(day, block, -k)
        stored = client.post("/api/observations", json={"observed_date": d.isoformat(), "time_block": b, "area": area, "road": road,
                                                        "traffic_volume": 1400 + 50 * k, "average_speed": 30, "travel_time_index": 1.6,
                                                        "congestion_level": "Medium", "source_name": "test_sequence"}, headers=authority)
        assert stored.status_code == 200, stored.text
        ids.append(stored.json()["id"])
    params = {"area": area, "road": road, "target": block_target(1)}
    assert client.get("/api/forecast", params=params, headers=citizen).json()["method"] != "sequence_model", "unverified data was used"
    for observation_id in ids:
        client.patch(f"/api/observations/{observation_id}", json={"quality_status": "verified"}, headers=authority)
    forecast = client.get("/api/forecast", params=params, headers=citizen).json()
    assert forecast["method"] == "sequence_model" and forecast["model_version"] == trained_version, forecast["method"]
    assert set(forecast["predicted_values"]) >= {"traffic_volume", "average_speed", "travel_time_index"}


def test_replay_runs_a_version_on_past_records(client, authority, trained_version, segments):
    road = segments["100 Feet Road"]
    for day in range(10, 28):
        replay = client.post("/api/forecast/replay", json={"segment_id": road["segment_id"], "as_of_date": f"2022-03-{day:02d}",
                                                           "as_of_time_block": "Noon", "horizons": 3, "version": trained_version}, headers=authority)
        if replay.status_code == 200:
            steps = replay.json()["steps"]
            assert len(steps) == 3 and all(s["predicted_class"] for s in steps)
            return
    raise AssertionError("no replayable date found in March 2022")
