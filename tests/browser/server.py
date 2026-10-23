"""Start FlowState for the browser tests: fake dataset, fake Google services, a test build.

Usage (from the project root):  python tests/browser/server.py [port]
Everything is written to a new temporary folder; the project's data/, models/ and
frontend/dist/ are never touched, and no real Google key is used.
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
WORK = Path(tempfile.mkdtemp(prefix="flowstate-browser-"))

os.environ.update({
    "FLOWSTATE_DATA_DIR": str(WORK / "data"), "FLOWSTATE_MODELS_DIR": str(WORK / "models"),
    "FLOWSTATE_FRONTEND_DIST": str(WORK / "dist"), "DATABASE_URL": "",
    "JWT_SECRET_KEY": "browser-test-secret-" + "x" * 40, "AUTHORITY_USERNAME": "officer",
    "AUTHORITY_PASSWORD": "officer-password-123", "AUTHORITY_TOKEN": "", "GOOGLE_MAPS_API_KEY": "test-server-key",
    "SCHEDULER_ENABLED": "0", "ALLOW_FILE_STREAMS": "1", "TF_CPP_MIN_LOG_LEVEL": "3", "PYTHONIOENCODING": "utf-8",
})
sys.path[:0] = [str(ROOT / "tests" / "backend"), str(ROOT / "backend")]

import fake_google  # noqa: E402
import fixture_dataset  # noqa: E402

fixture_dataset.write_archive(WORK / "data" / "bangalore_traffic_timely_dataset.zip")

# A build of the website with a placeholder browser key; the tests replace Google Maps with a stub.
node = shutil.which("node")
if not node:
    sys.exit("Node.js is required to build the website for the browser tests.")
subprocess.run([node, str(ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"), "build",
                "--outDir", str(WORK / "dist"), "--emptyOutDir", "--logLevel", "error"],
               cwd=ROOT / "frontend", env={**os.environ, "VITE_GOOGLE_MAPS_API_KEY": "test-browser-key"}, check=True)

import core  # noqa: E402

fake_google.install(core)

# A quick model version so the model screens have an evaluation to show.
import train_hybrid  # noqa: E402

train_hybrid.main(["--epochs", "4", "--trained-by", "browser-tests"])

import uvicorn  # noqa: E402

import app as appmod  # noqa: E402

print(f"FlowState test server on http://127.0.0.1:{PORT} (data in {WORK})", flush=True)
uvicorn.run(appmod.app, host="127.0.0.1", port=PORT, log_level="warning")
