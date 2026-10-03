"""Compatibility CLI for the hard-constraint time baseline."""
import argparse
import math
import osmnx as ox
from src.evaluation.baseline_routes import route_baseline
from src.vehicle.geometry_constraints import _parse_osm_float
from src.routing.risk_aware_router import _physics_travel_time

def get_edge_data(G, u, v, vehicle_width=2., vehicle_height=2., vehicle_weight=2.):
    edges = G.get_edge_data(u, v)
    if not edges:
        return {}
    candidates = edges.values() if G.is_multigraph() else [edges]
    viable = [d for d in candidates if (
        _parse_osm_float(d.get('width'), 6.5) >= vehicle_width and
        _parse_osm_float(d.get('maxheight'), 4.5) >= vehicle_height and
        _parse_osm_float(d.get('maxweight'), 10., 'weight') >= vehicle_weight)]
    return min(viable, key=_physics_travel_time) if viable else {}

def custom_weight_function(u, v, d, vehicle_width=2., vehicle_height=2., alpha=.5, beta=.5):
    # NetworkX passes a key->attributes dictionary on MultiDiGraph callbacks.
    del u, v, alpha, beta
    candidates = list(d.values()) if d and all(isinstance(value, dict) for value in d.values()) else [d]
    feasible = [data for data in candidates
                if _parse_osm_float(data.get('width'), 6.5) >= vehicle_width
                and _parse_osm_float(data.get('maxheight'), 4.5) >= vehicle_height]
    return min((_physics_travel_time(data) for data in feasible), default=math.inf)

def run_astar_routing(orig_node, dest_node, G=None, graph_path=None,
                      vehicle_width=2., vehicle_height=2., vehicle_weight=2.,
                      max_network_speed_mps=33.33):
    from src.evaluation.ablations import benchmark_vehicle
    del max_network_speed_mps
    if G is None:
        if graph_path is None:
            raise ValueError('provide a graph or graph_path')
        G = ox.load_graphml(graph_path)
    vehicle = benchmark_vehicle('exploratory')
    vehicle.width_m, vehicle.height_m, vehicle.gross_weight_t = vehicle_width, vehicle_height, vehicle_weight
    vehicle.axle_load_t = vehicle_weight/2.
    return route_baseline(G, orig_node, dest_node, vehicle, hard_constraints=True)[0]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', required=True)
    parser.add_argument('--orig', type=int, required=True)
    parser.add_argument('--dest', type=int, required=True)
    args = parser.parse_args()
    print(run_astar_routing(args.orig, args.dest, graph_path=args.graph))
