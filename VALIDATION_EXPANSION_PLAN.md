# Public-data validation and Bengaluru-wide routing

**Completed 10 November stage record.** Missing-archive and missing-feed findings below were true at that time. The supplied original archive and public Banaswadi webcam were subsequently recovered/connected. `ACCURACY_IMPROVEMENT_PLAN.md` and `ACCURACY_UPGRADE_RESULTS.md` document the 11 November follow-up and current limitations. Earlier negative benchmark results remain preserved.

Requested scope: find suitable datasets and traffic videos, measure forecasting and vehicle-count performance, and extend the installed D* Lite graph across Bengaluru. Existing private settings, accounts and earlier work are preserved. Each implementation stage must pass relevant tests and a frontend build before the next stage.

| Stage | Deliverable | Acceptance | Status |
|---|---|---|---|
| 1. Acquire and audit sources | Public Bengaluru traffic records, source/licence/hash manifests, annotated real traffic-video samples, full-region OSM extract | Check schemas, dates, units, missingness and independent reference annotations | Complete |
| 2. Forecast validation | Chronological, reproducible model/baseline comparison on the actual public data, with appropriate time granularity | No invented time blocks, no target leakage; held-out metrics and acceptance decisions; tests/build | Complete |
| 3. Vehicle-count validation | Actual annotated traffic clips, independent count/detection/line-crossing references, validation-only setting selection and held-out results | Report errors including failures, retain annotated-source provenance, tests/build | Complete |
| 4. Bengaluru-wide D* Lite | Real drivable-road graph spanning the application's Bengaluru bounds, efficient snapping/cost updates, map provenance and coverage checks | Paths across multiple city sectors, A* equivalence, closures/reopening/turn restrictions, latency/memory checks, tests/build | Complete |
| 5. Integrate and verify | Running app, accessible results, updated operating notes, full regressions | Actual application checks, backend/browser/build gates, accurate completion report | Complete |

## Source audit findings

- Downloaded Bangalore's Traffic Pulse from the author's public Kaggle API. The metadata declares CC0. It contains 8,936 daily rows from 2022-01-01 to 2024-08-09 across the project's 16 roads.
- This is not the missing 33,072-row six-block archive described in the report. Its missing time-of-day labels must not be invented. Daily evaluation must stay distinct from block and live predictions.
- The original six-block archive was not recovered in the accessible laptop search. Public benchmark results must not be presented as a reproduction of the report's results.
- Annotated UA-DETRAC traffic videos were acquired from the dataset authors' public download page.
- Acquired all reference XML files and six 15-second, frame-aligned samples using only 232 MB of the 9.2 GB image archive. Validation: MVI_41063 (sunny), MVI_39761 (night). Held-out test: MVI_39051 (sunny), MVI_39501 (cloudy), MVI_40863 (rainy), MVI_40742 (night). Original annotation/frame/video hashes and split assignments are preserved under `data/research/vehicle-counts/`.
- Acquired the official Geofabrik southern-India OSM extract and built a road graph spanning 12.70–13.25 N and 77.30–77.90 E, with a boundary buffer. This contains the mapped Bengaluru city boundary and surrounding approaches; it is not a surveyed administrative boundary or a live traffic feed.

Raw archives and videos belong under ignored `data/research/`; map downloads and built graphs under ignored `data/routing/`. Reproducible acquisition/evaluation code and small benchmark reports can be versioned. Never treat detector outputs as reference annotations, select settings on the test set, or publish benchmark-video counts as live Bengaluru observations.

## Daily model result

The actual public-data run `daily-hybrid-20261110-164655` used 1,488 labelled next-day sequences; 225 one-day test outcomes were available. Hybrid accuracy was 0.7956 and macro-F1 0.2991. The RF challenger achieved 0.8489 accuracy and 0.3076 macro-F1. Historical-profile macro-F1 was 0.3396. Both trained candidates failed acceptance, including zero recall for the rare Low/Medium test classes, and remain rejected/inactive. No thresholds were lowered or test results used to force activation. Daily historical patterns remain the supported fallback. This is an honest validation result, not evidence of an accuracy gain or reproduction of the missing six-block archive.

The source/import/model gate passed 8 focused tests and a production build, followed by 19 integration/model/forecast tests and another build. The daily-data path preserves calendar days in training, stored artifacts, replay, coverage and user-facing forecast units. Daily and block model activation are kept separate.

## Vehicle-count result

Held-out evaluation used 752 sampled frames with 9,771 annotated vehicle instances across four 15-second scenes. The validation-selected YOLOv8s/960/0.35 configuration improved class-agnostic IoU50 F1 from 0.8064 to 0.8460 and mean per-frame count error from 3.3564 to 2.0332. Absolute line-crossing error across scenes fell from 11 to 6 over 32 reference crossings. Frame and crossing checks passed.

Distinct-clip counts did not improve: summed absolute errors were 42 (baseline) and 50 (selected), over 86 reference vehicles. Track fragmentation is therefore explicitly flagged, and the interface distinguishes average/peak visible vehicles from track IDs. These results do not validate a Bengaluru camera site or per-class taxonomy accuracy. Report files are `evaluation/vehicle-count-selection.json` and `evaluation/vehicle-count-benchmark.json`.

Fourteen camera/reference/ROI tests passed, one optional authorised-stream test was skipped, and the production build passed. A further focused count-validation run and build passed after clarifying track-total semantics.

## Citywide routing result

The real 2026-11-09 OSM extract produced 1,038,087 directed drivable edges, 456,292 routing nodes, and 1,113,673 geometry points. The mapped city boundary (OSM relation 7902476) is contained within the installed region. Shape contraction retains route geometry, and spatial indexes accelerate snapping and live-cost matching. Turn restrictions and access exclusions remain enforced, including conservative handling of complex restrictions.

All eight sector routes passed D* Lite/A* cost equivalence, closure avoidance, repaired-cost equivalence and reopening restoration. Routes include Kengeri–Whitefield, Bannerghatta–Yelahanka, the airport approach and Nelamangala–Attibele. Initial routes took 1.889–8.226 seconds; closure repairs took 0.227–3.368 seconds. Map load took 9.066 seconds and benchmark peak memory was 2.318 GiB. Seventeen focused routing tests and the production build passed. Full measurements are in `evaluation/city-routing-benchmark.json`.

## Final integration gate

Final full backend suite after the category-consistency correction: 132 passed, no skips in 70.31 s, with acquired MVI_39501 footage supplied for real stream collection. The stream test also passed separately (1 passed in 5.89 s). Production build passed. The complete browser run passed 46 checks before Chrome closed the authority phone-layout page; the full 23-check authority recheck after the correction passed in 42.2 s, covering all 48 distinct workflows across the runs.

The restarted actual app passed sign-in, daily source/forecast/readiness checks, rejected-model checks, a 36.95 km D* Lite route with full geometry, all public benchmark screens, authenticated reference-clip download and YOLOv8s/960 video analysis. The three new screens fit a 390 px phone viewport; no page-script errors occurred. Processing public video left operational traffic observations unchanged. Sources, hashes, negative accuracy findings, timing/memory limits and reproduction steps are recorded in `PUBLIC_DATA_VALIDATION.md` and `VERIFICATION_RESULTS.md`. No real Bengaluru camera feed was invented or registered, and neither rejected forecast candidate was activated.
