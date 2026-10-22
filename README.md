# FlowState — Bengaluru Traffic Monitoring and Journey Planning

FlowState is a student research prototype for collaborative traffic monitoring in Bengaluru. Commuters can plan journeys, view route and traffic context, report incidents, and see authority alerts. Authorized traffic authorities have a separate dashboard for reviewing incidents, publishing alerts, managing approved camera sources, and reviewing uploaded CCTV clips.

The project combines a map-based web interface, a Python API, historical traffic records, and experimental prediction tools. It is intended for learning and demonstration; it is not an official traffic service.

> **Please read:** Route durations are estimates from the road routing graph and are not live traffic travel times. Traffic predictions and video vehicle counts are experimental and are not safety advice. Generated synthetic data is simulated; it is not real sensor data or evaluation ground truth.

## What the website can do

### Commuter

- View the Bengaluru map and search for places.
- Select specific points for a journey and compare available routes.
- View historical traffic context and experimental congestion estimates where data is available.
- Sign up/sign in, report an incident at a location, and view authority alerts.

### Authority

- Sign in using the authority account configured by the project operator.
- View reported incidents and update their status.
- Publish incident broadcasts for commuters.
- Register approved camera sources and upload/review authorized CCTV video clips.

Authority accounts cannot be created through commuter sign-up. A project operator must configure the authority login in a private local `.env` file.

## Technology

- **Frontend:** React 18, Vite 8, Leaflet 1.9, Lucide React
- **Backend:** Python 3.12, FastAPI, Uvicorn
- **Database:** SQLAlchemy 2, SQLite for simple local use, optional PostgreSQL with Psycopg 2
- **Data and models:** NumPy, pandas, scikit-learn, TensorFlow/Keras, Joblib
- **Authentication:** JWT with `python-jose`; password hashing with Passlib
- **Optional video analysis:** Ultralytics YOLOv8, OpenCV, and PyTorch
- **Map services:** OpenStreetMap tiles, Nominatim place search, OSRM route alternatives

## Run FlowState on your computer

These instructions are for a local development run on Windows 10/11. The same project requires Python, Node.js, and npm on the computer that will run it. VS Code is recommended but not required.

### 1. Install prerequisites

Install:

- Python 3.12
- Node.js 20.19+ or 22.12+ (npm is included with Node.js)
- Git, if you are cloning the project from GitHub

Then open PowerShell or the VS Code terminal.

### 2. Get the project files

After the project has been uploaded to GitHub, clone it. Replace the example URL with the URL of your repository:

```powershell
git clone https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
Set-Location YOUR-REPOSITORY
```

Alternatively, download the repository as a ZIP from GitHub and extract it, then open the extracted project folder in the terminal.

### 3. Install backend packages

Run these commands from the project root (the folder containing `README.md`):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The single root `requirements.txt` contains the Python API, database, machine-learning, and video-analysis packages. Installation can take several minutes because TensorFlow and PyTorch are large packages. The first model use may also require model artifacts to be trained or generated; see the note below.

### 4. Create private local settings

From the project root:

```powershell
Copy-Item .env.example .env
```

Open `.env` in a text editor and change the placeholder values:

- Set `JWT_SECRET_KEY` to a long, random secret.
- Set `AUTHORITY_USERNAME` and `AUTHORITY_PASSWORD` to credentials chosen by the local project operator.
- For the easiest local database setup, set `DATABASE_URL=` (leave the value empty). The app then uses a local SQLite database at `data\traffic_monitor.db`.

PostgreSQL is optional. To use it instead, create/configure a PostgreSQL database and set `DATABASE_URL` to its connection string. Do not use the example connection string unchanged.

**Never upload `.env` to GitHub.** It contains private credentials. The included `.gitignore` excludes it; `.env.example` is the safe template and contains placeholders.

### 5. Start the backend API

Open a terminal at the project root and run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000
```

Leave this terminal running. The API documentation will be available at <http://127.0.0.1:8000/docs>.

### 6. Start the frontend website

Open a second terminal at the project root and run:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173> in your browser. Keep both terminals running while using the website. Stop each server with **Ctrl+C** in its terminal.

Commuter users can register from the website. The authority account is provisioned from the `.env` credentials when the API starts for the first time. If those settings are changed after an account already exists, changing the environment values alone does not change the existing account password; update the account in the database or configure a fresh local database.

## Video analysis

Video-analysis packages are included in the single root `requirements.txt`. Only upload video that you are authorized to use. This feature reviews uploaded clips; it is not a live CCTV streaming service. Counts are uncalibrated estimates and must not be treated as verified traffic measurements.

## Data and model files

- Dataset archives and generated data are not included in the GitHub upload. The app can start without them, but historical coverage and model training will be unavailable until an authorized historical dataset is supplied.
- To enable historical-data features, place an authorized copy of the CSV archive at `data\bangalore_traffic_timely_dataset.zip`. The app imports it as historical data and marks it unverified. Check the dataset's license before sharing it.
- To generate a separate simulated dataset from that archive, run this from the root folder:

  ```powershell
  .\.venv\Scripts\python.exe backend\generate_synthetic_dataset.py
  ```

- Generated CSVs, trained models, model metrics, and local databases are not included in the GitHub upload. After adding the authorized historical archive, run the training scripts in `backend\train_model.py` or `backend\train_lstm.py` if a prediction model is needed. Model outputs are research estimates only.

## Project structure

```text
FINAL_YEAR_PROJECT/
├── backend/             FastAPI API, database layer, model and data scripts
├── data/                Local datasets and database (not included in GitHub)
├── frontend/            React and Vite website
├── models/              Local trained model artifacts (not included in GitHub)
├── .env.example         Safe configuration template
├── .gitignore           Excludes local settings, databases, environments, and builds
├── README.md
└── requirements.txt     Root-level Python dependency entry point
```

## Local website versus a public website

The steps above run the website on **the same computer** at `127.0.0.1`. Other people cannot access that address from their devices. Uploading the source code to GitHub stores and shares the code; it does not host or run the website.

To let people use FlowState over the internet, the frontend and backend must be deployed to hosting services, a production database must be configured, and secrets must be set as private deployment environment variables. This project does not currently include a production deployment configuration. Do not expose the development server or authority credentials publicly.

## Known limitations

- Route times are based on OSRM's route graph, not a live traffic speed feed.
- Historical data does not provide road geometry for drawing verified live traffic segments.
- Weather context depends on external provider availability and should be treated as contextual information.
- Traffic predictions are experimental; their reported scores do not prove reliable live performance.
- CCTV review works on uploaded clips, not continuously running live camera streams.
- A public deployment still needs security review, production database operations, monitoring, account recovery/rate limiting, a camera retention policy, and reliable live traffic data sources.

## Before publishing to GitHub

1. Review the files that will be committed. Do not commit `.env`, database files, `.venv`, `node_modules`, build output, private footage, or secret credentials.
2. The included `.gitignore` excludes the local environment, settings, database, frontend packages/build, and model artifacts. Check GitHub's staged file list before pushing.
3. Confirm that you have permission to redistribute the historical dataset archive. If its license does not allow redistribution, remove it from the repository and explain how to obtain an authorized copy.
4. Add a `LICENSE` file if you want to specify how others may reuse the source code. No license is included yet.

## License

No license has been selected for this project yet.
