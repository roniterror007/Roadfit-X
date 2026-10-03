"""Acquire OSM topology/building centres and TNTP benchmarks with dated hashes.

No traffic counts, road widths, or dwelling counts are manufactured by this script.
OSM building footprints are an incomplete mapped-building proxy, not a census.
"""
import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import osmnx as ox
from osmnx import _overpass
import requests
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
BBOX = (77.48, 12.82, 77.80, 13.10)  # west, south, east, north
TAGS = ['access', 'motor_vehicle', 'motorcycle', 'bicycle', 'hgv', 'motorcar',
        'width', 'maxheight', 'maxweight', 'lanes', 'maxspeed', 'surface',
        'highway', 'oneway', 'name', 'osmid', 'service', 'junction']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def acquire_tntp(out):
    api = requests.get('https://api.github.com/repos/bstabler/TransportationNetworks/commits/master', timeout=60)
    api.raise_for_status()
    revision = api.json()['sha']
    files = {}
    for folder, prefix in [('SiouxFalls', 'SiouxFalls'), ('Anaheim', 'Anaheim'), ('Chicago-Sketch', 'ChicagoSketch')]:
        target = out / 'tntp' / folder
        target.mkdir(parents=True, exist_ok=True)
        for name in [f'{prefix}_net.tntp', f'{prefix}_trips.tntp', 'README.md']:
            url = f'https://raw.githubusercontent.com/bstabler/TransportationNetworks/{revision}/{folder}/{name}'
            response = requests.get(url, timeout=90)
            response.raise_for_status()
            path = target / name
            path.write_bytes(response.content)
            files[str(path.relative_to(ROOT))] = {'url': url, 'sha256': digest(path)}
            print(f'TNTP {folder}/{name}: {len(response.content)} bytes', flush=True)
    (out / 'tntp' / 'manifest.json').write_text(json.dumps({
        'retrieved_utc': datetime.now(timezone.utc).isoformat(), 'revision': revision,
        'source': 'https://github.com/bstabler/TransportationNetworks', 'files': files,
        'limitations': 'Benchmark capacities/OD are supplied values. Dynamic departures and vehicle classes in our experiments are synthetic.'
    }, indent=2), encoding='utf-8')


def acquire_osm(out, buildings=True):
    out.mkdir(parents=True, exist_ok=True)
    path = out / 'bengaluru_drive.graphml'
    ox.settings.requests_timeout = 240
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(out / 'osm_cache')
    ox.settings.useful_tags_way = sorted(set(ox.settings.useful_tags_way + TAGS))
    if path.exists():
        graph = ox.load_graphml(path)
        print('Using already acquired Bengaluru topology.', flush=True)
    else:
        print(f'Acquiring expanded Bengaluru drive_service topology: {BBOX}', flush=True)
        graph = ox.graph_from_bbox(BBOX, network_type='drive_service', retain_all=False)
        graph.graph.update(constraint_basis='osm_tags_unvalidated', bbox=json.dumps(BBOX),
                           retrieved_utc=datetime.now(timezone.utc).isoformat(),
                           source='OpenStreetMap via OSMnx/Overpass', license='ODbL 1.0')
        for _, _, _, data in graph.edges(keys=True, data=True):
            for tag in ['width', 'maxheight', 'maxweight']:
                if tag in data:
                    data[tag + '_source'] = 'osm_tag_unvalidated'
        ox.save_graphml(graph, path)
        print(f'Topology saved: {len(graph)} nodes, {graph.number_of_edges()} edges.', flush=True)
    building_files = []
    centres, houses = [], []
    if buildings:
        west, south, east, north = BBOX
        raw = out / 'building_centres'
        raw.mkdir(exist_ok=True)
        # Nine bounded queries, with cached responses for interruption recovery.
        for row in range(3):
            for col in range(3):
                s, n = south+(north-south)*row/3, south+(north-south)*(row+1)/3
                w, e = west+(east-west)*col/3, west+(east-west)*(col+1)/3
                query = f'[out:json][timeout:180];way["building"]({s},{w},{n},{e});out tags center;'
                tile = raw / f'{row}_{col}.json'
                if not tile.exists():
                    # Use OSMnx's Overpass client (headers, throttling, cache and
                    # server-status handling), as for the successful map request.
                    payload = _overpass._overpass_request({'data': query})
                    if 'remark' in payload:
                        raise RuntimeError(f'Incomplete building query: {payload["remark"]}')
                    tile.write_text(json.dumps(payload), encoding='utf-8')
                payload = json.loads(tile.read_text(encoding='utf-8'))
                building_files.append({'file': str(tile.relative_to(ROOT)), 'sha256': digest(tile), 'query': query})
                for element in payload['elements']:
                    c = element.get('center')
                    if c:
                        centres.append((element['id'], c['lon'], c['lat']))
                        if element.get('tags', {}).get('building') in {'house', 'detached', 'semidetached_house', 'terrace'}:
                            houses.append(element['id'])
                print(f'Building tile {row},{col}: {len(payload["elements"])} mapped footprints.', flush=True)
        unique = {item[0]: item[1:] for item in centres}
        ordered = list(unique)
        xy = np.array([unique[i] for i in ordered])
        projection = np.array([111320*math.cos(math.radians(13)), 111320])
        tree = cKDTree(xy * projection) if len(xy) else None
        house_ids = set(houses)
        for u, v, _, data in graph.edges(keys=True, data=True):
            geometry = data.get('geometry')
            if geometry is not None:
                midpoint = geometry.interpolate(.5, normalized=True)
                point = np.array([midpoint.x, midpoint.y])
            else:
                point = np.array([(graph.nodes[u]['x']+graph.nodes[v]['x'])/2,
                                  (graph.nodes[u]['y']+graph.nodes[v]['y'])/2])
            nearby = tree.query_ball_point(point*projection, 50) if tree else []
            data['building_count_50m'] = len(nearby)
            data['mapped_house_footprints_50m'] = sum(ordered[i] in house_ids for i in nearby)
            data['locality_source'] = 'osm_building_centres_50m_incomplete'
        graph.graph['mapped_building_count'] = len(unique)
        graph.graph['locality_basis'] = 'mapped footprint centroids within 50m of edge midpoint; not dwelling or vehicle counts'
        ox.save_graphml(graph, path)
    coverage = {tag: sum(tag in d for _, _, _, d in graph.edges(keys=True, data=True))
                for tag in ['width', 'maxheight', 'maxweight', 'lanes', 'maxspeed', 'building_count_50m']}
    manifest = {'retrieved_utc': graph.graph['retrieved_utc'], 'bbox_wsen': BBOX,
                'nodes': len(graph), 'edges': graph.number_of_edges(), 'tags_coverage_edges': coverage,
                'map_sha256': digest(path), 'building_tiles': building_files,
                'mapped_buildings': graph.graph.get('mapped_building_count'),
                'license': 'OpenStreetMap contributors, ODbL 1.0',
                'limitations': 'OSM topology and tags are unvalidated. No live traffic/parking/road-width imagery measurements. Building mapping is incomplete and footprint counts do not count households.'}
    (out / 'bengaluru_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in manifest.items() if k != 'building_tiles'}, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', choices=['osm', 'tntp'], required=True)
    parser.add_argument('--no-buildings', action='store_true')
    args = parser.parse_args()
    if args.dataset == 'tntp':
        acquire_tntp(ROOT / 'data' / 'public')
    else:
        acquire_osm(ROOT / 'data' / 'public', not args.no_buildings)
