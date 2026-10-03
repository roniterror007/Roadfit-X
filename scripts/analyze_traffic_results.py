"""Seed-cluster paired contrasts and exportable scientific plots from raw episodes."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'research'
EXPERIMENTS = {'TNTP': ('traffic_control_final', 'dataset'),
               'Bengaluru random ODs': ('bengaluru_control_final', 'region'),
               'Bengaluru truck arterial ODs': ('bengaluru_arterial', 'region')}


def main():
    OUT.mkdir(exist_ok=True)
    all_results = {}
    contrasts = [('balanced', 'static'), ('balanced', 'reactive'), ('balanced', 'no_future'),
                 ('balanced', 'no_social'), ('balanced', 'no_locality'), ('balanced', 'bpr_balanced')]
    for name, (folder, network) in EXPERIMENTS.items():
        frame = pd.read_csv(ROOT/f'experiments/{folder}/episodes.csv')
        indices = [network, 'seed', 'capacity_scale'] + (['adoption'] if 'adoption' in frame else [])
        means = frame.groupby('method').mean(numeric_only=True).to_dict('index')
        pivot = frame.pivot(index=indices, columns='method', values='penalized_mean_s')
        effects = {}
        rng = np.random.default_rng(123)
        for left, right in contrasts:
            # Keep load/adoption conditions sharing a random seed together.
            delta = pivot[left]-pivot[right]
            groups = delta.groupby(level=[network, 'seed']).mean().to_numpy()
            boots = rng.choice(groups, (10000, len(groups)), replace=True).mean(axis=1)
            effects[f'{left}_minus_{right}'] = {'mean_delta_s': float(delta.mean()),
                'seed_cluster_95ci_s': list(np.quantile(boots, [.025, .975])), 'clusters': len(groups),
                'mean_relative_reduction_pct': float(((pivot[right]-pivot[left])/pivot[right]*100).mean())}
        all_results[name] = {'means': means, 'contrasts': effects,
                             'episodes': int(len(frame)/frame.method.nunique()), 'methods': sorted(frame.method.unique())}
    eta = json.loads((ROOT/'experiments/eta_sample/results.json').read_text())
    all_results['ETA recorded Chengdu sample'] = eta
    (OUT/'analysis.json').write_text(json.dumps(all_results, indent=2), encoding='utf-8')
    plt.rcParams.update({'font.size': 11, 'figure.dpi': 160})
    figure, axes = plt.subplots(1, 2, figsize=(10.6, 4.2), constrained_layout=True)
    comparisons = ['balanced_minus_static', 'balanced_minus_no_future', 'balanced_minus_no_social', 'balanced_minus_bpr_balanced']
    labels = ['Static ETA', 'No arrival forecast', 'No social cost', 'BPR balanced']
    values = all_results['TNTP']['contrasts']
    x = np.arange(len(labels))
    y = np.array([values[key]['mean_delta_s'] for key in comparisons])
    low = np.array([values[key]['seed_cluster_95ci_s'][0] for key in comparisons])
    high = np.array([values[key]['seed_cluster_95ci_s'][1] for key in comparisons])
    axes[0].errorbar(x, y, yerr=[y-low, high-y], fmt='o', color='#0f766e', capsize=5)
    axes[0].axhline(0, color='#64748b', linestyle='--', linewidth=1)
    axes[0].set_xticks(x, labels, rotation=20, ha='right')
    axes[0].set_ylabel('Balanced − comparison delay (seconds)')
    axes[0].set_title('Synthetic TNTP scenarios; seed-cluster 95% CI')
    methods = ['median_speed', 'distance_linear', 'hist_full', 'hist_no_clock', 'hist_no_geometry', 'hist_distance_only']
    axes[1].barh(range(6), [eta['metrics'][m]['mae_min'] for m in methods], color=['#94a3b8', '#94a3b8', '#0f766e', '#64748b', '#64748b', '#64748b'])
    axes[1].set_yticks(range(6), ['Median speed', 'Distance linear', 'Route + clock', 'Without clock', 'Without geometry', 'Distance only'])
    axes[1].invert_yaxis(); axes[1].set_xlabel('Held-out MAE (minutes)')
    axes[1].set_title('Recorded Chengdu sample; 1,400 test trips')
    for extension in ['png', 'svg', 'pdf']:
        figure.savefig(OUT/f'results.{extension}')
    plt.close(figure)
    figure, axis = plt.subplots(figsize=(7.5, 4.2), constrained_layout=True)
    frame = pd.read_csv(ROOT/'experiments/bengaluru_arterial/trips.csv')
    selected = frame[frame.method.isin(['static', 'balanced', 'no_locality'])]
    means = selected.groupby(['vehicle', 'method']).local_distance_m.mean().unstack().reindex(['motorcycle', 'hatchback', 'truck'])
    means[['static', 'balanced', 'no_locality']].plot.bar(ax=axis, color=['#94a3b8', '#0f766e', '#f59e0b'], rot=0)
    axis.set_ylabel('Local-street metres per requested trip')
    axis.set_xlabel('Vehicle class (truck endpoints restricted to connected main roads)')
    axis.set_title('Bengaluru topology; synthetic traffic and vehicle limits')
    axis.legend(['Static', 'Balanced', 'Without locality'], title=None)
    for extension in ['png', 'svg', 'pdf']:
        figure.savefig(OUT/f'locality.{extension}')
    plt.close(figure)
    print(json.dumps({name: value.get('contrasts') for name, value in all_results.items() if 'contrasts' in value}, indent=2))


if __name__ == '__main__':
    main()
