# Independent microscopic evaluation protocol

This protocol was written after a 30-trip functionality pilot and before the full comparison. A subsequent 900-trip seed-41 pilot exposed rejections caused by the hard residential load cutoff. Before running the final seeds, one additional corrective variant was specified: remove that cutoff while retaining the soft locality cost and all vehicle/access filters. Both pilots are retained separately. This is a local exploratory protocol, not an external preregistration. No method parameters will be selected using the final 51–55 evaluation seeds.

## Fixed comparison

- SUMO 1.27.1, `libsumo`, one-second simulation steps, 3,600-second horizon, no teleporting.
- Two frozen public OSM regional graphs: Indiranagar and Hebbal. Generated connections and intersection right of way are handled by SUMO. Eight high-degree junctions per network receive assumed signal programs. Static 60-second programs and SUMO actuated programs are two separate conditions. Neither program is calibrated against Bengaluru timing data.
- Five seeds (51–55), 300 and 900 requests uniformly departing over 900 seconds, and 100% or 70% compliance. This gives 80 scenarios and 640 policy runs. The same requests, random seed, vehicle mix and network are reused across policies within a scenario.
- Vehicles are 40% motorcycles, 50% hatchbacks and 10% trucks. Endpoints come from directional boundary anchors in each vehicle's feasible strongly connected component. Trucks use main roads only. This deliberately restricted cohort does not estimate general citywide truck coverage.
- All methods use the same six free-flow loopless candidate paths. At advice time, adaptive methods retain up to three paths within 20% of the fastest current-cost candidate. Observations refresh every 30 seconds. The entropy footprint horizon is 60 seconds. Pending departures remain in the intention ledger. Departed vehicles keep their chosen route; only departure advice is evaluated.
- Policies: static fastest, DSP current-cost minimum, uniform RkSP, EBkSP weighted entropy, RoadFit, RoadFit without future scoring, RoadFit without locality, and RoadFit with soft locality alone. The latter disables only the hard residential admission cutoff, retaining the soft penalty. RoadFit keeps its existing queue, detour and locality weights. It is evaluated against independently simulated car-following, lane changing, signals and finite link storage.
- The current-cost estimator uses Greenshields' equation with a declared 5% speed floor. Storage is lane-length divided by an assumed 7.5-m vehicle-plus-gap length. Actual simulated vehicle lengths differ by class. No future realized travel times enter policy decisions.
- Full route observability and prompt compliance feedback are assumed. A driver who ignores advice follows the static route, including when the controller abstains. This is a favorable information assumption, not an implementation of partially observed city telemetry.
- The retained RoadFit predictor assumes 15% background capacity consumption. SUMO contains only the scheduled requests, with no separately injected background fleet. This explicit model mismatch is not a calibrated background estimate.

## Primary outcomes and uncertainty

For completed trips, elapsed time is arrival minus requested departure, including insertion delay. The primary score caps this at 3,600 seconds and assigns 3,600 to unassigned or unfinished requests. Report assignment, arrival count, conditional mean and p90, insertion delay, local-street route distance, collision events and teleports alongside it. Route-locality exposure is planned distance; unfinished trips may not traverse the whole plan.

Compare original RoadFit and the specified soft-locality correction against static, DSP, RkSP, EBkSP and the ablations. Bootstrap paired **region–seed clusters**, keeping signal, demand and compliance conditions together, with 10,000 resamples and fixed analysis seed 123. Report per-region results and all policy means. Compare actuated against static signal control under the static routing policy to assess the user's signal-management hypothesis. These exploratory intervals are not familywise-adjusted evidence of universal superiority.

## Prior-art fidelity

The DSP, RkSP and EBkSP selectors follow Pan et al., *Proactive Vehicle Re-routing Strategies for Congestion Avoidance*, DCOSS 2012, DOI 10.1109/DCOSS.2012.29. The entropy implementation reproduces the three published Figure 1 values (1.49, 1.16, 0.58) and uses network-average storage capacity weights. The paper's printed entropy index is ambiguous; its worked example determines summation over the chosen path, with normalization over the candidate union.

This is a **matched departure-policy adaptation**, not a full reproduction of the 2012 experiment. Its common frozen candidate pool, mixed vehicles, 30-second observations and departure-only advice differ from dynamically generated k-shortest paths, congestion-threshold upstream vehicle selection, urgency ordering and periodic en-route rerouting in the paper. Claims must use this restricted description. The original Brooklyn/Newark reported gains are not comparable percentages.

## Evidence boundary

Only the underlying map is public observed topology. Demand, permitted vehicle geometry, default lanes, car-following behavior, signals and traffic conditions are assumed. SUMO makes the execution model more detailed; it does not turn those assumptions into field measurements. Fewer-than-requested completions and any adverse or null results must remain visible in the manuscript. No universal conference acceptance-accuracy target is defined.

## Reproduction and audit

Use the existing Python 3.11 environment and the pinned base requirements. The additional SUMO wheels are installed separately:

```powershell
python -m pip install -r requirements.lock.txt -r requirements-sumo.txt
python -m scripts.prepare_sumo
python -m src.evaluation.sumo_benchmark --workers 8 --output experiments/sumo_reproduced
python -m scripts.analyze_sumo --input experiments/sumo_reproduced --output research/sumo_reproduced
python -m scripts.verify_sumo --input experiments/sumo_reproduced --output experiments/verification/sumo-reproduced.json
```

The supplied network XML files are the exact evaluation inputs. Regeneration can change native XML metadata or generated connections across platforms; retain and compare hashes instead of assuming byte identity. The original run is in `experiments/sumo_control`. A quick pilot can use `--regions Indiranagar --signals static --seeds 41 --counts 30 --adoptions 1 --workers 1` with a separate output directory; the full verifier deliberately requires the complete 80-scenario design.

All full-study jobs use CPU workers. SUMO simulation and graph search here have no CUDA backend; GPU allocation would not accelerate these implementations. Windows GIS and SUMO wheels can load conflicting DLL versions if imported into the same long-lived process. Execute the benchmark as a module in a fresh process as above. Its test likewise isolates SUMO from other GIS tests.

The independent verifier reads native `tripinfo.xml`, per-step `summary.xml`, demand and selected-route decisions. It checks every request score, completion, insertion-delay identity, class permission, selected-route connectivity, zero truck local-road exposure, and episode means/p90. It hashes raw files, source and network inputs. All assigned vehicles, including those not inserted or not finished by the horizon, remain in the native output.

The aggregate IOC survey in `data/public/ioc_2021/survey.json` is a separate historical evidence source. It was found after the fixed experiment began and was not used for fitting. The report is dated 2025 but its count sheets are dated 2021. Source-table inconsistencies are retained explicitly. It is neither linked ETA ground truth nor a citywide traffic matrix.
