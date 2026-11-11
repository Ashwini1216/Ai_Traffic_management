# Verification results — 11 November 2026

The original project archive is restored, the selected passing Random Forest is active, confirmed native-frame clip tracking is enabled, and the deliberately public Banaswadi webcam is connected. The actual application was restarted and checked with these settings. Detailed scientific results and limitations are in `ACCURACY_UPGRADE_RESULTS.md`; operating instructions are in `LOCAL_SETUP.md`.

## Final software gates

- **Backend: 143 passed, no skips, in 70.95 seconds.** The complete suite used acquired MVI_39501 footage for its real stream test. This verifies recording collection, not Bengaluru-site accuracy.
- **Browser: all 49 passed in one complete run** using the installed Playwright Chromium browser. This includes commuter/authority workflows, OpenStreetMap maps, authenticated camera previews, video review, model replay, closure alternatives, phone layouts and script-error checks.
- **Production frontend build: passed** after the final UI integration. The running app served that build during the actual checks below.
- **Focused module gates:** source/model 13 passed plus build; camera reader/snapshot 15 passed plus one optional recording skip and build; confirmed clips 12 passed plus build. The later full backend gate supplied an actual recording and had no skips.
- **Production counting parity:** actual MVI_41063 processing returned 46 tracks on 375 native frames, identical to its benchmark with the same ignored regions.
- **Visual review:** model comparison, real webcam image, actual clip results and the phone screens reviewed. All eleven authority pages had 390 px page width at a 390 px viewport; no sideways page overflow or page-script errors were observed.
- **Private files:** `.env`, its accuracy-upgrade backup and the pre-restoration database backup have owner-only read/write permissions. Settings, source archives, videos, runtime databases, model artifacts, map files and weights remain ignored. `git diff --check` passed.

Earlier Chrome runs closed unexpectedly on the authority phone page or during the actual clip check. A complete 24-check authority rerun passed, and the final full run and actual-app check both passed with installed Playwright Chromium. Earlier failed attempts remain in the local logs; they are superseded by the clean final run, not counted as additional distinct tests.

Regression tests use isolated fixtures and service stand-ins. Their scores are not research benchmark results. The live-app check below uses the real database, graph, selected model, detector and public camera.

## Actual running application

Completed **2026-11-11 05:25:43 UTC / 10:55:43 IST**.

| Check | Observed result |
|---|---|
| Home and authority sign-in | Passed; SQLite connected |
| Original source restored | 33,072 block + 8,936 daily = 42,008 historical records; source provenance shown |
| Active model | `rf-20261111-041533`; historical accuracy 0.9341; macro-F1 0.6321 |
| Data readiness | Block model ready; 21,654 labelled sequences |
| Current forecast | `historical_pattern`; original data is too old to supply a current six-block sequence |
| Historical replay | Active version returned three horizons with observed reference classes |
| Local routing | Bengaluru-wide graph; D* Lite route 36,950 m / 1,048 geometry points; A* alternative also returned |
| Cross-city request | 1.715 seconds on the warmed graph; not a cold-start latency measurement |
| Published research results | Original recovery, active model, prior/new counting reports, public camera and city routing available |
| Reference samples | 12 saved clips; authenticated download succeeded |
| Actual public webcam preview | Real 960×594 image; live-view start/stop succeeded |
| Actual public webcam collection | 16 frames at 0.95 fps; average visible 0.69, peak 2; stored count 2 |
| Camera trust/units | Quality unverified; automatic verification blocked; hourly flow null |
| Real uploaded clip | MVI_39051, 15 seconds; all 375 native frames; YOLOv8s/960/confidence 0.35, agnostic NMS |
| Enabled clip policy | Association confidence 0.1; new track 0.55; buffer 90; five-frame confirmation |
| Actual clip output | Average visible 2.82, peak 5; 13 confirmed tracks = 11 car + 2 bus tracks |
| Clip semantics | Unverified estimates; standalone sample counter creates no traffic observation |
| Phone widths | All 11 authority pages: 390 px at a 390 px viewport |
| Page-script errors | None |

Camera numbers change with real traffic and are predictions, not manually verified counts. The uploaded clip workflow uses raw unmasked imagery; its output is not the independent ignored-region test score. Real camera collection and the enabled scheduler can add genuine unverified observations while checks are running, so total operational database rows are not asserted to stay constant across the entire session.

## Evidence retained locally

- `data/logs/accuracy-final-backend.log`: full backend results.
- `../accuracy-browser-bundled.log` and `../browser-results.json`: clean final 49-test run.
- `../accuracy-browser-validation.log`, `../accuracy-browser-authority.log`: earlier Chrome attempts and authority recheck.
- `../accuracy-app-checks.json`: sanitised actual-app evidence, source counts, model status, replay, routing, live camera, clip settings/counts and phone widths.
- `../accuracy-app-validation.log`: final actual-app check.
- `../accuracy-models.png`, `../accuracy-live-camera.png`, `../accuracy-clip-counts.png`, `../accuracy-phone-*.png`: screenshots reviewed.
- `evaluation/original-dataset-recovery.json`, `evaluation/block-model-selection-benchmark.json`, `evaluation/tracking-upgrade-benchmark.json`, `evaluation/confirmed-video-parity.json`, `evaluation/public-camera-inspection.json`: measured research/source reports.
- `evaluation/city-routing-benchmark.json`: earlier unchanged eight-sector D* Lite/A*/closure/reopening benchmark.

## Practical limits that remain

- Historical forecast accuracy is **not** present-day live accuracy. Both archives end in 2024; block volumes are derived fixed daily shares. Low-class recall is only 0.1087. Fresh independent prospective labels, especially Low/Medium examples, are needed for stronger evidence.
- Clip distinct-count error improved 70→48, but remains substantial. Full flow preservation failed because crossing error rose 1→2; the new policy is restricted to uploaded clips. Chinese footage does not validate Indian categories or this camera site.
- The Banaswadi source is independent, approximate in location, and about one frame per second. It supplies real images and visible-count estimates, not established distinct totals or reliable flow. It remains uncalibrated/unverified and does not automatically adjust commuter congestion. Independent site counts and suitable higher-rate authorised footage are still needed for reliable live fusion.
- Citywide map coverage does not imply citywide live traffic coverage. Route weights and live-fusion weights remain research heuristics; missing roads/restrictions and publisher outages can affect availability.

The app was checked at [FlowState](http://127.0.0.1:8000). Restart with `Start FlowState.command`. Private settings/accounts were preserved and remain outside Git. Source code, tests and small reports can be published separately from the laptop's runtime assets. The publication PDF was not edited in this phase.
