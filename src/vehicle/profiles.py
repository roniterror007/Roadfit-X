"""Explicit vehicle classes and OSM access checks; defaults are research assumptions."""
from dataclasses import dataclass
import math

from src.vehicle.vehicle_digital_twin import VehicleDigitalTwin
from src.vehicle.geometry_constraints import highway_category


@dataclass(frozen=True)
class Profile:
    width: float
    height: float
    weight: float
    radius: float
    speed: float
    pcu: float


PROFILES = {
    'bicycle': Profile(.65, 1.7, .1, 2., 18., .25),
    'motorcycle': Profile(.9, 1.4, .2, 3., 50., .5),
    'hatchback': Profile(1.8, 1.6, 1.2, 5., 80., 1.),
    'suv': Profile(2.1, 2., 2.5, 6., 80., 1.2),
    'van': Profile(2.4, 2.8, 5., 7., 65., 1.5),
    'truck': Profile(2.5, 3.2, 12., 9., 60., 2.5),
}


def make_vehicle(kind, policy='conservative', width=None, height=None, weight=None):
    p = PROFILES[kind]
    return VehicleDigitalTwin(
        vehicle_type=kind, width_m=p.width if width is None else width,
        height_m=p.height if height is None else height,
        gross_weight_t=p.weight if weight is None else weight,
        axle_load_t=(p.weight if weight is None else weight) / (3 if kind == 'truck' else 2),
        wheelbase_m=5. if kind == 'truck' else 3., turning_radius_m=p.radius,
        ground_clearance_m=.2, max_grade_pct=15., surface_tolerance=['asphalt', 'concrete', 'paved', 'compacted'],
        rain_tolerance='medium', risk_preference='moderate', unknown_data_policy=policy)


def tag_values(value):
    if isinstance(value, (list, tuple)):
        return {str(v).lower() for v in value}
    return {v.strip().lower() for v in str(value).split(';')}


def legal_access(data, kind, terminal=False):
    """Specific-mode tags override generic access; any merged restriction is conservative.

    Destination/delivery access is permitted only in the terminal neighbourhood.
    Conditional restrictions and turn-restriction relations are not implemented.
    """
    mode_tags = {
        'bicycle': ['bicycle', 'vehicle', 'access'],
        'motorcycle': ['motorcycle', 'motor_vehicle', 'vehicle', 'access'],
        'truck': ['hgv', 'motor_vehicle', 'vehicle', 'access'],
    }.get(kind, ['motorcar', 'motor_vehicle', 'vehicle', 'access'])
    specific = None
    selected_tag = None
    for key in mode_tags:
        if data.get(key) is not None:
            specific = tag_values(data[key])
            selected_tag = key
            break
    if specific:
        if specific & {'no', 'private', 'agricultural', 'forestry', 'customers'}:
            return False
        if specific & {'destination', 'delivery'} and not terminal:
            return False
    highway = highway_category(data)
    mode_specific = selected_tag in {'bicycle', 'motorcycle', 'hgv', 'motorcar', 'motor_vehicle'}
    explicit = bool(mode_specific and specific and specific <= {'yes', 'permissive', 'designated', 'official'})
    if kind == 'bicycle' and highway in {'motorway', 'trunk'}:
        return explicit and 'bicycle' in data
    if highway in {'footway', 'pedestrian', 'steps', 'path', 'cycleway'}:
        if highway == 'steps':
            return False
        return (kind == 'bicycle' and highway == 'cycleway') or explicit
    return True


def terminal_edge(graph, u, v, origin, destination, radius_m=350.):
    if u in (origin, destination) or v in (origin, destination):
        return True
    for node in (u, v):
        a = graph.nodes[node]
        for terminal in (origin, destination):
            b = graph.nodes[terminal]
            if all(k in a and k in b for k in ('x', 'y')):
                dx = (float(a['x'])-float(b['x']))*111320*math.cos(math.radians(float(b['y'])))
                dy = (float(a['y'])-float(b['y']))*111320
                if math.hypot(dx, dy) <= radius_m:
                    return True
    return False
