# FlowState implementation and verification plan

These are the initial implementation gates. Follow-on public-data validation and citywide routing were completed, followed by original-data recovery, model selection, clip tracking and public-camera integration. See `ACCURACY_UPGRADE_RESULTS.md`, `SELF_TEST_GUIDE.md` and `VERIFICATION_RESULTS.md` for the current installed state. The pilot-only counts, missing-source statements and zero-record checks below describe earlier stages.

The existing local setup and user settings are preserved. Each module must pass its relevant backend checks and a production frontend build before the next module begins. Browser regressions cover the changed user workflows. Production records are never replaced with test fixtures.

| Module | Implementation | Acceptance gate | Status |
|---|---|---|---|
| 1. Open road routing | Configurable OpenStreetMap/OSRM road routes, alternatives, arbitrary searched places and persisted road geometry | Offline provider/error/geometry tests, frontend build, OpenStreetMap browser workflows, real route smoke check | Complete |
| 2. Incident detours | Verified-only incidents, hard closure exclusion, detour requests, no-open-route response, automatic refresh of displayed routes | Verification/detour/no-route tests, frontend build, incident browser checks | Complete |
| 3. Data fusion | Timestamped historical baseline plus fresh camera and verified observations; explicit provenance, age limits and consistent units | Historical/live/stale/missing/rejected input tests, frontend build | Complete |
| 4. Dynamic routing | Cached local OpenStreetMap pilot graph, A* reference, incremental D* Lite, changing road costs and closures, honest coverage boundaries | Path equivalence, directed access, changed/removed edge and no-path tests, real graph smoke check, frontend build | Complete |
| 5. CCTV reliability | Explicit ByteTrack, reproducible settings, line crossings/directions, timestamps, reconnect/failure handling and annotated validation metrics | Synthetic motion and real recorded-clip tests, stream failure tests, browser video checks, frontend build | Complete |
| 6. Model evaluation | Additional baseline, validation-only selection, robust class metrics, data readiness and reproducible benchmark artifacts | Training/evaluation/activation tests, model browser workflows and frontend build | Complete |
| 7. Integration and handoff | Active-journey consistency, full regression, real local application smoke checks, operating instructions | Complete backend/browser suites, build, real graph and detector checks, running app | Complete |

## Integration acceptance

- A user can search for Bengaluru places and obtain actual road geometry without Google.
- Only verified reports affect routes. Closed routes are not offered as usable alternatives.
- A selected route is reassessed after relevant incidents change; no route oscillation or repeated notifications.
- A fresh camera observation affects route traffic estimates even when historical data is available. Stale, rejected and incompatible-unit measurements are not silently treated as current traffic.
- The routing response identifies the algorithm and whether local graph coverage, external road routing or insufficient coverage applies.
- CCTV reports distinguish visible objects from line crossings. Accuracy is measured against labelled counts, not inferred from successful execution.
- Model quality is judged against chronological baselines using macro-F1 and per-class results. Improvement is never assumed.

## External inputs still required for field validation

The original historical archive and an authorised live camera/representative road recordings are absent. Tests use isolated, clearly identified fixtures and the detector's public sample clip. No new real-world accuracy claim can be made until representative observations and manually checked counts are supplied. Open road maps are not live traffic observations.

## Test record

Each completed module records commands, counts, limitations and any follow-up here.

### Modules 1-3

- Module 1: 23 backend checks, 6 OpenStreetMap browser checks, production build passed. Actual Bengaluru road service returned a 3,333 m route with 122 geometry points.
- Module 2: 19 backend checks, 18 commuter browser checks and build passed. Verified closures cause exclusion/detours; no-open-route has a distinct response. Route-state polling refreshes affected routes.
- Module 3: 15 forecasting/risk/fusion checks, then 5 focused fusion checks including verified CCTV and revoked-camera exclusion, with builds. Fresh readings are applied once, retain provenance and never substitute raw short-clip counts for historical volumes.

### Modules 4-5

- Module 4: 13 road-routing/detour/dynamic-path checks and build passed, followed by 4 focused dynamic-path checks and build. D* Lite matched A* after 100 seeded graph changes. Downloaded OSM pilot: 10,260 nodes, 20,141 directed edges. A real pilot route matched the A* cost. One-way and turn restrictions are enforced; complex restrictions are handled conservatively. Outside pilot coverage, external OSRM supplies road routes.
- Module 5: 16 camera/count-validation/fusion checks passed and 1 optional real traffic stream check was skipped because no authorised road footage is available; build passed. Real detector checks used the Ultralytics public bus photo converted to a short clip, which verifies execution rather than road-count accuracy. All 22 authority browser checks passed, including saving validation settings and comparing manual counts; the phone-layout check passed on repeat after an initial browser timeout.

### Module 6

- 10 model training/activation/rollback/forecast/quality checks and build passed; all 22 authority browser checks passed after the model and readiness UI changes.
- Each LSTM fold fits its own scaler; training profile features see only earlier labels. Both model families compare three forest configurations and class decision weights using validation data only. Standalone RF artifacts can be loaded without an LSTM and do not claim numeric forecasts. The family selected on validation is the only automatic activation choice, subject to held-out acceptance.
- Benchmark artifacts contain dataset fingerprints, seeds, package versions, class distributions, validation trials, held-out metrics and acceptance decisions. Live scoring uses only verified completed block outcomes for forecasts issued before the block; retraction removes that evidence. These checks used isolated fixture history, not the missing original dataset, so no real-project accuracy gain is claimed.

### Module 7

- Fixed overlapping detours being suppressed, distinguished local closures from nearby parallel roads, preserved monitoring when route refresh finds no open route, and ended monitoring when leaving the screen. Twelve focused route checks and build passed.
- Complete backend suite: 114 passed, 1 optional stream test skipped. Final evidence/readiness adjustment: 9 focused tests and build passed.
- All 48 browser checks passed across the main run and a 23-check authority recheck following an unexpected browser closure. The new integrated flow covers report verification, alternative routes, explicit switching and no-open-route handling.
- Restarted the real app after saving a database backup. Real Nominatim place search, local D* Lite, outside-pilot OSRM and detector/reference-count UI checks passed, with no page-script errors. Traffic observation count remains zero. See `VERIFICATION_RESULTS.md` and `LOCAL_SETUP.md`.
- Final complete run after the TensorFlow CPU-export portability check: **115 passed, 1 optional stream test skipped; production build passed**. The real CCTV/manual-count UI also fit a 390 px phone viewport with no sideways overflow.
