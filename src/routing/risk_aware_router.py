"""Resource-constrained multiobjective search with inspectable guarantees.

Exact mode keeps a nondominated label set. Rounded mode conservatively rounds
each additive resource upward and bottleneck clearance downward. Guarantees
refer to this declared edge model; its survival probabilities are uncalibrated.
"""
import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Optional
import networkx as nx

from src.models.traversability_heuristic import predict_traversability
from src.vehicle.geometry_constraints import compute_geometry_margins
from src.vehicle.operational_constraints import get_physics_speed, get_car_density

RISK_BUDGETS = {'aggressive': .51, 'moderate': .22, 'conservative': .051}


class RoutingSearchLimit(RuntimeError):
    """A resource limit interrupted search; this is not evidence of no path."""


@dataclass(frozen=True)
class SearchConfig:
    exact: bool = False
    risk_aggregation: str = 'independent'
    hazard_step: float = .0005
    uncertainty_step: float = 10.
    clearance_step: float = .1
    use_geometry: bool = True
    use_hazard: bool = True
    use_uncertainty: bool = True
    use_clearance: bool = True
    use_history: bool = True
    use_reverse_bounds: bool = True
    max_hazard: Optional[float] = None
    max_expansions: int = 100_000

    def __post_init__(self):
        if self.risk_aggregation not in ('independent', 'union_bound'):
            raise ValueError('risk_aggregation must be independent or union_bound')
        for name in ('hazard_step', 'uncertainty_step', 'clearance_step'):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f'{name} must be finite and positive')
        if self.max_expansions < 1:
            raise ValueError('max_expansions must be positive')
        if self.max_hazard is not None and (not math.isfinite(self.max_hazard) or self.max_hazard < 0):
            raise ValueError('max_hazard must be finite and nonnegative')


@dataclass(eq=False)
class Label:
    travel_time_s: float
    integrated_hazard: float
    uncertainty_penalty: float
    min_clearance_m: float
    node: Any
    edge_key: Any = None
    predecessor: Optional['Label'] = field(default=None, repr=False)
    f_score: float = 0.
    hazard_resource: float = 0.
    uncertainty_resource: float = 0.
    clearance_resource: float = math.inf
    active: bool = True

    def dominates(self, other):
        a = (self.travel_time_s, self.integrated_hazard,
             self.uncertainty_penalty, -self.min_clearance_m)
        b = (other.travel_time_s, other.integrated_hazard,
             other.uncertainty_penalty, -other.min_clearance_m)
        return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def haversine_admissible_heuristic(lat1, lon1, lat2, lon2, max_network_speed_mps=33.33):
    if max_network_speed_mps <= 0:
        raise ValueError('max speed must be positive')
    a = (math.sin(math.radians(lat2-lat1)/2)**2
         + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))
         * math.sin(math.radians(lon2-lon1)/2)**2)
    return 6371000. * 2 * math.asin(math.sqrt(min(1., max(0., a)))) / max_network_speed_mps


def _physics_travel_time(edge_data, rain_level='none', traffic_level='normal'):
    length = float(edge_data.get('length', 50.))
    if not math.isfinite(length) or length < 0:
        raise ValueError('edge length must be finite and nonnegative')
    if 'travel_time' in edge_data and rain_level == 'none' and traffic_level == 'normal':
        try:
            duration = float(edge_data['travel_time'])
            if math.isfinite(duration) and duration > 0:
                return duration
        except (TypeError, ValueError):
            pass
    speed = get_physics_speed(edge_data, rain_level, traffic_level)
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError('edge speed must be finite and positive')
    return max(1e-6, length / (speed / 3.6))


def _edge_model(data, vehicle, provenance, edge_id, rain, traffic, config):
    margins = compute_geometry_margins(data, vehicle)
    if config.use_geometry and any(v is None or not math.isfinite(v) or v < 0 for v in margins.values()):
        return None
    model_data = dict(data)
    if not config.use_history:
        model_data.pop('_history_penalty', None)
    probability, uncertainty = predict_traversability(
        model_data, vehicle, provenance, edge_id, rain, traffic,
        include_geometry=config.use_geometry, include_uncertainty=config.use_uncertainty)
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        return None
    if config.use_hazard and probability <= 0:
        return None
    hazard = -math.log(probability) if probability > 0 else math.inf
    length = float(data.get('length', 50.))
    time_s = _physics_travel_time(data, rain, traffic)
    if not math.isfinite(length) or length < 0 or not math.isfinite(uncertainty):
        return None
    clearance = margins['width_clearance_m']
    return time_s, hazard, uncertainty * length, (clearance if clearance is not None else -math.inf)


def _weak_dominates(a, b):
    return (a.travel_time_s <= b.travel_time_s
            and a.hazard_resource <= b.hazard_resource
            and a.uncertainty_resource <= b.uncertainty_resource
            and a.clearance_resource >= b.clearance_resource)


@dataclass
class PreparedSearch:
    graph: Any
    config: SearchConfig
    vehicle_signature: dict
    rain: str
    traffic: str
    model: dict
    reverse: Any
    rejected_edges: int


def prepare_search(G, vehicle, provenance=None, rain_level='none', traffic_level='normal', config=None):
    """Precompute an immutable experiment condition. Do not mutate G afterward."""
    cfg = config or SearchConfig()
    model, reverse = {}, nx.DiGraph()
    reverse.add_nodes_from(G)
    rejected = 0
    for u, v, key, data in G.edges(keys=True, data=True):
        try:
            values = _edge_model(data, vehicle, provenance, (u, v, key), rain_level, traffic_level, cfg)
        except (ValueError, TypeError, OverflowError):
            values = None
        if values is None:
            rejected += 1
            continue
        model[u, v, key] = values
        time_s, hazard, _, _ = values
        resource = -math.expm1(-hazard) if cfg.risk_aggregation == 'union_bound' else hazard
        if not reverse.has_edge(v, u):
            reverse.add_edge(v, u, time=time_s, hazard=resource)
        else:
            reverse[v][u]['time'] = min(reverse[v][u]['time'], time_s)
            reverse[v][u]['hazard'] = min(reverse[v][u]['hazard'], resource)
    return PreparedSearch(G, cfg, vehicle.model_dump(), rain_level, traffic_level, model, reverse, rejected)


def route_risk_aware_multilabel(G, orig_node, dest_node, vehicle, provenance=None,
                               rain_level='none', traffic_level='normal',
                               max_network_speed_mps=33.33, max_candidates_to_find=10,
                               config=None, diagnostics=None, prepared=None):
    """Return exact-edge candidates. None for the candidate cap exhausts the search.

    A finite cap intentionally truncates search, and frontier_complete is false.
    Node state is sufficient only because actual turn-dependent costs are absent.
    """
    del max_network_speed_mps  # Reverse graph bounds do not assume geometric speeds.
    cfg = config or SearchConfig()
    if not G.is_multigraph() or not G.is_directed():
        raise TypeError('a directed MultiDiGraph is required')
    if orig_node not in G or dest_node not in G:
        raise nx.NodeNotFound('origin or destination is absent')
    if max_candidates_to_find is not None and max_candidates_to_find < 1:
        raise ValueError('candidate cap must be positive or None')
    info = diagnostics if diagnostics is not None else {}
    info.update(expanded_labels=0, generated_labels=1, pruned_labels=0,
                stale_labels=0, max_labels_per_node=1, rejected_edges=0,
                frontier_complete=False, status='running',
                method='exact' if cfg.exact else 'conservative_rounding')
    budget = (cfg.max_hazard if cfg.max_hazard is not None else RISK_BUDGETS[vehicle.risk_preference]) if cfg.use_hazard else math.inf
    if cfg.risk_aggregation == 'union_bound' and math.isfinite(budget):
        budget = -math.expm1(-budget)
    prepared = prepared or prepare_search(G, vehicle, provenance, rain_level, traffic_level, cfg)
    if (prepared.graph is not G or prepared.config != cfg or prepared.vehicle_signature != vehicle.model_dump()
            or prepared.rain != rain_level or prepared.traffic != traffic_level):
        raise ValueError('prepared search does not match the requested condition')
    model, reverse = prepared.model, prepared.reverse
    info['rejected_edges'] = prepared.rejected_edges
    if cfg.use_reverse_bounds:
        time_lb = nx.single_source_dijkstra_path_length(reverse, dest_node, weight='time')
        hazard_lb = nx.single_source_dijkstra_path_length(reverse, dest_node, weight='hazard') if cfg.use_hazard else {}
    else:
        time_lb, hazard_lb = {}, {}
    if cfg.use_reverse_bounds and orig_node not in time_lb:
        info.update(status='no_path', frontier_complete=True)
        return []

    def lower_time(n):
        return time_lb.get(n, math.inf) if cfg.use_reverse_bounds else 0.

    start = Label(0., 0., 0., math.inf, orig_node)
    fronts = {orig_node: [start]}
    heap = [(lower_time(orig_node), 0, start)]
    counter = 1
    completed = []
    truncated = False

    def rounded_add(value, step):
        # Integer ticks avoid compounding floating-point bucket errors.
        return math.ceil(value / step)

    while heap:
        _, _, current = heapq.heappop(heap)
        if not current.active:
            info['stale_labels'] += 1
            continue
        if current.node == dest_node:
            completed.append(current)
            if max_candidates_to_find is not None and len(completed) >= max_candidates_to_find:
                truncated = bool(heap)
                break
            continue
        if info['expanded_labels'] >= cfg.max_expansions:
            info.update(status='search_limit', frontier_complete=False)
            raise RoutingSearchLimit(f'Search exceeded {cfg.max_expansions} label expansions')
        info['expanded_labels'] += 1
        for _, v, key in G.out_edges(current.node, keys=True):
            values = model.get((current.node, v, key))
            if values is None:
                continue
            t, h, uncertainty, clearance = values
            resource = -math.expm1(-h) if cfg.risk_aggregation == 'union_bound' else h
            hr = 0. if not cfg.use_hazard else (resource if cfg.exact else rounded_add(resource, cfg.hazard_step))
            ur = uncertainty if cfg.exact else rounded_add(uncertainty, cfg.uncertainty_step)
            cr = clearance if cfg.exact else math.floor(clearance / cfg.clearance_step) if math.isfinite(clearance) else clearance
            if not cfg.use_hazard:
                hr = 0.
            if not cfg.use_uncertainty:
                ur = 0.
            if not cfg.use_clearance:
                cr = 0.
            new_hr = current.hazard_resource + hr
            hazard_bound = new_hr if cfg.exact else new_hr * cfg.hazard_step
            remaining_h = hazard_lb.get(v, math.inf) if cfg.use_reverse_bounds and cfg.use_hazard else 0.
            if hazard_bound + remaining_h > budget or not math.isfinite(lower_time(v)):
                info['pruned_labels'] += 1
                continue
            label = Label(current.travel_time_s + t, current.integrated_hazard + h,
                          current.uncertainty_penalty + uncertainty,
                          min(current.min_clearance_m, clearance), v, key, current,
                          hazard_resource=new_hr,
                          uncertainty_resource=current.uncertainty_resource + ur,
                          clearance_resource=min(current.clearance_resource, cr))
            front = fronts.setdefault(v, [])
            if any(_weak_dominates(existing, label) for existing in front):
                info['pruned_labels'] += 1
                continue
            survivors = []
            for existing in front:
                if _weak_dominates(label, existing):
                    existing.active = False
                    info['pruned_labels'] += 1
                else:
                    survivors.append(existing)
            survivors.append(label)
            fronts[v] = survivors
            info['max_labels_per_node'] = max(info['max_labels_per_node'], len(survivors))
            label.f_score = label.travel_time_s + lower_time(v)
            heapq.heappush(heap, (label.f_score, counter, label))
            counter += 1
            info['generated_labels'] += 1
    completed = [label for label in completed if label.active]
    # In rounded mode, remove any dominated candidates in original objectives too.
    if cfg.risk_aggregation == 'independent':
        completed = [a for a in completed if not any(b.dominates(a) for b in completed if b is not a)]
    info.update(status='candidate_limit' if truncated else ('ok' if completed else 'no_path'),
                frontier_complete=not truncated, candidate_count=len(completed))
    results = []
    for final in completed:
        nodes, edges = [], []
        current = final
        while current.predecessor is not None:
            prev = current.predecessor
            nodes.append(current.node)
            edges.append((prev.node, current.node, current.edge_key,
                          dict(G[prev.node][current.node][current.edge_key])))
            current = prev
        nodes.append(orig_node)
        nodes.reverse()
        edges.reverse()
        stats = _compute_path_stats(G, edges, vehicle, provenance, rain_level, traffic_level, cfg)
        upper_bound = final.hazard_resource if cfg.exact else final.hazard_resource * cfg.hazard_step
        stats['risk_certificate'] = {
            'aggregation': cfg.risk_aggregation,
            'resource_upper_bound': upper_bound,
            'resource_budget': budget if math.isfinite(budget) else None,
            'completion_probability_lower_bound': (max(0., 1.-upper_bound) if cfg.risk_aggregation == 'union_bound'
                                                     else math.exp(-upper_bound)) if cfg.use_hazard else None,
            'basis': 'conditional_on_valid_edge_probability_bounds',
        }
        stats['hazard_upper_bound'] = upper_bound if cfg.risk_aggregation == 'independent' else stats['integrated_hazard']
        stats['modeled_hazard_budget'] = budget if math.isfinite(budget) else None
        stats['search'] = dict(info)
        results.append((nodes, edges, stats))
    return sorted(results, key=lambda item: item[2]['travel_time_s'])


def route_risk_aware(G, orig_node, dest_node, vehicle, provenance=None,
                     rain_level='none', traffic_level='normal', config=None, diagnostics=None, prepared=None):
    candidates = route_risk_aware_multilabel(
        G, orig_node, dest_node, vehicle, provenance, rain_level, traffic_level,
        max_candidates_to_find=1, config=config, diagnostics=diagnostics, prepared=prepared)
    return candidates[0] if candidates else (None, None, {})


def _compute_path_stats(G, path_edges, vehicle, provenance=None,
                        rain_level='none', traffic_level='normal', config=None):
    cfg = config or SearchConfig()
    duration = distance = hazard = uncertainty = density = 0.
    clearance = math.inf
    for u, v, key, data in path_edges:
        values = _edge_model(data, vehicle, provenance, (u, v, key), rain_level, traffic_level, cfg)
        if values is None:
            probability, ue = predict_traversability(data, vehicle, provenance, (u, v, key), rain_level, traffic_level)
            h = -math.log(probability) if probability > 0 else math.inf
            t = _physics_travel_time(data, rain_level, traffic_level)
            margins = compute_geometry_margins(data, vehicle)
            c = margins.get('width_clearance_m')
            c = c if c is not None else -math.inf
            unc = ue * float(data.get('length', 50.))
        else:
            t, h, unc, c = values
        length = float(data.get('length', 50.))
        duration += t
        distance += length
        hazard += h
        uncertainty += unc
        clearance = min(clearance, c)
        data['_traversability_prob'] = math.exp(-h)
        try:
            density += get_car_density(data, traffic_level) * length
        except (ValueError, TypeError):
            pass
    return {
        'distance_m': distance, 'travel_time_s': duration,
        'completion_probability': math.exp(-hazard),
        'integrated_hazard': hazard if math.isfinite(hazard) else None,
        'uncertainty_penalty': uncertainty,
        'min_clearance_m': clearance if math.isfinite(clearance) else None,
        'avg_speed_kmh': distance / duration * 3.6 if duration else 0.,
        'avg_cars_per_km': density / distance if distance else 0.,
        'rain_level': rain_level, 'traffic_level': traffic_level,
        'edge_count': len(path_edges), 'probability_basis': 'uncalibrated_engineering_model',
    }
