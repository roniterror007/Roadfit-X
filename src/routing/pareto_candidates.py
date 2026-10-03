"""
RoadFit-X: Pareto Route Candidate Generator (Refactored Phase 2)
-----------------------------------------------------------------
Retrieves up to K modeled feasible candidate paths.
Instead of flattening the MultiDiGraph and using Yen's algorithm, this now
directly leverages the multi-label constrained A* router which natively
returns a Pareto frontier of non-dominated paths.
"""
import networkx as nx
import osmnx as ox
import argparse
import os
import sys
import time

from src.vehicle.vehicle_digital_twin import VehicleDigitalTwin
from src.routing.risk_aware_router import route_risk_aware_multilabel
from src.data.provenance_store import ProvenanceStore


def get_k_shortest_paths(
    G: nx.MultiDiGraph,
    orig_node: int,
    dest_node: int,
    vehicle: VehicleDigitalTwin,
    provenance: ProvenanceStore = None,
    k: int = 3,
    rain_level: str = 'none',
    traffic_level: str = 'normal',
) -> list:
    """
    Find up to K nondominated search candidates; the cap may truncate the frontier.
    Returns a list of path_node lists to maintain the same API signature
    as the old Yen's implementation for downstream CVaR optimizer.
    """
    print(f"Finding {k} Pareto-optimal paths from {orig_node} to {dest_node} "
          f"using Multi-Label Search for vehicle {vehicle.vehicle_type}...")

    start_time = time.time()
    
    candidates = route_risk_aware_multilabel(
        G, orig_node, dest_node, vehicle, provenance, 
        rain_level, traffic_level, max_candidates_to_find=k
    )
    
    end_time = time.time()
    print(f"Found {len(candidates)} candidate paths in "
          f"{end_time - start_time:.4f} seconds.")
          
    # Return just the node lists for compatibility with the old interface
    return [c[0] for c in candidates]


# ────────────────────────────────────────────────────
#  CLI Entry Point
# ────────────────────────────────────────────────────
def _parse_args():
    parser = argparse.ArgumentParser(
        description="RoadFit-X Pareto Route Candidate Generator"
    )
    parser.add_argument("--graph", type=str, required=True,
                        help="Path to GraphML file")
    parser.add_argument("--orig-lat", type=float, required=True,
                        help="Origin Latitude")
    parser.add_argument("--orig-lon", type=float, required=True,
                        help="Origin Longitude")
    parser.add_argument("--dest-lat", type=float, required=True,
                        help="Destination Latitude")
    parser.add_argument("--dest-lon", type=float, required=True,
                        help="Destination Longitude")
    parser.add_argument("--width", type=float, default=2.4,
                        help="Vehicle width in meters")
    parser.add_argument("--height", type=float, default=2.6,
                        help="Vehicle height in meters")
    parser.add_argument("--weight", type=float, default=3.5,
                        help="Vehicle weight in tonnes")
    parser.add_argument("--k", type=int, default=3,
                        help="Number of Pareto candidates")
    parser.add_argument("--policy", type=str, default="conservative",
                        choices=["strict", "conservative", "exploratory"],
                        help="Missing data policy")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if not os.path.exists(args.graph):
        print(f"Error: Graph file '{args.graph}' not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading GraphML from {args.graph}...")
    G = ox.load_graphml(args.graph)

    vehicle = VehicleDigitalTwin(
        vehicle_type="cli_vehicle",
        width_m=args.width,
        height_m=args.height,
        gross_weight_t=args.weight,
        axle_load_t=args.weight / 2.0,
        wheelbase_m=3.0,
        turning_radius_m=6.0,
        ground_clearance_m=0.2,
        max_grade_pct=15.0,
        surface_tolerance=["asphalt", "concrete", "paved", "compacted"],
        rain_tolerance="medium",
        cargo_class="standard",
        risk_preference="moderate",
        unknown_data_policy=args.policy
    )

    source_node = ox.distance.nearest_nodes(G, X=args.orig_lon, Y=args.orig_lat)
    target_node = ox.distance.nearest_nodes(G, X=args.dest_lon, Y=args.dest_lat)

    print(f"Routing from node {source_node} to {target_node} "
          f"for vehicle: {vehicle} (Policy: {args.policy})")

    paths = get_k_shortest_paths(G, source_node, target_node, vehicle, k=args.k)

    if not paths:
        print("No feasible route found satisfying physical constraints.")
        sys.exit(2)

    for i, path in enumerate(paths):
        print(f"\n--- Candidate {i+1} ---")
        print(f"  Nodes: {len(path)}")
