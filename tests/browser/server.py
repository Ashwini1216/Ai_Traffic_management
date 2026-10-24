"""Start FlowState for the browser tests: fake dataset, fake Google services, a test build.

Usage (from the project root):  python tests/browser/server.py [port] [google|openstreetmap]
"google" (the default) uses fake Google services; "openstreetmap" runs with no Google keys at
all, like a computer whose .env has none, so the website must show the OpenStreetMap map.
Everything is written to a new temporary folder; the project's data/, models/ and
frontend/dist/ are never touched, and no real Google key or public service is used.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
GOOGLE = (sys.argv[2] if len(sys.argv) > 2 else "google") == "google"
WORK = Path(tempfile.mkdtemp(prefix="flowstate-browser-"))

os.environ.update({
    "FLOWSTATE_DATA_DIR": str(WORK / "data"), "FLOWSTATE_MODELS_DIR": str(WORK / "models"),
    "FLOWSTATE_FRONTEND_DIST": str(WORK / "dist"), "DATABASE_URL": "",
    "JWT_SECRET_KEY": "browser-test-secret-" + "x" * 40, "AUTHORITY_USERNAME": "officer",
    "AUTHORITY_PASSWORD": "officer-password-123", "AUTHORITY_EMAIL": "", "AUTHORITY_TOKEN": "",
    "GOOGLE_MAPS_API_KEY": "test-server-key" if GOOGLE else "", "PLACE_SEARCH": "auto", "NOMINATIM_URL": "",
    # The website gets the browser key from the server at run time; the build below has none.
    "VITE_GOOGLE_MAPS_API_KEY": "test-browser-key" if GOOGLE else "",
    "SCHEDULER_ENABLED": "0", "ALLOW_FILE_STREAMS": "1", "TF_CPP_MIN_LOG_LEVEL": "3", "PYTHONIOENCODING": "utf-8",
})
sys.path[:0] = [str(ROOT / "tests" / "backend"), str(ROOT / "backend")]

import fake_google  # noqa: E402
import fixture_dataset  # noqa: E402

fixture_dataset.write_archive(WORK / "data" / "bangalore_traffic_timely_dataset.zip")

# A build of the website without a browser key (the tests replace Google Maps with a stub).
node = shutil.which("node")
if not node:
    sys.exit("Node.js is required to build the website for the browser tests.")
subprocess.run([node, str(ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"), "build",
                "--outDir", str(WORK / "dist"), "--emptyOutDir", "--logLevel", "error"],
               cwd=ROOT / "frontend", env={**os.environ, "VITE_GOOGLE_MAPS_API_KEY": ""}, check=True)

import core  # noqa: E402

fake_google.install(core)

# Video analysis: a stand-in that reports progress and fixed counts, so no detector is needed.
import fake_vision  # noqa: E402
import vision  # noqa: E402

vision.analyze_video = fake_vision.analyze_video
fake_vision.STEP_SECONDS = 0.8

# A quick model version so the model screens have an evaluation to show.
if GOOGLE:
    import train_hybrid  # noqa: E402

    train_hybrid.main(["--epochs", "4", "--trained-by", "browser-tests"])

import uvicorn  # noqa: E402

import app as appmod  # noqa: E402

print(f"FlowState test server on http://127.0.0.1:{PORT} (data in {WORK})", flush=True)
uvicorn.run(appmod.app, host="127.0.0.1", port=PORT, log_level="warning")
