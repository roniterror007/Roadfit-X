"""Seeded Monte Carlo loss estimates over feasible exact-edge candidates.

These are simulations of an uncalibrated model, not observed failure rates.
"""
import hashlib
import math
import numpy as np
from src.routing.risk_aware_router import route_risk_aware_multilabel, _physics_travel_time
from src.models.traversability_heuristic import predict_traversability

def empirical_cvar(losses, tau=.9):
    values = np.sort(np.asarray(losses, dtype=float))
    if not 0 <= tau < 1 or not len(values) or not np.all(np.isfinite(values)):
        raise ValueError('finite nonempty losses and 0 <= tau < 1 are required')
    mass = len(values) * (1.-tau)
    whole = min(int(math.floor(mass)), len(values))
    remainder = mass - whole
    total = values[-whole:].sum() if whole else 0.
    if remainder > 1e-12 and whole < len(values):
        total += remainder * values[-whole-1]
    return float(total / mass)

def compute_catastrophic_cvar(path_edges, vehicle, provenance=None,
                             rain_level='none', traffic_level='normal',
                             num_scenarios=1000, tau=.90,
                             c_catastrophic_seconds=14400., seed=0):
    if num_scenarios < 1 or not 0 <= tau < 1:
        raise ValueError('scenario count must be positive and 0 <= tau < 1')
    if not math.isfinite(c_catastrophic_seconds) or c_catastrophic_seconds < 0:
        raise ValueError('failure penalty must be finite and nonnegative')
    rng = np.random.default_rng(seed)
    multipliers = rng.lognormal(mean=0., sigma=.25, size=num_scenarios)
    losses = np.zeros(num_scenarios)
    failed = np.zeros(num_scenarios, dtype=bool)
    full_duration = 0.
    # Edge-key seeds give common random numbers for shared edges across candidates.
    for u, v, key, data in path_edges:
        probability, _ = predict_traversability(data, vehicle, provenance, (u, v, key),
                                                rain_level, traffic_level)
        duration = _physics_travel_time(data, rain_level, traffic_level)
        full_duration += duration
        edge_seed = int.from_bytes(hashlib.sha256(f'{seed}:{u}:{v}:{key}'.encode()).digest()[:8], 'big')
        uniform = np.random.default_rng(edge_seed).random(num_scenarios)
        new_failure = ~failed & (uniform > probability)
        losses[new_failure] += c_catastrophic_seconds
        failed |= new_failure
        losses[~failed] += duration * multipliers[~failed]
    travel_times = full_duration * multipliers
    return {
        'expected_loss_sec': float(losses.mean()),
        'var_90_sec': float(np.quantile(losses, tau, method='inverted_cdf')),
        'cvar_90_sec': empirical_cvar(losses, tau),
        'sample_failure_rate': float(failed.mean()),
        'eta_p50_sec': float(np.quantile(travel_times, .5)),
        'eta_p90_sec': float(np.quantile(travel_times, .9)),
        'tau': tau, 'num_scenarios': num_scenarios, 'seed': seed,
        'basis': 'uncalibrated_model_simulation',
    }

def optimize_cvar_route(base_graph, scenarios, orig_node, dest_node, vehicle,
                        provenance=None, k=10, tau=.90,
                        c_catastrophic_seconds=14400., rain_level='none',
                        traffic_level='normal', seed=0):
    # Retain edge keys: reconstructing from node-only paths can pick a blocked parallel edge.
    candidates = route_risk_aware_multilabel(
        base_graph, orig_node, dest_node, vehicle, provenance, rain_level, traffic_level,
        max_candidates_to_find=k)
    if not candidates:
        return None, None, {}
    evaluated = []
    for nodes, edges, stats in candidates:
        risk = compute_catastrophic_cvar(
            edges, vehicle, provenance, rain_level, traffic_level,
            num_scenarios=max(1000, len(scenarios or []) * 20), tau=tau,
            c_catastrophic_seconds=c_catastrophic_seconds, seed=seed)
        stats.update(cvar_risk=risk['cvar_90_sec'], expected_loss_sec=risk['expected_loss_sec'],
                     sample_failure_rate=risk['sample_failure_rate'], scenario_summary=risk)
        evaluated.append((nodes, edges, stats))
    return min(evaluated, key=lambda route: (route[2]['cvar_risk'], route[2]['travel_time_s']))
