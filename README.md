# FlowState — Bengaluru Traffic Monitoring and Journey Planning

FlowState is a student research prototype for collaborative traffic monitoring and congestion prediction in Bengaluru. Commuters plan trips, compare routes by risk, check a road's expected congestion, report incidents, and get notified when a planned trip is affected. Traffic authorities use a separate console to monitor zones, review reports, publish official notices (VIP movements, closures, metro work, diversions), see planned demand, manage camera sources, review the data pipeline, and evaluate forecasting models.

The project combines a map-based web interface, a Python API, a historical traffic dataset, a hybrid LSTM → Random Forest forecasting pipeline, and optional camera-based vehicle counting. It is intended for learning and demonstration; it is not an official traffic service.

> **Please read:** Forecasts, route risk scores, and camera vehicle counts are research estimates, not live sensor readings or safety advice. Every forecast states the method that produced it and a confidence level. Google driving times are traffic-aware estimates when Google returns traffic data. Generated synthetic data is simulated; it is not real sensor data or evaluation ground truth.

## What the website can do

### Commuter

- See the Bengaluru map with the current congestion outlook per zone, verified incidents, and official notices.
- **Place search:** type any place in Bengaluru (for example *Avalahalli*), pick the right match from the list, and the map zooms to it. A found place can be used as the starting point or destination of a journey or a planned trip.
- **Road forecast:** expected congestion (Low / Medium / High) for a road at a chosen time, with the probability of each level, the method used, a confidence level, and the evidence (nearby incidents and notices, rain forecast, planned trips, recent camera measurements).
- **Compare routes:** each route is scored for travel risk from traffic (congestion forecasts, and Google's traffic delay when Google routing is set up), planned demand, accidents and hazards, official notices, and weather (rain, thunderstorms, fog, strong wind), at the time you reach each road. Each route lists the reasons behind its score. Routes through an official closure are marked restricted. When the fastest route is clearly riskier, a safer route is suggested; when traffic adds a delay, you are told how much earlier to leave; the best route also gets departure-time advice.
- **Journey monitoring:** with permission, your live location is shown on the map and checked against the road ahead. When a closure, accident or other serious hazard is reported ahead, or you leave the route, FlowState checks for a better route from where you are (and also every ten minutes) and lets you switch to it. Positions are not stored after the journey ends.
- **Plan a trip:** save a future trip with its vehicle type. Each trip shows how many other travellers plan to arrive near the same destination around the same time (groups smaller than three are shown only as "fewer than 3", for privacy). About an hour before departure you get an advance notice with the expected traffic, the delay, and a better route or departure time when there is one. Trips are also re-checked automatically, and you are notified if the risk rises materially or a closure appears on the route. New notices can pop up as desktop alerts while the website is open.
- **Account:** change your password; other devices signed in to the account are signed out.
- Report an incident with your location. Matching reports nearby are merged as confirmations.
- Request authority access from your account (for traffic police and city officials).

### Authority

- **Operations overview** with active alerts, reports awaiting review, cameras, and the forecasting model in use.
- **Zone monitor:** congestion outlook per zone and road for now or a later time.
- **Alerts:** raised automatically for high-risk zones, closures, planned-demand hotspots, and data-quality problems (offline cameras, quarantine spikes), each with a recommended action. Alerts can be acknowledged or resolved.
- **Incident map:** verify, reject, or resolve community reports. Verified reports expire after a category-based validity period; reports nobody reviews go stale after six hours.
- **Official notices:** publish VIP movements, closures, metro work, diversions, events, and restrictions for a place and time window, optionally broadcast to commuters.
- **Planned demand:** future load per road and time block, weighted by vehicle type (PCU), hotspot scores, zone-to-zone corridors, and popular destinations (places at least three people plan to reach within the same hour). Individual trips are never shown, and small groups are hidden.
- **Data coverage:** every road with its map location, record count, period, missing data, weather coverage, and the forecasting method its data supports. Being on the map is not the same as having traffic data.
- **Observations and jobs:** add measured observations, verify or reject pending ones, view the quarantine of rejected input with reasons, and run background jobs.
- **Models:** train a new version, see its evaluation against simple baselines and its acceptance checks, activate or roll back versions, and replay a version on past records.
- **CCTV:** count people and vehicles in any uploaded video without registering a camera (the video is deleted as soon as the count is finished), register approved cameras, sample approved live streams on a schedule, and review uploaded clips.
- **Access requests:** approve or reject requests for authority access, revoke authority accounts, and see recent authority sign-ins.

The first authority account is created from the `.env` settings. Further authority accounts are granted only by an existing authority approving an access request; commuter sign-up can never create one.

**Sign-in security.** Passwords are stored only as salted bcrypt hashes. After five wrong passwords for an account within fifteen minutes, its sign-in is paused for the rest of that window, even with the right password (`LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_MINUTES`); one network address may make four times as many failed attempts across all accounts. The authority sign-in page never signs in a commuter account. Authority passwords need at least 12 characters, and changing a password signs the account out everywhere else. Every authority action is also checked against the account's current role, so revoked access stops at once.

## How forecasts are made

- **Data and time blocks.** The historical dataset records each road in six daily time blocks (Midnight, Morning, Daytime, Noon, Evening, Night). The dataset does not define their hours, so FlowState assumes start hours in IST (`TIME_BLOCK_STARTS` in `.env`) to place live timestamps into blocks.
- **Hybrid model.** An LSTM reads a road's last six blocks and forecasts the next block's traffic volume, speed, and travel-time index. A Random Forest then classifies congestion as Low, Medium, or High from those forecasts plus calendar features, road type, and the road's historical pattern. Forecasts further ahead (up to three blocks) are made step by step.
- **Honest evaluation.** Data is split by date (70% train, 15% validation, 15% test, oldest to newest), never shuffled. Each version is compared on the test period with three simple baselines (most common level, same as the last block, and the road's historical pattern), and with the same Random Forest trained without the LSTM, to measure what the LSTM adds.
- **Acceptance.** A new version is used for live forecasts only if it passes predefined checks: its macro-F1 beats the best baseline by at least `MODEL_ACCEPTANCE_MIN_GAIN` (0.02 by default), it also beats the Random Forest without the LSTM by that margin (so the LSTM must add value), its accuracy matches the most-common-level baseline, every level is recalled, and it is not more than 0.01 macro-F1 below the active version. Versions are kept in a registry, so an older version can be reactivated (rolled back).
- **Fallbacks.** Without six consecutive verified observations ending at the latest block, or when no version is active, the forecast uses the road's historical pattern for that time block and day type. A road with too little history borrows the pattern of similar roads, but only if that transfer beat a baseline on held-out roads; otherwise it gets a clearly labelled limited-data estimate.
- **Continuous learning.** New observations are validated, quarantined if invalid, and join the training history once verified. Forecasts are recorded and later compared with what was observed, so live performance can be checked.

## Technology

- **Frontend:** React 18, Vite 8, Google Maps JavaScript API or Leaflet with OpenStreetMap, Lucide React
- **Backend:** Python 3.12 (3.11 also works), FastAPI, Uvicorn, background jobs inside the API process
- **Database:** SQLAlchemy 2, SQLite for simple local use, optional PostgreSQL with Psycopg 2
- **Forecasting:** NumPy, pandas, scikit-learn, TensorFlow/Keras, Joblib
- **Authentication:** JWT with `python-jose`; password hashing with Passlib; commuter and authority roles
- **Video analysis:** Ultralytics YOLOv8 with ByteTrack tracking, OpenCV, and PyTorch
- **Map services:** Google Maps JavaScript API, Google Places API (New), Google Routes API; OpenStreetMap map tiles and Nominatim place search when Google is not set up
- **Weather:** Open-Meteo observations and forecasts

## Run FlowState on your computer

These instructions cover a local development run on **Windows 10/11** and **macOS**. Each step lists the Windows (PowerShell) command first and the macOS (Terminal) command second. VS Code is recommended but not required.

### 1. Install prerequisites

Install:

- **Python 3.12**
  - Windows: the installer from [python.org](https://www.python.org/downloads/) (tick *Add python.exe to PATH*), or `winget install Python.Python.3.12`
  - macOS: `brew install python@3.12` with [Homebrew](https://brew.sh), or the installer from python.org
- **Node.js** 20.19+ or 22.12+ (npm is included with Node.js)
- **Git**, if you are cloning the project from GitHub
- Optionally, a Google Cloud project with Google Maps Platform APIs enabled (see step 4). The app also runs without Google keys: maps and place search then use OpenStreetMap, and routes are straight-line estimates.

On macOS, the machine-learning and video packages (TensorFlow and PyTorch) are published only for Apple Silicon Macs (M1 or newer). Intel Macs cannot install current versions of those packages.

Then open PowerShell (Windows), Terminal (macOS), or the VS Code terminal.

### 2. Get the project files

```bash
git clone https://github.com/Ashwini1216/Ai_Traffic_management.git
cd Ai_Traffic_management
```

Alternatively, download the repository as a ZIP from GitHub and extract it, then open the extracted project folder in the terminal.

### 3. Install backend packages

Run these commands from the project root (the folder containing `README.md`).

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS (Terminal):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

The single root `requirements.txt` contains the Python API, database, machine-learning, and video-analysis packages, and selects the right TensorFlow package for each operating system. Installation can take several minutes because TensorFlow and PyTorch are large packages.

### 4. Create private local settings

From the project root:

```powershell
# Windows
Copy-Item .env.example .env
```

```bash
# macOS
cp .env.example .env
```

Open `.env` in a text editor and change the placeholder values:

- Set `JWT_SECRET_KEY` to a long, random secret.
- Set `AUTHORITY_USERNAME` and `AUTHORITY_PASSWORD` (at least 12 characters) to credentials chosen by the local project operator. Optionally set `AUTHORITY_EMAIL` so this account can also sign in with an email address.
- Leave `DATABASE_URL=` empty for the easiest setup. The app then uses a local SQLite database at `data/traffic_monitor.db`.
- Set `GOOGLE_MAPS_API_KEY` to a server key used by place search and route requests.
- Set `VITE_GOOGLE_MAPS_API_KEY` to a separate browser key used to display the map. Vite reads this project-root `.env` file; if the website was started before the key was added, the API hands the key to the website, so restarting the API is enough.
- Leave both Google keys as placeholders to use OpenStreetMap instead (see [Maps and place search without Google](#maps-and-place-search-without-google)).

The rest of `.env.example` lists optional settings with their defaults (time-block hours, model acceptance margin, vehicle PCU weights, retention periods, background jobs, video sampling, sign-in limits, place search, trip reminders). Leave them commented out unless you need to change one.

In Google Cloud Console, enable **Maps JavaScript API**, **Places API (New)**, and **Routes API**. Restrict the browser key to the Maps JavaScript API and your allowed website referrers (for local development, `http://localhost:5173/*`, `http://127.0.0.1:5173/*` and `http://127.0.0.1:8000/*`). Restrict the server key to Places API (New) and Routes API; for a deployed backend, also restrict it to the server's IP address where practical. The browser key is visible to website visitors by design, so its referrer restriction is important. Never commit either real key. Set API quotas and a budget alert before sharing the site; Google Maps Platform usage is billed by service and may incur charges beyond the applicable no-charge usage caps. See Google's [pricing](https://developers.google.com/maps/billing-and-pricing/pricing) and [API key security guidance](https://developers.google.com/maps/api-security-best-practices). Place search, route comparison, forecasts, and weather lookups require a signed-in account, so the billed keys cannot be used anonymously through the API.

PostgreSQL is optional. To use it instead, create/configure a PostgreSQL database and set `DATABASE_URL` to its connection string (see the commented example in `.env.example`).

**Never upload `.env` to GitHub.** It contains private credentials. The included `.gitignore` excludes it; `.env.example` is the safe template and contains placeholders.

### 5. Add the historical dataset

Place an authorized copy of the dataset archive at `data/bangalore_traffic_timely_dataset.zip` (create the `data` folder if it does not exist). The CSV is read directly from inside the zip; do not extract it. On the next API start, every row is validated and imported, and the roads are registered on the map. The app also starts without the dataset, but forecasts are then limited-data estimates and model training is unavailable.

### 6. Start the backend API

Open a terminal at the project root and run:

```powershell
# Windows
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000
```

```bash
# macOS
.venv/bin/python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000
```

Leave this terminal running. The API documentation will be available at <http://127.0.0.1:8000/docs>. The first start with the dataset takes longer while it is imported.

### 7. Start the frontend website

Open a second terminal at the project root and run (same on Windows and macOS):

```bash
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173> in your browser. Keep both terminals running while using the website. Stop each server with **Ctrl+C** in its terminal.

Commuters register and sign in on the website with their email address and a password (addresses are not verified by email yet). The authority account is provisioned from the `.env` credentials when the API starts for the first time, and signs in on the authority page with its username or `AUTHORITY_EMAIL`. If those settings are changed after an account already exists, changing the environment values alone does not change the existing account password; update the account in the database or configure a fresh local database.

### 8. Train the forecasting model

Until a model version passes its acceptance checks, forecasts use each road's historical pattern, which still works. Decide the acceptance margin (`MODEL_ACCEPTANCE_MIN_GAIN`) before training, not after seeing a result. To train a version, either sign in to the authority console and open **Models → Train new version**, or run this from the project root (with the dataset in place):

```powershell
# Windows
.\.venv\Scripts\python.exe backend\train_hybrid.py --activate
```

```bash
# macOS
.venv/bin/python backend/train_hybrid.py --activate
```

Training takes several minutes. It prints the evaluation and registers the version. With `--activate`, the version is used for live forecasts only if it passes the acceptance checks; otherwise it is kept as "rejected" with the reasons, and the **Models** page shows its full evaluation. The API picks up a newly activated version without a restart.

## Maps and place search without Google

Without Google keys, every map is an [OpenStreetMap](https://www.openstreetmap.org/copyright) map (drawn with Leaflet) and place search uses OpenStreetMap's Nominatim service. Google's terms do not allow Google route or place results on a non-Google map, so when the server has a Google key the website also needs `VITE_GOOGLE_MAPS_API_KEY`.

Both OpenStreetMap services are free but run by volunteers, and their usage policies set conditions that FlowState follows:

- **[Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/):** at most one request per second for the whole application (FlowState spaces requests out), an identifying User-Agent, visible attribution ("© OpenStreetMap contributors" appears under the search results), no search-as-you-type (FlowState searches only when you press Search or Enter), repeated searches answered from a cache (24 hours here), and the service must be switchable. Heavy use is not allowed; for a public deployment use your own Nominatim server (`NOMINATIM_URL`) or a commercial provider.
- **[Tile usage policy](https://operations.osmfoundation.org/policies/tiles/):** visible attribution on every map (bottom right), no bulk downloading or offline use, and a configurable tile address (`VITE_MAP_TILE_URL`), because the public tile servers are not meant for heavy traffic.

`PLACE_SEARCH` chooses the search service: `auto` (Google when a server key is set, otherwise OpenStreetMap; the default), `google`, `openstreetmap`, or `off`. Places outside the Bengaluru area are never offered.

## Background jobs

While the API runs, these jobs run automatically inside it. Set `SCHEDULER_ENABLED=0` in `.env` to switch them off; authorities can still run any job from **Observations & jobs**.

| Job | Every | What it does |
|---|---|---|
| Collect weather | 1 h | Weather observations and forecasts for each zone (Open-Meteo) |
| Reassess trips | 15 min | Re-checks planned trips departing soon and notifies owners of material changes |
| Trip reminders | 5 min | Sends each planned trip's advance notice about an hour before departure (`TRIP_REMINDER_MINUTES`): expected traffic, delay, and a better route or time |
| Expire stale reports | 15 min | Marks community reports left unreviewed for six hours as stale |
| Camera collection | 1 min | Samples approved live camera streams that are due |
| Zone alerts | 30 min | Raises alerts for high-risk zones, closures, and planned-demand hotspots |
| Data quality | 1 h | Flags offline or silent cameras and quarantine spikes |
| Retention | 24 h | Applies the retention policy and keeps daily summaries |
| Sync segments | 1 h | Registers new roads found in observations |

## Cameras and video analysis

Video-analysis packages are included in the single root `requirements.txt`. Only use video and camera feeds that you are authorized to use. Counts are uncalibrated estimates.

- **Live streams.** An approved, online camera with an `rtsp://`, `rtmp://`, `http://`, or `https://` stream address is sampled on its schedule (every 1, 5, or 15 minutes, for a few seconds each time). Vehicles are detected and tracked; with a counting line set, vehicles crossing it give a flow estimate (vehicles per hour). Occupancy and queue length are also estimated. Stream video is analysed and not stored. A camera that fails three times in a row is marked offline and raises a data-quality alert.
- **Count people and vehicles in a video.** On **CCTV review**, an authority can upload any authorised clip without registering a camera. The page shows the most people seen at once, the number of different people tracked, the average number in view, vehicles by type, and the busiest frame with people outlined in orange and vehicles in blue. The video is deleted as soon as the count is finished, and the result is kept for an hour. In dense crowds people hide behind each other, so the real numbers are higher than the counts.
- **Uploaded clips.** Authorities can upload a clip from a registered camera for review; it is saved with its vehicle and people counts. Uploaded video files are deleted after the retention period (30 days by default); their analysis is kept.
- Camera measurements are shown as recent evidence in forecasts for the next two hours; they are not used as training labels.
- The first analysis downloads the YOLOv8n weights file (about 6 MB) to `models/yolov8n.pt`, so it needs an internet connection once. Ultralytics' anonymous usage statistics are switched off while FlowState analyses video.
- Video is sampled at 10 frames per second so the tracker can follow moving vehicles without counting them twice. Up to 1,500 sampled frames (2.5 minutes at that rate) are analysed per clip; if a clip is longer, the result reports how much of it was analysed. Both values can be changed in `.env` (`VIDEO_SAMPLE_FPS`, `VIDEO_MAX_SAMPLED_FRAMES`).

## Data and model files

- Dataset archives, databases, trained models, and generated data are not included in the GitHub upload. Check the dataset's license before sharing it.
- Every archive row is validated during import (date, time block, area, road, and numeric ranges). Invalid or exactly duplicated rows are kept out of the history and stored in the quarantine table with the reason; training uses the same validated rows. Databases created by an older version of the app are rebuilt from the archive automatically on the next start.
- Observations entered by authorities are stored as unverified until an authority verifies them. Only the historical dataset and verified block-level observations are used for training and as model input.
- Trained versions are saved under `models/<version>/`, and the registry of versions with their metrics lives in the database. Training logs for runs started from the console are saved under `data/logs/`.
- To generate a separate simulated dataset from the archive, run this from the root folder:

  ```powershell
  # Windows
  .\.venv\Scripts\python.exe backend\generate_synthetic_dataset.py
  ```

  ```bash
  # macOS
  .venv/bin/python backend/generate_synthetic_dataset.py
  ```

## Running the tests

The tests run on a small fake dataset with stand-ins for Google Maps and the weather service, in a temporary folder. They never touch your `data/`, `models/`, or `.env`, and never use your Google key.

**Backend tests** (about 30 seconds; one of them trains a model version through the API):

```powershell
# Windows
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

```bash
# macOS
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

To run the same tests on PostgreSQL, create an empty database and set `FLOWSTATE_TEST_DATABASE_URL` to its connection string first (PowerShell: `$env:FLOWSTATE_TEST_DATABASE_URL="postgresql+psycopg2://..."`; macOS: `export FLOWSTATE_TEST_DATABASE_URL=postgresql+psycopg2://...`). The tests that count people and vehicles with the real detector run when `models/yolov8n.pt` is present (it is downloaded by the first video analysis); the stream-sampling test runs only when `FLOWSTATE_TEST_VIDEO` points to an authorised traffic clip.

**Browser tests** (about a minute). They start two test servers (one with stand-ins for Google, one without any Google key, for the OpenStreetMap map) with a separate build of the website, so they need the backend packages (step 3) and the website packages (`npm install` in `frontend/`, step 7). Map tiles are answered locally, so the tests never load OpenStreetMap's tiles. From the project root (same on Windows and macOS):

```bash
cd tests/browser
npm install
npx playwright install chromium
npx playwright test
```

## Troubleshooting

- **Windows: `pip install` fails with a message about Windows Long Path support** while installing TensorFlow. Enable long paths (see Microsoft's [Maximum Path Length Limitation](https://learn.microsoft.com/windows/win32/fileio/maximum-file-path-limitation) guide) or move the project to a shorter folder path such as `C:\projects\`, then run the install again.
- **Windows: `DLL load failed` when TensorFlow or PyTorch is imported.** Install the latest [Microsoft Visual C++ Redistributable](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist) (x64) and restart the terminal.
- **macOS: `python3.12: command not found`.** Install Python 3.12 with `brew install python@3.12` or from python.org, then open a new Terminal window.
- **`Port 5173 is already in use` or the API port is busy.** Stop the other program using that port, or stop an older FlowState terminal with Ctrl+C.
- **Map shows "Map setup needed".** The server has a Google key but the website has none: add `VITE_GOOGLE_MAPS_API_KEY` to the project-root `.env` and restart the API. Without any Google key the map is an OpenStreetMap map instead. If the notice says Google rejected the key, check the key, that Maps JavaScript API is enabled, and that `http://127.0.0.1:5173/*` is an allowed referrer.
- **"Sign-in is paused".** Too many wrong passwords were entered for that account; wait the number of minutes shown, then sign in with the correct password.
- **Place search finds nothing.** Type at least three letters and press Search. With Google, Places API (New) must be enabled for the server key; without Google, OpenStreetMap search needs an internet connection.
- **Routes are labelled "Estimated corridor".** Google routing is not configured; set `GOOGLE_MAPS_API_KEY` and restart the API.
- **The Models page shows no active model.** Train a version (step 8). If it was rejected, its acceptance checks explain why; forecasts keep using historical patterns meanwhile.

## Project structure

```text
Ai_Traffic_management/
├── backend/
│   ├── app.py                 API entry point: startup, sign-in, summaries, website routes
│   ├── core.py                Settings, IST time blocks, geometry, external requests
│   ├── places.py              Place search (Google Places, OpenStreetMap Nominatim)
│   ├── store.py               Database tables, migrations, and historical import
│   ├── dataset.py             Row validation shared by import and training
│   ├── segments.py            Road and zone registry, data-coverage report
│   ├── observations.py        Observation ingestion, review, and quarantine
│   ├── weather.py             Weather observations and forecasts
│   ├── hybrid_model.py        LSTM → Random Forest model
│   ├── train_hybrid.py        Training, evaluation, and acceptance checks
│   ├── model_registry.py      Model versions, activation, and rollback
│   ├── patterns.py            Historical patterns and transfer between similar roads
│   ├── forecasting.py         Live forecasts, method fallbacks, replay, evaluation
│   ├── incidents.py           Community reports, official notices, broadcasts
│   ├── demand.py              Planned trips, PCU demand, corridors, hotspots
│   ├── routing.py             Route risk scoring and departure advice
│   ├── notifications.py       Trip re-checks, notifications, journey monitoring
│   ├── cameras.py, vision.py  Camera registry, stream sampling, video analysis
│   ├── alerts.py              Operational alerts and recommendations
│   ├── retention.py           Retention policy and daily summaries
│   ├── scheduler.py           Background jobs
│   ├── access.py              Authority access requests and revocation
│   └── security.py            Tokens, roles, and password hashing
├── data/                      Dataset archive, local database, logs (not included in GitHub)
├── frontend/                  React and Vite website
├── models/                    Trained model versions (not included in GitHub)
├── tests/
│   ├── backend/               API tests (pytest) with a fake dataset and fake Google services
│   └── browser/               Website tests (Playwright) against a test server
├── .env.example               Safe configuration template
├── .gitignore                 Excludes local settings, databases, environments, and builds
├── pytest.ini                 Test settings
├── README.md
├── requirements.txt           Root-level Python dependency entry point
└── requirements-dev.txt       Test tools (pytest)
```

## Local website versus a public website

The steps above run the website on **the same computer** at `127.0.0.1`. Other people cannot access that address from their devices. Uploading the source code to GitHub stores and shares the code; it does not host or run the website.

To let people use FlowState over the internet, the frontend and backend must be deployed to hosting services, a production database must be configured, and secrets must be set as private deployment environment variables. This project does not currently include a production deployment configuration. Do not expose the development server or authority credentials publicly.

## Known limitations

- The historical dataset ends in 2024 and has no live feed, so live forecasts rely on historical patterns unless verified recent observations are added. Its congestion labels come from the dataset, not from verified sensors.
- Road locations are approximate reference points, not surveyed road geometry; authorities can correct them on **Data coverage** (or place them by name with place search).
- The time-block hours are an assumption (see `TIME_BLOCK_STARTS`).
- Route risk is a transparent research heuristic, and planned trips are intentions, not confirmed vehicles.
- Google Maps, Places, and Routes features require valid restricted API keys. The OpenStreetMap fallback is for light use only (see its usage policies above). Weather depends on Open-Meteo availability.
- Camera counts are uncalibrated estimates from a general-purpose detector; dense crowds are undercounted.
- Live location and desktop alerts work only in a browser on this computer (`127.0.0.1`) or over `https://`; browsers block both on plain `http://` addresses of other devices. Alerts are not sent while the website is closed, and there are no SMS or email alerts.
- A public deployment still needs security review, production database operations, monitoring, and account recovery (there is no "forgot password" email yet).

## Before publishing to GitHub

1. Review the files that will be committed. Do not commit `.env`, database files, `.venv`, `node_modules`, build output, private footage, or secret credentials.
2. The included `.gitignore` excludes the local environment, settings, database, frontend packages/build, downloaded model weights, and model artifacts. Check GitHub's staged file list before pushing.
3. Confirm that you have permission to redistribute the historical dataset archive. If its license does not allow redistribution, keep it out of the repository and explain how to obtain an authorized copy.
4. Add a `LICENSE` file if you want to specify how others may reuse the source code. No license is included yet.

## License

No license has been selected for this project yet.
