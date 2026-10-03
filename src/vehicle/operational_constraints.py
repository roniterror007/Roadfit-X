"""
RoadFit-X: Operational Constraints
Evaluates the operational feasibility of a road edge based on vehicle capabilities.
Includes physics-based speed estimation, car density, rain factor, and traffic models.
"""
from typing import Dict, Any
from .vehicle_digital_twin import VehicleDigitalTwin


# Physics-based free-flow speeds by highway type (km/h)
HIGHWAY_FREEFLOW_SPEED = {
    'motorway': 90.0,
    'motorway_link': 60.0,
    'trunk': 70.0,
    'trunk_link': 50.0,
    'primary': 50.0,
    'primary_link': 40.0,
    'secondary': 40.0,
    'secondary_link': 30.0,
    'tertiary': 30.0,
    'tertiary_link': 25.0,
    'residential': 20.0,
    'living_street': 10.0,
    'service': 15.0,
    'unclassified': 25.0,
    'track': 10.0,
}

# Lane-based car density estimates (vehicles per km per lane during peak)
LANE_DENSITY = {
    'motorway': 45.0,
    'trunk': 35.0,
    'primary': 30.0,
    'secondary': 20.0,
    'tertiary': 15.0,
    'residential': 8.0,
    'service': 4.0,
    'unclassified': 10.0,
}

# Rain speed reduction multipliers
RAIN_SPEED_FACTOR = {
    'none': 1.0,
    'light': 0.88,
    'moderate': 0.75,
    'heavy': 0.55,
    'extreme': 0.35,
}


def get_physics_speed(edge_data: Dict[str, Any], rain_level: str = 'none', traffic_level: str = 'normal') -> float:
    """
    Estimates physics-based travel speed (km/h) using:
    - Highway type free-flow speed
    - Posted speed limit (if available)
    - Rain reduction factor
    - Congestion: BPR (Bureau of Public Roads) function
    """
    highway = str(edge_data.get('highway', 'residential'))
    freeflow = HIGHWAY_FREEFLOW_SPEED.get(highway, 25.0)

    # Override with posted speed limit if available
    maxspeed = edge_data.get('maxspeed')
    if maxspeed is not None:
        try:
            import re
            nums = re.findall(r'\d+\.?\d*', str(maxspeed))
            if nums:
                posted = float(nums[0])
                # If the tag is in mph, convert
                if 'mph' in str(maxspeed).lower():
                    posted *= 1.60934
                freeflow = min(posted, freeflow * 1.2)
        except Exception:
            pass

    # BPR congestion model: v = v0 / (1 + 0.15 * (vol/cap)^4)
    CONGESTION_MULTIPLIERS = {'low': 0.7, 'normal': 1.0, 'heavy': 1.6, 'gridlock': 2.8}
    traffic_mult = CONGESTION_MULTIPLIERS.get(traffic_level, 1.0)
    base_congestion = float(edge_data.get('congestion_factor', 1.0))
    total_congestion = base_congestion * traffic_mult

    if total_congestion > 1.0:
        bpr_speed = freeflow / (1.0 + 0.15 * (total_congestion ** 4))
    else:
        bpr_speed = freeflow

    # Apply rain factor
    rain_mult = RAIN_SPEED_FACTOR.get(rain_level, 1.0)
    return max(5.0, bpr_speed * rain_mult)  # Never go below 5 km/h


def get_car_density(edge_data: Dict[str, Any], traffic_level: str = 'normal') -> float:
    """Estimates cars per km on this edge segment."""
    highway = str(edge_data.get('highway', 'residential'))
    base_density = LANE_DENSITY.get(highway, 10.0)
    lanes = float(edge_data.get('lanes', 1))
    
    CONGESTION_MULTIPLIERS = {'low': 0.7, 'normal': 1.0, 'heavy': 1.6, 'gridlock': 2.8}
    traffic_mult = CONGESTION_MULTIPLIERS.get(traffic_level, 1.0)
    base_congestion = float(edge_data.get('congestion_factor', 1.0))
    total_congestion = base_congestion * traffic_mult
    
    return base_density * lanes * min(total_congestion, 3.0)


def evaluate_operational_risks(
    edge_data: Dict[str, Any],
    vehicle: VehicleDigitalTwin,
    rain_level: str = 'none',
    traffic_level: str = 'normal'
) -> Dict[str, float]:
    """
    Evaluates operational risk factors: slope, surface, rain, traffic density.
    Returns normalized risk scores 0.0 (safe) to 1.0 (failure).
    """
    risks = {}

    # 1. Grade/Slope risk
    road_grade = float(edge_data.get('grade_pct', 2.0))  # Default 2% urban grade
    if road_grade > vehicle.max_grade_pct:
        risks['grade_risk'] = 1.0
    else:
        risks['grade_risk'] = min(0.3, road_grade / (vehicle.max_grade_pct + 0.01))

    # 2. Surface tolerance — OSM roads without surface tag are ASSUMED paved
    surface = str(edge_data.get('surface', 'asphalt')).lower()
    paved_surfaces = {'asphalt', 'concrete', 'paved', 'paving_stones', 'sett',
                      'compacted', 'fine_gravel', 'tar'}
    if surface in vehicle.surface_tolerance or surface in paved_surfaces:
        risks['surface_risk'] = 0.05  # minimal risk on known good surface
    else:
        # Unpaved: small penalty, not catastrophic
        risks['surface_risk'] = 0.25

    # 3. Rain risk
    rain_risk_map = {'none': 0.0, 'light': 0.05, 'moderate': 0.15, 'heavy': 0.30, 'extreme': 0.50}
    rain_risk = rain_risk_map.get(rain_level, 0.0)
    # Higher for vehicles with low rain tolerance
    if vehicle.rain_tolerance == 'low':
        rain_risk *= 1.5
    elif vehicle.rain_tolerance == 'high':
        rain_risk *= 0.5
    risks['rain_risk'] = min(rain_risk, 0.6)

    # 4. Traffic density risk
    cars_per_km = get_car_density(edge_data, traffic_level)
    traffic_risk = min(0.3, cars_per_km / 200.0)  # Max 30% risk from traffic
    risks['traffic_risk'] = traffic_risk

    return risks
