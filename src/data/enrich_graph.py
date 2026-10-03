"""Seeded synthetic constraints with a separate evaluator-only truth graph."""
from pathlib import Path
import argparse
import numpy as np
import osmnx as ox
from src.vehicle.geometry_constraints import WIDTH_PRIORS, highway_category

def synthetic_benchmark_graphs(graph, drop_rate=.3, seed=42):
    if not 0 <= drop_rate <= 1:
        raise ValueError('drop_rate must be in [0, 1]')
    observed, truth = graph.copy(), graph.copy()
    rng = np.random.default_rng(seed)
    roads = {}
    for u, v, key, data in observed.edges(keys=True, data=True):
        # Same OSM road in opposite directions shares latent geometry and missingness.
        road = (tuple(sorted((str(u), str(v)))), str(data.get('osmid', key)))
        if road not in roads:
            mean, std = WIDTH_PRIORS.get(highway_category(data), (4., 1.))
            width = round(max(.8, rng.normal(mean, std)), 3)
            height = round(max(2., rng.normal(4.5, .4)), 3)
            nominal_weight = 20. if highway_category(data) in ('motorway', 'trunk', 'primary') else 10.
            maxweight = round(max(1., nominal_weight + rng.normal(0., 2.)), 3)
            roads[road] = (width, height, maxweight, bool(rng.random() < drop_rate))
        width, height, maxweight, hidden = roads[road]
        for tag, value in [('width', width), ('maxheight', height), ('maxweight', maxweight)]:
            truth[u][v][key][tag] = value
            truth[u][v][key][f'{tag}_source'] = 'synthetic_truth'
            data[tag] = value
            data[f'{tag}_source'] = 'synthetic_observation'
        # Width-only missingness isolates the effect of missing lateral constraints.
        if hidden:
            data.pop('width', None)
            data['width_source'] = 'missing'
        for attrs in (data, truth[u][v][key]):
            # Remove legacy or hidden simulator state from the model inputs.
            for name in list(attrs):
                if name.startswith('_') or name.startswith('truth_'):
                    attrs.pop(name)
    for g, role in [(observed, 'observed'), (truth, 'evaluation_only')]:
        g.graph.update(constraint_basis='synthetic', constraint_seed=seed,
                       width_missing_rate=drop_rate, data_role=role)
    return observed, truth

def enrich_graph_widths(input_path, output_path, drop_rate=.3, seed=42, truth_path=None):
    graph = ox.load_graphml(input_path)
    observed, truth = synthetic_benchmark_graphs(graph, drop_rate, seed)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    truth_file = Path(truth_path) if truth_path else output.with_name(output.stem + '_truth.graphml')
    truth_file.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(observed, output)
    ox.save_graphml(truth, truth_file)
    print(f'Synthetic observations: {output}; evaluator-only synthetic truth: {truth_file}')
    return observed, truth

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='data/koramangala_enhanced.graphml')
    parser.add_argument('--output', default='data/koramangala_synthetic_observed.graphml')
    parser.add_argument('--truth')
    parser.add_argument('--drop-rate', type=float, default=.3)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    enrich_graph_widths(args.input, args.output, args.drop_rate, args.seed, args.truth)

if __name__ == '__main__':
    main()
