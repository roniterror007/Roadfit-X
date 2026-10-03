"""Convert frozen OSM regional graphs to SUMO with explicit synthetic signals."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import osmnx as ox
import sumolib

from src.routing.anticipatory import free_time, LOCAL
from src.vehicle.geometry_constraints import compute_geometry_margins, _parse_osm_float, highway_category
from src.vehicle.profiles import make_vehicle, legal_access

ROOT = Path(__file__).resolve().parents[1]
CLASSES = {'motorcycle': 'motorcycle', 'hatchback': 'passenger', 'truck': 'truck'}


def write_xml(root, path):
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def prepare(region, signal):
    source = ROOT / f'data/public/bengaluru_regions/{region}.graphml'
    graph = ox.load_graphml(source)
    out = ROOT / f'data/public/sumo/{region}-{signal}'
    out.mkdir(parents=True, exist_ok=True)
    lon0 = min(float(d['x']) for _, d in graph.nodes(data=True))
    lat0 = min(float(d['y']) for _, d in graph.nodes(data=True))
    def xy(lon, lat):
        return ((float(lon)-lon0)*111320*math.cos(math.radians(lat0)), (float(lat)-lat0)*111320)
    # Eight high-degree junctions get assumed signal plans, held equal across
    # policies. They are not claimed to be surveyed Bengaluru signal locations.
    junctions = [n for n in graph if len(set(graph.predecessors(n)) | set(graph.successors(n))) >= 4]
    junctions.sort(key=lambda n: (-graph.degree(n), n))
    signals = set(junctions[:8])
    nodes, edges = ET.Element('nodes'), ET.Element('edges')
    for n, d in graph.nodes(data=True):
        x, y = xy(d['x'], d['y'])
        attrs = dict(id=str(n), x=str(x), y=str(y), type='traffic_light' if n in signals else 'priority')
        if n in signals:
            attrs['tlType'] = signal
        ET.SubElement(nodes, 'node', attrs)
    metadata = {}
    for i, (u, v, k, d) in enumerate(graph.edges(keys=True, data=True)):
        if u == v:
            continue
        permitted = []
        for kind, vclass in CLASSES.items():
            vehicle = make_vehicle(kind, 'exploratory')
            if legal_access(d, kind, terminal=False) and all(x is not None and x >= 0 for x in compute_geometry_margins(d, vehicle).values()):
                if kind != 'truck' or highway_category(d) not in LOCAL:
                    permitted.append(vclass)
        if not permitted:
            continue
        lanes = _parse_osm_float(d.get('lanes'), 1.)
        if str(d.get('oneway', False)).lower() not in {'true', 'yes', '1'}:
            lanes /= 2
        lanes = min(3, max(1, int(lanes)))
        length = max(5., float(d.get('length', 1.)))
        speed = length/free_time({**d, 'length': length}, 'hatchback')
        edge = 'e'+str(i)
        attrs = dict(id=edge, **{'from': str(u), 'to': str(v)}, numLanes=str(lanes),
                     speed=str(speed), length=str(length), allow=' '.join(permitted), width='3.2',
                     priority='3' if highway_category(d) not in LOCAL else '1')
        shape = d.get('geometry')
        if shape is not None:
            attrs['shape'] = ' '.join(f'{a:.3f},{b:.3f}' for a, b in [xy(x, y) for x, y in shape.coords])
        ET.SubElement(edges, 'edge', attrs)
        metadata[edge] = dict(original=[u, v, k], highway=highway_category(d), lanes=lanes,
                              building_count_50m=float(d.get('building_count_50m', 0.)),
                              allowed=permitted, from_lonlat=[graph.nodes[u]['x'], graph.nodes[u]['y']],
                              to_lonlat=[graph.nodes[v]['x'], graph.nodes[v]['y']])
    write_xml(nodes, out/'nodes.nod.xml'); write_xml(edges, out/'edges.edg.xml')
    command = [sumolib.checkBinary('netconvert'), '--node-files', str(out/'nodes.nod.xml'),
               '--edge-files', str(out/'edges.edg.xml'), '--output-file', str(out/'network.net.xml'),
               '--lefthand', 'true', '--no-turnarounds', 'true', '--tls.cycle.time', '60', '--seed', '41']
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    (out/'conversion.log').write_text(run.stdout+run.stderr, encoding='utf-8')
    if run.returncode:
        raise RuntimeError((run.stdout+run.stderr)[-3000:])
    net = sumolib.net.readNet(str(out/'network.net.xml'))
    manifest = {'region': region, 'signal': signal, 'signal_junctions': sorted(signals),
                'edges': {e.getID(): metadata[e.getID()] for e in net.getEdges()},
                'source_graph_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'network_sha256': hashlib.sha256((out/'network.net.xml').read_bytes()).hexdigest(),
                'sumo_version': '1.27.1', 'converter_command': command,
                'assumptions': ['Eight high-degree junctions have generated 60-second static or SUMO actuated signal programs.',
                    'Lane counts use OSM where available; lane width 3.2m and missing physical limits are assumptions.',
                    'Trucks use a main-road-only cohort; destination-only access is excluded for every through path.',
                    'No Bengaluru demand, signal timing or car-following calibration has been performed.']}
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'{region} {signal}: {len(net.getEdges())} edges, {len(net.getTrafficLights())} signal controllers', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--regions', nargs='+', default=['Indiranagar', 'Hebbal'])
    args = parser.parse_args()
    for name in args.regions:
        for mode in ['static', 'actuated']:
            prepare(name, mode)
