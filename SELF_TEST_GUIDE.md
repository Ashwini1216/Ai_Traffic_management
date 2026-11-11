# FlowState expectations and self-test checklist

Prepared on 11 November 2026 for the installed laptop project. This guide separates a working software feature from independently validated prediction accuracy. No software or scientific test is claimed to pass merely because an image or a number appears.

## Start and sign in

1. Double-click `Start FlowState.command` in this project folder. Keep its terminal open. If FlowState is already running, use the existing instance.
2. Open [FlowState](http://127.0.0.1:8000).
3. Use a commuter account for journey/report tests. Use **Authority sign in** for administration; the local username is `admin`, and the password is in the private `../LOCAL_LOGIN.txt` file.
4. For tests involving both roles, use separate browser profiles or a normal window and an incognito window. The app stores sign-in per tab/session; do not assume duplicating a signed-in tab changes its role.
5. Use clearly labelled `TEST` incidents/notices and resolve/end them afterward. Use the isolated automated tests for invented traffic observations; do not verify made-up counts into the real historical/training data.

Status terms below: **implemented/tested** means the stated software behaviour was checked; **partial** means a required data source, accuracy result or field validation remains missing; **supported, not connected** means the connection feature exists but your personal camera has not been supplied/configured.

## 1. Select places across Bengaluru without Google Maps

**Expectation:** users can choose places beyond the original fixed list, using OpenStreetMap inside FlowState.

**Work done:** searchable places, map-selected journey endpoints, OpenStreetMap map display, road routes and alternatives. The installed local graph spans the supported Bengaluru region.

**Status:** implemented/tested for supported map coverage.

**Try it yourself:**

1. Enter the commuter portal and open **Compare routes**.
2. Search for a Bengaluru origin and destination; choose the correct address from each result list. Try a place that is absent from the historical-road dropdown.
3. Compare a cross-city journey such as Kengeri to Whitefield. Repeat with Yelahanka to a southern Bengaluru destination.
4. Inspect the route drawn on streets, its distance, estimated duration and road-source description. Zoom in to check that the path follows mapped roads.

**Expected:** selectable searched places and actual road geometry. A local result is described as **Local OpenStreetMap road route · dynamic replanning**. An external OpenStreetMap route can be returned as a fallback.

**Limits:** search/tiles need internet; ambiguous names require choosing the right match. Historical observations still cover 16 roads. A citywide road map does not create measured traffic on every road. Missing/disconnected roads, access restrictions and points outside coverage can prevent a local route. The **Road forecast** dropdown intentionally remains tied to registered roads; arbitrary-place search is provided in journey planning.

## 2. Combine history with real, recent traffic observations

**Expectation:** current trustworthy measurements can influence the historical estimate and route comparison.

**Work done:** a verified-measurement blend with road matching, unit checks, timestamps and freshness limits. Accepted inputs include measured congestion, occupancy, travel-time index or queue information. A raw short-clip vehicle count is not substituted for a multi-hour volume.

**Status:** software implemented/tested; reliable citywide live-data coverage is partial.

**Try the available app workflow:**

1. As a commuter, open **Road forecast**, select a registered road and request a near-term forecast.
2. Read the named method and confidence. With old history and no current compatible sequence, a historical-pattern result is expected.
3. As authority, use **CCTV review → Collect now** for the public Banaswadi camera.
4. Open **Observations & jobs** and locate the new camera reading under **Awaiting review**. It should remain unverified.
5. Do not treat that unverified reading as an automatic verified live adjustment. Its road is Banaswadi; it also cannot affect an unrelated road merely because both are in Bengaluru.

**Positive fusion check:** once a genuine independently checked, compatible measurement is verified for the same road, a near-term forecast/route can show **Includes a verified CCTV/traffic reading**. Look for the evidence and timestamp; a changed congestion category is not guaranteed for every measurement.

**Safe isolated test of the full logic:** run `.venv/bin/python -m pytest -q tests/backend/test_18_data_fusion.py` from the project folder. It tests fresh verified input, stale input, incompatible raw counts, and revoked-camera exclusion using a temporary database.

**Limits:** measurements fade over 15 minutes and are not projected beyond two hours. The public camera is not calibrated and does not automatically adjust commuter forecasts. Current model forecasts need recent compatible history; having an active model does not make the 2024 records current. Fusion weights remain research estimates requiring field validation.

## 3. Verify user incidents and suggest alternatives

**Expectation:** a commuter report is reviewed by an authority; verified hazards affect route recommendations, with alternative routes when available.

**Work done:** verification workflow, pending-report exclusion, verified incident risk, hard exclusion of verified closures, route refresh and explicit route switching.

**Status:** implemented/tested; an alternative depends on an open road path existing.

**Test A — report verification:**

1. In the commuter window, open **Report an incident**.
2. Select an incident category, enter the correct area and road, and use a description beginning `TEST —`.
3. Press **Send report**. It should remain unverified.
4. In the authority window, open **Incident map**, find it and press **Verify**.
5. Return to the commuter route screen. Allow a refresh cycle or compare again. A nearby verified incident should be reflected in route evidence/risk; a different route is not guaranteed for every minor hazard.
6. In the authority incident filter, show verified reports and **Resolve** the test report afterward.

**Test B — clear closure/detour behaviour:**

1. First calculate a route and identify a road segment it actually uses.
2. In authority **Official notices**, select **Road closure**, enter a `TEST —` description and place the notice on that exact road using the map. Keep its time window active now and **Road is closed** checked.
3. Press **Publish notice**.
4. Return to the commuter route view. Wait approximately 15–30 seconds or compare again.
5. If an open alternative exists, the app should avoid the blocked segment. If all available paths are blocked, it should explain that no open route was found.
6. If journey monitoring is running and an alternative is offered, press **Switch to this route** and confirm the accepted route changes.
7. End the test notice in **Official notices → End notice**, then compare again.

**Limits:** location must match the actual road, particularly near parallel roads/junctions. An accident raises risk; it does not always close a road. Route suggestions depend on available paths. Displayed conditions refresh while the relevant page is open; a monitored journey switches after user acceptance. Speed and arrival-time estimates are not guaranteed real travel times.

## 4. D* Lite across Bengaluru

**Expectation:** dynamic routing beyond the old small pilot area, including route repair after closures.

**Work done:** installed OSM graph with 456,292 routing nodes and 1,038,087 directed edges; D* Lite for the primary local path and A* comparisons/alternatives. Eight city-sector route, closure and reopening cases passed.

**Status:** implemented/tested throughout the installed supported region.

**Try it yourself:**

1. Open authority **Data coverage** and inspect **Bengaluru routing validation**. It should show eight passing sector cases and the graph size.
2. In the commuter view, request cross-city routes rather than only routes around the original pilot roads.
3. Inspect the local dynamic-replanning description and street geometry.
4. Repeat the exact-road closure/reopening procedure in section 3. Avoidance followed by restored availability checks dynamic route behaviour.

**Limits:** a changed route alone does not prove which algorithm produced it; use the validation results and route provenance as well. Routes served by external OSRM are not D* Lite results. The first map load and long routes can take seconds; benchmark initial routes were approximately 2–8 seconds with about 2.3 GiB peak memory. Map coverage is wider than live traffic coverage.

## 5. Improve forecasting accuracy and show evidence

**Expectation:** improve model performance, evaluate properly and use a version that passes quality checks.

**Work done:** restored original data; chronological validation/model comparisons; unchanged acceptance checks; activation of `rf-20261111-041533`, a standalone Random Forest. Historical test accuracy is 93.41% versus 88.94% for the historical-profile baseline; macro-F1 is 0.6321 versus 0.6039 on the same 3,246 outcomes.

**Status:** historical comparison completed; present-day accuracy and strong performance for every class are not established.

**Try it yourself:**

1. Open authority **Models**.
2. Check that **Random Forest** is active and select version `rf-20261111-041533`.
3. Read its baseline comparison, per-class precision/recall and acceptance checks. Rejected candidates should remain rejected.
4. In **Replay on past records**, choose **Yeshwanthpur Circle, Yeshwanthpur**, date **2024-07-23**, block **Daytime**. Press **Replay**.
5. Inspect the next three blocks' predicted and observed classes. These inputs were checked successfully in the running app.
6. Separately request a current road forecast. If recent required observations are absent, its historical-pattern fallback is expected and should be labelled.

**Expected:** active RF, stored results, three replay horizons and honest current-method labels. RF is a class predictor; a blank numeric forecast-volume cell is not itself a failure.

**Limits:** the 93.41% is historical re-evaluation, not live accuracy or a new untouched test. Low recall is only 10.87% (5 of 46 cases). Source blocks are derived fixed daily shares, and both archives end in 2024. The active result belongs to standalone RF, not the original LSTM + RF hybrid; do not attribute it to the hybrid in the paper. Replay demonstrations alone are not a statistical accuracy test. Do not retrain/activate a candidate merely to change the displayed score.

## 6. Working live camera view

**Expectation:** the authority's live CCTV feature displays a genuine camera and collects observations.

**Work done:** public Banaswadi webcam connected; warm-up placeholders skipped; authenticated snapshots; start/stop preview and interval collection.

**Status:** connected and tested; source availability and site accuracy remain conditional.

**Try it yourself:**

1. Open authority **CCTV review**.
2. In **Live camera view**, select **Public Banaswadi road webcam** and press **Start live view**.
3. Allow a few seconds for warm-up. Compare the road image across refreshes; movement/scene content should change when traffic changes.
4. Press **Stop live view**. Refreshing should stop, with the last image identified as stopped.
5. Find the same source under **Registered cameras** and press **Collect now**.
6. In **Observations & jobs**, confirm a new unverified camera reading is stored. The source supports peak visible counts; hourly flow is intentionally unavailable.

**Limits:** independent public webcam, not a police feed; approximate location; approximately one frame per second, with preview refresh every ten seconds. Vehicles can pass between source frames. Distinct totals, reliable line flow and queues are not established. Automatic verification is blocked for such low-refresh imagery. A camera opening successfully does not prove its predicted counts are correct. Availability can change.

## 7. Improve vehicle counts and compare with manual references

**Expectation:** uploaded/live footage provides useful vehicle counts, supported by an accuracy check.

**Work done:** YOLOv8s detector, confirmed native-frame tracking for uploaded clips, one category per track, separate visible-frame and distinct-track estimates, and manual-reference comparison. Four new annotated test scenes showed distinct-count absolute error 70→48, a 31.4% reduction. Twelve unique reference clips are available across the studies.

**Status:** uploaded-clip improvement measured; exact counts and reliable new-policy flow are not established.

**Try it yourself:**

1. In **CCTV review**, expand **Vehicle-count benchmark results** and read the old/new comparisons, including the failed flow check.
2. Download a saved clip, then upload it under **Count people and vehicles in a video**.
3. Wait for counting to finish. Inspect **Vehicles in view, on average**, **Most vehicles at once**, **Vehicle tracks seen**, analysed frames and preview.
4. Check that vehicle-category totals equal the displayed track total. Average visible vehicles and distinct tracks are different quantities and should not be expected to match.
5. For an accuracy test, independently count distinct vehicles in the same analysed interval. Enter reference counts and a review note under **Check against a manual count**, then press **Compare reference counts**. Enter zero only for a class you actually checked; leave unreviewed classes blank.
6. Repeat on separate footage covering quiet/busy traffic and different conditions. A one-clip check does not establish general accuracy.

**Useful smoke example:** the completed MVI_39051 upload check analysed all 375 frames of a 15-second clip and returned 13 confirmed tracks (11 car, 2 bus), average visible 2.82 and peak 5. These are recorded predictions from the current configuration, not manual ground truth or a universal expected count.

**Limits:** occlusion, small objects, darkness, viewpoint and class confusion cause misses/duplicate IDs. The new tracking policy worsened line-crossing error 1→2, so its full flow gate failed and it is restricted to uploaded clips. Stream line counting retains the previous policy. UA-DETRAC test scenes are Chinese; Indian classes and your camera need independent validation. Long clips may be only partly analysed; inspect completion warnings and use references for the analysed interval. Standalone video review does not create live traffic observations.

## 8. Connect your own CCTV

**Expectation:** use your own camera/recorder instead of depending only on the public source.

**Work done:** registry and supported stream readers are available for accessible RTSP/RTSPS, RTMP and supported HTTP/HTTPS video feeds.

**Status:** supported, but your personal camera is not connected yet; brand/model and stream details have not been supplied.

**Try it yourself once the address is known:**

1. Obtain the actual video-stream address from the camera or DVR/NVR. The address of its mobile-app login page is not the video stream.
2. Make sure the laptop can reach it; the same local network is usually simplest.
3. In **CCTV review → Camera registry**, add the name, actual road/location and **Stream address**.
4. Set **Approved** and **Online**, then save. Leave **Counts checked against manually counted road footage** unchecked until independent checks are complete.
5. Use **Start live view**, then **Collect now**. Check both the image and the resulting observation.
6. Validate representative recordings with manual references before treating measurements as trusted route evidence.

**Limits:** compatibility depends on the model/recorder, authentication, format and network access. Cloud-app-only cameras may not expose a directly usable stream. A faster stream can support better tracking but does not guarantee accurate counts. Your own camera must observe a relevant road, be located correctly and be calibrated before its measurements can reliably influence routing.

## Data recovery and local setup checks

- In authority **Traffic data**, expect **42,008 historical observations**, combining 33,072 original blocks and 8,936 daily records. Operational totals can be higher because genuine camera samples continue to arrive.
- In **Data coverage**, inspect the actual roads, record dates and coverage. Road routing coverage is larger than historical-data coverage.
- The laptop uses SQLite; PostgreSQL is supported but is not configured locally. `.env` and backup files are retained privately.

## Optional automated software checks

Open Terminal in this project folder, or first run:

```bash
# Replace this with the folder where you cloned the repository.
cd /path/to/Ai_Traffic_management
```

Backend suite with the acquired recording supplied for the optional real-stream case:

```bash
TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1 FLOWSTATE_TEST_VIDEO="$PWD/data/research/vehicle-counts/clips/MVI_39501/sample.avi" .venv/bin/python -m pytest -q tests/backend
```

Frontend build:

```bash
npm --prefix frontend run build
```

Browser suite using the installed test browser and isolated test services:

```bash
TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1 npm --prefix tests/browser test
```

The latest completed checks were **143 backend tests passed**, **49 browser tests passed in one run**, and **production build passed**. Backend/browser tests use separate temporary test data. Some detector cases depend on locally installed weights and the recording path; omitting optional assets can cause skips. These checks confirm tested software behaviour; they do not replace forecasting or camera-site accuracy validation.

Research evidence: `ACCURACY_UPGRADE_RESULTS.md`. Software/app evidence: `VERIFICATION_RESULTS.md`. Operating instructions: `LOCAL_SETUP.md`.
