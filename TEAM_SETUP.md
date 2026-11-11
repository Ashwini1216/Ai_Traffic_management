# Team setup after pulling FlowState

Pulling gets the source, tests, guides and small evaluation reports. It does **not** copy the configured laptop's private settings, accounts/database, datasets, trained models, road graph, detector weights or camera registrations.

A new checkout can start with an empty database after installing dependencies, creating settings and building the website. Data-dependent features need the additional setup below. Each teammate's default SQLite database is separate; reports and accounts are not automatically shared between laptops. A shared demonstration needs everyone to use the same running backend.

## 1. Get the latest code

From an existing checkout:

```bash
git pull --ff-only
```

For a first checkout:

```bash
git clone https://github.com/Ashwini1216/Ai_Traffic_management.git
cd Ai_Traffic_management
```

Keep any existing local settings/data. The following copy step is for a new `.env`, not a replacement for an already configured one.

## 2. Install dependencies once

Use Python 3.12 and Node.js compatible with Vite: 20.19+ in the Node 20 series, or 22.12+. The macOS instructions assume a machine supported by the TensorFlow/PyTorch dependencies; the configured laptop uses Apple Silicon.

Windows PowerShell, from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
npm --prefix frontend ci
```

macOS, from the repository root:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
npm --prefix frontend ci
```

Skip environment creation/copying if already configured. After later pulls, update Python dependencies if `requirements.txt` changed, and run `npm --prefix frontend ci` if the frontend dependency files changed.

## 3. Configure this machine's `.env`

Set a new long random `JWT_SECRET_KEY`, an authority username and an authority password of at least 12 characters. These create the initial authority account in this machine's new database. Existing accounts keep their stored password; editing `.env` is not an account-password reset.

For the Google-free local setup, use:

```dotenv
DATABASE_URL=
GOOGLE_MAPS_API_KEY=
VITE_GOOGLE_MAPS_API_KEY=
ROUTING_PROVIDER=openstreetmap
PLACE_SEARCH=openstreetmap
```

An empty database URL selects local SQLite. PostgreSQL and Google keys are optional. Do not copy the project owner's private `.env` or database merely to obtain access; create this instance's own credentials.

## 4. Build, then start the website

Build **before** starting the backend so its static-assets route is registered:

```bash
npm --prefix frontend run build
```

Windows:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000
```

macOS:

```bash
.venv/bin/python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000
```

Open [FlowState](http://127.0.0.1:8000), then sign in using the authority credentials configured on this machine. Keep the terminal open. On macOS, `Start FlowState.command` runs the same backend once the environment, settings and frontend build exist.

The default empty checkout has zero historical records, zero trained versions and no registered cameras. The app should show the corresponding empty states, rather than the configured laptop's counts. Map services need internet. Without a local road graph, supported road queries use the external routing fallback.

Rebuild after frontend changes. Restart the backend after Python or `.env` changes; it also needs a restart if the frontend was first built after that backend process started. Development with a separate Vite server on port 5173 remains supported by the README.

## 5. Add the inputs needed for the full project demonstration

| Feature | What a teammate needs |
|---|---|
| Original 33,072 block records and 8,936 daily records | Authorised copies of both ZIP archives, then the restoration command below |
| Selected forecasting pipeline | Restored history and a local model-training run; a folder of model files alone does not register a version in a fresh database |
| Bengaluru-wide local D* Lite | Copy the approximately 17 MB `data/routing/bengaluru_osm.json.gz` from the configured project, preserving its OSM provenance/attribution, or build the graph from the documented source |
| Selected vehicle detector | Copy `models/yolov8s.pt` (approximately 22 MB) or allow its initial download, then configure the settings below |
| Improved uploaded-clip tracking | Set `YOLO_CLIP_TRACKING=confirmed_native`; the default template leaves legacy mode enabled |
| Public Banaswadi camera | Register the deliberately published source on this database using the command below, subject to source availability |
| Benchmark clips/download buttons | Acquire the actual samples; JSON evaluation reports alone do not contain video files |

The authorised source archives can be shared with teammates separately. They are not silently downloaded from the original project owner's laptop. The configured laptop's trained version folder is also local (approximately 17 MB), but accounts/model-registry records are not part of a Git pull. Retraining is the documented route for registering a local model without sharing the owner's database.

### Restore data and train

Place both ZIPs in this checkout's `data` folder. On macOS:

```bash
.venv/bin/python backend/recover_project_data.py --blocks data/bangalore_traffic_timely_dataset.zip --daily data/bangalore-dataset.zip --restore
.venv/bin/python backend/train_cycle.py --activate
```

On Windows, replace `.venv/bin/python` with `.\.venv\Scripts\python.exe`. Activation still requires passing acceptance. A rerun has a new timestamped version; the reported historical score is not a guarantee for different package versions or changed data. See the evaluation report's recorded environment.

### Configure the selected video settings

Add or uncomment these values in `.env`, then restart the backend:

```dotenv
YOLO_WEIGHTS=models/yolov8s.pt
YOLO_IMAGE_SIZE=960
YOLO_CONFIDENCE=0.35
YOLO_AGNOSTIC_NMS=1
YOLO_CLIP_TRACKING=confirmed_native
```

These select the evaluated detector and uploaded-clip policy. They do not establish camera-site accuracy or change the failed flow result. Model loading may require internet if the weight file is not present.

### Register the public camera and publish local report status

```bash
.venv/bin/python backend/connect_public_camera.py --register --collect
.venv/bin/python backend/validation_reports.py
```

This registers an independent public webcam and creates a genuine **unverified** sample. The camera is not calibrated, is not a police feed and does not automatically adjust commuter traffic. The source must still be publicly available and return useful changing images. Personal-camera addresses and credentials must be configured separately.

The optional clip acquisition and graph-building tools need `requirements-research.txt`; source acquisition commands and exact benchmark scope are in `PUBLIC_DATA_VALIDATION.md` and `ACCURACY_UPGRADE_RESULTS.md`. The full OSM source extract is much larger than the compressed built graph; sharing the permitted built graph avoids rebuilding it on every teammate's laptop.

Local training, camera inspection and report-generation commands can update files under `evaluation/`. Review those new measurements before committing them; do not replace the published historical results merely to make an installation appear complete.

## Clean-source check and its limits

A clean export of code commit `6945ffd`, without the configured laptop's `.env`, data, graph, models or database, passed:

- Frontend dependency installation with `npm ci` and production build.
- New local database creation and authority-account sign-in.
- Models, Data coverage, CCTV review, Traffic data and Observations & jobs empty-data screens.
- No page-script errors or API server errors in those checks.

This check used the laptop's **already installed Python dependencies**, a new test configuration with background jobs disabled, and an installed test browser. It did not reinstall the Python stack or verify Windows/Linux dependency installation. It also did not turn missing assets into a complete forecasting/camera installation. Evidence is in `evaluation/fresh-start-smoke.json`.

After configuring the desired inputs, follow `SELF_TEST_GUIDE.md` to test actual routes, incidents, replay and camera behaviour. Software-startup success does not prove forecasting or vehicle-count accuracy.
