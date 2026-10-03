# RoadFit-X: vehicle-aware routing and anticipatory traffic control

RoadFit-X is a working research prototype for vehicle-aware routing through an expanded Bengaluru road multigraph. It includes an arrival-bin queue forecaster, voluntary route reservations, an authenticated traffic-officer console, exact and conservatively rounded resource-constrained search, a FastAPI backend, and a React/MapLibre dashboard.

**Evidence status:** public OSM topology/building footprints and a recorded Chengdu travel-time sample are acquired with provenance. Routing demand, capacities, vehicle limits and traffic shocks remain simulated. The paper is an internal research draft with executed ablations and negative findings; it does not establish field effectiveness, safety, unprecedented algorithms, or A* submission readiness. Vehicle-aware routing already exists in [Valhalla](https://valhalla.github.io/valhalla/api/route/api-reference/), and proactive rerouting predates this project ([Pan et al., DCOSS 2012](https://doi.org/10.1109/DCOSS.2012.29)).

## What works and has been checked

- 104 Python tests pass in the Python 3.11 validation environment. They cover exhaustive small-graph comparisons, parallel-edge/access rules, risk budgets, FIFO execution, atomic reservations, officer workflows, the published path-entropy example and SUMO noncompliance behavior.
- `npm ci`, Oxlint, and the production frontend build pass. A live browser test calculated and displayed a route from the backend.
- A CPU training smoke test generated 16 simulated routes, recorded 12 edge experiences, and fitted/persisted a semantic model in temporary storage.
- The new traffic experiments contain **303,360 per-policy records** across 3 TNTP networks and 6 Bengaluru regions, including two explicitly different endpoint cohorts. A separate ETA experiment evaluates 1,400 held-out recorded trips. The historical constraint benchmark has 12,600 records and a preserved source bundle.
- An independent **SUMO comparison adds 640 runs and 384,000 records**, all audited against native outputs. One eight-policy scenario reproduces exactly. It exposes a transfer failure: original RoadFit's mean scenario-relative request-time score is **20.50% worse than static routing**; removing hard locality admission is still **11.86% worse**. Actuated signals improve the static-routing score by **1.01%** under the assumed settings.

See the [latest SUMO study](docs/SUMO_STUDY.md), [fixed protocol and reproduction commands](docs/SUMO_PROTOCOL.md), [preceding research guide](docs/ANTICIPATORY_RESEARCH.md), [manuscript source](research/roadfit_paper.tex), and [historical constraint dossier](docs/PROJECT_DEEP_DIVE.md). CI is configured; these checks were executed locally.

## Run the application

Use Python **3.11** and Node.js **22**. Run commands from the repository root unless a command changes directory.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
python -m src.api.server
```

On Linux/macOS activate with `source .venv/bin/activate`. The lock file includes development/test dependencies. `requirements.txt` provides the pinned application dependencies; `requirements-dev.txt` adds the tests.

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open [the dashboard](http://127.0.0.1:5173) and [the API documentation](http://127.0.0.1:8000/docs). `frontend/.env.example` documents `VITE_API_BASE_URL`; its localhost default works without creating an environment file.

Set endpoints by clicking the map or searching the local Bengaluru area/road-name index. Select a vehicle, missing-data policy, and simulation context, then calculate a route. Another map click moves the destination; markers can also be dragged. “Show map” hides the controls on narrow screens. The default goal reduces forecast ETA and balances expected arrivals. Set a future departure and enable reservation to let later requests account for your intended route. Recalculation releases the previous reservation; driver views poll for officer revisions.

The default graph is `data/public/bengaluru_drive.graphml` when present: **198,984 nodes, 490,185 directed edges and 750,484 mapped building footprints** within the declared bounding box. It falls back to `data/koramangala_enriched_v2.graphml` if absent. Full observed width/height/weight coverage is zero because weight tags are absent. Strict/conservative policies can abstain; exploratory results use unvalidated category priors. Missing clearance is displayed explicitly. Basemap tiles need internet; place search is local. Satellite imagery is an optional visual basemap, not measured road-width evidence.

For the separate officer login, initialize the local account once:

```powershell
python -m src.api.officer_auth
```

Read `.local/officer-login.txt` locally, then sign in through the Officer login tab. This ignored file contains the generated password; keep it private. The console shows reserved future arrivals, supports manual future load scenarios and directed segment closures, and can rebalance up to ten trips **before their departure**. Driver views receive replacement routes. It has no live traffic feed or moving-vehicle tracking; sessions and reservations are in memory and reset on restart.

For a fresh, explicitly labelled synthetic demonstration, create separate observed and evaluator-only graphs:

```powershell
python -m src.data.enrich_graph --input data/koramangala_bengaluru_karnataka_india_drive.graphml --output data/koramangala_synthetic_observed.graphml --drop-rate 0.3 --seed 42
$env:ROADFIT_GRAPH = "data/koramangala_synthetic_observed.graphml"
python -m src.api.server
```

On Linux/macOS use `ROADFIT_GRAPH=data/koramangala_synthetic_observed.graphml python -m src.api.server`. The paired `_truth.graphml` file is for evaluation only. Do not serve it as observed road data.

Local map coverage is enforced for both routing endpoints. Dynamic OSM downloads are disabled by default. An optional `ROADFIT_ALLOW_DOWNLOAD=1` enables bounded downloads for trips up to 50 km. `ROADFIT_CORS_ORIGINS` changes the allowed frontend origins. The API binds to `127.0.0.1:8000`.

## Historical constraint experiments

The original benchmark reads raw OSM topology and builds separate observed/truth copies. The preserved ZIP below contains the source version corresponding to its manifest; use that snapshot for exact historical reproduction. Current source has additional modules and therefore a different global source hash. For the current traffic and recorded-ETA experiments use [ANTICIPATORY_RESEARCH.md](docs/ANTICIPATORY_RESEARCH.md).

```powershell
python -m pytest -q tests
python -m src.evaluation.ablations --graph data/koramangala_bengaluru_karnataka_india_drive.graphml --num-pairs 100 --seeds 41 42 43 --missing-rates 0 0.3 0.6 --workers 3 --output experiments/my_run/ablations.csv
python -m src.evaluation.plot_results --csv experiments/my_run/ablations.csv
python scripts/verify_reproduction.py --reference experiments/reproducible/final/ablations.csv --candidate experiments/my_run/ablations.csv --output experiments/my_run/reproduction.json
```

The verifier requires the candidate's source and graph hashes to match the current workspace. Source edits, operating-system path formatting, or dependency changes can change a manifest; report them rather than rewriting provenance. CSV outcomes exclude timing from exact comparison. Three CPU workers were used here; GPU 0 was detected but these graph searches do not use CUDA.

For a quick execution check use `--num-pairs 3 --seeds 41 --missing-rates 0.3 --workers 1`. This is a smoke test, not the full benchmark.

Artifacts from the validated run:

- [Per-query results](experiments/reproducible/final/ablations.csv), [summary](experiments/reproducible/final/ablations.summary.csv), [manifest](experiments/reproducible/final/ablations.manifest.json).
- [Paired descriptive bootstrap comparisons](experiments/reproducible/final/ablations.paired.json) and [reproduction verification](experiments/verification/reproduction.json).
- [Trade-off figure](experiments/reproducible/final/ablation_tradeoffs.png) and [clearance distribution](experiments/reproducible/final/clearance_cdf.png); SVG versions are alongside them.
- [Historical constraint bundle](experiments/reproducible/final/roadfit-research-bundle.zip) and [preceding point-queue/ETA bundle](experiments/roadfit-vnext-bundle.zip) preserve their source and result snapshots. The [latest SUMO research supplement](experiments/roadfit-sumo-study.zip) contains native outputs, provenance and the revised manuscript. Local credentials, memories and serialized user models are excluded.

At **30% missing widths**, 300 queries per method:

| Method | Routes returned | Physical violations among returned routes | Returned and physically feasible queries |
|---|---:|---:|---:|
| ETA baseline | 100.0% | 66.0% | 34.0% |
| Hard-constraint baseline | 39.7% | 0.0% | 39.7% |
| RoadFit exact, conservative | 38.7% | 0.0% | 38.7% |
| RoadFit exact, strict | 1.3% | 0.0% | 1.3% |
| RoadFit exact, exploratory | 87.0% | 36.8% | 55.0% |
| Fully observed synthetic oracle | 85.0% | 0.0% | 85.0% |

“Physical” here means width/height/weight feasibility against **held-out synthetic dimensions**. The conservative router does not dominate the hard baseline on availability. Zero conditional violations alongside substantial abstention is not a deployment safety claim. The dossier reports negative and null ablations as well as improvements.

## Algorithms and architecture

The router tracks time, additive risk, additive uncertainty, and bottleneck clearance. Reverse Dijkstra bounds guide and prune search. Exact mode retains nondominated labels; rounded mode rounds additive resources upward and clearance downward. Rounding conservatively respects a declared modeled budget but can discard feasible paths. A finite candidate cap or expansion limit prevents a complete-frontier claim.

The union-bound variant sums edge failure bounds instead of assuming independent failures. Its route guarantee is conditional on valid marginal bounds; the present heuristic probabilities have not been calibrated to provide those bounds. These are implemented algorithms with tests, not a claim that multi-label search or the union bound is new.

Episodic SQLite memory, short-lived roadblocks, and optional semantic consolidation form a separate simulated-learning component. Training reports actual background-job status through `/brain/jobs/{job_id}`. Existing serialized models are loaded only when explicitly configured with `ROADFIT_SEMANTIC_MODEL`. The GNN module is a scaffold, not a trained or benchmarked contribution.

## Research readiness

The current paper makes the **failure to transfer across traffic models** a main result. Point-queue gains (2.09% on TNTP) reverse in SUMO: original RoadFit is worse by **138.60 s [99.12, 179.01]**, and the soft-locality variant by **79.44 s [15.71, 159.16]**, versus static. Unassigned and unfinished trips remain penalized. All 48,000 static-policy trips finish; only 44,109 original-RoadFit trips do. These findings do not establish real Bengaluru effectiveness. The separate recorded Chengdu predictor retains MAE **5.14 minutes** and MAPE **22.45%**.

The software, results and manuscript are ready for internal inspection and reproduction. A successful-controller submission still needs an improved method, a defensible contribution, full published-system comparisons and calibrated independent evidence. Three published selector adaptations and a historical IOC Junction count transcription have been added, with fidelity and data-quality limits stated. The original LaTeX file remains open and updated; Codex's compiler still fails with a Windows platform runtime error. The older review PDF is a historical snapshot and does not contain this revision. No paper has been submitted.
