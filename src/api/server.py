"""Local RoadFit research API. Routes and simulated estimates carry evidence labels."""
import os
import math
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import numpy as np
import osmnx as ox
import shapely.wkt
from scipy.spatial import KDTree
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from src.routing.risk_aware_router import route_risk_aware, RoutingSearchLimit, haversine_admissible_heuristic
from src.routing.cvar_optimizer import optimize_cvar_route, compute_catastrophic_cvar
from src.vehicle.vehicle_digital_twin import VehicleDigitalTwin
from src.evaluation.baseline_routes import route_baseline
from src.evaluation.metrics_engine import MetricsEngine
from src.data.provenance_store import ProvenanceStore
from src.brain.cognitive_core import CognitiveCore
from src.api.officer_auth import require_officer
from src.api.traffic_api import install_traffic_api
from src.routing.anticipatory import ArrivalRouter
from src.vehicle.profiles import make_vehicle, legal_access
from src.vehicle.geometry_constraints import highway_category

ROOT = Path(__file__).resolve().parents[2]
app = FastAPI(title='RoadFit-X Research API', version='4.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get('ROADFIT_CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(','),
    allow_credentials=False, allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'Authorization'])
MASTER_GRAPH = KD_TREE = NODE_IDS = None
PROVENANCE = ProvenanceStore()
BRAIN = CognitiveCore(db_path=str(ROOT / 'brain_memory.db'),
                      model_path=os.environ.get('ROADFIT_SEMANTIC_MODEL'))
JOBS = {}
JOB_LOCK = threading.Lock()
WORKERS = ThreadPoolExecutor(max_workers=1, thread_name_prefix='roadfit-job')
ARRIVAL_ROUTER = None

def _build_kd_tree(graph):
    nodes = list(graph.nodes(data=True))
    return KDTree(np.array([[float(d['y']), float(d['x'])] for _, d in nodes])), [n for n, _ in nodes]

try:
    expanded = ROOT / 'data/public/bengaluru_drive.graphml'
    default_graph = expanded if expanded.exists() else ROOT / 'data/koramangala_enriched_v2.graphml'
    graph_file = Path(os.environ.get('ROADFIT_GRAPH', str(default_graph)))
    MASTER_GRAPH = ox.load_graphml(graph_file)
    if 'constraint_basis' not in MASTER_GRAPH.graph:
        MASTER_GRAPH.graph['constraint_basis'] = 'legacy_synthetic_unvalidated'
        # The old enrichment overwrote observed widths without retaining provenance.
        for _, _, _, data in MASTER_GRAPH.edges(keys=True, data=True):
            if 'width' in data:
                data['width_source'] = 'legacy_synthetic_unvalidated'
    KD_TREE, NODE_IDS = _build_kd_tree(MASTER_GRAPH)
except (OSError, ValueError) as error:
    print(f'Map unavailable: {error}')

class CoordinateRequest(BaseModel):
    vehicle_class: Literal['custom', 'bicycle', 'motorcycle', 'hatchback', 'suv', 'van', 'truck'] = 'custom'
    orig_lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    orig_lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    dest_lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    dest_lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    vehicle_width: float = Field(default=2., gt=0, le=6., allow_inf_nan=False)
    vehicle_height: float = Field(default=2., gt=0, le=8., allow_inf_nan=False)
    vehicle_weight: float = Field(default=2., gt=0, le=100., allow_inf_nan=False)
    unknown_data_policy: Literal['strict', 'conservative', 'exploratory'] = 'conservative'
    simulate_congestion: bool = False
    rain_level: Literal['none', 'light', 'moderate', 'heavy', 'extreme'] = 'none'
    traffic_level: Literal['low', 'normal', 'heavy', 'gridlock'] = 'normal'

class RoadblockRequest(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    severity: float = Field(default=1., ge=0, le=1, allow_inf_nan=False)

@app.get('/')
def read_root():
    return {'message': 'RoadFit-X Research API v4.0 is online'}

@app.get('/health')
def health():
    return {'status': 'ok' if MASTER_GRAPH is not None else 'map_unavailable',
            'nodes': MASTER_GRAPH.number_of_nodes() if MASTER_GRAPH is not None else 0,
            'edges': MASTER_GRAPH.number_of_edges() if MASTER_GRAPH is not None else 0,
            'constraint_basis': MASTER_GRAPH.graph.get('constraint_basis') if MASTER_GRAPH is not None else None}

def _submit_job(function):
    with JOB_LOCK:
        if any(j['status'] in ('queued', 'running') for j in JOBS.values()):
            raise HTTPException(409, 'A training job is already active.')
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {'status': 'queued', 'message': 'Queued'}
    def run():
        with JOB_LOCK:
            JOBS[job_id] = {'status': 'running', 'message': 'Running simulated training'}
        try:
            result = function()
        except Exception as error:
            with JOB_LOCK:
                JOBS[job_id] = {'status': 'failed', 'message': str(error)}
        else:
            with JOB_LOCK:
                JOBS[job_id] = {'status': 'complete', 'message': 'Simulated training completed', 'result': result}
    WORKERS.submit(run)
    return {'job_id': job_id, 'message': 'Simulated training queued; follow the job status.'}

class TrainingRequest(BaseModel):
    vehicle_class: Literal['custom', 'bicycle', 'motorcycle', 'hatchback', 'suv', 'van', 'truck'] = 'custom'
    vehicle_width: float = Field(default=2.4, gt=0, le=6., allow_inf_nan=False)
    vehicle_height: float = Field(default=2.8, gt=0, le=8., allow_inf_nan=False)
    vehicle_weight: float = Field(default=5., gt=0, le=100., allow_inf_nan=False)

def _vehicle_profile(request, policy='conservative'):
    if request.vehicle_class != 'custom':
        return make_vehicle(request.vehicle_class, policy,
                            request.vehicle_width if 'vehicle_width' in request.model_fields_set else None,
                            request.vehicle_height if 'vehicle_height' in request.model_fields_set else None,
                            request.vehicle_weight if 'vehicle_weight' in request.model_fields_set else None)
    return VehicleDigitalTwin(
        vehicle_type=f'custom_{request.vehicle_width:g}_{request.vehicle_height:g}_{request.vehicle_weight:g}',
        width_m=request.vehicle_width, height_m=request.vehicle_height,
        gross_weight_t=request.vehicle_weight, axle_load_t=request.vehicle_weight/2.,
        wheelbase_m=3., turning_radius_m=6., ground_clearance_m=.2, max_grade_pct=15.,
        surface_tolerance=['asphalt', 'concrete', 'paved', 'compacted'],
        rain_tolerance='medium', risk_preference='moderate', unknown_data_policy=policy)

@app.post('/brain/train')
def train_brain(request: TrainingRequest | None = None,
                iterations: int = Query(default=1000, ge=1, le=100000)):
    if MASTER_GRAPH is None:
        raise HTTPException(503, 'Master graph not loaded.')
    vehicle = _vehicle_profile(request or TrainingRequest(), policy='exploratory')
    return _submit_job(lambda: BRAIN.train_brain(MASTER_GRAPH, iterations, vehicle=vehicle))

@app.post('/brain/consolidate')
def consolidate_semantic_memory():
    if MASTER_GRAPH is None:
        raise HTTPException(503, 'Master graph not loaded.')
    return _submit_job(lambda: BRAIN.semantic.consolidate(BRAIN.episodic.db_path, MASTER_GRAPH))

@app.get('/brain/jobs/{job_id}')
def get_job(job_id: str):
    with JOB_LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, 'Unknown job')
        return {'job_id': job_id, **JOBS[job_id]}

def _snap(graph, tree, ids, lat, lon):
    _, index = tree.query([lat, lon])
    node = ids[int(index)]
    data = graph.nodes[node]
    distance = haversine_admissible_heuristic(lat, lon, float(data['y']), float(data['x']), 1.)
    if distance > 1000:
        raise HTTPException(422, 'Point is outside map coverage (nearest road is over 1 km away).')
    return node

@app.post('/brain/roadblock')
def report_live_roadblock(request: RoadblockRequest, _=Depends(require_officer)):
    if KD_TREE is None:
        raise HTTPException(503, 'Map not loaded.')
    node = _snap(MASTER_GRAPH, KD_TREE, NODE_IDS, request.lat, request.lon)
    edges = list(MASTER_GRAPH.out_edges(node, keys=True))
    for u, v, key in edges:
        BRAIN.working.report_live_hazard(f'{u}_{v}_{key}', request.severity, ttl_seconds=900)
    return {'message': f'Reported hazard on {len(edges)} outgoing edges for 15 minutes.'}

def _extract_route_coords_from_edges(graph, path_nodes, path_edges):
    coords = []
    for u, v, _, data in path_edges:
        geometry = data.get('geometry')
        points = []
        if geometry is not None:
            try:
                geometry = shapely.wkt.loads(geometry) if isinstance(geometry, str) else geometry
                points = [[float(x), float(y)] for x, y in geometry.coords]
                start = graph.nodes[u]
                if points and ((points[-1][0]-start['x'])**2+(points[-1][1]-start['y'])**2
                               < (points[0][0]-start['x'])**2+(points[0][1]-start['y'])**2):
                    points.reverse()
            except (ValueError, AttributeError):
                points = []
        if not points:
            points = [[float(graph.nodes[n]['x']), float(graph.nodes[n]['y'])] for n in (u, v)]
        for point in points:
            if not coords or point != coords[-1]:
                coords.append(point)
    return coords

def _active_map(request):
    if MASTER_GRAPH is None:
        raise HTTPException(503, 'Master graph not loaded.')
    try:
        origin = _snap(MASTER_GRAPH, KD_TREE, NODE_IDS, request.orig_lat, request.orig_lon)
        dest = _snap(MASTER_GRAPH, KD_TREE, NODE_IDS, request.dest_lat, request.dest_lon)
        return MASTER_GRAPH, origin, dest
    except HTTPException:
        if os.environ.get('ROADFIT_ALLOW_DOWNLOAD') != '1':
            raise
    distance = haversine_admissible_heuristic(request.orig_lat, request.orig_lon, request.dest_lat, request.dest_lon, 1.)
    if distance > 50_000:
        raise HTTPException(422, 'Dynamic map downloads are limited to trips within 50 km.')
    ox.settings.requests_timeout = 30
    padding = .015
    try:
        graph = ox.graph_from_bbox(
            bbox=(min(request.orig_lon, request.dest_lon)-padding,
                  min(request.orig_lat, request.dest_lat)-padding,
                  max(request.orig_lon, request.dest_lon)+padding,
                  max(request.orig_lat, request.dest_lat)+padding), network_type='drive')
        graph.graph['constraint_basis'] = 'osm_unvalidated'
        tree, ids = _build_kd_tree(graph)
        return graph, _snap(graph, tree, ids, request.orig_lat, request.orig_lon), _snap(graph, tree, ids, request.dest_lat, request.dest_lon)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, 'Could not fetch map data.') from error

@app.post('/route/plan')
def route_plan(request: CoordinateRequest):
    graph, origin, dest = _active_map(request)
    if origin == dest:
        raise HTTPException(400, 'Origin and destination resolve to the same node.')
    vehicle = _vehicle_profile(request, request.unknown_data_policy)
    rain, traffic = request.rain_level, request.traffic_level
    b0_nodes, b0_edges, b0_stats = route_baseline(graph, origin, dest, vehicle, rain_level=rain, traffic_level=traffic)
    # All selection methods respect live roadblocks and memory. An unbiased route
    # cannot defeat a roadblock merely by receiving a shorter travel-time score.
    biased = BRAIN.apply_cognitive_bias(graph, rain, traffic, vehicle.vehicle_type)
    if request.vehicle_class != 'custom':
        tree, ids = (KD_TREE, NODE_IDS) if graph is MASTER_GRAPH else _build_kd_tree(graph)
        terminal_nodes = {origin, dest}
        for node in (origin, dest):
            point = graph.nodes[node]
            radius = 350/(111320*math.cos(math.radians(float(point['y']))))
            terminal_nodes.update(ids[i] for i in tree.query_ball_point([point['y'], point['x']], radius))
        remove = []
        for u, v, key, data in biased.edges(keys=True, data=True):
            terminal = u in terminal_nodes or v in terminal_nodes
            if (not legal_access(data, request.vehicle_class, terminal) or
                    (request.vehicle_class == 'truck' and highway_category(data) in {'residential', 'service', 'living_street'} and not terminal)):
                remove.append((u, v, key))
        biased.remove_edges_from(remove)
    try:
        if request.simulate_congestion:
            nodes, edges, stats = optimize_cvar_route(biased, [], origin, dest, vehicle,
                                                      PROVENANCE, rain_level=rain, traffic_level=traffic)
            method = 'CVaR over feasible search candidates'
        else:
            nodes, edges, stats = route_risk_aware(biased, origin, dest, vehicle, PROVENANCE, rain, traffic)
            method = 'Resource-constrained search with memory'
    except RoutingSearchLimit as error:
        raise HTTPException(503, str(error)) from error
    if nodes is None:
        raise HTTPException(404, f"No route satisfies the modeled constraints under '{request.unknown_data_policy}' policy.")
    coords = _extract_route_coords_from_edges(graph, nodes, edges)
    if len(coords) < 2:
        raise HTTPException(500, 'Route geometry could not be extracted.')
    risk = compute_catastrophic_cvar(edges, vehicle, PROVENANCE, rain, traffic, seed=0)
    metrics = MetricsEngine(vehicle).evaluate_route(graph, nodes, edges, stats['travel_time_s'],
                                                   b0_stats.get('travel_time_s', 0.),
                                                   rain_level=rain, traffic_level=traffic, seed=0)
    warnings = [
        'Research estimates: survival and tail loss are from an uncalibrated engineering model.',
        f"Road constraint evidence: {graph.graph.get('constraint_basis', 'unvalidated')}.",
    ]
    if metrics['ConstraintCoverage_%'] < 100:
        warnings.append('Some physical limits are unknown. Feasibility depends on the selected category priors.')
    return {
        'selected_route': {
            'geometry': {'type': 'LineString', 'coordinates': coords},
            'eta_nominal_min': round(stats['travel_time_s']/60., 2),
            'eta_p50_min': round(risk['eta_p50_sec']/60., 2),
            'eta_p90_min': round(risk['eta_p90_sec']/60., 2),
            'completion_probability': round(stats['completion_probability'], 6),
            'probability_basis': stats['probability_basis'],
            'cvar_risk': round(risk['cvar_90_sec'], 2),
            'cvar_unit': 'seconds_of_generalized_loss',
            'distance_km': round(stats['distance_m']/1000., 3),
            'avg_speed_kmh': round(stats['avg_speed_kmh'], 2),
            'rain_level': rain, 'traffic_level': traffic,
            'ensemble_winner': method, 'academic_metrics': metrics,
            'search': stats['search'], 'simulation': risk,
        },
        'baseline_route': {
            'geometry': {'type': 'LineString', 'coordinates': _extract_route_coords_from_edges(graph, b0_nodes, b0_edges)},
            'eta_p50_min': round(b0_stats['travel_time_s']/60., 2),
        } if b0_nodes else None,
        'warnings': warnings,
    }

def _arrival_router(graph):
    global ARRIVAL_ROUTER
    if ARRIVAL_ROUTER is None or ARRIVAL_ROUTER.graph is not graph:
        ARRIVAL_ROUTER = ArrivalRouter(graph)
    return ARRIVAL_ROUTER


def _blocked_edges(graph):
    return frozenset((u, v, k) for u, v, k in graph.edges(keys=True)
                     if BRAIN.working.get_live_penalty(f'{u}_{v}_{k}') >= 1.)


install_traffic_api(app, _arrival_router, lambda: MASTER_GRAPH,
                    lambda lat, lon: _snap(MASTER_GRAPH, KD_TREE, NODE_IDS, lat, lon),
                    _extract_route_coords_from_edges, _blocked_edges,
                    lambda edge, ttl: BRAIN.working.report_live_hazard('_'.join(map(str, edge)), 1., ttl))

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8000)
