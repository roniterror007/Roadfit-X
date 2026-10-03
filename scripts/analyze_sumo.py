"""Analyze all prespecified microscopic scenarios without hiding abstentions."""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'experiments/sumo_control'
OUT = ROOT/'research'


def interval(delta, rng):
    groups = delta.groupby(level=['region', 'seed']).mean().to_numpy()
    boots = rng.choice(groups, (10000, len(groups)), replace=True).mean(axis=1)
    return {'mean_delta_s': float(delta.mean()), 'seed_cluster_95ci_s': list(np.quantile(boots, [.025, .975])),
            'clusters': len(groups)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=BASE)
    parser.add_argument('--output', type=Path, default=OUT)
    args = parser.parse_args()
    base, out = args.input.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    frame = pd.read_csv(base/'episodes.csv')
    keys = ['region', 'signal', 'seed', 'trips', 'adoption']
    pivot = frame.pivot(index=keys, columns='method', values='penalized_mean_s')
    rng = np.random.default_rng(123)
    comparisons = [('roadfit', m) for m in ['static', 'dsp', 'rksp', 'ebksp', 'roadfit_no_future', 'roadfit_no_locality']]
    comparisons += [('roadfit_soft_locality', m) for m in ['static', 'dsp', 'rksp', 'ebksp', 'roadfit', 'roadfit_no_locality']]
    # New variants: compare against all baselines and the original RoadFit.
    for variant in ['roadfit_graduated', 'roadfit_queue_aware']:
        if variant in pivot.columns:
            comparisons += [(variant, m) for m in ['static', 'dsp', 'rksp', 'ebksp', 'roadfit', 'roadfit_soft_locality']]
    effects = {}
    for left, right in comparisons:
        if left in pivot.columns and right in pivot.columns:
            effects[f'{left}_minus_{right}'] = {**interval(pivot[left]-pivot[right], rng),
                'mean_relative_reduction_pct': float(((pivot[right]-pivot[left])/pivot[right]*100).mean())}
    signal_pivot = frame[frame.method == 'static'].pivot(index=['region', 'seed', 'trips', 'adoption'], columns='signal', values='penalized_mean_s')
    # Static routing ignores compliance, so these duplicate pairs must not be
    # treated as extra independent observations (clustering already groups them).
    signals = interval(signal_pivot.actuated-signal_pivot.static, rng)
    signals['mean_relative_reduction_pct'] = float(((signal_pivot.static-signal_pivot.actuated)/signal_pivot.static*100).mean())
    frame['assignment_pct'] = frame.assigned/frame.trips*100
    frame['arrival_pct'] = frame.arrived/frame.trips*100
    frame['local_m_per_request'] = frame.local_distance_km*1000/frame.trips
    trips = pd.read_csv(base/'trips.csv')
    # Descriptive diagnostics only; these are not new independent samples or
    # a basis for selecting controller parameters on the evaluation seeds.
    class_diagnostics = []
    for (region, method, kind), group in trips.groupby(['region', 'method', 'kind']):
        class_diagnostics.append({'region': region, 'method': method, 'kind': kind,
            'requests': len(group), 'assigned_pct': float(group.assigned.mean()*100),
            'arrived_pct': float(group.arrived.mean()*100),
            'capped_request_mean_s': float(group.penalized_time_s.mean()),
            'planned_local_m_per_request': float(group.local_distance_m.mean())})
    result = {'episodes': len(pivot), 'policy_runs': len(frame),
        'means': frame.groupby('method').mean(numeric_only=True).to_dict('index'),
        'per_region': frame.groupby(['region', 'method']).penalized_mean_s.mean().unstack().to_dict('index'),
        'per_load': frame.groupby(['trips', 'method']).penalized_mean_s.mean().unstack().to_dict('index'),
        'vehicle_diagnostics': class_diagnostics,
        'incomplete_policy_runs': int((frame.arrived < frame.trips).sum()),
        'unassigned_requests': int((~trips.assigned).sum()),
        'unfinished_assigned_requests': int((trips.assigned & ~trips.arrived).sum()),
        'contrasts': effects, 'actuated_minus_static_signal_under_static_routing': signals,
        'collision_vehicle_events': int(frame.collision_vehicle_events.sum()), 'teleports': int(frame.teleport_events.sum()),
        'analysis_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'episodes_sha256': hashlib.sha256((base/'episodes.csv').read_bytes()).hexdigest(),
        'limitation': 'Ten region-seed clusters, two uncalibrated regional networks. Departure-only adaptations, not full published experiment reproductions.'}
    (out/'sumo_analysis.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    plt.rcParams.update({'font.size': 10, 'figure.dpi': 170})
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.6), constrained_layout=True)
    methods = ['static', 'dsp', 'rksp', 'ebksp', 'roadfit', 'roadfit_no_future', 'roadfit_no_locality',
               'roadfit_soft_locality', 'roadfit_graduated', 'roadfit_queue_aware']
    labels = ['Static', 'DSP adaptation', 'RkSP adaptation', 'EBkSP adaptation', 'RoadFit original',
              'Without future', 'Without locality', 'Soft locality only', 'Graduated locality', 'Queue-aware']
    means = frame.groupby('method').penalized_mean_s.mean()
    available_methods = [m for m in methods if m in means.index]
    available_labels = [labels[methods.index(m)] for m in available_methods]
    colors = {'static': '#94a3b8', 'dsp': '#94a3b8', 'rksp': '#94a3b8', 'ebksp': '#94a3b8',
              'roadfit': '#b91c1c', 'roadfit_no_future': '#64748b', 'roadfit_no_locality': '#64748b',
              'roadfit_soft_locality': '#0f766e', 'roadfit_graduated': '#1d4ed8', 'roadfit_queue_aware': '#15803d'}
    axes[0].barh(range(len(available_methods)), [means[m] for m in available_methods],
                 color=[colors.get(m, '#94a3b8') for m in available_methods])
    axes[0].set_yticks(range(len(available_methods)), available_labels); axes[0].invert_yaxis()
    axes[0].set_xlabel('Mean capped request-time score (seconds)')
    axes[0].set_title('SUMO: includes unassigned and unfinished requests')
    # Paired intervals: compare best new variant against baselines.
    best_variant = 'roadfit_queue_aware' if 'roadfit_queue_aware' in pivot.columns else 'roadfit_soft_locality'
    compare_against = ['static', 'dsp', 'ebksp', 'roadfit']
    available_comparisons = [m for m in compare_against if f'{best_variant}_minus_{m}' in effects]
    selected = [f'{best_variant}_minus_{m}' for m in available_comparisons]
    if selected:
        y = np.array([effects[m]['mean_delta_s'] for m in selected])
        low = np.array([effects[m]['seed_cluster_95ci_s'][0] for m in selected])
        high = np.array([effects[m]['seed_cluster_95ci_s'][1] for m in selected])
        axes[1].errorbar(range(len(selected)), y, yerr=[y-low, high-y], fmt='o', capsize=5, color='#15803d')
        axes[1].axhline(0, linestyle='--', color='#64748b', lw=1)
        axes[1].set_xticks(range(len(selected)), [m.split('_minus_')[1].replace('_', ' ').title() for m in selected],
                           rotation=20, ha='right')
        axes[1].set_ylabel(f'{best_variant.replace("_", " ").title()} minus comparison (seconds)')
        axes[1].set_title('Paired region–seed cluster 95% intervals')
    for extension in ['png', 'svg']:
        fig.savefig(out/f'sumo_results.{extension}')
    plt.close(fig)
    print(json.dumps({k:v for k,v in result.items() if k not in ['means', 'per_region', 'per_load', 'vehicle_diagnostics']}, indent=2))


if __name__ == '__main__':
    main()
