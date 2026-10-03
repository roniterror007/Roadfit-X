"""Held-out ETA prediction on the authors' DeepTTE sample (not a DeepTTE reproduction).

Features exclude elapsed time, time_gap, future timestamps, states and driver IDs.
Route geometry/distance are assumed supplied by a planned route at departure.
"""
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def features(records):
    rows = []
    for row in records:
        t = float(row['timeID'])/1440*2*math.pi
        w = float(row['weekID'])/7*2*math.pi
        rows.append([float(row['dist']), float(row['lngs'][0]), float(row['lats'][0]),
                     float(row['lngs'][-1]), float(row['lats'][-1]),
                     math.sin(t), math.cos(t), math.sin(w), math.cos(w)])
    return np.array(rows, dtype=float)


def errors(truth, prediction):
    error = prediction-truth
    return {'mae_min': float(np.mean(abs(error))), 'rmse_min': float(np.sqrt(np.mean(error**2))),
            'mape_pct': float(np.mean(abs(error)/truth)*100), 'bias_min': float(np.mean(error))}


def main():
    data = ROOT / 'data/public/deeptte'
    out = ROOT / 'experiments/eta_sample'
    out.mkdir(parents=True, exist_ok=True)
    fitting = [record for path in sorted(data.glob('train_*')) if path.name != 'train_04' for record in load(path)]
    calibration = load(data/'train_04')
    train = fitting+calibration
    test = load(data/'test')
    # Audit exact duplicate trajectories across canonical files before fitting.
    def fingerprint(row):
        return hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    test_ids = {fingerprint(row) for row in test}
    overlap = sum(fingerprint(row) in test_ids for row in train)
    valid = lambda row: fingerprint(row) not in test_ids and float(row['time']) > 0 and float(row['dist']) > 0
    fitting = [row for row in fitting if valid(row)]
    calibration = [row for row in calibration if valid(row)]
    train = fitting+calibration
    test = [row for row in test if float(row['time']) > 0 and float(row['dist']) > 0]
    # Authors' config: train_00..03 fit, train_04 calibration, later dates test.
    drivers = sorted(set(str(r['driverID']) for r in train))
    rng = np.random.default_rng(41)
    if not fitting or not calibration:
        raise ValueError('Insufficient fitting/calibration records.')
    xf, xc, xt = features(fitting), features(calibration), features(test)
    # Actual sample units and paper MAE/RMSE are seconds; the README says minutes
    # inconsistently. Convert once, before fitting and scoring.
    yf, yc, yt = [np.array([float(r['time'])/60. for r in rows]) for rows in (fitting, calibration, test)]
    predictions, intervals = {}, {}
    speed = np.median(xf[:, 0]/yf)
    predictions['median_speed'] = xt[:, 0]/speed
    linear = LinearRegression().fit(xf[:, :1], yf)
    predictions['distance_linear'] = np.maximum(.01, linear.predict(xt[:, :1]))
    selections = {'hist_full': list(range(9)), 'hist_no_clock': list(range(5)),
                  'hist_no_geometry': [0, 5, 6, 7, 8], 'hist_distance_only': [0]}
    for name, columns in selections.items():
        model = HistGradientBoostingRegressor(max_iter=200, max_leaf_nodes=15,
                learning_rate=.06, l2_regularization=5., random_state=41, early_stopping=False)
        model.fit(xf[:, columns], np.log(yf))
        prediction = np.exp(model.predict(xt[:, columns]))
        predictions[name] = prediction
        calibration_prediction = np.exp(model.predict(xc[:, columns]))
        residuals = np.sort(abs(yc-calibration_prediction))
        quantile = residuals[min(len(residuals)-1, math.ceil((len(residuals)+1)*.9)-1)]
        intervals[name] = {'radius_min': float(quantile),
                           'test_coverage_pct': float(np.mean(abs(yt-prediction) <= quantile)*100),
                           'mean_interval_width_min': float(np.mean(prediction+quantile-np.maximum(0, prediction-quantile)))}
        print(f'{name}: {errors(yt, prediction)}', flush=True)
    frame = pd.DataFrame({'trip': range(len(test)), 'driver': [r['driverID'] for r in test], 'truth_min': yt, **predictions})
    frame.to_csv(out/'predictions.csv', index=False)
    groups = frame.groupby('driver').indices
    names = list(groups)
    delta = abs(predictions['hist_full']-yt)-abs(predictions['median_speed']-yt)
    boot = []
    for _ in range(2000):
        sample = rng.choice(names, len(names), replace=True)
        indices = np.concatenate([groups[name] for name in sample])
        boot.append(float(delta[indices].mean()))
    result = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'seed': 41,
              'canonical_train_records_after_overlap_removal': len(train), 'fit_records': len(fitting),
              'calibration_records': len(calibration), 'test_records': len(test), 'exact_cross_split_duplicates_removed': overlap,
              'train_drivers': len(drivers), 'test_drivers': len(names),
              'test_driver_overlap_with_fit': len(set(str(r['driverID']) for r in test) & set(str(r['driverID']) for r in fitting)),
              'train_dates': sorted(set(r['dateID'] for r in train)), 'test_dates': sorted(set(r['dateID'] for r in test)),
              'metrics': {name: errors(yt, prediction) for name, prediction in predictions.items()},
              'intervals': intervals,
              'paired_full_minus_speed_mae_driver_bootstrap_95ci_min': list(np.quantile(boot, [.025, .975])),
              'source_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'dataset_manifest_sha256': hashlib.sha256((data/'manifest.json').read_bytes()).hexdigest(),
              'raw_time_unit': 'seconds (paper Table 1 and sample time_gap); outputs in minutes',
              'split_protocol': 'authors config: train_00..03 fitting; train_04 calibration; canonical later-date test',
              'limitations': ['Chengdu sample is not Bengaluru and does not validate route choice or officer interventions.',
                             'No DeepTTE/HetETA neural implementation is reproduced; these are transparent prediction baselines and feature ablations.',
                             'Later-date calibration/test shifts may violate exchangeability; interval coverage is empirical, not guaranteed.',
                             'Feature route geometry comes from recorded trips; prospective deployment assumes equivalent planned-route geometry.']}
    (out/'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
