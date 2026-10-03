"""ETA previews/voluntary reservations and a separately authenticated officer console."""
from datetime import datetime, timezone
from typing import Literal
import math
import time
import threading

from fastapi import Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field

from src.api.officer_auth import AUTH, require_officer
from src.routing.anticipatory import edge_id, capacity, LOCAL
from src.vehicle.profiles import make_vehicle, PROFILES
from src.vehicle.geometry_constraints import _parse_osm_float, highway_category


class ETARequest(BaseModel):
    orig_lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    orig_lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    dest_lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    dest_lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    vehicle_class: Literal['bicycle', 'motorcycle', 'hatchback', 'suv', 'van', 'truck'] = 'hatchback'
    vehicle_width: float | None = Field(default=None, gt=0, le=6., allow_inf_nan=False)
    vehicle_height: float | None = Field(default=None, gt=0, le=8., allow_inf_nan=False)
    vehicle_weight: float | None = Field(default=None, gt=0, le=100., allow_inf_nan=False)
    unknown_data_policy: Literal['strict', 'conservative', 'exploratory'] = 'conservative'
    rain_level: Literal['none', 'light', 'moderate', 'heavy', 'extreme'] = 'none'
    traffic_level: Literal['low', 'normal', 'heavy', 'gridlock'] = 'normal'
    departure_in_min: float = Field(default=0., ge=0., le=120., allow_inf_nan=False)
    reserve: bool = False


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ForecastRequest(BaseModel):
    edge_id: str = Field(min_length=3, max_length=100)
    flow_pcu_h: float = Field(ge=0., le=20000., allow_inf_nan=False)
    starts_in_min: float = Field(default=0., ge=0., le=120., allow_inf_nan=False)
    duration_min: float = Field(default=10., ge=2., le=60., allow_inf_nan=False)


class CancelRequest(BaseModel):
    token: str = Field(min_length=32, max_length=64)


class ClosureRequest(BaseModel):
    edge_id: str = Field(min_length=3, max_length=100)
    minutes: int = Field(default=15, ge=1, le=60)


class RebalanceRequest(BaseModel):
    max_trips: int = Field(default=10, ge=1, le=20)


def observed_route_metrics(edges, vehicle):
    length = sum(float(d.get('length', 0.)) for _, _, _, d in edges)
    complete = sum(float(d.get('length', 0.)) for _, _, _, d in edges if all(
        math.isfinite(_parse_osm_float(d.get(tag), math.nan, 'weight' if tag == 'maxweight' else 'length'))
        for tag in ['width', 'maxheight', 'maxweight']))
    missing = sum(float(d.get('length', 0.)) for _, _, _, d in edges
                  if not math.isfinite(_parse_osm_float(d.get('width'), math.nan)))
    has_missing = any(not math.isfinite(_parse_osm_float(d.get('width'), math.nan)) for _, _, _, d in edges)
    return {'ConstraintCoverage_%': 100 * complete / max(length, .001),
            'MDEF_%': 100 * missing / max(length, .001),
            'MinClearance_m': None if has_missing else min(
                _parse_osm_float(d['width'], math.nan)-vehicle.width_m-vehicle.width_buffer_m for _, _, _, d in edges),
            'ISER_%': 0.}


def install_traffic_api(app, get_router, get_graph, snap, extract_coords, blocked, close_edge):
    place_cache = {'graph': None, 'records': []}
    place_lock = threading.Lock()
    def active():
        graph = get_graph()
        if graph is None:
            raise HTTPException(503, 'Map not loaded.')
        return graph, get_router(graph)

    @app.post('/officer/login')
    def login(data: LoginRequest, request: Request):
        return AUTH.login(data.username, data.password, request.client.host if request.client else 'local')

    @app.post('/officer/logout')
    def logout(token=Depends(require_officer)):
        AUTH.logout(token)
        return {'message': 'Officer session ended.'}

    @app.get('/traffic/coverage')
    def coverage():
        graph, _ = active()
        return {'nodes': len(graph), 'edges': graph.number_of_edges(),
                'bbox_wsen': graph.graph.get('bbox'), 'mapped_buildings': graph.graph.get('mapped_building_count'),
                'constraint_basis': graph.graph.get('constraint_basis'),
                'bicycle_coverage': 'Drive-service network: cycle-only paths and contraflow cycling are incomplete.',
                'traffic_basis': 'Manual forecasts and voluntarily reserved trips; background load and capacities are uncalibrated assumptions.'}

    @app.get('/traffic/places')
    def places(q: str = Query(min_length=2, max_length=100)):
        graph, _router = active()
        with place_lock:
            if place_cache['graph'] is not graph:
                areas = [('Koramangala', 77.6245, 12.9352), ('Indiranagar', 77.640, 12.974),
                         ('Whitefield', 77.752, 12.966), ('Hebbal', 77.599, 13.035),
                         ('Yeshwantpur', 77.557, 13.023), ('Jayanagar', 77.583, 12.926),
                         ('Electronic City', 77.670, 12.846)]
                xs = [float(d['x']) for _, d in graph.nodes(data=True)]
                ys = [float(d['y']) for _, d in graph.nodes(data=True)]
                records = [{'place_id': 'area-'+name, 'display_name': name+', Bengaluru area reference point', 'lon': lon, 'lat': lat}
                           for name, lon, lat in areas if min(xs) <= lon <= max(xs) and min(ys) <= lat <= max(ys)]
                seen = set()
                for u, v, _k, d in graph.edges(keys=True, data=True):
                    names = d.get('name', [])
                    if not isinstance(names, (list, tuple)):
                        names = [names]
                    for name in names:
                        if str(name).casefold() in seen:
                            continue
                        seen.add(str(name).casefold())
                        a, b = graph.nodes[u], graph.nodes[v]
                        records.append({'place_id': 'road-'+str(name), 'display_name': str(name)+', mapped Bengaluru road',
                                        'lon': (float(a['x'])+float(b['x']))/2, 'lat': (float(a['y'])+float(b['y']))/2})
                place_cache.update(graph=graph, records=records)
            query = q.strip().casefold()
            matched = [row for row in place_cache['records'] if query in row['display_name'].casefold()]
            matched.sort(key=lambda row: (not row['display_name'].casefold().startswith(query), not row['place_id'].startswith('area-')))
            return matched[:8]

    @app.post('/traffic/route')
    def eta_route(data: ETARequest):
        graph, router = active()
        origin = snap(data.orig_lat, data.orig_lon)
        dest = snap(data.dest_lat, data.dest_lon)
        if origin == dest:
            raise HTTPException(400, 'Origin and destination resolve to the same node.')
        vehicle = make_vehicle(data.vehicle_class, data.unknown_data_policy,
                               data.vehicle_width, data.vehicle_height, data.vehicle_weight)
        departure = time.time()+data.departure_in_min*60
        background = {'low': .15, 'normal': .35, 'heavy': .7, 'gridlock': 1.15}[data.traffic_level]
        plan = router.plan(origin, dest, vehicle, data.vehicle_class, departure, background,
                           rain=data.rain_level, blocked=blocked(graph), commit=data.reserve)
        if plan is None or plan.get('status') == 'no_feasible_plan':
            diag = plan.get('diagnostics', {}) if isinstance(plan, dict) else {}
            detail = diag.get('reason', 'unknown')
            raise HTTPException(404, f'No candidate meets vehicle access, physical limits and the residential load budget ({detail}). Try a different data policy or endpoints.')
        if plan.get('reservation_token'):
            with router.ledger.lock:
                router.ledger.bookings[plan['reservation_token']]['intent'] = {
                    'origin': origin, 'destination': dest, 'vehicle': vehicle,
                    'kind': data.vehicle_class, 'departure': departure,
                    'background': background, 'rain': data.rain_level}
        edges = [(u, v, k, graph[u][v][k]) for u, v, k in plan['edges']]
        nodes = [origin]+[e[1] for e in plan['edges']]
        metrics = observed_route_metrics(edges, vehicle)
        road_mix = {}
        for _, _, _, d in edges:
            category = highway_category(d)
            road_mix[category] = road_mix.get(category, 0.)+float(d.get('length', 0.))
        result = {
            'geometry': {'type': 'LineString', 'coordinates': extract_coords(graph, nodes, edges)},
            'eta_nominal_min': round(plan['eta_s']/60, 2),
            'eta_p50_min': None, 'eta_p90_min': None,
            'completion_probability': None, 'cvar_risk': None,
            'distance_km': round(plan['distance_m']/1000, 3),
            'avg_speed_kmh': round(plan['distance_m']/max(plan['eta_s'], .001)*3.6, 2),
            'rain_level': data.rain_level, 'traffic_level': data.traffic_level,
            'ensemble_winner': 'Arrival-aware ETA with bounded detours and locality protection',
            'vehicle_class': data.vehicle_class, 'road_mix_m': road_mix,
            'local_distance_km': round(plan['local_distance_km'], 3),
            'fastest_candidate_eta_min': round(plan['fastest_candidate_eta_s']/60, 2),
            'academic_metrics': metrics,
            'search': {'status': plan['status'], 'candidate_count': plan['candidate_count'],
                       'admissible_candidates': plan['admissible_candidates']},
            'prediction_basis': plan['basis'], 'ledger_revision': plan['ledger_revision'],
            'departure_utc': datetime.fromtimestamp(departure, timezone.utc).isoformat(),
            'reservation_token': plan.get('reservation_token'),
        }
        warnings = ['ETA is a model forecast; it has not been validated against Bengaluru trip times.',
                    'Physical fit uses OSM tags and the selected missing-data policy. Turn-restriction relations and time-conditional access are not modeled.',
                    'Reservations represent voluntary route intentions, not measured traffic. The 25% detour bound applies within the generated candidate set.']
        if metrics['ConstraintCoverage_%'] < 100.:
            warnings.append('Some physical limits are unknown; category priors cannot certify safe clearance.')
        if data.vehicle_class == 'bicycle':
            warnings.append('This driving map omits some cycle-only paths and cycling exceptions to one-way roads.')
        return {'selected_route': result, 'baseline_route': None, 'warnings': warnings}

    @app.post('/traffic/reservations/cancel')
    def cancel(data: CancelRequest):
        _, router = active()
        if not router.ledger.cancel(data.token):
            raise HTTPException(404, 'Reservation does not exist or has expired.')
        return {'message': 'Reservation released.'}

    @app.post('/traffic/reservations/status')
    def reservation_status(data: CancelRequest):
        graph, router = active()
        with router.ledger.lock:
            router.ledger.prune()
            booking = router.ledger.bookings.get(data.token)
            if booking is None:
                raise HTTPException(404, 'Reservation does not exist or has expired.')
            plan = booking.get('plan')
            if plan is None:
                return {'status': 'needs_route', 'message': 'Officer review found no feasible replacement. Recalculate your route.'}
            edges = [(u, v, k, graph[u][v][k]) for u, v, k in plan['edges']]
            nodes = [plan['edges'][0][0]]+[edge[1] for edge in plan['edges']]
            return {'status': 'reserved', 'reviewed': bool(booking.get('reviewed')),
                    'geometry': {'type': 'LineString', 'coordinates': extract_coords(graph, nodes, edges)},
                    'eta_min': round(plan['eta_s']/60, 2), 'distance_km': round(plan['distance_m']/1000, 3),
                    'local_distance_km': round(plan['local_distance_km'], 3),
                    'avg_speed_kmh': round(plan['distance_m']/max(plan['eta_s'], .001)*3.6, 2),
                    'academic_metrics': observed_route_metrics(edges, booking['intent']['vehicle'])}

    @app.post('/officer/rebalance')
    def rebalance(data: RebalanceRequest, _=Depends(require_officer)):
        graph, router = active()
        updated = changed = rejected = 0
        with router.ledger.lock:
            router.ledger.prune()
            now = time.time()
            # Only future departures can be replanned without current vehicle GPS.
            pending = [(token, booking) for token, booking in router.ledger.bookings.items()
                       if booking.get('intent', {}).get('departure', 0.) > now]
            pending.sort(key=lambda item: (item[1]['intent']['departure'], item[0]))
            selected = pending[:data.max_trips]
            closures = blocked(graph)
            for token, _booking in selected:
                router.ledger.cancel(token)
            try:
                for token, booking in selected:
                    intent = booking['intent']
                    plan = router.plan(intent['origin'], intent['destination'], intent['vehicle'], intent['kind'],
                                       intent['departure'], intent['background'], rain=intent['rain'], blocked=closures, commit=True)
                    updated += 1
                    if plan is None or plan.get('status') == 'no_feasible_plan':
                        rejected += 1
                        router.ledger.bookings[token] = {'entries': [], 'expires': booking['expires'],
                                                         'intent': intent, 'plan': None, 'reviewed': True}
                    else:
                        replacement = router.ledger.bookings.pop(plan['reservation_token'])
                        plan['reservation_token'] = token
                        replacement.update(intent=intent, reviewed=True)
                        router.ledger.bookings[token] = replacement
                        old = booking.get('plan')
                        changed += int(old is None or old['edges'] != plan['edges'])
            except Exception:
                # Restore every selected booking and its capacity on unexpected
                # failure; no driver loses an intention because the review crashed.
                for token, booking in selected:
                    router.ledger.cancel(token)
                    for edge, slot, amount in booking['entries']:
                        router.ledger.entries[(edge, slot)] += amount
                    router.ledger.bookings[token] = booking
                raise
        return {'updated': updated, 'changed_routes': changed, 'needs_route': rejected,
                'remaining_future_intentions': max(0, len(pending)-len(selected)),
                'message': f'Reviewed {updated} future departures; changed {changed} routes. {rejected} require a new route. Driver views refresh automatically.'}

    @app.post('/officer/forecast')
    def forecast(data: ForecastRequest, _=Depends(require_officer)):
        graph, router = active()
        lookup = {edge_id((u, v, k)): (u, v, k) for u, v, k in graph.edges(keys=True)}
        edge = lookup.get(data.edge_id)
        if edge is None:
            raise HTTPException(404, 'Unknown directed road segment.')
        router.ledger.set_forecast(edge, time.time()+data.starts_in_min*60, data.duration_min*60, data.flow_pcu_h)
        return {'message': 'Manual future-load scenario applied. New route requests use it before arrival.',
                'revision': router.ledger.revision, 'basis': 'officer_manual_scenario'}

    @app.get('/officer/dashboard')
    def dashboard(_=Depends(require_officer)):
        graph, router = active()
        entries, forecasts, revision = router.ledger.snapshot()
        now = time.time()
        keys = {key for key in set(entries) | set(forecasts)
                if now-120 <= key[1]*router.ledger.control.bin_seconds <= now+7200}
        if not keys:
            # Starter examples are clearly labelled assumptions, not live hotspots.
            for u, v, k, d in graph.edges(keys=True, data=True):
                if highway_category(d) in {'primary', 'secondary', 'trunk'}:
                    keys.add(((u, v, k), router.ledger.bin(now)))
                if len(keys) >= 8:
                    break
        rows = []
        for edge, slot in keys:
            u, v, k = edge
            d = graph[u][v][k]
            cap = capacity(d)
            booked = entries.get((edge, slot), 0.)
            f = forecasts.get((edge, slot), .35*cap)
            rows.append({'edge_id': edge_id(edge), 'name': str(d.get('name', highway_category(d))),
                         'highway': highway_category(d), 'starts_utc': datetime.fromtimestamp(slot*router.ledger.control.bin_seconds, timezone.utc).isoformat(),
                         'capacity_pcu_h': round(cap, 1), 'forecast_pcu_h': f, 'booked_pcu': booked,
                         'load_ratio': (f+booked*3600/router.ledger.control.bin_seconds)/cap,
                         'building_footprints_50m': d.get('building_count_50m'),
                         'basis': 'officer_manual_scenario' if (edge, slot) in forecasts else 'assumed_background_plus_reservations',
                         'geometry': {'type': 'LineString', 'coordinates': extract_coords(graph, [u, v], [(u, v, k, d)])}})
        rows.sort(key=lambda r: -r['load_ratio'])
        return {'active_reservations': len(router.ledger.bookings), 'revision': revision,
                'bin_seconds': router.ledger.control.bin_seconds, 'roads': rows[:30],
                'message': 'Forecast scenarios and reserved link entries. No live traffic feed connected.'}

    @app.post('/officer/closure')
    def closure(data: ClosureRequest, _=Depends(require_officer)):
        graph, _router = active()
        lookup = {edge_id((u, v, k)): (u, v, k) for u, v, k in graph.edges(keys=True)}
        edge = lookup.get(data.edge_id)
        if edge is None:
            raise HTTPException(404, 'Unknown directed road segment.')
        close_edge(edge, data.minutes*60)
        return {'message': f'Selected directed segment closed in the local scenario for {data.minutes} minutes. New route requests avoid it.'}
