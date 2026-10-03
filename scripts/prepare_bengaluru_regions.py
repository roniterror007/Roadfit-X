"""Extract six reproducible city regions from the acquired expanded OSM network."""
import hashlib
import json
from pathlib import Path
import osmnx as ox

ROOT = Path(__file__).resolve().parents[1]
REGIONS = {'Yeshwantpur': (77.557, 13.023), 'Hebbal': (77.599, 13.035),
           'Indiranagar': (77.640, 12.974), 'Whitefield': (77.752, 12.966),
           'Jayanagar': (77.583, 12.926), 'ElectronicCity': (77.670, 12.846)}


def main():
    source = ROOT/'data/public/bengaluru_drive.graphml'
    graph = ox.load_graphml(source)
    out = ROOT/'data/public/bengaluru_regions'
    out.mkdir(exist_ok=True)
    records = {}
    for name, (lon, lat) in REGIONS.items():
        nodes = [n for n, d in graph.nodes(data=True) if abs(float(d['x'])-lon) <= .012 and abs(float(d['y'])-lat) <= .012]
        sub = graph.subgraph(nodes).copy()
        sub.graph.update(region=name, region_bbox_wsen=json.dumps([lon-.012, lat-.012, lon+.012, lat+.012]),
                         extraction='induced coordinate rectangle; boundary routes may be disconnected')
        path = out/f'{name}.graphml'
        ox.save_graphml(sub, path)
        records[name] = {'nodes': len(sub), 'edges': sub.number_of_edges(), 'bbox': [lon-.012, lat-.012, lon+.012, lat+.012],
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        print(f'{name}: {len(sub)} nodes, {sub.number_of_edges()} edges', flush=True)
    (out/'manifest.json').write_text(json.dumps({'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                                               'regions': records, 'boundary_limit': 'Induced subgraphs do not allow paths outside the rectangle.'}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
