# FlowState on this laptop

Open [FlowState](http://127.0.0.1:8000) while the server is running. To start it again, double-click **Start FlowState.command** in this folder and leave its terminal open.

Private settings are in `.env`. The authority username is `admin`; the password is saved privately in `../LOCAL_LOGIN.txt`. Commuters can register from the commuter portal. The installed database is SQLite. PostgreSQL is supported but is not configured locally. Maps and routing use OpenStreetMap; no Google key is needed for this setup.

## Installed data and model

Both archives supplied by the user were audited. The restored original archive contains **33,072 six-block records**, across 16 roads and eight areas, dated 2023-01-01–2024-08-09. The other archive is byte-identical to the public daily source already installed: **8,936 daily records**, dated 2022-01-01–2024-08-09. There are **42,008 historical records**, plus separately identified actual camera observations.

The six block volumes are rounded fixed shares of the daily source. They are derived time blocks, not independent CCTV measurements. The app displays this provenance. Daily values remain daily and are excluded from block training.

The validation-selected Random Forest `rf-20261111-041533` passed the existing acceptance checks and is active. On 3,246 historical test outcomes it achieved accuracy **0.9341** and macro-F1 **0.6321**; historical-profile macro-F1 was **0.6039**. Low-class recall remains weak at **0.1087**. These are historical re-evaluation results, not present-day accuracy. Rejected cycle/daily candidates remain inactive.

Current forecasts still use clearly labelled historical patterns when there is no recent compatible six-block sequence. Fresh verified observations can influence near-term estimates under the existing trust, age and unit checks. Old 2024 records and unverified camera samples do not establish current congestion.

## Maps and route updates

The installed map spans the supported Bengaluru region: 456,292 routing nodes and 1,038,087 directed edges. D* Lite is available throughout this graph. Eight sector routes passed route-cost, closure and reopening checks. Initial routes took about 2–8 seconds on this laptop; map loading took about nine seconds. **Data coverage** shows the map scope and benchmark. Map coverage does not create traffic observations on every road.

External map tiles and place search need internet access. External OSRM remains a fallback when a local route cannot be found. Route duration and risk weights are research estimates. Verified closures are excluded; recommendations check conditions every 15 seconds and on returning to the tab. A monitored journey switches after **Switch to this route** is pressed. If no open route is found, monitoring remains available until stopped; leaving the page ends monitoring.

## Live Bengaluru camera

**CCTV review → Live camera view** includes **Public Banaswadi road webcam**. Select it and press **Start live view**. The view fetches a current image every ten seconds while open; **Stop live view** stops refreshing. **Collect now** records a separate interval sample. The existing scheduler is enabled locally and also checks approved sources when due; this camera's interval is five minutes while the app is running.

This independent webcam is deliberately linked by its [publisher](https://diagramchasing.fun/2026/mapping-clouds/stations/bengaluru). It is **not a Bengaluru Traffic Police feed**. Its location is approximate. Checked images were 960×594 at about one frame per second, after a small warm-up placeholder. The reader skips placeholders and rejects unusable imagery.

The source supports average/peak visible-vehicle estimates. Its low refresh rate does not establish reliable distinct totals, line crossings, queues or hourly flow. Samples remain **unverified**, and automatic verification is blocked even if someone marks the camera calibrated. They do not automatically alter commuter route congestion. Availability depends on the public publisher.

For a higher-rate authorised camera, register its real stream address, road, region and counting line. Compare representative site recordings with independent manual references before enabling **Counts checked against manually counted road footage**, and record the evidence. Approval and measurement validation are separate settings.

## Uploaded videos and measured counting results

The local detector uses YOLOv8s at size 960, confidence 0.35 and agnostic NMS. Uploaded clips use `YOLO_CLIP_TRACKING=confirmed_native`: every source frame is processed, lower-confidence detections can maintain associations, and a vehicle track requires five frames before contributing to the total. Average/peak visible counts and confirmed track estimates are shown separately. Class totals count each confirmed track once.

On four new annotated UA-DETRAC test scenes, distinct-count absolute error fell **70→48 (31.4%)**, and identity F1 improved **0.7574→0.7619**. Line-crossing error worsened **1→2**, so the full flow gate failed and stream line tracking retains its previous method. Confirmed clip counts still have substantial error. The Chinese benchmark scenes do not validate Indian classes or the Banaswadi camera.

Open **Vehicle-count benchmark results** for both dated comparisons and **12 saved 15-second samples**. Uploading a reference clip through the standalone counter creates no traffic observation. Camera-linked uploads and actual stream samples follow their separate observation/review paths.

## Evidence and backups

See `ACCURACY_UPGRADE_RESULTS.md` for the latest findings and reproduction commands, `VERIFICATION_RESULTS.md` for software/app checks, and `PUBLIC_DATA_VALIDATION.md` for the earlier detector/daily study. Small reports are in `evaluation/`; raw archives, clips, maps, weights and runtime databases remain ignored.

Private `.env` and login details are excluded from Git. `.env`, `.env.before-accuracy-upgrade` and `../flowstate-before-original-restoration.db` have owner-only permissions. Earlier database/settings backups are retained. Source code, tests and small evaluation reports are suitable for GitHub; the installed runtime assets and accounts stay local. The publication PDF was not changed during this implementation.
