"""Per-trip and per-scenario failure diagnostics for routing evaluation.

Provides structured analysis of *why* trips fail — unassigned, unfinished,
excessive wait, or route quality deterioration. This supports the requirement
to diagnose individual failed runs before adding architectural complexity.
"""
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Any


@dataclass
class TripDiagnostic:
    """Structured explanation of a single trip outcome."""
    vehicle_id: str
    kind: str
    origin: str
    destination: str
    departure: float
    assigned: bool
    arrived: bool
    actual_time_s: Optional[float]
    penalized_time_s: float
    depart_delay_s: float
    failure_reason: str  # 'ok', 'unassigned', 'unfinished', 'excessive_wait', 'timeout'
    bottleneck_edges: List[str]  # edges where the trip was delayed
    recommendation: str


def diagnose_trip(trip_row: Dict[str, Any], decisions: Dict[str, Any],
                  horizon: float = 3600.) -> TripDiagnostic:
    """Analyze a single trip and explain its outcome.

    Parameters
    ----------
    trip_row : dict
        Row from the trips DataFrame with columns: vehicle, kind, origin,
        destination, scheduled_departure, assigned, arrived, actual_time_s,
        penalized_time_s, depart_delay_s, follows.
    decisions : dict
        Per-vehicle decision records from the routing policy.
    horizon : float
        Simulation horizon in seconds.

    Returns
    -------
    TripDiagnostic with structured failure explanation.
    """
    vid = trip_row['vehicle']
    decision = decisions.get(vid, {})
    assigned = bool(trip_row.get('assigned', False))
    arrived = bool(trip_row.get('arrived', False))
    actual = trip_row.get('actual_time_s')
    penalized = trip_row.get('penalized_time_s', horizon)
    delay = float(trip_row.get('depart_delay_s', 0.) or 0.)
    bottlenecks = []

    if not assigned:
        reason = 'unassigned'
        rec = ('No feasible route was found. Check whether the locality cutoff '
               'or vehicle-class restrictions eliminated all candidates.')
    elif not arrived:
        reason = 'unfinished'
        route = decision.get('selected_edges', [])
        rec = (f'Vehicle was assigned route [{" → ".join(route[:5])}...] '
               f'but did not finish by horizon. Check link storage/congestion '
               f'along the route for gridlock.')
        bottlenecks = route[-3:] if route else []
    elif delay > 120:
        reason = 'excessive_wait'
        rec = (f'Insertion delay was {delay:.0f}s. The departure edge was '
               f'congested; consider staggered departures or alternate entry.')
    elif actual is not None and actual > horizon * 0.8:
        reason = 'timeout'
        rec = ('Trip completed but consumed >80% of horizon. This is likely '
               'a very long or congested route.')
    else:
        reason = 'ok'
        rec = 'Trip completed within expected parameters.'

    return TripDiagnostic(
        vehicle_id=vid,
        kind=trip_row.get('kind', ''),
        origin=str(trip_row.get('origin', decision.get('origin', ''))),
        destination=str(trip_row.get('destination', decision.get('destination', ''))),
        departure=float(trip_row.get('scheduled_departure', 0.)),
        assigned=assigned,
        arrived=arrived,
        actual_time_s=actual,
        penalized_time_s=penalized,
        depart_delay_s=delay,
        failure_reason=reason,
        bottleneck_edges=bottlenecks,
        recommendation=rec,
    )


def diagnose_scenario(trips_df, decisions: Dict, horizon: float = 3600.) -> Dict[str, Any]:
    """Produce a structured scenario-level failure analysis.

    Returns
    -------
    dict with:
      - 'summary': counts by failure reason
      - 'worst_trips': the 10 highest-penalized trips with explanations
      - 'bottleneck_edges': edges appearing most in failed routes
      - 'class_breakdown': failure rates by vehicle class
      - 'temporal_pattern': failure rates by departure quartile
    """
    diagnostics = []
    for _, row in trips_df.iterrows():
        d = diagnose_trip(row.to_dict(), decisions, horizon)
        diagnostics.append(d)

    # Summary counts
    reason_counts = Counter(d.failure_reason for d in diagnostics)

    # Worst trips
    worst = sorted(diagnostics, key=lambda d: d.penalized_time_s, reverse=True)[:10]

    # Bottleneck edges
    bottleneck_counter = Counter()
    for d in diagnostics:
        if d.failure_reason in ('unfinished', 'timeout'):
            bottleneck_counter.update(d.bottleneck_edges)

    # Class breakdown
    class_breakdown = defaultdict(lambda: {'total': 0, 'failed': 0})
    for d in diagnostics:
        class_breakdown[d.kind]['total'] += 1
        if d.failure_reason != 'ok':
            class_breakdown[d.kind]['failed'] += 1

    # Temporal pattern
    departures = [d.departure for d in diagnostics]
    if departures:
        quartile_size = (max(departures) - min(departures)) / 4 if max(departures) > min(departures) else 1.
        temporal = defaultdict(lambda: {'total': 0, 'failed': 0})
        for d in diagnostics:
            q = min(3, int((d.departure - min(departures)) / quartile_size))
            temporal[f'Q{q+1}']['total'] += 1
            if d.failure_reason != 'ok':
                temporal[f'Q{q+1}']['failed'] += 1
    else:
        temporal = {}

    return {
        'summary': dict(reason_counts),
        'total_trips': len(diagnostics),
        'completion_rate_pct': reason_counts.get('ok', 0) / max(1, len(diagnostics)) * 100,
        'worst_trips': [
            {'vehicle': d.vehicle_id, 'kind': d.kind, 'reason': d.failure_reason,
             'penalized_s': d.penalized_time_s, 'recommendation': d.recommendation}
            for d in worst
        ],
        'bottleneck_edges': bottleneck_counter.most_common(10),
        'class_breakdown': {k: {'total': v['total'], 'failed': v['failed'],
                                'failure_rate_pct': v['failed']/max(1, v['total'])*100}
                            for k, v in class_breakdown.items()},
        'temporal_pattern': dict(temporal),
    }
