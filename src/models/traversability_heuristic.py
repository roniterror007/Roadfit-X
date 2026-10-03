"""
RoadFit-X: Rule-Based Traversability Estimator
------------------------------------------------
Predicts per-edge traversability (p_e) and epistemic uncertainty (u_e).

NOTE: Despite the filename (retained for import compatibility), this module
contains NO machine-learning or Graph Neural Network logic. It is a
hand-crafted heuristic engine using logistic clearance functions and
hazard-rate estimation.

KEY DESIGN: Spatial Poisson Hazard Model
  The estimator computes a *hazard rate per meter* (lambda_e) from road
  geometry and operational risks, then derives the edge survival probability as:
      p_e = exp(-lambda_e * L_e)
  This ensures that the product of per-edge probabilities across a route is
  invariant to OSM graph meshing (i.e., splitting a 1 km edge into 20 x 50 m
  edges produces the same route survival probability).
"""
import math
import numpy as np
from typing import Dict, Any, Tuple
from src.vehicle.vehicle_digital_twin import VehicleDigitalTwin
from src.vehicle.geometry_constraints import compute_geometry_margins
from src.vehicle.operational_constraints import evaluate_operational_risks
from src.data.provenance_store import ProvenanceStore


def predict_traversability(
    edge_data: Dict[str, Any],
    vehicle: VehicleDigitalTwin,
    provenance: ProvenanceStore = None,
    edge_id: tuple = None,
    rain_level: str = 'none',
    traffic_level: str = 'normal',
    include_geometry: bool = True,
    include_uncertainty: bool = True,
) -> Tuple[float, float]:
    """
    Returns (p_e, u_e):
      p_e: probability this edge is physically traversable by vehicle [0..1].
           Computed via spatial Poisson hazard model: p_e = exp(-λ · L).
      u_e: epistemic uncertainty about this edge [0..1].
    """
    try:
        margins = compute_geometry_margins(edge_data, vehicle)
    except (TypeError, ValueError):
        return 0.0, 1.0

    # ── 1. Hard physical blocks (length-independent, binary) ──
    if include_geometry and any(v is None or not math.isfinite(v) or v < 0 for v in margins.values()):
        return 0.0, 1.0

    # ── 2. Compute spatial hazard rate (failures per meter) ──
    length_m = max(float(edge_data.get('length', 50.0)), 0.0)

    # Width squeeze hazard: inverse-square of lateral clearance
    # Tight clearance (0.1m) → 1e-3/m ≈ catastrophic over 1km
    # Comfortable (2.0m) → 2.5e-6/m ≈ negligible
    w_val = margins.get('width_clearance_m')
    w_val = w_val if w_val is not None else 1.0
    w_clearance = max(w_val, 0.01)
    width_hazard_per_m = 1e-5 / (w_clearance ** 2)

    # Height squeeze hazard
    h_val = margins.get('height_clearance_m')
    h_val = h_val if h_val is not None else 1.0
    h_clearance = max(h_val, 0.01)
    height_hazard_per_m = 1e-6 / (h_clearance ** 2)
    if not include_geometry:
        width_hazard_per_m = height_hazard_per_m = 0.0

    # ── 3. Operational hazard rate ──
    try:
        risks = evaluate_operational_risks(edge_data, vehicle, rain_level, traffic_level)
        max_op_risk = max(risks.values()) if risks else 0.0
        avg_op_risk = sum(risks.values()) / max(len(risks), 1)
        op_hazard_per_m = (avg_op_risk * 0.6 + max_op_risk * 0.4) * 1e-4
    except Exception:
        op_hazard_per_m = 1e-5

    # ── 4. Aggregate hazard and compute length-aware survival ──
    total_hazard_per_m = width_hazard_per_m + height_hazard_per_m + op_hazard_per_m
    p_e = math.exp(-total_hazard_per_m * length_m)

    # ── 5. Epistemic uncertainty — based on OSM tag completeness ──
    from src.vehicle.geometry_constraints import _parse_osm_float
    missing_count = sum(
        not math.isfinite(_parse_osm_float(edge_data.get(tag), math.nan,
                         'weight' if tag == 'maxweight' else 'length'))
        or edge_data.get(f'{tag}_source') == 'inferred'
        for tag in ('width', 'maxheight', 'maxweight')
    )
    u_e = 0.1 + 0.3 * missing_count

    # Override with provenance if available
    if provenance and edge_id:
        try:
            width_ev = provenance.get_evidence(edge_id, 'width')
            if width_ev:
                u_e = 1.0 - width_ev.evidence_quality
        except Exception:
            pass

    # ── 6. Uncertainty-adjusted survival ──
    # A large vehicle on an unknown road is riskier than a bike on an unknown road.
    # CRITICAL: This must be modeled as a per-meter hazard rate (not a flat
    # multiplier) to preserve discretization invariance under Poisson composition.
    footprint_factor = (vehicle.width_m * vehicle.height_m) / 10.0
    uncertainty_hazard_per_m = u_e * min(footprint_factor, 0.8) * 1e-4
    if not include_uncertainty:
        uncertainty_hazard_per_m = 0.0
    total_hazard_per_m += uncertainty_hazard_per_m

    # Recompute p_e with the full hazard including uncertainty
    p_e = math.exp(-total_hazard_per_m * length_m)

    # Search, CVaR, and route statistics share the same memory adjustment.
    history = float(edge_data.get('_history_penalty', 0.0))
    if not math.isfinite(history):
        return 0.0, 1.0
    history = min(max(history, 0.0), 1.0)
    p_e *= 1.0 - history
    u_e = max(u_e, history)

    return float(np.clip(p_e, 0.0, 1.0)), float(np.clip(u_e, 0.0, 1.0))
