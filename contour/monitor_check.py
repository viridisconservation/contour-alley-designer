"""Check field observations against a designer run's predictions.

    python3 -m contour.monitor_check --run runs/hall --observations obs.csv

obs.csv columns: indicator,year,value. Prints one status per row (on_track / watch /
off_track / no_prediction) and writes <run>/observation_check.json.
"""
import argparse
import csv
import json
from pathlib import Path

from .monitoring import check_observations


def load_predictions(path):
    with open(path) as f:
        return [{'indicator': r['indicator'], 'year': int(r['year']), 'low': float(r['low']),
                 'central': float(r['central']), 'high': float(r['high'])} for r in csv.DictReader(f)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    ap.add_argument('--observations', required=True)
    a = ap.parse_args()
    pred = load_predictions(Path(a.run) / 'predictions.csv')
    with open(a.observations) as f:
        obs = list(csv.DictReader(f))
    res = check_observations(pred, obs)
    for r in res:
        print(f"{r['indicator']:<32} yr {r['year']:>3}  {float(r['value']):>10.3f}  {r['status']}")
    (Path(a.run) / 'observation_check.json').write_text(json.dumps(res, indent=2) + '\n')


if __name__ == '__main__':
    main()
