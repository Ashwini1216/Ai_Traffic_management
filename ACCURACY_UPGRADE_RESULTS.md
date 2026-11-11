# FlowState accuracy, original data and live-source results

Checked on this laptop on **11 November 2026**. The original archive has been restored, a passing Random Forest is active, uploaded-clip tracking has been improved, and a deliberately public Bengaluru webcam is connected. These findings distinguish historical forecast evaluation, independent foreign video benchmarks, and actual unverified local camera observations.

## Original project data recovered

The two archives supplied by the user were audited before import. A database backup was created at `../flowstate-before-original-restoration.db`.

| Archive | Result |
|---|---|
| `bangalore_traffic_timely_dataset.zip` | 695,179 bytes; 33,072 valid rows; 16 roads; eight areas; six daily blocks; 2023-01-01–2024-08-09 |
| `bangalore-dataset.zip` | 572,018 bytes; 8,936 daily rows; byte-identical to the public daily archive already installed; 2022-01-01–2024-08-09 |

The first archive matches the size, schema, row count and first-row values recorded in the old project history. Its SHA-256 is `82b228ffa5bfd3c3f01dd8ac829c0d6d9b98301e2dd710ad0d7ba2b702314927`. The daily archive's SHA-256 is `09202e1aeaff512980a15009d18e328a5cd75d386a98df6ea150629776d68998`.

**Provenance finding:** all six-block volumes are daily volumes multiplied by fixed shares and rounded: Midnight 4%, Morning 8%, Daytime 26%, Noon 19%, Evening 28%, Night 15%. Maximum rounding discrepancy is 0.5. These are derived blocks, not six independent sensor measurements. No import rows were rejected, and daily records were preserved separately. The database now has **42,008 historical records**, plus genuine camera observations added separately.

Evidence: `evaluation/original-dataset-recovery.json`, ignored `data/research/original-archive/source-manifest.json`, and `backend/recover_project_data.py`. The restored original archive is in ignored `data/`; the user's Downloads files remain unchanged.

## Forecasting result and activation

The selected model is **`rf-20261111-041533`**, a standalone Random Forest using the existing causal feature set. It passed the unchanged baseline, majority-accuracy and supported-class-recall checks and is active in the model registry.

Chronological target-date split: training before **2024-02-16**, validation until **2024-05-14**, then testing. There are 15,228 training, 3,180 validation and 3,246 test outcomes. Settings and decision weights use validation data only; historical training profiles exclude target/future labels.

| Same 3,246 test outcomes | Accuracy | Macro-F1 |
|---|---:|---:|
| Selected Random Forest | **0.9341** | **0.6321** |
| Historical profile | 0.8894 | 0.6039 |
| Persistence | 0.7575 | 0.3642 |
| Majority class | 0.8460 | 0.3055 |

Macro-F1 gain over the strongest simple baseline is **0.0282**, above the existing required 0.02. Accuracy exceeds the majority baseline. Every class with at least five test records is recalled at least once. The selected RF is the candidate, not an additional baseline against itself.

| Class | Test support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| Low | 46 | 0.1351 | **0.1087** | 0.1205 |
| Medium | 454 | 0.7208 | 0.9097 | 0.8043 |
| High | 2,746 | 0.9917 | 0.9519 | 0.9714 |

**Limits:** the aggregate improvement is not uniform. Low recall remains poor and is lower than the profile baseline's 0.1522. The source is imbalanced and its block volumes are derived. The historical test period has already been examined during project development and successive experiments; call this **historical re-evaluation**, not a new untouched test or a 93.41% live-traffic accuracy claim. The next evaluation should use independent, recent, prospectively collected labels with enough Low and Medium cases.

Additional causal cycle features, gradient boosting and regularised logistic models were compared. A paired date-clustered validation one-standard-error rule selected the simpler existing-feature RF: validation macro-F1 difference 0.0008, standard error 0.0124057, 400 resamples, seed 42. This selection uses validation only; acceptance thresholds were not reduced. Earlier cycle candidates failed their extra gain comparison and remain rejected. Training metadata, source fingerprint, class metrics and settings are retained in `evaluation/block-model-selection-benchmark.json` and the model artifact directory.

A gap-aware daily Extra Trees research experiment scored accuracy 0.7461 and macro-F1 0.4776 on 1,461 eligible daily targets. This broader population differs from the earlier 225 consecutive-day targets, so those scores are not directly comparable. That research artifact is not registered or active. The two earlier daily RF/hybrid candidates remain rejected.

Current 2026 forecasts still use a clearly labelled historical-pattern fallback without six recent compatible blocks. A successful historical replay does not justify predicting directly from a two-year-old sequence. Fresh verified observations can influence near-term estimates under the existing age/unit/trust checks; unverified public-camera counts cannot automatically supply that evidence.

## Vehicle-count improvement and failed flow check

The previously selected detector remains YOLOv8s, image size 960, display confidence 0.35, agnostic NMS. The new comparison changes association and track confirmation for **uploaded clips only**.

Four official training scenes were used for validation: MVI_41063 (sunny), MVI_39761 (night), MVI_63521 (rainy), MVI_40141 (cloudy). Four new official test scenes were selected from metadata before inference, excluding previously scored test scenes: **MVI_39211, MVI_40701, MVI_40864, MVI_40743**. Each is a 15-second, 375-frame, 25 fps excerpt. Source/annotation/weight/software hashes are retained locally. Additional source transfer was approximately 198.5 MB, avoiding the full 9.2 GB archive.

Validation selected native-frame association with confidence 0.1, new-track confidence 0.55, buffer 90 and **five observed frames to confirm a track**. The benchmark uses independent annotated vehicle identities, symmetric source ignored regions and a fixed line selected before viewing detector performance. Detection/identity scores use the same 752 common frames; distinct and crossing references use all source frames. No settings were changed after the new test results.

| Four new test scenes | Previous tracking | Selected clip tracking |
|---|---:|---:|
| Reference distinct vehicles | 105 | 105 |
| Predicted distinct track estimates | 175 | 153 |
| Summed absolute distinct-count error | 70 | **48** |
| Identity F1 | 0.7574 | **0.7619** |
| Reference crossings | 26 | 26 |
| Summed absolute crossing error | **1** | 2 |
| Frame count MAE | 2.3484 | 2.3484 |
| Detection F1, IoU50 | 0.8199 | 0.8199 |

Distinct-count error decreased **31.4%**. Frame detections were unchanged because the detector was frozen. **The full flow acceptance gate failed**: crossing error increased 1→2. Consequently the new policy is restricted to uploaded-clip estimates; stream line-counting retains the previous method. The failed flow result is visible in the app and saved report, not relabelled as a pass.

This is a scoped implementation decision, not satisfaction of the original full-flow gate. Clip estimates still overcount and miss vehicles, and do not become verified distinct counts. Class totals use one majority class per confirmed track; average and peak visible vehicles remain separate. A validation-scene parity check processed 375 actual frames through production and benchmark code: both returned 46 tracks with the same source ignored regions.

These UA-DETRAC scenes are from China. They do not establish accuracy for Indian motorcycles/autorickshaws, a Bengaluru camera site or every weather condition. The older detector comparison's MAE 3.3564→2.0332 and F1 0.8064→0.8460 belong to its different four-scene test set; do not combine those scores with this new tracking test.

Evidence: `evaluation/tracking-upgrade-selection.json`, `evaluation/tracking-upgrade-benchmark.json`, `evaluation/confirmed-video-parity.json`. Runtime: `backend/confirmed_video.py`, enabled privately by `YOLO_CLIP_TRACKING=confirmed_native`. The legacy option remains available for comparison.

## Genuine public Bengaluru source connected

The [Diagram Chasing Bengaluru station](https://diagramchasing.fun/2026/mapping-clouds/stations/bengaluru) deliberately publishes the [public webcam stream](https://view.custardlev.uk/video_feed). Source inspection received changing **960×594** road images at approximately **0.9–1 frame per second**, after an initial tiny placeholder. The app skips those placeholders and measures actual frame arrival rather than assuming 25 fps.

The registered source is **Public Banaswadi road webcam**, ID `public-banaswadi-webcam`, approximately Banaswadi Main Road–2nd Cross. Its map reference is approximate. This is an **independent public webcam, not a Bengaluru Traffic Police feed**. No private camera was discovered through scanning or bypassing access controls.

Authority workflow: **CCTV review → Live camera view → Start live view**. Images refresh every ten seconds while open and can be stopped. **Collect now** records a 20-second sample; the enabled local scheduler checks this camera every five minutes when the app is running.

An initial actual collection analysed 19 frames at 0.95 fps, average visible vehicles 3.05 and peak 5; the stored count was the peak **5**, with `flow_per_hour=null` and quality **unverified**. These are predictions, not manually established accuracy measurements. Live-app recheck values are recorded separately in `../accuracy-app-checks.json` because road traffic changes.

Low-refresh samples have no line-crossing flow/queue claim and cannot be automatically verified, even if the registry's calibration option were enabled. Unverified samples do not automatically alter commuter traffic forecasts. The camera view proves a real accessible changing source; it does not establish trustworthy citywide real-time congestion. Site calibration requires independent manual references and representative footage; reliable flow also requires a suitable higher-rate source. Public-source availability can change.

Evidence: `evaluation/public-camera-inspection.json`, ignored capture manifests/photos, the authenticated snapshot/collect endpoints, and the running-app check. Public camera addresses are not returned by the registry API; the published source link is deliberately visible in the source report.

## Reproduction on the configured laptop

Run from the project root; raw downloads and model artifacts stay ignored. Training reruns register a new candidate and do not activate it unless explicitly requested.

```bash
.venv/bin/python backend/recover_project_data.py --blocks "$HOME/Downloads/bangalore_traffic_timely_dataset.zip" --daily "$HOME/Downloads/bangalore-dataset.zip" --restore
.venv/bin/python backend/train_cycle.py
.venv/bin/python backend/acquire_counting_holdout.py
.venv/bin/python backend/evaluate_tracking_upgrade.py
.venv/bin/python backend/connect_public_camera.py --register --collect
.venv/bin/python backend/validation_reports.py
```

The recovery command recognises the installed archive and avoids duplicate import. Reconnecting and collecting creates a genuine unverified camera observation. The optional daily research command is `.venv/bin/python backend/train_daily_challenger.py`.

`VERIFICATION_RESULTS.md` records the final software and actual-app checks. The small reports are available in authority **Models**, **CCTV review**, and **Data coverage**. Raw benchmark videos never become live traffic records through the standalone video counter. Private settings and restoration backups have owner-only permissions. Only source code, tests, documentation and small reports belong in Git; runtime assets remain local. The publication PDF was not edited in this phase.
