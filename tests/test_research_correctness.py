"""Independent small-graph oracles and regressions for research correctness."""
import math
import random
from dataclasses import replace
import networkx as nx
import numpy as np
import pytest
from pydantic import ValidationError

from src.data.enrich_graph import synthetic_benchmark_graphs
from src.evaluation.ablations import benchmark_vehicle, sample_stratified_od_pairs, git_revision
from src.evaluation.baseline_routes import route_baseline
from src.evaluation.metrics_engine import MetricsEngine
from src.models.traversability_heuristic import predict_traversability
from src.routing.cvar_optimizer import compute_catastrophic_cvar, empirical_cvar, optimize_cvar_route
from src.routing.risk_aware_router import (
    SearchConfig, RoutingSearchLimit, route_risk_aware, route_risk_aware_multilabel,
    prepare_search, _physics_travel_time)
from src.vehicle.geometry_constraints import _parse_osm_float, compute_geometry_margins

def test_experiment_provenance_without_git_checkout(tmp_path):
    assert git_revision(tmp_path) is None


def test_experiment_provenance_without_git_executable(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('git is unavailable')
    monkeypatch.setattr('src.evaluation.ablations.subprocess.check_output', unavailable)
    assert git_revision(tmp_path) is None


def edge(width=5., **overrides):
    return dict(width=width, maxheight=4.5, maxweight=15., length=100.,
                highway='secondary', travel_time=10., **overrides)

def graph():
    G = nx.MultiDiGraph()
    for n in range(4):
        G.add_node(n, y=12.935+n*.001, x=77.624+n*.001)
    G.add_edge(0, 1, **edge())
    G.add_edge(1, 3, **edge())
    G.add_edge(0, 2, **{**edge(), 'travel_time': 20.})
    G.add_edge(2, 3, **{**edge(), 'travel_time': 20.})
    return G


def test_rounded_hazard_ablation_handles_zero_probability():
    G = graph()
    for _, _, _, data in G.edges(keys=True, data=True):
        data['_history_penalty'] = 1.
    vehicle = benchmark_vehicle()
    nodes, edges, stats = route_risk_aware(
        G, 0, 3, vehicle, config=SearchConfig(exact=False, use_hazard=False))
    assert nodes == [0, 1, 3]
    assert len(edges) == 2
    assert stats['completion_probability'] == 0.
    assert stats['risk_certificate']['completion_probability_lower_bound'] is None

def _brute_front(G, source, target, vehicle):
    candidates = []
    for path in nx.all_simple_edge_paths(G, source, target):
        duration = hazard = uncertainty = 0.
        clearance = math.inf
        valid = True
        for u, v, key in path:
            data = G[u][v][key]
            margins = compute_geometry_margins(data, vehicle)
            if any(val is None or val < 0 for val in margins.values()):
                valid = False
                break
            probability, ue = predict_traversability(data, vehicle)
            if probability <= 0:
                valid = False
                break
            duration += _physics_travel_time(data)
            hazard -= math.log(probability)
            uncertainty += ue * data['length']
            clearance = min(clearance, margins['width_clearance_m'])
        if valid and hazard <= .51:
            candidates.append((duration, hazard, uncertainty, -clearance))
    return set(a for a in candidates if not any(
        all(x <= y for x, y in zip(b, a)) and any(x < y for x, y in zip(b, a))
        for b in candidates))

@pytest.mark.parametrize('seed', range(20))
def test_exact_front_matches_exhaustive_edge_paths(seed):
    rng = random.Random(seed)
    G = nx.MultiDiGraph()
    # Coordinates deliberately inconsistent with supplied travel times.
    for n in range(5):
        G.add_node(n, x=n*15., y=n*10.)
    for u in range(5):
        for v in range(5):
            if u != v and (v == u+1 or rng.random() < .32):
                for key in range(1 + int(rng.random() < .3)):
                    G.add_edge(u, v, key=key, **{**edge(width=rng.choice([2.8, 3.5, 5.])),
                                               'length': rng.choice([30., 100., 200.]),
                                               'travel_time': rng.choice([1., 3., 7.])})
    vehicle = benchmark_vehicle('exploratory')
    vehicle.risk_preference = 'aggressive'
    diagnostic = {}
    candidates = route_risk_aware_multilabel(
        G, 0, 4, vehicle, max_candidates_to_find=None,
        config=SearchConfig(exact=True), diagnostics=diagnostic)
    result = {(s['travel_time_s'], s['integrated_hazard'], s['uncertainty_penalty'], -s['min_clearance_m'])
              for _, _, s in candidates}
    expected = _brute_front(G, 0, 4, vehicle)
    assert len(result) == len(expected)
    assert all(any(np.allclose(a, b, rtol=1e-10, atol=1e-10) for b in expected) for a in result)
    assert diagnostic['frontier_complete']

@pytest.mark.parametrize('quantity,value,expected', [
    ('length', '10 ft', 3.048), ('length', "6'6\"", 1.9812),
    ('length', '350 cm', 3.5), ('length', ['3.5 m', '3.0 m'], 3.),
    ('weight', '3500 kg', 3.5), ('weight', '1000 lbs', .45359237),
])
def test_limit_units(quantity, value, expected):
    assert _parse_osm_float(value, math.nan, quantity) == pytest.approx(expected)

@pytest.mark.parametrize('value', ['unknown', '-2 m', float('nan'), float('inf'), 0])
def test_invalid_limits_stay_unknown(value):
    assert math.isnan(_parse_osm_float(value, math.nan))

def test_policy_separation_and_inferred_values():
    vehicle = benchmark_vehicle()
    data = {**edge(), 'highway': 'secondary'}
    data.pop('width')
    conservative = compute_geometry_margins(data, vehicle)['width_clearance_m']
    vehicle.unknown_data_policy = 'exploratory'
    exploratory = compute_geometry_margins(data, vehicle)['width_clearance_m']
    assert 0 < conservative < exploratory
    vehicle.unknown_data_policy = 'strict'
    assert compute_geometry_margins(data, vehicle)['width_clearance_m'] is None
    data.update(width=100, width_source='inferred')
    assert compute_geometry_margins(data, vehicle)['width_clearance_m'] is None

@pytest.mark.parametrize('tag,value', [('width', 2.39), ('maxheight', 2.79), ('maxweight', 4.99)])
def test_small_physical_violations_are_not_tolerated(tag, value):
    G = nx.MultiDiGraph()
    G.add_edge(0, 1, **{**edge(), tag: value})
    assert route_risk_aware(G, 0, 1, benchmark_vehicle('exploratory'))[0] is None

def test_parallel_baseline_and_cvar_retain_safe_key():
    G = nx.MultiDiGraph()
    G.add_edge(0, 1, key='narrow', **{**edge(width=1.8), 'travel_time': 1.})
    G.add_edge(0, 1, key='wide', **{**edge(), 'travel_time': 10.})
    vehicle = benchmark_vehicle('exploratory')
    baseline = route_baseline(G, 0, 1, vehicle, hard_constraints=True)
    assert baseline[1][0][2] == 'wide'
    selected = optimize_cvar_route(G, [], 0, 1, vehicle)
    assert selected[1][0][2] == 'wide'

def test_near_budget_prefix_is_not_pruned_by_faster_riskier_prefix(monkeypatch):
    import src.routing.risk_aware_router as router
    G = nx.MultiDiGraph()
    for u, v, key, h, t in [(0, 1, 0, .029, 1.), (0, 1, 1, .021, 2.), (1, 2, 0, .190, 1.)]:
        G.add_edge(u, v, key=key, **{**edge(), 'test_probability': math.exp(-h), 'travel_time': t})
    monkeypatch.setattr(router, 'predict_traversability', lambda data, *args, **kw: (data['test_probability'], .1))
    vehicle = benchmark_vehicle('exploratory')
    result = route_risk_aware(G, 0, 2, vehicle, config=SearchConfig(exact=True, max_hazard=.215))
    assert result[1][0][2] == 1
    assert result[2]['integrated_hazard'] == pytest.approx(.211)

def test_rounded_and_union_certificates(monkeypatch):
    import src.routing.risk_aware_router as router
    G = graph()
    monkeypatch.setattr(router, 'predict_traversability', lambda *args, **kw: (.94, .1))
    vehicle = benchmark_vehicle('exploratory')
    config = SearchConfig(risk_aggregation='union_bound', hazard_step=.01)
    _, _, stats = route_risk_aware(G, 0, 3, vehicle, config=config)
    certificate = stats['risk_certificate']
    assert certificate['resource_upper_bound'] >= 2*(1-.94)
    assert certificate['resource_upper_bound'] <= certificate['resource_budget']
    assert certificate['completion_probability_lower_bound'] <= .88 + 1e-12

def test_limit_is_distinct_from_no_path():
    G = graph()
    diag = {}
    with pytest.raises(RoutingSearchLimit):
        route_risk_aware(G, 0, 3, benchmark_vehicle(), config=SearchConfig(max_expansions=1), diagnostics=diag)
    assert diag['status'] == 'search_limit'

def test_capped_candidates_do_not_claim_complete_frontier():
    G = graph()
    G[0][2][0]['width'] = G[2][3][0]['width'] = 9.
    diag = {}
    route_risk_aware_multilabel(G, 0, 3, benchmark_vehicle(), max_candidates_to_find=1, diagnostics=diag)
    assert diag['frontier_complete'] is False

def test_prepared_graph_cannot_be_reused_for_a_different_vehicle():
    G = graph()
    vehicle = benchmark_vehicle()
    prepared = prepare_search(G, vehicle)
    wider = vehicle.model_copy(update={'width_m': 3.})
    with pytest.raises(ValueError):
        route_risk_aware(G, 0, 3, wider, prepared=prepared)

def test_synthetic_truth_is_separate_and_seeded():
    G = graph()
    observed, truth = synthetic_benchmark_graphs(G, 1., 42)
    _, truth_other_rate = synthetic_benchmark_graphs(G, .3, 42)
    assert observed.graph['data_role'] == 'observed'
    for u, v, key, data in observed.edges(keys=True, data=True):
        assert 'width' not in data
        assert not any(k.startswith('truth_') for k in data)
        assert truth[u][v][key]['width'] == truth_other_rate[u][v][key]['width']
        assert truth[u][v][key]['width_source'] == 'synthetic_truth'
    assert G[0][1][0]['width'] == 5.  # Input is not overwritten.

def test_unknown_and_no_route_are_not_scored_as_success():
    engine = MetricsEngine(benchmark_vehicle())
    missing = {**edge()}
    missing.pop('width')
    assert engine.compute_min_clearance([(0, 1, 0, missing)]) is None
    metrics = engine.evaluate_route(graph(), None, None, 0., 100.)
    assert metrics['ISER_%'] is None and metrics['ETTP_%'] is None

def test_truth_metrics_detect_hidden_width_height_and_weight_violations():
    observed, truth = graph(), graph()
    truth[0][1][0]['width'] = 1.
    truth[1][3][0]['maxweight'] = 1.
    route = route_baseline(observed, 0, 3, benchmark_vehicle())
    metrics = MetricsEngine(benchmark_vehicle()).evaluate_route(
        observed, route[0], route[1], 20., 20., truth_graph=truth, num_scenarios=100)
    assert metrics['PhysicalFailure'] == 1 and metrics['ISER_%'] == 100.

def test_cvar_uses_actual_failure_events_and_fractional_tail():
    assert empirical_cvar([1., 2., 3., 4.], .625) == pytest.approx(11./3.)
    vehicle = benchmark_vehicle('exploratory')
    data = {**edge(width=100.), 'maxheight': 100., 'length': 0., 'travel_time': 20000.}
    result = compute_catastrophic_cvar([(0, 1, 0, data)], vehicle, seed=123)
    assert result['sample_failure_rate'] == 0.  # Long traffic delays are not physical failures.
    assert result == compute_catastrophic_cvar([(0, 1, 0, data)], vehicle, seed=123)

@pytest.mark.parametrize('tau,count', [(1., 100), (-.1, 100), (.9, 0)])
def test_invalid_cvar_configuration(tau, count):
    with pytest.raises(ValueError):
        compute_catastrophic_cvar([], benchmark_vehicle(), num_scenarios=count, tau=tau)

def test_sampling_small_graph_has_bounded_fallback():
    G = graph()
    G.add_edge(3, 0, **edge())
    pairs = sample_stratified_od_pairs(G, 1, 2, 1)
    assert len(pairs) == 4 and len({p[:2] for p in pairs}) == 4

def test_vehicle_rejects_invalid_dimensions():
    vehicle = benchmark_vehicle()
    with pytest.raises(ValidationError):
        vehicle.width_m = -1.

