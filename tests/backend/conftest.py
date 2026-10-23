"""Test setup: one isolated FlowState instance with a fake dataset and fake Google services.

The settings below are applied before the app is imported, and python-dotenv never overrides
variables that are already set, so the project's own .env, data/ and models/ are not used.
Set FLOWSTATE_TEST_DATABASE_URL to run the same tests on an empty, disposable PostgreSQL database.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = Path(tempfile.mkdtemp(prefix="flowstate-tests-"))
os.environ.update({
    "FLOWSTATE_DATA_DIR": str(WORK / "data"), "FLOWSTATE_MODELS_DIR": str(WORK / "models"),
    "DATABASE_URL": os.environ.get("FLOWSTATE_TEST_DATABASE_URL", ""),
    "JWT_SECRET_KEY": "test-secret-" + "x" * 40, "AUTHORITY_USERNAME": "officer",
    "AUTHORITY_PASSWORD": "officer-password-123", "AUTHORITY_EMAIL": "officer@example.gov.in", "AUTHORITY_TOKEN": "",
    "GOOGLE_MAPS_API_KEY": "test-key-not-real",
    "SCHEDULER_ENABLED": "0", "ALLOW_FILE_STREAMS": "1", "TF_CPP_MIN_LOG_LEVEL": "3", "PYTHONIOENCODING": "utf-8",
    # Defaults spelled out so a developer's own .env cannot change the results.
    "TIME_BLOCK_STARTS": "", "PCU_FACTORS": "", "MODEL_ACCEPTANCE_MIN_GAIN": "0.02", "DEMAND_MIN_GROUP_TRIPS": "3",
    "DEMAND_HOTSPOT_PCU_SCALE": "50", "TRIP_REASSESS_HOURS": "12", "FOOTAGE_RETENTION_DAYS": "30",
    "DETAILED_RETENTION_DAYS": "365", "OBSERVATION_RETENTION_YEARS": "5", "VIDEO_SAMPLE_FPS": "10",
    "VIDEO_MAX_SAMPLED_FRAMES": "1500",
})
sys.path[:0] = [str(HERE), str(ROOT / "backend")]

import fake_google  # noqa: E402
import fixture_dataset  # noqa: E402

FIXTURE = fixture_dataset.write_archive(WORK / "data" / "bangalore_traffic_timely_dataset.zip")
AUTHORITY_LOGIN = {"username": "officer", "password": "officer-password-123"}


@pytest.fixture(scope="session")
def appmod():
    import app
    import core

    fake_google.install(core)
    return app


@pytest.fixture(scope="session")
def client(appmod):
    from fastapi.testclient import TestClient

    with TestClient(appmod.app) as test_client:
        yield test_client


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def authority(client) -> dict:
    response = client.post("/api/auth/login", json=AUTHORITY_LOGIN)
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


def register(client, email: str | None = None) -> tuple[str, dict]:
    """A new commuter account; returns its username and authorization header."""
    email = email or f"rider_{uuid.uuid4().hex[:8]}@example.com"
    response = client.post("/api/auth/register", json={"email": email, "password": "citizen-password-1"})
    assert response.status_code == 201, response.text
    return response.json()["user"]["username"], bearer(response.json()["access_token"])


@pytest.fixture
def citizen(client) -> dict:
    """A new commuter account for each test."""
    return register(client)[1]


@pytest.fixture(scope="session")
def segments(client, authority) -> dict:
    """Road name -> registry row for the fixture's 16 roads."""
    rows = client.get("/api/segments", headers=authority).json()["segments"]
    return {row["road"]: row for row in rows}


def block_target(blocks_ahead: int = 1) -> str:
    """An ISO time one minute into the time block `blocks_ahead` blocks from now."""
    import core

    day, block = core.next_block(*core.block_for(datetime.now(timezone.utc)), blocks_ahead)
    return (core.block_bounds(day, block)[0] + timedelta(minutes=1)).isoformat()


def wait_for_training(client, authority, timeout_s: float = 900) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = client.get("/api/models/train/status", headers=authority).json()
        if not status["running"]:
            return status
        time.sleep(1)
    raise AssertionError("training did not finish in time")


TRAINING: dict = {}


@pytest.fixture(scope="session")
def trained_version(client, authority) -> str:
    """Train one model version through the API (the same path as the Train button)."""
    started = client.post("/api/models/train", headers=authority)
    assert started.status_code == 202, started.text
    TRAINING["second_start_status"] = client.post("/api/models/train", headers=authority).status_code
    status = wait_for_training(client, authority)
    assert status["return_code"] == 0, status.get("log_tail")
    return client.get("/api/models", headers=authority).json()["models"][0]["version"]
