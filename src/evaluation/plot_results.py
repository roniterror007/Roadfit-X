"""Plot descriptive synthetic results without asserting a Pareto frontier."""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

MODELS = ['B0 ETA', 'B1 Hard constraints', 'RF Exact', 'RF Rounded',
          'RF Union bound', 'RF Strict', 'RF Exploratory']

def generate_plots(csv_path, output_dir=None):
    path = Path(csv_path)
    output = Path(output_dir) if output_dir else path.parent
    output.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(path)
    summaries = []
    for (model, rate), group in df.groupby(['Model', 'MissingRate']):
        routes = group[group['Feasible']==1]
        summaries.append({
            'Model': model, 'MissingRate': rate,
            'Return': group['Feasible'].mean()*100.,
            'Failure': routes['PhysicalFailure'].mean()*100.,
            'SafeSuccess': group['SafeSuccess'].mean()*100.,
            'ETTP': routes['ETTP_%'].mean(),
            'Runtime': group['Runtime_ms'].median(),
        })
    table = pd.DataFrame(summaries)
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout='constrained')
    for model in MODELS:
        data = table[table['Model']==model].sort_values('MissingRate')
        for ax, column in zip(axes.flat, ['Return', 'Failure', 'SafeSuccess', 'Runtime']):
            ax.plot(data['MissingRate']*100, data[column], marker='o', label=model, linewidth=1.8)
    for ax, title in zip(axes.flat, ['Returned routes (%)', 'Physical violations among returned routes (%)',
                                   'Returned and physically feasible queries (%)', 'Median routing time (ms)']):
        ax.set(xlabel='Missing width observations (%)', ylabel=title)
        ax.grid(alpha=.2)
    axes[1, 1].set_yscale('log')
    axes[0, 0].legend(fontsize=8, loc='best')
    fig.suptitle('RoadFit ablations: synthetic constraints on one OSM topology')
    for extension in ('png', 'svg'):
        fig.savefig(output/f'ablation_tradeoffs.{extension}', dpi=200)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 5), layout='constrained')
    for model in MODELS:
        data = df[(df['Model']==model)&(df['Feasible']==1)&np.isclose(df['MissingRate'], .3)]
        values = np.sort(data['MinClearance_m'].dropna().to_numpy())
        if len(values):
            ax.step(values, np.arange(1, len(values)+1)/len(values), where='post', label=model)
    ax.axvline(0., linestyle='--', color='black', linewidth=1)
    ax.set(xlabel='Bottleneck clearance after vehicle buffer (m)', ylabel='Empirical cumulative fraction',
           title='Held-out synthetic clearance; 30% width missingness; returned routes')
    ax.grid(alpha=.2)
    ax.legend(fontsize=8)
    for extension in ('png', 'svg'):
        fig.savefig(output/f'clearance_cdf.{extension}', dpi=200)
    plt.close(fig)
    print(f'Figures written to {output}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', default='experiments/reproducible/ablations.csv')
    parser.add_argument('--output-dir')
    args = parser.parse_args()
    generate_plots(args.csv, args.output_dir)
