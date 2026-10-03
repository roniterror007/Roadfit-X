"""Queue prediction with observed-state initialization and downstream spillback.

This module extracts and extends the queue prediction logic from anticipatory.py.
Key improvements over the original ``predicted_queue()``:
  1. Accepts an initial observed backlog instead of assuming zero.
  2. Models downstream storage limits: when a downstream edge is full,
     the upstream edge's effective service rate drops.
  3. Estimates signal-phase delay as an additive component.

The model remains an engineering heuristic. All parameters require calibration
before any deployment or accuracy claim.
"""
import math
from collections import defaultdict


def predicted_queue(edge, slot, timestamp, cap, background, control, entries,
                    forecasts, index, initial_backlog=0., downstream_edge_state=None,
                    signal_delay_s=0.):
    """Fluid backlog from reserved entry bins, observed queue state, and spillback.

    Parameters
    ----------
    edge : tuple
        Directed keyed edge identifier.
    slot : int
        Current time bin.
    timestamp : float
        Current simulation or planning clock.
    cap : float
        Edge capacity in PCU/hour.
    background : float
        Fraction of capacity consumed by unmodeled background traffic.
    control : Control
        Planning control parameters (bin_seconds, etc.).
    entries : dict
        {(edge, slot): pcu_count} reservation ledger.
    forecasts : dict
        {(edge, slot): flow_pcu_h} manual forecasts.
    index : dict
        {edge: {slot: pcu_count}} pre-indexed entries.
    initial_backlog : float
        Observed queue length in PCU at the start of prediction. This replaces
        the previous assumption of zero.
    downstream_edge_state : dict or None
        {'occupancy': float, 'storage': float} for the NEXT edge in the path.
        When the downstream edge's occupancy exceeds its storage, the upstream
        service rate is reduced proportionally.
    signal_delay_s : float
        Estimated average signal delay per vehicle in seconds. Added to the
        predicted queue time. Zero when no signal is present.

    Returns
    -------
    (backlog, available_rate) : (float, float)
        Current estimated backlog in PCU, and available service rate in PCU/s.
    """
    bins = index.get(edge, {})
    start = min(bins, default=slot)
    backlog = max(0., initial_backlog)
    duration = control.bin_seconds

    # Downstream spillback factor: reduce service rate when downstream is full.
    spillback_factor = 1.0
    if downstream_edge_state is not None:
        occupancy = downstream_edge_state.get('occupancy', 0.)
        storage = downstream_edge_state.get('storage', float('inf'))
        if storage > 0 and occupancy >= storage:
            # Fully blocked: reduce to 5% service (junction clearance only).
            spillback_factor = 0.05
        elif storage > 0 and occupancy > 0.8 * storage:
            # Approaching full: linear reduction from 100% at 80% to 5% at 100%.
            fraction = (occupancy - 0.8 * storage) / (0.2 * storage)
            spillback_factor = max(0.05, 1.0 - 0.95 * fraction)

    for b in range(start, slot):
        flow = forecasts.get((edge, b), background * cap)
        base_available = cap * max(.05, 1. - flow / cap) * duration / 3600.
        available = base_available * spillback_factor
        backlog = max(0., backlog + bins.get(b, 0.) - available)

    flow = forecasts.get((edge, slot), background * cap)
    base_rate = cap * max(.05, 1. - flow / cap) / 3600.
    available_rate = base_rate * spillback_factor
    elapsed = max(0., min(duration, timestamp - slot * duration))
    backlog = max(0., backlog + (bins.get(slot, 0.) / duration - available_rate) * elapsed)

    # Add signal delay as queued time equivalent.
    if signal_delay_s > 0 and available_rate > 0:
        backlog += signal_delay_s * available_rate

    return backlog, available_rate


def observe_queue_from_sumo(net, free, edge_map, vehicle_list, pending_list,
                            clock, storage):
    """Extract per-edge observed queue state from a SUMO simulation snapshot.

    Returns a dict mapping keyed edges to their initial backlog in PCU,
    and a downstream_state dict for spillback estimation.
    """
    from src.vehicle.profiles import PROFILES
    try:
        import libsumo as sim
    except ImportError:
        return {}, {}

    occupancy = defaultdict(float)
    for vehicle in vehicle_list:
        edge = sim.vehicle.getRoadID(vehicle)
        if edge in free:
            kind = sim.vehicle.getTypeID(vehicle)
            pcu = PROFILES.get(kind, PROFILES['hatchback']).pcu
            occupancy[edge] += pcu

    # Pending (waiting-to-depart) vehicles also occupy the departure edge.
    for vehicle in pending_list:
        route = sim.vehicle.getRoute(vehicle)
        if route and route[0] in free:
            kind = sim.vehicle.getTypeID(vehicle)
            pcu = PROFILES.get(kind, PROFILES['hatchback']).pcu
            occupancy[route[0]] += pcu

    initial_backlog = {}
    downstream_state = {}
    for edge_name, keyed_edge in edge_map.items():
        store = storage.get(edge_name, float('inf'))
        occ = occupancy.get(edge_name, 0.)
        # Backlog: vehicles exceeding free-flow storage capacity.
        initial_backlog[keyed_edge] = max(0., occ - store * 0.7)
        downstream_state[keyed_edge] = {
            'occupancy': occ,
            'storage': store,
        }

    return initial_backlog, downstream_state


def estimate_signal_delay(net, edge_name, clock):
    """Estimate average signal delay for an edge approaching a signalized junction.

    Returns the estimated per-vehicle delay in seconds, or 0 if uncontrolled.
    This is a coarse Webster-style estimate, not a calibrated signal model.
    """
    try:
        edge = net.getEdge(edge_name)
        to_node = edge.getToNode()
        node_type = to_node.getType()
    except Exception:
        return 0.

    if node_type not in ('traffic_light', 'traffic_light_unregulated',
                         'traffic_light_right_on_red'):
        return 0.

    # Default assumption: 60s cycle, 45% effective green for the approach.
    # Webster's delay formula: d ≈ C(1-g/C)² / 2(1-min(1, x)·g/C)
    # Simplified for uncalibrated use: ~15s average delay.
    cycle_s = 60.
    green_fraction = 0.45
    avg_delay = cycle_s * (1 - green_fraction)**2 / (2 * (1 - 0.9 * green_fraction))
    return max(0., avg_delay)
