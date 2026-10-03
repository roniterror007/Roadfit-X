"""Route metrics with explicit evidence coverage and no-path handling."""
import math
from src.vehicle.geometry_constraints import _parse_osm_float
from src.routing.cvar_optimizer import compute_catastrophic_cvar

METRIC_FIELDS = ('ISER_%', 'CNME_%', 'MDEF_%', 'ConstraintCoverage_%',
                 'TRR', 'ETTP_%', 'MinClearance_m', 'PhysicalFailure')

class MetricsEngine:
    def __init__(self, vehicle):
        self.vehicle = vehicle

    def _limits(self, data):
        return [_parse_osm_float(data.get(tag), math.nan, quantity) for tag, quantity in
                [('width', 'length'), ('maxheight', 'length'), ('maxweight', 'weight')]]

    def compute_iser(self, path_edges):
        total = violations = 0.
        for _, _, _, data in path_edges or []:
            length = float(data.get('length', 50.))
            total += length
            limits = self._limits(data)
            required = (self.vehicle.width_m + self.vehicle.width_buffer_m,
                        self.vehicle.height_m + self.vehicle.height_buffer_m,
                        self.vehicle.gross_weight_t)
            if any(math.isfinite(limit) and limit < need for limit, need in zip(limits, required)):
                violations += length
        return violations / total * 100 if total else 0.

    def compute_cnme(self, path_edges):
        total = narrow = 0.
        for _, _, _, data in path_edges or []:
            length = float(data.get('length', 50.))
            total += length
            width = self._limits(data)[0]
            clearance = width - self.vehicle.width_m - self.vehicle.width_buffer_m
            if 0 <= clearance <= .2:
                narrow += length
        return narrow / total * 100 if total else 0.

    def compute_mdef(self, path_edges):
        total = missing = 0.
        for _, _, _, data in path_edges or []:
            length = float(data.get('length', 50.))
            total += length
            if not math.isfinite(self._limits(data)[0]) or data.get('width_source') == 'inferred':
                missing += length
        return missing / total * 100 if total else 0.

    def compute_min_clearance(self, path_edges):
        widths = [self._limits(data)[0] for _, _, _, data in path_edges or []]
        if not widths or any(not math.isfinite(v) for v in widths):
            return None
        return min(widths) - self.vehicle.width_m - self.vehicle.width_buffer_m

    def compute_coverage(self, path_edges):
        total = known = 0.
        for _, _, _, data in path_edges or []:
            length = float(data.get('length', 50.))
            total += length
            if all(math.isfinite(v) for v in self._limits(data)):
                known += length
        return known / total * 100 if total else 100.

    def compute_trr(self, G, path_nodes, path_edges, median_eta, seed=0):
        if path_nodes is None or median_eta <= 0:
            return None
        result = compute_catastrophic_cvar(path_edges, self.vehicle, seed=seed)
        return result['cvar_90_sec'] / median_eta

    def compute_ettp(self, route_time, baseline_b0_time):
        return (route_time-baseline_b0_time) / baseline_b0_time * 100 if baseline_b0_time > 0 else None

    def evaluate_route(self, G, path_nodes, path_edges, median_eta, baseline_b0_time,
                       truth_graph=None, rain_level='none', traffic_level='normal',
                       seed=0, num_scenarios=1000):
        if path_nodes is None:
            return {**dict.fromkeys(METRIC_FIELDS), 'metric_basis': 'no_route'}
        measured_edges = path_edges
        if truth_graph is not None:
            measured_edges = [(u, v, k, dict(truth_graph[u][v][k])) for u, v, k, _ in path_edges]
        risk = compute_catastrophic_cvar(measured_edges, self.vehicle,
                                        rain_level=rain_level, traffic_level=traffic_level,
                                        num_scenarios=num_scenarios, seed=seed)
        coverage = self.compute_coverage(measured_edges)
        iser = self.compute_iser(measured_edges)
        values = {
            'ISER_%': iser, 'CNME_%': self.compute_cnme(measured_edges),
            'MDEF_%': self.compute_mdef(path_edges),
            'ConstraintCoverage_%': coverage,
            'TRR': risk['cvar_90_sec']/median_eta if median_eta > 0 else None,
            'ETTP_%': self.compute_ettp(median_eta, baseline_b0_time),
            'MinClearance_m': self.compute_min_clearance(measured_edges),
            'PhysicalFailure': int(iser > 0) if coverage == 100. else None,
        }
        return {**{k: round(v, 6) if isinstance(v, float) else v for k, v in values.items()},
                'metric_basis': 'held_out_synthetic_truth' if truth_graph is not None else 'available_observations',
                'risk_basis': risk['basis']}
