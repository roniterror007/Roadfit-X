# Microscopic evaluation: the earlier gains do not transfer

The independent SUMO comparison is complete: **80 scenarios, 640 policy runs and 384,000 method–request records** across Indiranagar and Hebbal. Its results reject a general effectiveness claim for the current controller. The original point-queue findings remain valid for their stated model, but cannot establish real Bengaluru travel-time improvement.

## Results

The primary score is arrival minus requested departure, including insertion delay, capped at 3,600 seconds; unassigned and unfinished requests receive 3,600. Scenario means are weighted equally. The counts below pool 48,000 requests for each policy.

| Policy | Mean request score (s) | Assigned | Arrived by horizon |
|---|---:|---:|---:|
| Static fastest | 623.98 | 48,000 | 48,000 |
| DSP adaptation | 631.81 | 48,000 | 47,707 |
| RkSP adaptation | 851.93 | 48,000 | 42,330 |
| EBkSP adaptation | 677.27 | 48,000 | 46,619 |
| Original RoadFit | 762.58 | 46,476 | 44,109 |
| RoadFit without future scoring | 883.73 | 41,452 | 41,452 |
| RoadFit without locality | 672.14 | 48,000 | 46,628 |
| RoadFit with soft locality only | 703.42 | 48,000 | 45,822 |

Original RoadFit minus static is **+138.60 s**, paired region–seed cluster 95% interval **[99.12, 179.01]**. Its mean scenario-relative increase is **20.50%**. Soft locality alone restores assignment but leaves 2,178 trips unfinished: **+79.44 s [15.71, 159.16]** against static, an **11.86%** mean scenario-relative increase. Percentages average each scenario's relative effect; they are not ratios of the table's overall means.

Future scoring helps relative to its own ablation, but that does not make the complete method effective. Original RoadFit minus no-future is −121.15 s [−208.45, −32.17]. Original RoadFit is worse than DSP by 130.77 s [92.43, 172.14] and EBkSP by 85.31 s [22.38, 145.28]. Removing the hard locality cutoff improves the mean against original RoadFit, but its interval spans zero: −59.16 s [−137.89, 24.35]. All contrasts, including null results, are in [the analysis](../research/sumo_analysis.json).

Holding routing static, actuated signals improve the score by **6.55 s [3.82, 9.38]**, approximately **1.01%** by mean scenario-relative effect. This contradicts the blanket hypothesis that signal management cannot help within these conditions. It is not evidence about a calibrated Bengaluru signal deployment.

Across all policies, **8,072 requests are unassigned**, **13,261 assigned requests remain unfinished**, and **163 of 640 policy runs** do not complete every request. There are **zero recorded collision-vehicle events and zero teleports**. Zero simulator collisions do not establish actual road safety. The completed-trip-only average would favor original RoadFit (601.53 s versus 623.98 s), concealing its worse complete-request score. Planned local-road distance likewise must not be mistaken for actual exposure when vehicles fail to finish.

![Microscopic outcomes and paired intervals](../research/sumo_results.png)

## What was implemented and verified

- Native SUMO 1.27.1 execution, generated road connections, class permissions, car following, lane changes, finite storage and static/actuated signals. Four frozen network configurations have 4,275 or 5,569 edges and eight assumed signal controllers each.
- Three prior-art selector adaptations from [Pan et al., DCOSS 2012](https://doi.org/10.1109/DCOSS.2012.29). The entropy primitive matches the paper's Figure 1 values 1.49, 1.16 and 0.58. This is departure-only advice from common frozen candidates, not a complete reproduction of periodic upstream rerouting.
- Full audit of **384,000 records against native XML**, including insertion delay, noncompletion penalties, assignments, routes, vehicle permissions, zero truck local-road exposure and episode summaries. All recorded source/network hashes match. See [verification](../experiments/verification/sumo-verification.json).
- One independent 300-request replay matches all eight policies exactly in decisions, native trip outcomes and per-step summaries: **2,400 records**. XML timestamps and output paths are excluded. See [replay check](../experiments/verification/sumo-replay.json).
- **104 Python tests pass**, with one existing Starlette deprecation warning; `pip check` passes. A Windows DLL conflict between GIS and native SUMO imports was resolved in the test by executing SUMO in a separate process, matching the benchmark's process isolation.

The [fixed protocol and reproduction commands](SUMO_PROTOCOL.md) disclose pilots, data assumptions, full route observability, the 15% forecast background mismatch, ten bootstrap clusters and candidate restrictions. No parameters were chosen on seeds 51–55. GPU 0 was not used because these graph and SUMO implementations execute on CPUs.

## Additional Bengaluru evidence

A [B-SMILE project report mirrored by OpenCity](https://data.opencity.in/dataset/e66a48ed-bcad-4839-9779-854a1c60934c/resource/bfdd8181-2bc7-49cf-8c47-a700ec59caa1/download/514f62ef-b709-48e5-bb3b-bc01316b9d95.pdf) contains a classified vehicle survey along the IOC Junction corridor. The report is dated September 2025, but the count sheets are dated **24 August 2021**, 06:00–20:00. Visually checked aggregate direction totals are 15,321 and 14,784 (30,105 combined). PDF pages 130 and 133 correspond to printed pages 122 and 125.

The [structured transcription](../data/public/ioc_2021/survey.json) retains the source hash and quality flags. In the reverse direction, four interval total cells are printed as zero despite positive class counts. Blank cells and split digits also prevent reliable unsupervised text extraction. Aggregate arithmetic checks pass, but these do not validate the original survey. This historical single-corridor source was not used to fit the completed SUMO test and supplies no individual trip ETA ground truth.

## Paper status and artifact scope

The existing [manuscript](../research/roadfit_paper.tex) was updated in place with the transfer failure as a main finding, all eight policy outcomes, signal comparison, prior-art fidelity limits, survey quality issues and reproducibility evidence. The native editor's compiler still fails before typesetting with `Unable to find standard directories for platform`. Compilation and page layout therefore remain unverified. The editor remains open; no replacement PDF was generated. The earlier `output/pdf/roadfit_review.pdf` and `roadfit-vnext-bundle.zip` are preserved historical snapshots and do not contain this manuscript revision.

The new `experiments/roadfit-sumo-study.zip` is a verified **research supplement** with source, two regional maps, four SUMO networks, raw full-run outputs, pilots, replay, analysis, tests and the revised TeX source. It excludes credentials, user memories/models, full third-party reference PDFs and the stale review PDF. Use the preserved earlier bundle for the application and preceding point-queue/ETA experiments. Full OSM-derived map rights remain with OpenStreetMap contributors under ODbL.

This is ready for internal scientific review and reproduction, **not an A* submission claiming a successful new traffic controller**. The next defensible improvement requires explicit downstream queue/storage forecasts and uncertainty handling, calibration against appropriate observations, fresh untouched scenarios, and fuller published-system comparisons. The current seeds and findings must remain a reported development/evaluation record rather than becoming an undisclosed tuning set. No submission has been made.

## Engineering fixes applied

The following changes address the diagnosed flaws. **The original results above are the reported baseline; these fixes are development iterations that must be re-evaluated on fresh seeds (61–65) before any claim of improvement.**

### 1. Observed-queue initialization (Flaw 1, 2)
`predicted_queue()` now accepts an `initial_backlog` parameter. In the SUMO benchmark, backlog is computed from live vehicle occupancy exceeding 70% of link storage. This replaces the zero-backlog assumption. See [`queue_model.py`](../src/routing/queue_model.py).

### 2. Downstream spillback model (Flaw 2)
When a downstream edge's occupancy reaches its storage limit, the upstream edge's effective service rate is reduced to 5%. Between 80–100% occupancy, the rate degrades linearly. This is a first-order approximation of physical gridlock propagation.

### 3. Signal-phase delay estimation (Flaw 2)
A Webster-style average delay estimate (~15s) is added for edges approaching signalized junctions. This is uncalibrated and assumes a 60s cycle with 45% green time. It models the systematic delay that the original free-flow prediction ignored.

### 4. Graduated locality penalty (Flaw 3)
The hard residential admission cutoff (`residential_entry_limit = 0.8`) that caused 1,524 unassigned trips is replaced by a graduated cost penalty. Below the limit, base penalty applies; above it, a smooth multiplier (up to 3× at 100%, then quadratic) discourages but does not prohibit residential use. The hard cutoff is retained for ablation comparison via `graduated_locality=False`.

### 5. Background traffic correction (Flaw 4)
`DEFAULT_BACKGROUND` is now 0.0 in the SUMO benchmark, matching the zero-background SUMO fleet. The previous 15% assumption is removed. Background should only be set >0 when a corresponding background fleet is actually injected.

### 6. Failure diagnostics (Flaw 1)
A new [`failure_diagnostics.py`](../src/evaluation/failure_diagnostics.py) module provides per-trip and per-scenario structured analysis: unassigned vs. unfinished vs. excessive-wait breakdowns, bottleneck edge identification, vehicle-class and temporal failure patterns.

### 7. Evidence tracking (Flaw 5)
A new [`calibration_targets.py`](../src/evaluation/calibration_targets.py) module explicitly documents what is observed, assumed, and synthetic, and defines measurable calibration targets with required evidence for each.

### New policy variants
- **`roadfit_graduated`**: Uses graduated locality penalty (no hard cutoff).
- **`roadfit_queue_aware`**: Uses graduated locality + observed queue initialization + downstream spillback + signal delay estimation.

These variants must be evaluated on fresh seeds before any effectiveness claim. The `plan()` function now returns a diagnostics dict instead of `None` when all candidates are rejected, enabling structured failure analysis.
