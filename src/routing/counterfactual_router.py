"""
RoadFit-X: Counterfactual Router (Refactored)
-----------------------------------------------
Provides inspectable explanations for why a route succeeded or failed,
including Poisson survival probability breakdown and CVaR tail-risk context.
"""
from typing import Dict, Any, List


def generate_route_explanation(
    route_stats: Dict[str, Any],
    path: List[int],
) -> List[str]:
    """
    Analyzes the stats and path to produce human-readable counterfactuals.
    """
    explanations = []

    # 1. Physical limits — tight lateral clearance
    min_clearance = route_stats.get('min_clearance_m')
    min_clearance = min_clearance if min_clearance is not None else float('inf')
    if min_clearance < 0.2:
        explanations.append(
            f"Route selected despite tight lateral clearance "
            f"({min_clearance:.2f}m in the selected model)."
        )
    elif min_clearance < 0.5:
        explanations.append(
            f"Minimum lateral clearance on route is {min_clearance:.2f}m. "
            f"Reduce speed through narrow segments."
        )

    # 2. High epistemic uncertainty
    uncertainty = route_stats.get('uncertainty_penalty', 0)
    distance_m = route_stats.get('distance_m', 1)
    if distance_m > 0:
        uncertainty_fraction = uncertainty / distance_m
        if uncertainty_fraction > 0.5:
            explanations.append(
                f"High uncertainty on this route: "
                f"mean modeled uncertainty is {uncertainty_fraction:.2f}. "
                f"This score is not a measured fraction of missing road widths."
            )

    # 3. Completion probability (Poisson survival)
    prob = route_stats.get('completion_probability', 1.0)
    if prob < 0.70:
        explanations.append(
            f"Route survival probability is {prob*100:.1f}% "
            f"(spatial Poisson model). High cumulative risk from "
            f"{route_stats.get('edge_count', 0)} road segments. "
            f"Consider a wider alternative."
        )
    elif prob < 0.90:
        explanations.append(
            f"Route has a {(1-prob)*100:.1f}% integrated failure risk "
            f"based on vehicle-road compatibility across "
            f"{route_stats.get('edge_count', 0)} segments."
        )

    # 4. CVaR tail-risk context
    cvar = route_stats.get('cvar_risk', 0)
    expected_time = route_stats.get('travel_time_s', 0)
    if cvar > 0 and expected_time > 0:
        cvar_ratio = cvar / expected_time
        if cvar_ratio > 3.0:
            explanations.append(
                f"CVaR tail risk ({cvar/60:.0f} min) is "
                f"{cvar_ratio:.1f}× the expected travel time. "
                f"This indicates significant catastrophic failure risk "
                f"in worst-case scenarios."
            )
        elif cvar_ratio > 1.5:
            explanations.append(
                f"Worst-case (CVaR-90) travel time is "
                f"{cvar/60:.0f} min vs expected {expected_time/60:.0f} min. "
                f"Tail risk is elevated."
            )

    # 5. Sample failure rate (if CVaR was computed)
    failure_rate = route_stats.get('sample_failure_rate', 0)
    if failure_rate > 0.05:
        explanations.append(
            f"In Monte Carlo simulations, {failure_rate*100:.1f}% of scenarios "
            f"resulted in vehicle entrapment. Consider alternative route."
        )

    return explanations
