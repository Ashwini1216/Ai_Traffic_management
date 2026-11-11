# Public-data validation and citywide routing — 10 November 2026

**Historical report.** The statements below describe the 10 November run. On 11 November the user supplied the matching original archive, it was restored, a passing block Random Forest was activated, an additional clip-count comparison was completed, and a deliberately public Banaswadi webcam was connected. Read `ACCURACY_UPGRADE_RESULTS.md` and `LOCAL_SETUP.md` for the current state. The earlier daily-model failures and detector scores below remain valid for their stated populations; they are not the newer model or tracking results.

This report describes real downloaded data and measured results on this laptop. It does not reproduce the publication's missing six-block archive. No benchmark-video measurements were added as live Bengaluru traffic. Small, machine-readable reports are in `evaluation/`; source archives, annotations, videos, map files and weights remain in ignored local folders.

## Traffic records and forecasting

The author's [Bangalore's Traffic Pulse dataset](https://www.kaggle.com/datasets/preethamgouda/banglore-city-traffic-dataset) contains **8,936 daily records**, **16 roads**, and **8 areas**, dated **2022-01-01 to 2024-08-09**. The publisher metadata declares CC0. The schema has a numeric congestion index, but no time-of-day blocks or Low/Medium/High labels. The import preserves the raw index and daily units; project severity bins are fixed equal thirds of the index range. These labels are not independently verified sensor ground truth.

The original report describes a different 33,072-row, six-block archive. That archive was not recovered. The public source was imported as `public_daily_dataset`, `time_block=Daily`, with 86,400-second intervals and unverified quality. It is excluded from block-model training. Daily and block model activation are separate.

Training used six consecutive calendar days and chronological target-date splits: training before 2023-10-26, validation until 2024-03-04, then held-out testing. Settings were chosen on validation data. Scalers and historical profiles were fitted without later targets; forecast-origin sequence inputs can include already observed preceding days. There were 1,488 labelled sequences overall and 225 one-day test outcomes. Full seeds, settings, splits, fingerprints, baselines, class metrics and acceptance checks are saved in `evaluation/daily-forecast-benchmark.json` and each model's `benchmark.json`.

| One-day held-out result | Accuracy | Macro-F1 |
|---|---:|---:|
| Hybrid LSTM + RF | 0.7956 | 0.2991 |
| Standalone RF | 0.8489 | 0.3076 |
| Majority baseline | 0.8889 | 0.3137 |
| Persistence baseline | 0.7778 | 0.3096 |
| Historical profile | 0.8311 | 0.3396 |

**Both candidates failed acceptance and remain rejected/inactive.** Both had zero recall for the Low and Medium test classes (supports 7 and 18; High support 200). No accuracy gain is claimed. Historical patterns remain the fallback. Daily patterns cannot supply different measured morning/evening values, and old records cannot validate present-day traffic. More representative labelled sequences are needed to improve this result; acceptance thresholds were not reduced.

## Independent video/count validation

Acquired six **15-second** frame-aligned clips and independent XML annotations from the [UA-DETRAC authors](https://sites.google.com/view/daweidu/projects/ua-detrac), described in their [benchmark paper](https://arxiv.org/abs/1511.04136). These are Chinese traffic scenes, not Bengaluru CCTV feeds. Source hashes, original frame hashes, annotation hashes, clip hashes, weather labels and splits are retained in `data/research/vehicle-counts/selection.json` and per-clip manifests. Only selected archive ranges were downloaded (232 MB), rather than the full 9.2 GB image archive. Consult the authors' terms before redistributing source footage.

- Validation scenes from the official training set: MVI_41063 (sunny), MVI_39761 (night).
- Held-out official test scenes: MVI_39051 (sunny), MVI_39501 (cloudy), MVI_40863 (rainy), MVI_40742 (night).
- Compared the original YOLOv8n/640/confidence 0.35 with YOLOv8s/960 candidates at confidence 0.25 and 0.35. Selected the latter 0.35 setting using validation-only detection F1, then count error. Agnostic NMS is enabled; ByteTrack is explicit. No settings were changed using the test results.
- Evaluation combines vehicle classes because source/COCO categories do not map one-to-one. Source ignored regions are applied to detections and references alike. Detection matching requires IoU ≥ 0.5; line-crossing references use all original annotated frames and vehicle IDs. Per-frame comparison uses the actual configured sampled frames.

| Four-scene test result | Original | Selected |
|---|---:|---:|
| Sampled frames | 752 | 752 |
| Independent vehicle instances | 9,771 | 9,771 |
| Mean absolute count error per frame | 3.3564 | 2.0332 |
| Weighted absolute percentage count error | 25.83% | 15.65% |
| Detection precision, IoU50 | 0.9426 | 0.9109 |
| Detection recall, IoU50 | 0.7045 | 0.7898 |
| Detection F1, IoU50 | 0.8064 | 0.8460 |
| Sum of absolute line-crossing errors (32 reference crossings) | 11 | 6 |
| Sum of absolute distinct-vehicle errors (86 reference vehicles) | 42 | 50 |

Frame-count and crossing checks improved. **Distinct-vehicle totals failed the comparison:** track IDs fragment, causing vehicles to be counted again. The interface now distinguishes average/peak visible vehicles from track IDs and warns about this overcount. F1 is a detection metric, not “84.6% counting accuracy.” Class-specific Indian vehicle accuracy, speed estimates, full-weather robustness and a real Bengaluru camera site have not been validated. Local `.env` uses the selected configuration; the original detector remains available.

The final app review also found and corrected a category-aggregation error: a track whose detector label changed could appear in several class totals. Each vehicle track now receives its most frequent vehicle label, so category totals sum to the track total. If tracking is unavailable, category counts come from the same peak vehicle frame rather than independent per-class peaks. This consistency fix does not establish per-class recognition accuracy or solve track fragmentation; it does not alter the class-agnostic benchmark results above.

The authority CCTV page shows the benchmark and provides downloads of the six reference clips. These downloads do not register cameras or create traffic observations. Raw videos and annotations are at `data/research/vehicle-counts/clips/<sequence>/`. Full per-scene results and package/weight hashes are in `evaluation/vehicle-count-benchmark.json`.

## Bengaluru-wide D* Lite

Built the local graph from [Geofabrik's southern India OpenStreetMap extract](https://download.geofabrik.de/asia/india/southern-zone.html), map timestamp **2026-11-09T21:22:17Z**. The installed bounds are **12.70–13.25 N, 77.30–77.90 E**, plus a 0.02-degree buffer. These contain the mapped city boundary (OSM relation 7902476) and surrounding approaches, including the airport route. Availability still depends on mapped, accessible, connected driving roads.

The runtime graph has **456,292 routing nodes, 1,038,087 directed edges and 1,113,673 geometry points**. Shape contraction preserves road polylines and distances; spatial indexes accelerate snapping and observation/closure matching. One-way/access rules, node-via turn restrictions and conservative complex-restriction handling remain enforced. D* Lite repairs changed road costs; A* supplies alternatives and an independent cost reference. The previous small pilot is retained as a fallback if the city graph is removed.

**All eight sector tests passed** initial-cost equivalence with A*, blocked-edge avoidance, repaired-cost equivalence and reopening restoration: Indiranagar–Hebbal, Kengeri–Whitefield, Central–Electronic City, Hoskote–Yeshwanthpur, Bannerghatta–Yelahanka, Nagarbhavi–Sarjapur, city centre–airport and Nelamangala–Attibele. Initial paths took **1.889–8.226 s**, repairs **0.227–3.368 s**, map load **9.066 s**, and peak benchmark memory **2.318 GiB** on this laptop. These timings exclude browser/network time and do not guarantee every requested route. Raw measurements and provenance are in `evaluation/city-routing-benchmark.json`.

The map is road geometry, not live traffic. Traffic history currently covers only the 16 source roads. The whole city cannot receive real CCTV traffic without actual approved camera integrations. External OSRM remains a fallback when configured; external map tiles/search require internet access. Road cost weights remain research heuristics, not validated real-time travel-time estimates.

## Reproduce

From the project root, use the installed Python environment (on Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe`):

```bash
.venv/bin/python -m pip install -r requirements-research.txt
.venv/bin/python backend/public_data.py --download --import-data
.venv/bin/python backend/train_hybrid.py --granularity day --trained-by public-data-validation
.venv/bin/python backend/download_validation_videos.py
.venv/bin/python backend/evaluate_vehicle_counts.py
# Download the official southern-zone-latest.osm.pbf into data/routing/ first.
.venv/bin/python backend/build_city_graph.py --pbf data/routing/southern-zone-latest.osm.pbf
.venv/bin/python backend/benchmark_city_routing.py
.venv/bin/python backend/validation_reports.py
```

The vehicle evaluator verifies clip/annotation hashes before scoring. Acquisition is separate from runtime; research dependencies are optional. Reacquiring a changing “latest” map may produce different routing counts and timings. The preserved manifests identify the exact files evaluated here. Restart the API after changing the map or detector settings. Final app/regression evidence is in `VERIFICATION_RESULTS.md`.
