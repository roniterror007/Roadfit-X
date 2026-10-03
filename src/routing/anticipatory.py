"""Bounded-candidate, arrival-bin routing with an atomic, expiring PCU ledger.

This is an engineering heuristic, not a newly proved global optimum. BPR response,
category capacities, PCUs and locality costs require calibration before deployment.
Bookings count link *entries*, not simultaneous vehicles occupying an entire link.

Changes from the SUMO study baseline:
  - Queue prediction accepts an observed initial backlog (was always zero).
  - Downstream spillback and signal delay are modelled via queue_model.
  - Hard residential admission cutoff is replaced by graduated cost penalty.
  - Background traffic fraction is configurable (was hardcoded 0.15).
"""
from collections import defaultdict
from dataclasses import dataclass
import math
import threading
import time
import uuid

import networkx as nx

from src.vehicle.geometry_constraints import compute_geometry_margins, highway_category, _parse_osm_float
from src.vehicle.profiles import PROFILES, legal_access, terminal_edge
from src.routing.queue_model import (
    predicted_queue as _predicted_queue_ext,
    observe_queue_from_sumo,
    estimate_signal_delay,
)

LOCAL = {'residential', 'living_street', 'service'}
SPEEDS = {'motorway': 80., 'trunk': 65., 'primary': 45., 'secondary': 35.,
          'tertiary': 28., 'residential': 20., 'living_street': 10., 'service': 15.,
          'unclassified': 25., 'cycleway': 18., 'path': 12., 'track': 12.}
CAPACITY = {'motorway': 1800., 'trunk': 1600., 'primary': 1300., 'secondary': 1000.,
            'tertiary': 800., 'residential': 350., 'living_street': 180., 'service': 250.}


@dataclass(frozen=True)
class Control:
    load_model: str = 'queue'  # 'bpr' is retained as an explicit research baseline.
    bin_seconds: int = 120
    candidates: int = 6
    max_detour: float = 1.25
    social_weight: float = .5
    locality_seconds_per_km: float = 20.
    residential_entry_limit: float = .8
    graduated_locality: bool = True  # graduated penalty instead of hard cutoff
    use_future: bool = True
    use_social: bool = True
    use_locality: bool = True
    use_vehicle_fit: bool = True  # disabled only in research ablations
    default_background: float = 0.  # honest default; set >0 only with a matching fleet


def edge_id(edge):
    return ':'.join(map(str, edge))


def capacity(data):
    if data.get('capacity_pcu_h') is not None:
        return max(float(data['capacity_pcu_h']), .001)
    lanes = _parse_osm_float(data.get('lanes'), 1.)
    # OSM lanes usually describe both directions on two-way ways.
    if str(data.get('oneway', False)).lower() not in {'true', 'yes', '1'}:
        lanes = max(1., lanes/2.)
    return CAPACITY.get(highway_category(data), 500.) * lanes


def free_time(data, kind, rain='none'):
    if data.get('free_time_s') is not None:  # TNTP supplies abstract link costs.
        return max(float(data['free_time_s']), .01)
    speed = SPEEDS.get(highway_category(data), 20.)
    values = data.get('maxspeed')
    if values is not None:
        if not isinstance(values, (list, tuple)):
            values = [values]
        for value in values:
            limit = _parse_osm_float(value, math.nan)
            if math.isfinite(limit):
                speed = min(speed, limit * (1.609344 if 'mph' in str(value).lower() else 1.))
    speed = min(speed, PROFILES[kind].speed)
    rain_factor = {'none': 1., 'light': .9, 'moderate': .8, 'heavy': .65, 'extreme': .5}[rain]
    return max(float(data.get('length', 1.)), .01) / (speed*rain_factor/3.6)


def bpr(free, flow, cap, alpha=.15, power=4.):
    return free*(1.+alpha*(max(0., flow)/cap)**power)


def predicted_queue(edge, slot, timestamp, cap, background, control, entries,
                    forecasts, index, initial_backlog=0., downstream_edge_state=None,
                    signal_delay_s=0.):
    """Fluid backlog from reserved bins, observed state, and downstream spillback.

    When initial_backlog > 0, prediction starts from the observed queue instead
    of zero.  Downstream spillback reduces service when the next edge is full.
    Within-bin intentions are uniform. Background consumes at most 95% of service.
    This approximation is evaluated against individually scheduled FIFO vehicles.
    """
    return _predicted_queue_ext(
        edge, slot, timestamp, cap, background, control, entries,
        forecasts, index, initial_backlog=initial_backlog,
        downstream_edge_state=downstream_edge_state, signal_delay_s=signal_delay_s)


class TrafficLedger:
    def __init__(self, control=None, clock=time.time):
        self.control = control or Control()
        self.clock = clock
        self.lock = threading.RLock()
        self.entries = defaultdict(float)
        self.bookings = {}
        self.forecasts = {}  # (directed keyed edge, bin) -> manual forecast PCU/hour
        self.revision = 0

    def bin(self, timestamp):
        return math.floor(timestamp/self.control.bin_seconds)

    def prune(self):
        now = self.clock()
        for token in [t for t, value in self.bookings.items() if value['expires'] <= now]:
            self.cancel(token)
        # Retain recent manual forecasts to reconstruct queue backlog, even after
        # their active window ends. This is not an observation of the current queue.
        threshold = self.bin(now-7200)
        for key in [k for k in self.forecasts if k[1] < threshold]:
            del self.forecasts[key]

    def cancel(self, token):
        with self.lock:
            booking = self.bookings.pop(token, None)
            if booking is None:
                return False
            for edge, slot, amount in booking['entries']:
                key = (edge, slot)
                self.entries[key] = max(0., self.entries[key]-amount)
                if self.entries[key] < 1e-9:
                    del self.entries[key]
            self.revision += 1
            return True

    def reserve(self, plan, amount):
        token = uuid.uuid4().hex
        records = [(edge, slot, amount) for edge, slot, _, _ in plan['schedule']]
        with self.lock:
            for edge, slot, value in records:
                self.entries[(edge, slot)] += value
            self.bookings[token] = {'entries': records, 'expires': plan['arrival']+300, 'plan': plan}
            self.revision += 1
        return token

    def set_forecast(self, edge, start, duration, flow):
        with self.lock:
            for slot in range(self.bin(start), self.bin(start+duration-1e-6)+1):
                self.forecasts[(edge, slot)] = float(flow)
            self.revision += 1

    def snapshot(self):
        with self.lock:
            self.prune()
            return dict(self.entries), dict(self.forecasts), self.revision


class ArrivalRouter:
    def __init__(self, graph, ledger=None):
        self.graph = graph
        self.ledger = ledger or TrafficLedger()
        self._prepared = {}

    def prepare(self, vehicle, kind, rain, enforce=True):
        key = (kind, vehicle.model_dump_json(), rain, enforce)
        if key in self._prepared:
            return self._prepared[key]
        result = nx.DiGraph()
        result.add_nodes_from(self.graph)
        for u, v, k, data in self.graph.edges(keys=True, data=True):
            if enforce and any(x is None or x < 0 for x in compute_geometry_margins(data, vehicle).values()):
                continue
            if float(data.get('_history_penalty', 0.)) >= 1:
                continue
            if not legal_access(data, kind, terminal=True):
                continue
            t = free_time(data, kind, rain)
            option = dict(key=k, free=t, capacity=capacity(data), category=highway_category(data), data=data)
            if result.has_edge(u, v):
                options = result[u][v]['options'] + [option]
            else:
                options = [option]
            if not result.has_edge(u, v) or t < result[u][v]['free']:
                result.add_edge(u, v, **option)
            result[u][v]['options'] = sorted(options, key=lambda d: d['free'])
        # Cache at most twelve combinations; the graph must be immutable.
        if len(self._prepared) >= 12:
            self._prepared.pop(next(iter(self._prepared)))
        self._prepared[key] = result
        return result

    def candidate_paths(self, origin, destination, vehicle, kind, rain, control, background, entries, forecasts, departure, blocked):
        base = self.prepare(vehicle, kind, rain, control.use_vehicle_fit)
        first_thru = int(self.graph.graph.get('first_thru_node', 0))
        selected = {}

        def allowed(u, v):
            if (u, v) in selected:
                return selected[(u, v)] is not None
            if first_thru and isinstance(u, int) and u < first_thru and u != origin:
                selected[(u, v)] = None
                return False
            terminal = terminal_edge(self.graph, u, v, origin, destination)
            for d in base[u][v]['options']:
                if (u, v, d['key']) in blocked or not legal_access(d['data'], kind, terminal):
                    continue
                if kind == 'truck' and d['category'] in LOCAL and not terminal:
                    continue
                selected[(u, v)] = d
                return True
            selected[(u, v)] = None
            return False

        filtered = nx.subgraph_view(base, filter_edge=allowed)
        paths, seen = [], set()
        penalties = defaultdict(lambda: 1.)
        slot = self.ledger.bin(departure)
        # Free-flow and current-load seeds, then diversifying penalized paths.
        for index in range(control.candidates):
            def weight(u, v, d):
                d = selected[(u, v)]
                f = background*d['capacity']
                if index > 0:
                    e = (u, v, d['key'])
                    f = forecasts.get((e, slot), f) + entries.get((e, slot), 0.)*3600/control.bin_seconds
                return bpr(d['free'], f, d['capacity'])*penalties[(u, v)]
            try:
                nodes = nx.shortest_path(filtered, origin, destination, weight=weight)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                break
            edges = tuple((u, v, selected[(u, v)]['key']) for u, v in zip(nodes, nodes[1:]))
            if edges not in seen:
                seen.add(edges)
                paths.append(edges)
            for u, v in zip(nodes, nodes[1:]):
                penalties[(u, v)] *= 1.6
        return paths

    def score(self, edges, kind, departure, background, control, entries, forecasts,
              origin, destination, rain='none', queue_index=None,
              initial_backlogs=None, downstream_state=None, signal_delays=None):
        clock = departure
        social = locality = local_km = distance = 0.
        schedule = []
        amount = PROFILES[kind].pcu
        delta = amount*3600/control.bin_seconds
        if queue_index is None:
            queue_index = defaultdict(dict)
            for (e, b), value in entries.items():
                queue_index[e][b] = value
        for i, edge in enumerate(edges):
            u, v, k = edge
            data = self.graph[u][v][k]
            slot = self.ledger.bin(clock if control.use_future else departure)
            cap = capacity(data)
            flow = forecasts.get((edge, slot), background*cap) + entries.get((edge, slot), 0.)*3600/control.bin_seconds
            free = free_time(data, kind, rain)
            alpha, power = float(data.get('bpr_alpha', .15)), float(data.get('bpr_power', 4.))
            if control.load_model == 'queue':
                evaluation_time = clock if control.use_future else departure
                obs_backlog = (initial_backlogs or {}).get(edge, 0.)
                sig_delay = (signal_delays or {}).get(edge, 0.)
                next_edge_state = None
                if downstream_state is not None and i + 1 < len(edges):
                    next_edge_state = downstream_state.get(edges[i+1])
                backlog, available_rate = predicted_queue(
                    edge, slot, evaluation_time, cap, background,
                    control, entries, forecasts, queue_index,
                    initial_backlog=obs_backlog,
                    downstream_edge_state=next_edge_state,
                    signal_delay_s=sig_delay)
                after = free+(backlog+amount)/available_rate
                marginal = after+backlog/available_rate
                
                # Prevent physical gridlock: mathematically, fluid queues can be infinite.
                # Physically, they spill back and block intersections when storage is full.
                if downstream_state is not None and edge in downstream_state:
                    edge_storage = downstream_state[edge].get('storage', float('inf'))
                    if edge_storage > 0:
                        occupancy_ratio = (backlog + amount) / edge_storage
                        if occupancy_ratio > 0.9:
                            # Massive exponential penalty for entering a physically full road
                            excess = occupancy_ratio - 0.9
                            gridlock_penalty = 3600.0 * (excess * 10)**2
                            after += gridlock_penalty
                            marginal += gridlock_penalty
            else:
                before = bpr(free, flow, cap, alpha, power)
                after = bpr(free, flow+delta, cap, alpha, power)
                # Marginal total PCU delay normalized to the entrant's flow increment.
                marginal = ((flow+delta)*after - flow*before)/delta
            social += marginal
            length = float(data.get('length', 0.))
            distance += length
            if highway_category(data) in LOCAL:
                local_km += length/1000.
                if not terminal_edge(self.graph, u, v, origin, destination):
                    load_ratio = (flow+delta)/cap
                    if control.use_locality and not control.graduated_locality:
                        # Legacy hard cutoff — retained for ablation comparison.
                        if load_ratio > control.residential_entry_limit:
                            return None
                    density = min(3., float(data.get('building_count_50m', 0.))/10.)
                    base_penalty = control.locality_seconds_per_km*(length/1000.)*(1.+density)*amount
                    if control.graduated_locality and control.use_locality:
                        # Graduated: penalty scales smoothly above the soft limit.
                        # Below 80% load: base penalty only.
                        # 80-100%: 1x-3x multiplier.  Above 100%: 3x+ (quadratic).
                        if load_ratio > control.residential_entry_limit:
                            excess = (load_ratio - control.residential_entry_limit) / max(0.01, 1. - control.residential_entry_limit)
                            multiplier = 1. + 2. * min(excess, 1.) + max(0., excess - 1.)**2
                            base_penalty *= multiplier
                    locality += base_penalty
            # Book the predicted physical arrival bin, even in the no-future ablation.
            schedule.append((edge, self.ledger.bin(clock), clock, after))
            clock += after
        eta = clock-departure
        score = eta + (control.social_weight*(social-eta) if control.use_social else 0.)
        score += locality if control.use_locality else 0.
        return {'edges': edges, 'eta_s': eta, 'score': score, 'departure': departure,
                'arrival': clock, 'distance_m': distance, 'local_distance_km': local_km, 'schedule': schedule}

    def plan(self, origin, destination, vehicle, kind, departure=None, background=.35,
             control=None, rain='none', blocked=frozenset(), commit=False, candidates=None,
             initial_backlogs=None, downstream_state=None, signal_delays=None):
        control = control or self.ledger.control
        if control.bin_seconds != self.ledger.control.bin_seconds:
            raise ValueError('Control and ledger time bins must agree.')
        departure = self.ledger.clock() if departure is None else departure
        # Planning and booking are one critical section; concurrent requests cannot
        # all consume the same stale capacity snapshot when commit=True.
        with self.ledger.lock:
            entries, forecasts, revision = self.ledger.snapshot()
            queue_index = defaultdict(dict)
            for (edge, slot), value in entries.items():
                queue_index[edge][slot] = value
            paths = candidates if candidates is not None else self.candidate_paths(
                origin, destination, vehicle, kind, rain, control, background, entries, forecasts, departure, blocked)
            scored = [self.score(p, kind, departure, background, control, entries, forecasts,
                                 origin, destination, rain, queue_index,
                                 initial_backlogs=initial_backlogs,
                                 downstream_state=downstream_state,
                                 signal_delays=signal_delays) for p in paths]
            valid = [p for p in scored if p is not None]
            if not valid:
                diagnostics = {
                    'reason': 'all_candidates_rejected',
                    'candidate_count': len(paths),
                    'scored_count': len(scored),
                    'null_count': sum(1 for s in scored if s is None),
                }
                return {'status': 'no_feasible_plan', 'diagnostics': diagnostics}
            fastest = min(p['eta_s'] for p in valid)
            eligible = [p for p in valid if p['eta_s'] <= fastest*control.max_detour+1e-9]
            result = min(eligible, key=lambda p: (p['score'], p['eta_s'], repr(p['edges'])))
            result.update(candidate_count=len(paths), admissible_candidates=len(valid),
                          fastest_candidate_eta_s=fastest, ledger_revision=revision,
                          basis='uncalibrated_arrival_bin_'+control.load_model+'_model', status='bounded_candidate_heuristic')
            if commit:
                result['reservation_token'] = self.ledger.reserve(result, PROFILES[kind].pcu)
            return result
