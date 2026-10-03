"""Time and hard-constraint baselines that retain the selected multigraph key."""
import networkx as nx
from src.routing.risk_aware_router import _physics_travel_time, _compute_path_stats
from src.vehicle.geometry_constraints import compute_geometry_margins

def prepare_baseline(G, vehicle, hard_constraints=False,
                     rain_level='none', traffic_level='normal', weight='time'):
    collapsed = nx.DiGraph()
    collapsed.add_nodes_from(G)
    for u, v, key, data in G.edges(keys=True, data=True):
        if hard_constraints:
            margins = compute_geometry_margins(data, vehicle)
            if any(value is None or value < 0 for value in margins.values()):
                continue
        duration = _physics_travel_time(data, rain_level, traffic_level)
        cost = float(data.get('length', 50.)) if weight == 'length' else duration
        if not collapsed.has_edge(u, v) or cost < collapsed[u][v]['cost']:
            collapsed.add_edge(u, v, cost=cost, key=key)
    return collapsed

def route_baseline(G, orig_node, dest_node, vehicle, hard_constraints=False,
                   rain_level='none', traffic_level='normal', weight='time', prepared=None):
    if orig_node not in G or dest_node not in G:
        raise nx.NodeNotFound('origin or destination is absent')
    collapsed = prepared if prepared is not None else prepare_baseline(
        G, vehicle, hard_constraints, rain_level, traffic_level, weight)
    try:
        nodes = nx.shortest_path(collapsed, orig_node, dest_node, weight='cost')
    except nx.NetworkXNoPath:
        return None, None, {}
    edges = [(u, v, collapsed[u][v]['key'], dict(G[u][v][collapsed[u][v]['key']]))
             for u, v in zip(nodes, nodes[1:])]
    return nodes, edges, _compute_path_stats(G, edges, vehicle, rain_level=rain_level, traffic_level=traffic_level)

def route_shortest_distance(G, orig_node, dest_node):
    try:
        return nx.shortest_path(G, orig_node, dest_node, weight='length')
    except nx.NetworkXNoPath:
        return None

def route_shortest_eta(G, orig_node, dest_node):
    def weight(u, v, edges):
        return min(_physics_travel_time(data) for data in edges.values())
    try:
        return nx.shortest_path(G, orig_node, dest_node, weight=weight)
    except nx.NetworkXNoPath:
        return None

def route_hard_constrained_astar(G, orig_node, dest_node, vehicle_profile):
    # Dijkstra (A* with h=0) is admissible for arbitrary supplied edge times.
    return route_baseline(G, orig_node, dest_node, vehicle_profile, hard_constraints=True)[0]
