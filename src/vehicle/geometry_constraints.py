"""Physical margins in SI units with explicit unknown observations.
Category priors are engineering assumptions, not calibrated bounds.
"""
import math
import re
from typing import Any, Dict
from .vehicle_digital_twin import VehicleDigitalTwin

WIDTH_PRIORS = {
    'motorway': (10., 1.5), 'trunk': (9., 1.5), 'primary': (8., 1.5),
    'secondary': (7., 1.2), 'tertiary': (6., 1.),
    'residential': (3.5, 1.), 'living_street': (3., .8),
    'service': (3., .8), 'unclassified': (3.5, 1.),
}

def highway_category(data):
    value = data.get('highway', 'residential')
    if isinstance(value, (list, tuple)):
        value = value[0] if value else 'residential'
    return str(value).removesuffix('_link')

def _parse_osm_float(value, default: float, quantity: str = 'length') -> float:
    """Parse metric, imperial and multivalue limits; invalid values stay unknown."""
    if isinstance(value, (list, tuple)):
        parsed = [_parse_osm_float(v, math.nan, quantity) for v in value]
        valid = [v for v in parsed if math.isfinite(v)]
        return min(valid) if valid else default
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) and value > 0 else default
    s = str(value).strip().lower()
    if ';' in s:
        return _parse_osm_float(s.split(';'), default, quantity)
    if quantity == 'length':
        feet_inches = re.fullmatch(r"(\d+(?:\.\d+)?)\s*'\s*(\d+(?:\.\d+)?)?\s*\"?", s)
        if feet_inches:
            return float(feet_inches[1]) * .3048 + float(feet_inches[2] or 0) * .0254
    values = re.findall(r'(?<![\w.])[-+]?\d+(?:\.\d+)?', s)
    if not values:
        return default
    numbers = [float(v) for v in values]
    if any(v <= 0 for v in numbers):
        return default
    result = min(numbers)
    if quantity == 'length':
        if 'ft' in s or 'feet' in s:
            result *= .3048
        elif 'cm' in s:
            result *= .01
        elif 'mm' in s:
            result *= .001
    elif quantity == 'weight':
        if 'kg' in s:
            result /= 1000.
        elif 'lb' in s:
            result *= .00045359237
    return result

def compute_geometry_margins(edge_data: Dict[str, Any], vehicle: VehicleDigitalTwin):
    policy = vehicle.unknown_data_policy
    highway = highway_category(edge_data)
    mean, std = WIDTH_PRIORS.get(highway, (4., 1.))
    mean = _parse_osm_float(edge_data.get('_width_mean'), mean)
    try:
        std = float(edge_data.get('_width_std', std))
        if not math.isfinite(std) or std < 0:
            std = 1.
    except (ValueError, TypeError):
        std = 1.

    def limit(tag, fallback, quantity='length'):
        value = _parse_osm_float(edge_data.get(tag), math.nan, quantity)
        inferred = edge_data.get(f'{tag}_source') == 'inferred'
        if math.isfinite(value) and not inferred:
            return value
        if policy == 'strict':
            return None
        return fallback[0] if policy == 'conservative' else fallback[1]

    width = limit('width', (max(.1, mean - 1.645 * std), mean))
    height = limit('maxheight', (3.5, 4.5))
    weight = _parse_osm_float(edge_data.get('maxweight', edge_data.get('max_weight')), math.nan, 'weight')
    if not math.isfinite(weight) or edge_data.get('maxweight_source') == 'inferred':
        nominal = 20. if highway in ('motorway', 'trunk', 'primary') else 10.
        weight = None if policy == 'strict' else nominal * (.7 if policy == 'conservative' else 1.)
    margins = {
        'width_clearance_m': None if width is None else width - vehicle.width_m - vehicle.width_buffer_m,
        'height_clearance_m': None if height is None else height - vehicle.height_m - vehicle.height_buffer_m,
        'weight_margin_t': None if weight is None else weight - vehicle.gross_weight_t,
    }
    # Category radius is a proxy. Actual junction turn geometry is not modeled.
    radius = {'motorway': 50., 'trunk': 40., 'primary': 30., 'secondary': 20.,
              'tertiary': 15., 'residential': 10., 'service': 8.}.get(highway, 12.)
    radius = _parse_osm_float(edge_data.get('turning_radius'), radius)
    margins['turning_radius_margin_m'] = radius - vehicle.turning_radius_m
    if edge_data.get('maxaxleload') is not None:
        axle = _parse_osm_float(edge_data['maxaxleload'], math.nan, 'weight')
        margins['axle_margin_t'] = axle - vehicle.axle_load_t if math.isfinite(axle) else None
    if edge_data.get('grade_pct') is not None:
        try:
            grade = float(edge_data['grade_pct'])
            margins['grade_margin_pct'] = vehicle.max_grade_pct - abs(grade) if math.isfinite(grade) else None
        except (ValueError, TypeError):
            margins['grade_margin_pct'] = None
    return margins
